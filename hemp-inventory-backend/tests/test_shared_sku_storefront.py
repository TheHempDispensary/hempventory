from app.routers import ecommerce_router as ec


SKU = "2025754319197"


def test_shared_sku_location_stock_uses_same_named_row():
    east = {
        SKU: 3,
        f"{SKU}\x00THC SNOW CAPS GUAVA SHAKE 28 GRAMS": 3,
        f"{SKU}\x00THC WAX THREE GRAMS INDICA KING LOUIS": 0,
        f"{SKU}\x00": 1,
    }
    assert ec._location_stock_lookup(east, SKU, "THC WAX THREE GRAMS INDICA KING LOUIS") == 0
    assert ec._location_stock_lookup(east, SKU, "THC Snow Caps  Guava Shake 28 Grams") == 3
    # shared SKU, name not present at this location -> nothing, not the other item's stock
    assert ec._location_stock_lookup(east, SKU, "SOMETHING ELSE") == 0


def test_unshared_sku_location_stock_falls_back_to_sku_then_name():
    west = {"ABC": 4, "ABC\x00WIDGET": 4, "NO SKU ITEM": 2}
    assert ec._location_stock_lookup(west, "ABC", "Widget") == 4
    assert ec._location_stock_lookup(west, "ABC", "Renamed Widget") == 4
    assert ec._location_stock_lookup(west, "", "NO  SKU ITEM") == 2


def test_find_item_at_location_resolves_shared_sku_by_name():
    king = {"id": "K", "sku": SKU, "name": "THC WAX THREE GRAMS INDICA KING LOUIS"}
    snow = {"id": "S", "sku": SKU, "name": "THC Snow Caps Guava Shake 28 Grams"}
    lookup = {
        "by_id": {"K": king, "S": snow},
        "by_sku": {SKU: snow},
        "by_name": {king["name"].upper(): king, snow["name"].upper(): snow},
        "shared_skus": {SKU},
    }
    assert ec._find_item_at_location(lookup, "HQID", SKU, "THC Wax Three Grams Indica King Louis") is king
    assert ec._find_item_at_location(lookup, "HQID", SKU, "THC Snow Caps Guava Shake 28 Grams") is snow
    assert ec._find_item_at_location(lookup, "HQID", SKU, "Unknown") is None

    lookup["shared_skus"] = set()
    assert ec._find_item_at_location(lookup, "HQID", SKU, "Unknown") is snow
