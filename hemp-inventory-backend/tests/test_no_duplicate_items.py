import pytest

from app.routers import inventory_router as inv


def _item_request(**overrides):
    data = {
        "name": "Test Item",
        "price": 1000,
        "sku": "SKU-1",
    }
    data.update(overrides)
    return inv.ItemCreate(**data)


async def test_create_item_rejects_existing_name_before_creating(monkeypatch):
    locations = [
        (1, "West", "WEST", "token-west"),
        (2, "East", "EAST", "token-east"),
    ]
    create_calls = []
    get_items_calls = []

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = merchant_id

        async def get_items(self):
            get_items_calls.append(self.merchant_id)
            if self.merchant_id == "WEST":
                return {"elements": [{"id": "WEST-1", "name": " test   item "}]}
            return {"elements": []}

        async def create_item(self, data):
            create_calls.append(data)
            return {"id": "NEW-1"}

    async def fake_locations(db, location_ids=None):
        return locations

    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    with pytest.raises(inv.HTTPException) as exc_info:
        await inv.create_item(_item_request(), user={}, db=None)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "'Test Item' already exists at West. "
        "Edit the existing item instead of creating a duplicate."
    )
    assert get_items_calls == ["WEST", "EAST"]
    assert create_calls == []


async def test_create_item_succeeds_when_absent(monkeypatch):
    locations = [(1, "West", "WEST", "token-west")]
    create_calls = []

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = merchant_id

        async def get_items(self):
            return {"elements": []}

        async def create_item(self, data):
            create_calls.append(data)
            return {"id": "NEW-1"}

    async def fake_locations(db, location_ids=None):
        return locations

    async def fake_invalidate_cache():
        return None

    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)
    monkeypatch.setattr(inv, "_invalidate_cache", fake_invalidate_cache)

    result = await inv.create_item(_item_request(), user={}, db=None)

    assert result["results"] == [{
        "location": "West",
        "clover_id": "NEW-1",
        "status": "created",
    }]
    assert len(create_calls) == 1


async def test_push_item_rejects_existing_target_item(monkeypatch):
    source = (1, "West", "WEST", "token-west")
    target = (2, "East", "EAST", "token-east")
    create_calls = []

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = merchant_id

        async def get_items(self, expand=""):
            if self.merchant_id == "EAST":
                return {"elements": [{
                    "id": "EAST-EXISTING",
                    "name": "Test Item",
                    "sku": "SKU-1",
                }]}
            return {"elements": [{
                "id": "WEST-1",
                "name": "Test Item",
                "sku": "SKU-1",
                "price": 1000,
            }]}

        async def create_item(self, data):
            create_calls.append(data)
            return {"id": "NEW-1"}

    async def fake_locations(db, location_ids=None):
        return [target] if location_ids else [source, target]

    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    with pytest.raises(inv.HTTPException) as exc_info:
        await inv.push_item_to_location(
            "SKU-1",
            inv.PushToLocationRequest(location_id=2),
            user={},
            db=None,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "status": "already_exists",
        "location": "East",
        "item_id": "EAST-EXISTING",
    }
    assert create_calls == []


async def test_create_item_group_rejects_existing_name(monkeypatch):
    locations = [(1, "West", "WEST", "token-west")]
    create_calls = []

    class FakeClient:
        def __init__(self, merchant_id, *args):
            self.merchant_id = merchant_id

        async def get_item_groups(self):
            return {"elements": [{"id": "GROUP-1", "name": "Test Group"}]}

        async def get_items(self):
            return {"elements": []}

        async def create_item_group(self, name):
            create_calls.append(name)
            return {"id": "NEW-GROUP"}

    async def fake_locations(db, location_ids=None):
        return locations

    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    req = inv.ItemGroupCreate(
        name="Test Group",
        price=1000,
        variants=[inv.VariantOption(attribute_name="Size", option_names=["Small"])],
    )
    with pytest.raises(inv.HTTPException) as exc_info:
        await inv.create_item_group(req, user={}, db=None)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "'Test Group' already exists at West. "
        "Edit the existing item instead of creating a duplicate."
    )
    assert create_calls == []
