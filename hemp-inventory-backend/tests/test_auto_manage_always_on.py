import pytest

from app.routers import inventory_router as inv


async def test_bulk_auto_manage_rejects_disable_without_clover_calls(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append(("init", args, kwargs))

    async def unexpected_locations(*args, **kwargs):
        calls.append(("locations", args, kwargs))
        return [(1, "West", "WEST", "token")]

    monkeypatch.setattr(inv, "CloverClient", FakeClient)
    monkeypatch.setattr(inv, "_get_locations", unexpected_locations)

    with pytest.raises(inv.HTTPException) as exc_info:
        await inv.bulk_auto_manage(
            inv.BulkAutoManageRequest(enable=False),
            user={},
            db=None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Stock tracking cannot be disabled; it is always on for every item."
    )
    assert calls == []


async def test_item_update_forces_auto_manage_true(monkeypatch):
    updates = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get_items(self):
            return {"elements": [{
                "id": "ITEM-1",
                "sku": "SKU-1",
                "name": "Test item",
            }]}

        async def update_item(self, item_id, payload):
            updates.append((item_id, payload))

    async def fake_locations(db, location_ids=None):
        return [(1, "West", "WEST", "token")]

    async def fake_invalidate_cache():
        return None

    monkeypatch.setattr(inv, "CloverClient", FakeClient)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "_invalidate_cache", fake_invalidate_cache)

    result = await inv.update_item(
        "SKU-1",
        inv.ItemUpdate(auto_manage=False),
        user={},
        db=None,
    )

    assert result["results"] == [{
        "location": "West",
        "status": "updated",
        "count": 1,
    }]
    assert updates == [("ITEM-1", {"autoManage": True})]
