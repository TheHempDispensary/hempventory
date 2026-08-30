from app.routers.inventory_router import _hq_locations_for_bulk, _is_hq

LOCATIONS = [
    (1, "East", "east-merchant", "east-token"),
    (2, "West", "west-merchant", "west-token"),
    (3, "HQ", "hq-merchant", "hq-token"),
]


def test_bulk_items_only_target_hq():
    assert _hq_locations_for_bulk(LOCATIONS, "Bulk - Green Crack THC Flower Grams") == [
        LOCATIONS[2]
    ]


def test_bulk_detection_ignores_case_and_spacing():
    assert _hq_locations_for_bulk(LOCATIONS, "bulk -  Nerds Baby Js") == [LOCATIONS[2]]


def test_retail_items_target_every_location():
    assert _hq_locations_for_bulk(LOCATIONS, "THC Flower Green Crack Sativa 3.5 Grams") == LOCATIONS


def test_bulk_at_retail_only_leaves_no_target():
    assert _hq_locations_for_bulk(LOCATIONS[:2], "Bulk - Nerds THC Flower Grams") == []


def test_hq_name_matching():
    assert _is_hq(" hq ")
    assert not _is_hq("East")
    assert not _is_hq("")
