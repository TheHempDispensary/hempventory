import pytest

from app.routers import inventory_router as inv


LOCATION = (7, "West", "WEST", "token")


def _item(
    item_id: str,
    *,
    sku: str | None = "SKU-1",
    code: str | None = "CODE-1",
    stock: float | None = 0,
    modified_time: int = 100,
) -> dict:
    return {
        "id": item_id,
        "name": "Test Item",
        "sku": sku,
        "code": code,
        "itemStock": {"quantity": stock},
        "hidden": False,
        "modifiedTime": modified_time,
    }


def _patch_catalog(monkeypatch, items, *, calls=None):
    class FakeClient:
        def __init__(self, *args):
            pass

        async def get_items(self, expand=""):
            return {"elements": items}

        async def update_item_stock(self, item_id, quantity):
            if calls is not None:
                calls.append(("stock", item_id, quantity))

        async def delete_item(self, item_id):
            if calls is not None:
                calls.append(("delete", item_id))

    async def fake_locations(db, location_ids=None):
        return [LOCATION]

    monkeypatch.setattr(inv, "CloverClient", FakeClient)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)


async def test_duplicates_same_code_keep_highest_stock_and_sum(monkeypatch):
    items = [
        _item("ITEM-1", stock=5, modified_time=200),
        _item("ITEM-2", stock=8, modified_time=100),
    ]
    _patch_catalog(monkeypatch, items)

    result = await inv.get_duplicate_items(user={}, db=None)

    group = result["duplicates"][0]
    assert group["mergeable"] is True
    assert group["reason"] is None
    assert group["keep_id"] == "ITEM-2"
    assert group["merged_stock"] == 13


async def test_duplicates_with_differing_codes_are_not_mergeable(monkeypatch):
    items = [
        _item("ITEM-1", code="CODE-1"),
        _item("ITEM-2", code="CODE-2"),
    ]
    _patch_catalog(monkeypatch, items)

    result = await inv.get_duplicate_items(user={}, db=None)

    group = result["duplicates"][0]
    assert group["mergeable"] is False
    assert group["reason"] == "barcodes differ"
    assert group["keep_id"] is None


async def test_one_coded_and_one_blank_code_keeps_coded_record(monkeypatch):
    items = [
        _item("ITEM-1", code="CODE-1", stock=2),
        _item("ITEM-2", code="", stock=9),
    ]
    _patch_catalog(monkeypatch, items)

    result = await inv.get_duplicate_items(user={}, db=None)

    group = result["duplicates"][0]
    assert group["mergeable"] is True
    assert group["keep_id"] == "ITEM-1"
    assert group["merged_stock"] == 11


async def test_gift_card_duplicates_are_not_mergeable(monkeypatch):
    items = [
        _item("ITEM-1", code="CLOVER_GIFT_CARD"),
        _item("ITEM-2", code="CLOVER_GIFT_CARD"),
    ]
    _patch_catalog(monkeypatch, items)

    result = await inv.get_duplicate_items(user={}, db=None)

    group = result["duplicates"][0]
    assert group["mergeable"] is False
    assert group["reason"] == "gift card"


async def test_duplicate_merge_refuses_non_mergeable_group(monkeypatch):
    items = [
        _item("ITEM-1", code="CODE-1"),
        _item("ITEM-2", code="CODE-2"),
    ]
    _patch_catalog(monkeypatch, items)

    with pytest.raises(inv.HTTPException) as exc_info:
        await inv.merge_duplicate_items(
            inv.DuplicateMergeRequest(location_id=7, name="Test Item"),
            user={},
            db=None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "barcodes differ"


async def test_duplicate_merge_dry_run_does_not_change_clover(monkeypatch):
    calls = []
    items = [
        _item("ITEM-1", stock=5, modified_time=200),
        _item("ITEM-2", stock=8, modified_time=100),
    ]
    _patch_catalog(monkeypatch, items, calls=calls)

    result = await inv.merge_duplicate_items(
        inv.DuplicateMergeRequest(location_id=7, name=" test item "),
        user={},
        db=None,
    )

    assert result["kept"] == "ITEM-2"
    assert result["deleted"] == []
    assert result["stock_set"] == 13
    assert calls == []


async def test_duplicate_merge_updates_keep_then_deletes_others(monkeypatch):
    calls = []
    items = [
        _item("ITEM-1", stock=5, modified_time=200),
        _item("ITEM-2", stock=8, modified_time=100),
    ]
    _patch_catalog(monkeypatch, items, calls=calls)

    async def fake_invalidate_cache():
        calls.append(("invalidate",))

    monkeypatch.setattr(inv, "_invalidate_cache", fake_invalidate_cache)
    result = await inv.merge_duplicate_items(
        inv.DuplicateMergeRequest(location_id=7, name="Test Item", dry_run=False),
        user={},
        db=None,
    )

    assert result["kept"] == "ITEM-2"
    assert result["deleted"] == ["ITEM-1"]
    assert result["stock_set"] == 13
    assert calls == [
        ("stock", "ITEM-2", 13),
        ("delete", "ITEM-1"),
        ("invalidate",),
    ]
