import os
import tempfile
import time

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_sales_item_id_test.db"))

import aiosqlite
import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import inventory_router as inv


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


def _order(ts: float, *lines: dict) -> dict:
    return {"createdTime": ts * 1000, "total": 1000, "lineItems": {"elements": list(lines)}}


def test_tally_matches_renamed_item_by_clover_id():
    """Sales rung up under an old name still count for the renamed product."""
    t = inv._SalesTally({"C1"})
    t.add_clover_orders([
        _order(100, {"name": "LEMON CHERRY GELATO SMALLS FLOWER 2 GRAMS", "unitQty": 3000,
                     "item": {"id": "C1"}}),
        _order(200, {"name": "THC Flower Smalls Lemon Cherry Gelato Hybrid 2 Grams",
                     "unitQty": 1000, "item": {"id": "C1"}}),
    ])
    units, first = t.product_sales("THC Flower Smalls Lemon Cherry Gelato Hybrid 2 Grams", ["C1"])
    assert units == 4
    assert first == 100


def test_tally_does_not_double_count_id_and_name():
    t = inv._SalesTally({"C1"})
    t.add_clover_orders([_order(100, {"name": "WIDGET", "unitQty": 2000, "item": {"id": "C1"}})])
    units, _ = t.product_sales("WIDGET", ["C1"])
    assert units == 2
    # Bestseller view still sees every line by name.
    assert t.by_name["widget"] == 2


def test_tally_falls_back_to_name_for_unknown_item_id():
    """Lines from deleted catalog items (or website orders) match by name."""
    t = inv._SalesTally({"C1"})
    t.add_clover_orders([
        _order(100, {"name": "WIDGET", "unitQty": 1000, "item": {"id": "GONE"}}),
        _order(100, {"name": "WIDGET", "unitQty": 1000}),
    ])
    t.add_ecommerce_rows([("WIDGET", 2, "2026-06-01T00:00:00")])
    units, _ = t.product_sales("WIDGET", ["C1"])
    assert units == 4


def test_tally_skips_refunds_and_voids():
    t = inv._SalesTally({"C1"})
    t.add_clover_orders([
        {"createdTime": 1000, "total": -500, "lineItems": {"elements": [
            {"name": "WIDGET", "unitQty": 1000, "item": {"id": "C1"}}]}},
        _order(100, {"name": "WIDGET", "unitQty": 1000, "item": {"id": "C1"}, "refunded": True}),
        _order(100, {"name": "WIDGET", "unitQty": 1000, "item": {"id": "C1"}}),
    ])
    assert t.product_sales("WIDGET", ["C1"])[0] == 1


def test_tally_cache_roundtrip():
    t = inv._SalesTally({"C1"})
    t.add_clover_orders([_order(100, {"name": "WIDGET", "unitQty": 1000, "item": {"id": "C1"}})])
    t2 = inv._SalesTally.from_cache(t.to_cache())
    assert t2.product_sales("WIDGET", ["C1"]) == (1, 100)
    assert t2.latest_ts == 100


async def test_fetch_all_clover_orders_raises_on_partial_pull():
    class FlakyClient:
        calls = 0

        async def get_orders(self, **kw):
            FlakyClient.calls += 1
            if kw["offset"] == 0:
                return {"elements": [{"id": str(i)} for i in range(100)]}
            raise RuntimeError("Clover 502")

    with pytest.raises(RuntimeError):
        await inv._fetch_all_clover_orders(FlakyClient())


