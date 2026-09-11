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


async def test_apply_bulk_deduction_no_recipe_reports_unlinked(monkeypatch, db):
    """Linking is manual: an unlinked product deducts nothing, but the operator
    has to be told rather than the batch quietly finishing."""
    fake = _patch_clover(monkeypatch, [
        {"id": "b", "name": "Bulk - Some Unlinked Product Flower Grams", "itemStock": {"quantity": 100}},
    ])
    res = await pr._apply_bulk_deduction(db, "Some Unlinked Product Flower 1 Gram", "", 50)
    assert res is not None and res["ok"] is False and res["unlinked"]
    assert "not linked" in res["reason"]
    assert fake.stock_sets == {}


async def _batch(db, name, sku=""):
    await db.execute("""
        CREATE TABLE IF NOT EXISTS production_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT, product_name TEXT, bulk_deducted INTEGER DEFAULT 0
        )
    """)
    cur = await db.execute(
        "INSERT INTO production_batches (sku, product_name, bulk_deducted) VALUES (?,?,0)",
        (sku, name),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM production_batches WHERE id = ?", (cur.lastrowid,))
    return await cur.fetchone()


async def test_deduct_bulk_once_is_idempotent(monkeypatch, db):
    fake = _patch_clover(monkeypatch, [
        {"id": "g", "name": "Bulk - Green Crack THC Ground Flower Grams", "itemStock": {"quantity": 1000}},
    ])
    await db.execute(
        "INSERT INTO bulk_recipes (packaged_key, packaged_name, bulk_name, bulk_per_unit) VALUES (?,?,?,?)",
        (pr._normalise_sales_name("Green Crack Ground 3.5 grams"), "Green Crack Ground 3.5 grams",
         "Bulk - Green Crack THC Ground Flower Grams", 3.5),
    )
    await db.commit()
    row = await _batch(db, "Green Crack Ground 3.5 grams")
    # First finish: 100 x 3.5g = 350 pulled -> 650, and the batch is flagged.
    res = await pr._deduct_bulk_once(db, row["id"], row, 100)
    assert res is not None and res["ok"] and res["new"] == 650
    cur = await db.execute("SELECT bulk_deducted FROM production_batches WHERE id = ?", (row["id"],))
    assert (await cur.fetchone())["bulk_deducted"] == 1

    # Re-finishing the same batch must not deduct again.
    cur = await db.execute("SELECT * FROM production_batches WHERE id = ?", (row["id"],))
    row2 = await cur.fetchone()
    res2 = await pr._deduct_bulk_once(db, row2["id"], row2, 100)
    assert res2 is None
    assert fake._items[0]["itemStock"]["quantity"] == 650


async def test_deduct_bulk_once_no_recipe_leaves_flag_unset(monkeypatch, db):
    _patch_clover(monkeypatch, [])
    row = await _batch(db, "Unlinked Packaged Item")
    res = await pr._deduct_bulk_once(db, row["id"], row, 10)
    assert res is not None and res["ok"] is False
    cur = await db.execute("SELECT bulk_deducted FROM production_batches WHERE id = ?", (row["id"],))
    assert (await cur.fetchone())["bulk_deducted"] == 0


async def test_recipe_total_grams_mistaken_for_per_unit_is_ignored(monkeypatch, db):
    """Recipes saved with a batch total (504 for a 28g bag) must not zero the bulk;
    the weight in the product name is what a unit uses."""
    fake = _patch_clover(monkeypatch, [
        {"id": "b", "name": "Bulk - Skywalker OG THC Smalls Flower Grams", "itemStock": {"quantity": 1000}},
    ])
    await db.execute(
        "INSERT INTO bulk_recipes (packaged_key, packaged_name, bulk_name, bulk_per_unit) VALUES (?,?,?,?)",
        (pr._normalise_sales_name("THC FLOWER SMALLS SKYWALKER OG Indica 28 GRAMS"),
         "THC FLOWER SMALLS SKYWALKER OG Indica 28 GRAMS",
         "Bulk - Skywalker OG THC Smalls Flower Grams", 504),
    )
    await db.commit()
    res = await pr._apply_bulk_deduction(db, "THC FLOWER SMALLS SKYWALKER OG Indica 28 GRAMS", "", 2)
    assert res["ok"] and res["deducted"] == 56 and fake.stock_sets["b"] == 944
