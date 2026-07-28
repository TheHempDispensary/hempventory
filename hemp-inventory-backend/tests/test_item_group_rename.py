"""Tests for renaming a Clover variant group (the only supported way to rename
items that belong to a group), including the LeafLife exclusion."""
import pytest

from app.routers import inventory_router as ir


def test_is_leaflife_group_by_sku():
    assert ir._is_leaflife_group("Sour Diesel Everyday", ["LF-SOUR-DIESEL-3.5"])
    assert not ir._is_leaflife_group("Tahoe OG Smalls", ["THD-TAHOE-3.5", ""])


def test_is_leaflife_group_by_tier_word():
    # Tier words mark LeafLife retail flower even if a SKU wasn't returned.
    assert ir._is_leaflife_group("BLUE DREAM ESSENTIAL", [])
    assert ir._is_leaflife_group("Purple Power Premium", [""])
    assert not ir._is_leaflife_group("Everyday Low Vibes OG", [])  # word not at the end


class _FakeClover:
    """Minimal Clover stand-in that simulates a group rename cascading to the
    variant items' names (group name + size option)."""

    # merchant_id -> {group_id: {"name": str, "items": [{"size": str, "sku": str}]}}
    store: dict[str, dict[str, dict]] = {}

    def __init__(self, merchant_id, api_token):
        self.merchant_id = merchant_id

    def _groups(self):
        return self.store.get(self.merchant_id, {})

    async def get_item_groups(self):
        elements = []
        for gid, g in self._groups().items():
            elements.append({
                "id": gid,
                "name": g["name"],
                "items": {"elements": [
                    {"name": f"{g['name']} {it['size']}", "sku": it["sku"]}
                    for it in g["items"]
                ]},
            })
        return {"elements": elements}

    async def update_item_group(self, group_id, name):
        self._groups()[group_id]["name"] = name
        return {"id": group_id, "name": name}

    async def get_item_group(self, group_id):
        g = self._groups()[group_id]
        return {
            "id": group_id,
            "name": g["name"],
            "items": {"elements": [
                {"name": f"{g['name']} {it['size']}", "sku": it["sku"]}
                for it in g["items"]
            ]},
        }


@pytest.fixture
def fake_clover(monkeypatch):
    _FakeClover.store = {
        "WEST": {"g1": {"name": "THC FLOWER SMALLS TAHOE OG",
                        "items": [{"size": "3.5 GRAMS", "sku": "T-3.5"},
                                  {"size": "7 GRAMS", "sku": "T-7"}]}},
        "EAST": {"g2": {"name": "THC FLOWER SMALLS TAHOE OG",
                        "items": [{"size": "3.5 GRAMS", "sku": "T-3.5"},
                                  {"size": "7 GRAMS", "sku": "T-7"}]}},
        "HQ": {"g3": {"name": "SOUR DIESEL EVERYDAY",
                      "items": [{"size": "3.5 GRAMS", "sku": "LF-SOUR-DIESEL-3.5"}]}},
    }

    async def fake_locations(db, location_ids=None):
        return [
            (1, "West", "WEST", "tok"),
            (2, "East", "EAST", "tok"),
            (3, "HQ", "HQ", "tok"),
        ]

    monkeypatch.setattr(ir, "CloverClient", _FakeClover)
    monkeypatch.setattr(ir, "_get_locations", fake_locations)
    yield
    _FakeClover.store = {}


async def test_rename_cascades_across_locations(fake_clover):
    res = await ir.rename_item_group(
        ir.ItemGroupRename(
            current_name="THC FLOWER SMALLS TAHOE OG",
            new_name="THC FLOWER SMALLS TAHOE OG INDICA",
        ),
        user={}, db=None,
    )
    by_loc = {r["location"]: r for r in res["results"]}
    assert by_loc["West"]["status"] == "renamed"
    assert by_loc["East"]["status"] == "renamed"
    # Strain type lands before the size, applied to every variant.
    assert by_loc["West"]["item_names"] == [
        "THC FLOWER SMALLS TAHOE OG INDICA 3.5 GRAMS",
        "THC FLOWER SMALLS TAHOE OG INDICA 7 GRAMS",
    ]
    # HQ doesn't carry this group -> not found, not an error.
    assert by_loc["HQ"]["status"] == "not_found"


async def test_rename_refuses_leaflife(fake_clover):
    with pytest.raises(ir.HTTPException) as exc:
        await ir.rename_item_group(
            ir.ItemGroupRename(
                current_name="SOUR DIESEL EVERYDAY",
                new_name="SOUR DIESEL EVERYDAY SATIVA",
            ),
            user={}, db=None,
        )
    assert exc.value.status_code == 400
    # The LeafLife group name must be untouched.
    assert _FakeClover.store["HQ"]["g3"]["name"] == "SOUR DIESEL EVERYDAY"


async def test_rename_covers_duplicate_named_groups(monkeypatch):
    # Clover holds several groups with the same name (a stale empty one + the
    # active one); the rename must hit every match so the live group is covered.
    _FakeClover.store = {
        "WEST": {
            "stale": {"name": "THC FLOWER BLUE DREAM", "items": []},
            "live": {"name": "THC FLOWER BLUE DREAM",
                     "items": [{"size": "3.5 GRAMS", "sku": "BD-3.5"}]},
        },
    }

    async def fake_locations(db, location_ids=None):
        return [(1, "West", "WEST", "tok")]

    monkeypatch.setattr(ir, "CloverClient", _FakeClover)
    monkeypatch.setattr(ir, "_get_locations", fake_locations)

    res = await ir.rename_item_group(
        ir.ItemGroupRename(
            current_name="THC FLOWER BLUE DREAM",
            new_name="THC FLOWER BLUE DREAM SATIVA",
        ),
        user={}, db=None,
    )
    west = next(r for r in res["results"] if r["location"] == "West")
    assert west["status"] == "renamed"
    assert west["groups_renamed"] == 2
    assert "THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS" in west["item_names"]
    assert _FakeClover.store["WEST"]["stale"]["name"] == "THC FLOWER BLUE DREAM SATIVA"
    _FakeClover.store = {}


async def test_rename_rejects_noop(fake_clover):
    with pytest.raises(ir.HTTPException) as exc:
        await ir.rename_item_group(
            ir.ItemGroupRename(
                current_name="THC FLOWER SMALLS TAHOE OG",
                new_name="THC FLOWER SMALLS TAHOE OG",
            ),
            user={}, db=None,
        )
    assert exc.value.status_code == 400
