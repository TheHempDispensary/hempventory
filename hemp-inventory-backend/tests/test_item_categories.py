"""Tests for fixing wrong Clover categories: setting an item's exact category
set (which removes the ones dropped in the UI) and bulk-detaching one category
from many items."""
import pytest

from app.routers import inventory_router as ir


class _FakeClover:
    """Minimal Clover stand-in tracking item<->category associations."""

    # merchant_id -> {"items": [{"id","sku","categories":[name,...]}], "categories": [names]}
    store: dict[str, dict] = {}

    def __init__(self, merchant_id, api_token):
        self.merchant_id = merchant_id

    def _data(self):
        return self.store[self.merchant_id]

    def _cat_id(self, name):
        return f"cat-{name.lower()}"

    async def get_items(self, *args, **kwargs):
        return {"elements": [
            {
                "id": it["id"],
                "sku": it["sku"],
                "categories": {"elements": [
                    {"id": self._cat_id(c), "name": c} for c in it["categories"]
                ]},
            }
            for it in self._data()["items"]
        ]}

    async def get_categories(self):
        return {"elements": [
            {"id": self._cat_id(c), "name": c} for c in self._data()["categories"]
        ]}

    async def create_category(self, name):
        self._data()["categories"].append(name)
        return {"id": self._cat_id(name), "name": name}

    async def assign_category(self, item_id, category_id):
        for it in self._data()["items"]:
            if it["id"] == item_id:
                name = next(c for c in self._data()["categories"] if self._cat_id(c) == category_id)
                if name not in it["categories"]:
                    it["categories"].append(name)
        return {}

    async def unassign_category(self, item_id, category_id):
        for it in self._data()["items"]:
            if it["id"] == item_id:
                it["categories"] = [c for c in it["categories"] if self._cat_id(c) != category_id]


@pytest.fixture
def fake_clover(monkeypatch):
    _FakeClover.store = {
        "WEST": {
            "categories": ["Edibles", "Accessories", "Concentrates"],
            "items": [
                {"id": "i1", "sku": "BUTANE-300", "categories": ["Edibles", "Accessories"]},
                {"id": "i2", "sku": "LF-GUSH-1G", "categories": ["Concentrates", "Edibles"]},
            ],
        },
        "EAST": {
            "categories": ["Edibles", "Accessories", "Concentrates"],
            "items": [
                {"id": "e1", "sku": "BUTANE-300", "categories": ["Edibles", "Accessories"]},
                {"id": "e2", "sku": "LF-GUSH-1G", "categories": ["Concentrates", "Edibles"]},
            ],
        },
    }

    async def fake_locations(db, location_ids=None):
        return [(1, "West", "WEST", "tok"), (2, "East", "EAST", "tok")]

    async def fake_invalidate():
        return None

    monkeypatch.setattr(ir, "CloverClient", _FakeClover)
    monkeypatch.setattr(ir, "_get_locations", fake_locations)
    monkeypatch.setattr(ir, "_invalidate_cache", fake_invalidate)
    yield
    _FakeClover.store = {}


def _cats(merchant, sku):
    return next(i["categories"] for i in _FakeClover.store[merchant]["items"] if i["sku"] == sku)


async def test_set_categories_drops_the_ones_removed(fake_clover):
    res = await ir.set_item_category(
        ir.SetItemCategoryRequest(sku="BUTANE-300", category_names=["Accessories"]),
        user={}, db=None,
    )
    assert res["categories"] == ["Accessories"]
    assert _cats("WEST", "BUTANE-300") == ["Accessories"]
    assert _cats("EAST", "BUTANE-300") == ["Accessories"]


async def test_set_categories_keeps_multiple_and_adds_new(fake_clover):
    await ir.set_item_category(
        ir.SetItemCategoryRequest(sku="LF-GUSH-1G", category_names=["Concentrates", "Vapor"]),
        user={}, db=None,
    )
    assert _cats("WEST", "LF-GUSH-1G") == ["Concentrates", "Vapor"]


async def test_set_categories_empty_clears_all(fake_clover):
    await ir.set_item_category(
        ir.SetItemCategoryRequest(sku="BUTANE-300", category_names=[]),
        user={}, db=None,
    )
    assert _cats("WEST", "BUTANE-300") == []


async def test_set_category_single_name_still_replaces(fake_clover):
    # Legacy single-category payload keeps working.
    await ir.set_item_category(
        ir.SetItemCategoryRequest(sku="BUTANE-300", category_name="Accessories"),
        user={}, db=None,
    )
    assert _cats("WEST", "BUTANE-300") == ["Accessories"]


async def test_bulk_remove_category_leaves_other_categories(fake_clover):
    res = await ir.bulk_remove_category(
        ir.BulkCategoryRequest(skus=["BUTANE-300", "LF-GUSH-1G"], category_name="Edibles"),
        user={}, db=None,
    )
    assert res["total_removed"] == 4  # two items at two locations
    assert _cats("WEST", "BUTANE-300") == ["Accessories"]
    assert _cats("WEST", "LF-GUSH-1G") == ["Concentrates"]
    assert _cats("EAST", "LF-GUSH-1G") == ["Concentrates"]


async def test_bulk_remove_category_requires_a_name(fake_clover):
    with pytest.raises(ir.HTTPException) as exc:
        await ir.bulk_remove_category(
            ir.BulkCategoryRequest(skus=["BUTANE-300"], category_name="  "),
            user={}, db=None,
        )
    assert exc.value.status_code == 400
