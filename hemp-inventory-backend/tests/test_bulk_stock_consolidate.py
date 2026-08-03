"""Setting stock consolidates duplicate Clover items onto one total.

A single logical product (esp. blank-SKU "Bulk -" items created per production
batch) can map to several Clover items at one location. The inventory view sums
them, so previously writing the entered quantity to every duplicate doubled the
total and made it impossible to lower ("keeps adding"). These tests lock in that
a stock edit now sets one canonical item to the requested total and zeros the
rest, so the merged total equals exactly what was entered.
"""
import pytest

from app.routers import inventory_router as ir


class _FakeClover:
    def __init__(self, items):
        self._items = items
        self.stock_sets: dict[str, float] = {}

    async def get_items(self, expand=None):
        return {"elements": self._items}

    async def update_item_stock(self, item_id, quantity):
        self.stock_sets[item_id] = quantity

    async def update_item(self, item_id, payload):
        return {"id": item_id}


async def test_set_consolidated_stock_zeros_extras():
    fake = _FakeClover([])
    matching = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    await ir._set_consolidated_stock(fake, matching, 3000)
    assert fake.stock_sets == {"a": 3000, "b": 0, "c": 0}


async def test_set_consolidated_stock_single_item_plain_set():
    fake = _FakeClover([])
    await ir._set_consolidated_stock(fake, [{"id": "only"}], 42)
    assert fake.stock_sets == {"only": 42}


@pytest.fixture
def patched_bulk(monkeypatch):
    # HQ holds two duplicate blank-SKU bulk items summing to 5859.
    items = [
        {"id": "dup1", "sku": None,
         "name": "Bulk - Green Crack THC Smalls Flower Grams", "itemStock": {"quantity": 3800}},
        {"id": "dup2", "sku": None,
         "name": "Bulk - Green Crack THC Smalls Flower Grams", "itemStock": {"quantity": 2059}},
    ]
    fake = _FakeClover(items)

    async def fake_locations(db, location_ids=None):
        return [(3, "HQ", "HQ_MID", "tok")]

    async def noop():
        return None

    monkeypatch.setattr(ir, "CloverClient", lambda mid, tok: fake)
    monkeypatch.setattr(ir, "_get_locations", fake_locations)
    monkeypatch.setattr(ir, "_invalidate_cache", noop)
    monkeypatch.setattr(ir, "invalidate_product_cache", lambda: None)
    yield fake


async def test_bulk_stock_update_lowers_total_across_duplicates(patched_bulk):
    fake = patched_bulk
    # Frontend references one duplicate by clover_item_id and asks for a lower
    # total (3000) than the current summed stock (5859).
    req = ir.BulkStockUpdateRequest(updates=[
        ir.BulkStockUpdateItem(
            sku="SYNTH-SKU", location_id=3, quantity=3000,
            item_name="Bulk - Green Crack THC Smalls Flower Grams",
            clover_item_id="dup1",
        )
    ])
    res = await ir.bulk_stock_update(req, user={}, db=None)

    assert res["total_updated"] == 1
    # Canonical item set to the requested total; the duplicate zeroed.
    assert fake.stock_sets["dup1"] == 3000
    assert fake.stock_sets["dup2"] == 0
    # Merged total now equals what was entered, not 3000 + 2059.
    assert fake.stock_sets["dup1"] + fake.stock_sets["dup2"] == 3000
