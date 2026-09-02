import os
import tempfile
import time

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_new_item_velocity_test.db"))

import aiosqlite
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


def test_product_days_uses_own_history_not_store_window():
    latest = 1_000_000_000.0
    first = latest - 29 * 86400  # product first sold 29 days ago
    days = inv._product_days_of_data(first, latest, days_of_data=200.0)
    assert days == 29.0


def test_product_days_bounded_by_overall_window():
    latest = 1_000_000_000.0
    first = latest - 500 * 86400
    assert inv._product_days_of_data(first, latest, days_of_data=200.0) == 200.0


def test_product_days_floor_for_brand_new_items():
    latest = 1_000_000_000.0
    first = latest - 2 * 86400  # sold for the first time 2 days ago
    assert inv._product_days_of_data(first, latest, days_of_data=200.0) == inv._MIN_VELOCITY_DAYS


def test_product_days_without_first_sale_falls_back():
    assert inv._product_days_of_data(None, 1_000_000_000.0, days_of_data=200.0) == 200.0
    assert inv._product_days_of_data(float("inf"), 1_000_000_000.0, days_of_data=200.0) == 200.0


async def test_smart_par_new_item_velocity(db, monkeypatch):
    """A product first sold 29 days ago must not have its velocity diluted by
    the store's full (200-day) order history."""
    latest = time.time()
    old_ts = latest - 200 * 86400
    new_first_ts = latest - 29 * 86400

    orders = [
        {"createdTime": old_ts * 1000, "total": 1000,
         "lineItems": {"elements": [{"name": "OLD PRODUCT", "unitQty": 10000}]}},
        {"createdTime": new_first_ts * 1000, "total": 1000,
         "lineItems": {"elements": [{"name": "NEW PRODUCT", "unitQty": 20000}]}},
        {"createdTime": latest * 1000, "total": 1000,
         "lineItems": {"elements": [{"name": "NEW PRODUCT", "unitQty": 7000}]}},
    ]

    async def fake_orders(client):
        return orders

    async def fake_locations(_db):
        return [(1, "East", "M1", "tok")]

    async def fake_sync(_db):
        return {"items": [
            {"name": "NEW PRODUCT", "sku": "NEW1", "categories": [], "price": 3500,
             "locations": {"East": {"stock": 4}}},
            {"name": "OLD PRODUCT", "sku": "OLD1", "categories": [], "price": 800,
             "locations": {"East": {"stock": 5}}},
        ]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "_do_sync", fake_sync)
    monkeypatch.setitem(inv._smart_par_cache, "data", None)
    monkeypatch.setitem(inv._smart_par_cache, "updated_at", 0)

    res = await inv.smart_par(months=3, user={}, db=db)
    by_sku = {p["sku"]: p for p in res["products"]}

    new = by_sku["NEW1"]
    assert new["units_sold"] == 27
    # 27 sold over its own 29 days -> ~28/mo, not 27/200days -> ~4/mo
    assert new["units_per_month"] > 25
    assert new["par_level"] > 75

    old = by_sku["OLD1"]
    # Old product still averaged over the whole window: 10/200days -> ~1.5/mo
    assert old["units_per_month"] < 2


async def test_smart_par_ignores_stale_cache_without_first_sale(db, monkeypatch):
    """A cache written before first-sale tracking existed must be recomputed."""
    latest = time.time()

    async def fake_orders(client):
        return [{"createdTime": latest * 1000, "total": 1000,
                 "lineItems": {"elements": [{"name": "NEW PRODUCT", "unitQty": 5000}]}}]

    async def fake_locations(_db):
        return [(1, "East", "M1", "tok")]

    async def fake_sync(_db):
        return {"items": [
            {"name": "NEW PRODUCT", "sku": "NEW1", "categories": [], "price": 3500,
             "locations": {"East": {"stock": 0}}},
        ]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "_do_sync", fake_sync)
    monkeypatch.setitem(inv._smart_par_cache, "data", {
        "sales_by_product": {"new product": 999},
        "earliest_ts": latest - 100 * 86400,
        "latest_ts": latest,
    })
    monkeypatch.setitem(inv._smart_par_cache, "updated_at", time.time())

    res = await inv.smart_par(months=3, user={}, db=db)
    by_sku = {p["sku"]: p for p in res["products"]}
    assert by_sku["NEW1"]["units_sold"] == 5  # recomputed, not the stale 999


async def test_auto_set_par_new_item_velocity(db, monkeypatch):
    """Per-location PAR must also use the item's own sales history."""
    latest = time.time()
    old_ts = latest - 200 * 86400
    new_first_ts = latest - 29 * 86400

    await db.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)",
        ("East", "M1", "tok"),
    )
    await db.commit()
    loc_id = (await (await db.execute("SELECT id FROM locations")).fetchone())["id"]

    async def fake_orders(client):
        return [
            {"createdTime": old_ts * 1000, "total": 1000,
             "lineItems": {"elements": [{"name": "OLD PRODUCT", "unitQty": 1000}]}},
            {"createdTime": new_first_ts * 1000, "total": 1000,
             "lineItems": {"elements": [{"name": "NEW PRODUCT", "unitQty": 20000}]}},
            {"createdTime": latest * 1000, "total": 1000,
             "lineItems": {"elements": [{"name": "NEW PRODUCT", "unitQty": 7000}]}},
        ]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def get_items(self, expand=""):
            return {"elements": [
                {"id": "C1", "sku": "NEW1", "name": "NEW PRODUCT",
                 "itemStock": {"quantity": 4}},
            ]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    await inv._run_auto_set_par(1, db)
    par = await (await db.execute(
        "SELECT par_level FROM par_levels WHERE sku = ? AND location_id = ?",
        ("NEW1", loc_id),
    )).fetchone()
    # 27 units over its own 29 days -> PAR ~28 for one month, not ~4
    assert par is not None
    assert par["par_level"] > 25
