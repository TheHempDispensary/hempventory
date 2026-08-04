"""Finishing a packaged batch deducts from its linked bulk product.

Recipe: packaged product -> bulk item + bulk-used-per-unit. On Done, the batch
adds units to the packaged SKU (existing behavior) AND subtracts
units x per_unit from the bulk item's HQ stock, matching the user's examples:
100 x 3.5g jars pull 350g of bulk; 100 x 10-counts pull 1000 gummies.
"""
import aiosqlite
import pytest

from app.routers import production_router as pr
from app.routers import ecommerce_router as er


class _FakeClover:
    def __init__(self, items):
        self._items = items
        self.stock_sets: dict[str, float] = {}

    async def get_items(self, expand=None):
        return {"elements": self._items}

    async def update_item_stock(self, item_id, quantity):
        self.stock_sets[item_id] = quantity
        for it in self._items:
            if it["id"] == item_id:
                it["itemStock"] = {"quantity": quantity}


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("""
        CREATE TABLE bulk_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            packaged_key TEXT NOT NULL UNIQUE,
            packaged_name TEXT NOT NULL,
            packaged_sku TEXT,
            bulk_name TEXT NOT NULL,
            bulk_per_unit REAL NOT NULL DEFAULT 0
        )
    """)
    await conn.commit()
    yield conn
    await conn.close()


def _patch_clover(monkeypatch, items):
    fake = _FakeClover(items)
    monkeypatch.setattr(er, "HQ_MERCHANT_ID", "HQ", raising=False)
    monkeypatch.setattr(er, "HQ_API_TOKEN", "tok", raising=False)
    monkeypatch.setattr(pr, "CloverClient", lambda mid, tok: fake)
    return fake


async def test_deduct_from_bulk_lowers_total_and_consolidates(monkeypatch):
    fake = _patch_clover(monkeypatch, [
        {"id": "b1", "name": "Bulk - Green Crack THC Flower Grams", "itemStock": {"quantity": 700}},
        {"id": "b2", "name": "Bulk - Green Crack THC Flower Grams", "itemStock": {"quantity": 300}},
    ])
    res = await pr._deduct_from_bulk("Bulk - Green Crack THC Flower Grams", 350)
    assert res["ok"] and res["previous"] == 1000 and res["new"] == 650
    # Combined total reduced to 650 and consolidated onto one record.
    assert fake.stock_sets["b1"] == 650 and fake.stock_sets["b2"] == 0


async def test_deduct_never_below_zero(monkeypatch):
    _patch_clover(monkeypatch, [
        {"id": "b1", "name": "Bulk - Nerds THC Flower Grams", "itemStock": {"quantity": 100}},
    ])
    res = await pr._deduct_from_bulk("Bulk - Nerds THC Flower Grams", 500)
    assert res["ok"] and res["new"] == 0


async def test_apply_bulk_deduction_units_times_per_unit(monkeypatch, db):
    fake = _patch_clover(monkeypatch, [
        {"id": "g", "name": "Bulk - Delta 9 THC 10mg Gummies", "itemStock": {"quantity": 2000}},
    ])
    await db.execute(
        "INSERT INTO bulk_recipes (packaged_key, packaged_name, bulk_name, bulk_per_unit) VALUES (?,?,?,?)",
        (pr._normalise_sales_name("Delta 9 10mg 10-count"), "Delta 9 10mg 10-count",
         "Bulk - Delta 9 THC 10mg Gummies", 10),
    )
    await db.commit()
    # 100 ten-counts -> 1000 gummies pulled -> 2000 - 1000 = 1000.
    res = await pr._apply_bulk_deduction(db, "Delta 9 10mg 10-count", "", 100)
    assert res is not None and res["ok"] and res["new"] == 1000
    assert fake.stock_sets["g"] == 1000


async def test_apply_bulk_deduction_no_recipe_is_noop(monkeypatch, db):
    _patch_clover(monkeypatch, [])
    res = await pr._apply_bulk_deduction(db, "Some Unlinked Product", "", 50)
    assert res is None
