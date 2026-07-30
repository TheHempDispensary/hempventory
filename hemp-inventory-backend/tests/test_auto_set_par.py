import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_auto_par_test.db"))

import aiosqlite
import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import inventory_router as inv
from app.routers.ecommerce_router import HQ_MERCHANT_ID


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

    res = await inv.auto_set_par(inv.AutoSetParRequest(months=1), user={}, db=db)
    assert res["total_set"] == 1

    par = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?",
        ("HQGREEN", hq_loc_id),
    )).fetchone()
    assert par is not None
    assert par["par_level"] > 0  # 12 units of website Green Crack -> non-zero PAR