async def test_smart_par_uses_clover_item_id(db, monkeypatch):
    latest = time.time()
    first = latest - 30 * 86400

    async def fake_orders(client):
        return [
            _order(first, {"name": "OLD NAME 2 GRAMS", "unitQty": 10000, "item": {"id": "E1"}}),
            _order(latest, {"name": "NEW NAME 2 GRAMS", "unitQty": 5000, "item": {"id": "W1"}}),
            # Deleted catalog item: only the name is left to match on.
            _order(latest, {"name": "NEW NAME 2 GRAMS", "unitQty": 1000, "item": {"id": "ZZZ"}}),
        ]

    async def fake_locations(_db):
        return [(1, "East", "M1", "tok")]

    async def fake_sync(_db):
        return {"items": [
            {"name": "NEW NAME 2 GRAMS", "sku": "S1", "categories": [], "price": 1000,
             "locations": {"East": {"stock": 0, "clover_item_id": "E1"},
                           "West": {"stock": 0, "clover_item_id": "W1"}}},
        ]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "_do_sync", fake_sync)
    monkeypatch.setitem(inv._smart_par_cache, "data", None)
    monkeypatch.setitem(inv._smart_par_cache, "updated_at", 0)

    res = await inv.smart_par(months=1, user={}, db=db)
    prod = {p["sku"]: p for p in res["products"]}["S1"]
    assert prod["units_sold"] == 16
    assert res["meta"]["days_of_data"] == pytest.approx(30, abs=0.1)


async def test_smart_par_recomputes_cache_without_item_ids(db, monkeypatch):
    latest = time.time()

    async def fake_orders(client):
        return [_order(latest, {"name": "WIDGET", "unitQty": 5000, "item": {"id": "E1"}})]

    async def fake_locations(_db):
        return [(1, "East", "M1", "tok")]

    async def fake_sync(_db):
        return {"items": [{"name": "WIDGET", "sku": "S1", "categories": [], "price": 1000,
                           "locations": {"East": {"stock": 0, "clover_item_id": "E1"}}}]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "_do_sync", fake_sync)
    monkeypatch.setitem(inv._smart_par_cache, "data", {
        "sales_by_product": {"widget": 999}, "first_sale_ts": {},
        "earliest_ts": latest - 100 * 86400, "latest_ts": latest,
    })
    monkeypatch.setitem(inv._smart_par_cache, "updated_at", time.time())

    res = await inv.smart_par(months=1, user={}, db=db)
    assert {p["sku"]: p for p in res["products"]}["S1"]["units_sold"] == 5


async def test_auto_set_par_aborts_instead_of_zeroing(db, monkeypatch):
    await db.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)",
        ("East", "M1", "tok"),
    )
    await db.commit()
    loc_id = (await (await db.execute("SELECT id FROM locations")).fetchone())["id"]
    await db.execute(
        "INSERT INTO par_levels (sku, location_id, par_level) VALUES (?, ?, ?)",
        ("S1", loc_id, 40.0),
    )
    await db.commit()

    async def failing_orders(client):
        raise RuntimeError("Clover 502")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def get_items(self, expand=""):
            return {"elements": [{"id": "C1", "sku": "S1", "name": "WIDGET"}]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", failing_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    with pytest.raises(RuntimeError):
        await inv._run_auto_set_par(1, db)
    par = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?", ("S1", loc_id)
    )).fetchone()
    assert par["par_level"] == 40.0


async def test_auto_set_par_matches_by_item_id(db, monkeypatch):
    latest = time.time()
    await db.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)",
        ("East", "M1", "tok"),
    )
    await db.commit()
    loc_id = (await (await db.execute("SELECT id FROM locations")).fetchone())["id"]

    async def fake_orders(client):
        return [
            _order(latest - 30 * 86400, {"name": "OLD NAME", "unitQty": 20000, "item": {"id": "C1"}}),
            _order(latest, {"name": "NEW NAME", "unitQty": 10000, "item": {"id": "C1"}}),
        ]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def get_items(self, expand=""):
            return {"elements": [{"id": "C1", "sku": "S1", "name": "NEW NAME"}]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    await inv._run_auto_set_par(1, db)
    par = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?", ("S1", loc_id)
    )).fetchone()
    # 30 units over 30 days -> ~30/mo (name-only matching would have given 10)
    assert par["par_level"] == pytest.approx(30, abs=1)
