"""LeafLife sync collapses duplicate Clover items for one SKU.

Duplicate Clover items sharing a SKU previously left one copy stale (below the
price floor) and double-counted stock in the merged inventory view, because the
sync only tracked/updated a single item per SKU. These tests lock in that the
sync now keeps one canonical item per SKU, deletes the extras, and floors the
kept item's price.
"""
import pytest

from app.routers import inventory_router as ir

FLOWER_TAB = "Retail Flower Menu"


def _flower_row(strain, tier, prices):
    """prices = (28g, 14g, 7g, 3.5g) dollar strings."""
    row = [""] * 16
    row[1] = "1000"      # inventory grams
    row[2] = tier
    row[3] = strain
    row[8] = "Hybrid"
    row[12], row[13], row[14], row[15] = prices
    return row


class _FakeClover:
    """Records item mutations so the test can assert dedupe + floor."""

    def __init__(self, merchant_id, api_token):
        # Two duplicate items for LF-HEADBAND-3.5: one stale/underpriced, one ok.
        self.items = [
            {"id": "stale", "sku": "LF-HEADBAND-3.5",
             "name": "HEADBAND ESSENTIAL 3.5 GRAMS", "price": 1750,
             "itemStock": {"quantity": 161}},
            {"id": "ok", "sku": "LF-HEADBAND-3.5",
             "name": "HEADBAND ESSENTIAL 3.5 GRAMS", "price": 2500,
             "itemStock": {"quantity": 161}},
        ]
        self.deleted = []
        self.updates = {}
        self.stock_updates = {}

    async def get_categories(self):
        return {"elements": [{"id": "cat-flower", "name": "Flower"}]}

    async def create_category(self, name):
        return {"id": f"cat-{name}", "name": name}

    async def get_items(self, expand=None):
        return {"elements": [i for i in self.items if i["id"] not in self.deleted]}

    async def update_item(self, item_id, payload):
        self.updates.setdefault(item_id, {}).update(payload)
        return {"id": item_id}

    async def update_item_stock(self, item_id, qty):
        self.stock_updates[item_id] = qty

    async def create_item(self, data):
        return {"id": "new"}

    async def assign_category(self, item_id, cat_id):
        return {}

    async def delete_item(self, item_id):
        self.deleted.append(item_id)


class _FakeDB:
    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


@pytest.fixture
def patched(monkeypatch):
    fake = _FakeClover("HQ", "tok")

    monkeypatch.setattr(ir, "CloverClient", lambda mid, tok: fake)

    async def fake_fetch(tab):
        if tab == FLOWER_TAB:
            return [_flower_row("HEADBAND", "ESSENTIAL",
                                ("$60", "$40", "$30", "$17.50"))]
        return []

    async def fake_age(client, name, n):
        return None

    async def noop_cache():
        return None

    monkeypatch.setattr(ir, "_fetch_leaflife_sheet", fake_fetch)
    monkeypatch.setattr(ir, "_get_age_restriction_obj", fake_age)
    monkeypatch.setattr(ir, "_invalidate_cache", noop_cache)
    yield fake


async def test_sync_dedupes_and_floors(patched):
    fake = patched
    res = await ir.run_leaflife_sync(_FakeDB())

    # Exactly one of the two duplicate items is deleted.
    assert fake.deleted == ["ok"], fake.deleted
    assert res["removed"] == 1

    # The surviving canonical item is floored to $25.00 (was $17.50).
    assert fake.updates.get("stale", {}).get("price") == 2500
