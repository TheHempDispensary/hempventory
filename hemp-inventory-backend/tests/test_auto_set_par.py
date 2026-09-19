import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_auto_par_test.db"))

import aiosqlite
import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import inventory_router as inv
from app.routers.ecommerce_router import HQ_MERCHANT_ID


def _order(ts: float, name: str, qty: int, item_id: str) -> dict:
    return {
        "createdTime": ts * 1000,
        "total": 1000,
        "lineItems": {"elements": [{
            "name": name,
            "unitQty": qty * 1000,
            "item": {"id": item_id},
        }]},
    }


async def _locations(db, specs):
    for name, merchant_id, token in specs:
        await db.execute(
            "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)",
            (name, merchant_id, token),
        )
    await db.commit()
    return await inv._get_locations(db)


@pytest_asyncio.fixture
async def db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


async def test_auto_set_par_uses_ecommerce_sales_for_hq(db, monkeypatch):
    """HQ PAR must come from website sales, not its Clover 'item 1' lines."""
    # HQ location (its Clover orders carry no product names).
    await db.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)",
        ("Hemp Dispensary HQ", HQ_MERCHANT_ID, "tok"),
    )
    row = await (await db.execute("SELECT id FROM locations")).fetchone()
    hq_loc_id = row["id"]

    # A website order selling Green Crack — this is where HQ demand actually lives.
    await db.execute(
        "INSERT INTO ecommerce_orders (order_number, status, created_at) VALUES (?, 'shipped', CURRENT_TIMESTAMP)"
        , ("HD-TEST-1",),
    )
    oid = (await (await db.execute("SELECT id FROM ecommerce_orders")).fetchone())["id"]
    await db.execute(
        "INSERT INTO ecommerce_order_items (order_id, product_name, quantity) VALUES (?, ?, ?)",
        (oid, "THC FLOWER GREEN CRACK 3.5 GRAMS", 12),
    )
    await db.commit()

    # Clover returns only generic, unnamed line items for HQ (as in production).
    async def fake_orders(client):
        return [{"createdTime": 0, "total": 5000,
                 "lineItems": {"elements": [{"name": "item 1", "unitQty": 1000}]}}]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def get_items(self, expand=""):
            return {"elements": [
                {"id": "HQGREEN", "sku": "",
                 "name": "THC FLOWER GREEN CRACK Sativa 3.5 GRAMS",
                 "itemStock": {"quantity": 0}},
            ]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    res = await inv._run_auto_set_par(1, db)
    assert res["total_set"] == 1

    par = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?",
        ("HQGREEN", hq_loc_id),
    )).fetchone()
    assert par is not None
    assert par["par_level"] > 0  # 12 units of website Green Crack -> non-zero PAR


async def test_auto_set_par_hq_uses_average_store_par(db, monkeypatch):
    latest = 1_700_000_000.0
    first = latest - 30 * 86400
    specs = [
        ("Hemp Dispensary HQ", HQ_MERCHANT_ID, "hq-token"),
        ("East", "M-EAST", "east-token"),
        ("West", "M-WEST", "west-token"),
    ]
    locations = await _locations(db, specs)
    catalogs = {
        str(HQ_MERCHANT_ID): [{"id": "HQ1", "sku": "S1", "name": "WIDGET"}],
        "M-EAST": [{"id": "E1", "sku": "S1", "name": "WIDGET"}],
        "M-WEST": [{"id": "W1", "sku": "S1", "name": "WIDGET"}],
    }
    orders = {
        str(HQ_MERCHANT_ID): [],
        "M-EAST": [
            _order(first, "WIDGET", 1, "E1"),
            _order(latest, "WIDGET", 14, "E1"),
        ],
        "M-WEST": [
            _order(first, "WIDGET", 1, "W1"),
            _order(latest, "WIDGET", 9, "W1"),
        ],
    }

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = str(merchant_id)

        async def get_items(self, expand=""):
            return {"elements": catalogs[self.merchant_id]}

    async def fake_orders(client):
        return orders[client.merchant_id]

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    result = await inv._run_auto_set_par(1, db)
    rows = await (await db.execute(
        "SELECT location_id, par_level FROM par_levels WHERE sku = ?",
        ("S1",),
    )).fetchall()
    by_location = {row["location_id"]: row["par_level"] for row in rows}
    loc_ids = {row[1]: row[0] for row in locations}

    assert by_location[loc_ids["East"]] == 15
    assert by_location[loc_ids["West"]] == 10
    assert by_location[loc_ids["Hemp Dispensary HQ"]] == 7
    hq_summary = next(
        summary for summary in result["by_location"]
        if summary["location"] == "Hemp Dispensary HQ"
    )
    assert hq_summary["with_par"] == 1


async def test_auto_set_par_hq_only_item_keeps_own_sales_par(db, monkeypatch):
    latest = 1_700_000_000.0
    first = latest - 30 * 86400
    locations = await _locations(db, [
        ("Hemp Dispensary HQ", HQ_MERCHANT_ID, "hq-token"),
    ])

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = str(merchant_id)

        async def get_items(self, expand=""):
            return {"elements": [{"id": "HQ2", "sku": "S2", "name": "HQ ONLY"}]}

    async def fake_orders(client):
        return [
            _order(first, "HQ ONLY", 1, "HQ2"),
            _order(latest, "HQ ONLY", 14, "HQ2"),
        ]

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    await inv._run_auto_set_par(1, db)
    row = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?",
        ("S2", locations[0][0]),
    )).fetchone()
    assert row["par_level"] == 15


async def test_auto_set_par_hq_matches_store_by_name_when_sku_blank(db, monkeypatch):
    latest = 1_700_000_000.0
    first = latest - 30 * 86400
    specs = [
        ("Hemp Dispensary HQ", HQ_MERCHANT_ID, "hq-token"),
        ("East", "M-EAST", "east-token"),
    ]
    locations = await _locations(db, specs)
    catalogs = {
        str(HQ_MERCHANT_ID): [{"id": "HQ3", "sku": "", "name": "SHARED WIDGET"}],
        "M-EAST": [{"id": "E3", "sku": "S3", "name": "SHARED WIDGET"}],
    }

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = str(merchant_id)

        async def get_items(self, expand=""):
            return {"elements": catalogs[self.merchant_id]}

    async def fake_orders(client):
        if client.merchant_id == str(HQ_MERCHANT_ID):
            return []
        return [
            _order(first, "SHARED WIDGET", 1, "E3"),
            _order(latest, "SHARED WIDGET", 9, "E3"),
        ]

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    await inv._run_auto_set_par(1, db)
    hq_id = next(row[0] for row in locations if row[1] == "Hemp Dispensary HQ")
    row = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?",
        ("HQ3", hq_id),
    )).fetchone()
    assert row["par_level"] == 5
