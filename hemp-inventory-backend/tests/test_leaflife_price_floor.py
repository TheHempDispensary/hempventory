"""LeafLife retail price floors are enforced at sheet-sync time.

The website already floors LeafLife prices; these tests lock in that the same
minimums are applied when building the desired product set from the sheet, so
Clover POS + Inventory show the same minimum instead of the raw sheet price.
"""
from app.routers.inventory_router import _build_leaflife_desired
from app.routers.ecommerce_router import _enforce_leaflife_price_floor

FLOWER_TAB = "Retail Flower Menu"

# Flower floors (cents) keyed by SKU suffix.
FLOWER_FLOORS = {"28": 10000, "14": 9500, "7 G": 5500, "3.5": 2500}


def _flower_row(strain: str, tier: str, prices):
    """Build one sheet row. prices = (28g, 14g, 7g, 3.5g) dollar strings."""
    row = [""] * 16
    row[1] = "1000"          # inventory grams — plenty for every weight
    row[2] = tier            # tier
    row[3] = strain          # strain name
    row[8] = "Hybrid"        # I/H/S
    row[12], row[13], row[14], row[15] = prices
    return row


def test_flower_below_floor_is_raised_all_tiers():
    rows = {
        FLOWER_TAB: [
            # SMALLS well below floor at every weight
            _flower_row("HINDU KUSH SMALLS", "SMALLS", ("$60", "$40", "$23.50", "$13.75")),
        ]
    }
    desired = _build_leaflife_desired(rows)
    for suffix, floor in FLOWER_FLOORS.items():
        sku = f"LF-HINDU-KUSH-SMALLS-{suffix}".upper()
        assert desired[sku]["price"] == floor, (sku, desired[sku]["price"])


def test_flower_above_floor_is_untouched():
    rows = {
        FLOWER_TAB: [
            # PREMIUM priced above every floor
            _flower_row("PURPLE POWER", "PREMIUM", ("$220", "$120", "$65", "$35")),
        ]
    }
    desired = _build_leaflife_desired(rows)
    assert desired["LF-PURPLE-POWER-3.5"]["price"] == 3500
    assert desired["LF-PURPLE-POWER-7 G"]["price"] == 6500
    assert desired["LF-PURPLE-POWER-14"]["price"] == 12000
    assert desired["LF-PURPLE-POWER-28"]["price"] == 22000


def test_sync_floor_matches_website_floor():
    # The sync-time floor and the website enforcement must agree for flower.
    for suffix, floor in FLOWER_FLOORS.items():
        sku = f"LF-TEST-{suffix}"
        assert _enforce_leaflife_price_floor(sku, "TEST", 100) == floor
