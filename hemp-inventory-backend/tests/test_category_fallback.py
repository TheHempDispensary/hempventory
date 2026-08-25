import pytest

from app.routers.ecommerce_router import _infer_categories


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("DELTA 9 THC TINCTURE", "Tinctures"),
        ("THC FLOWER SMALLS DIVINE SATIVA 3.5 GRAMS", "Flower"),
        ("THC INFUSED AGAVE 10 OZ", "Edibles"),
        ("THC CHOCOLATE SQUARE 1 COUNT", "Edibles"),
        ("CBD ISOLATE ONE GRAM", "Concentrates"),
        ("THC MOON ROCK ICE CREAM COOKIES 1 GRAM", "MoonRocks"),
        ("THC Flower Smalls Lemon Cherry Gelato Hybrid 3.5 Grams", "Flower"),
        ("COCONUT CREAM LIVE RESIN 1 GRAM", "Concentrates"),
        ("DELTA 9 THC VANILLA ICE CREAM 1.66 OZ", "Edibles"),
        ("Ooze Slim Twist Battery Black", "Accessories"),
        ("THC SNOW CAPS 1 GRAM", "Flower"),
        ('GLASS PIPE FLOWER COLOR CHANGING 3"', "Accessories"),
    ],
)
def test_infers_categories_from_product_names(name, category):
    assert _infer_categories(name) == [category]


@pytest.mark.parametrize(
    "name",
    [
        "Gift card",
        "Ground Shipping",
        "Promotional Dab - 420",
        "Deposit",
    ],
)
def test_excludes_non_products(name):
    assert _infer_categories(name) == []


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("PET CBD CALMING CHEWS", "Pets"),
        ("CBD POP TOP CONTAINER", "Packaging"),
        ("ALL IN ONE VAPE", "Vapor"),
        ("CBD MUSCLE BALM", "Topicals"),
        ("THC HASH", "Concentrates"),
        ("GLASS PIPE", "Accessories"),
    ],
)
def test_infers_all_category_rules(name, category):
    assert _infer_categories(name) == [category]


def test_batter_is_concentrate_but_battery_is_accessory():
    assert _infer_categories("GRAPE CREAM CAKE BADDER 1 GRAM") == ["Concentrates"]
    assert _infer_categories("BATTER 1 GRAM") == ["Concentrates"]
    assert _infer_categories("BATTERY") == ["Accessories"]


@pytest.mark.parametrize(
    "name",
    ["CARTON", "PODS-like accessory", "DABBER TOOL", "HASHBROWN"],
)
def test_short_tokens_require_word_boundaries(name):
    assert _infer_categories(name) == []
