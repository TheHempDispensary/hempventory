"""The website sends fulfillment_type="ship"; the sheet writer must accept it."""

from app.routers.ecommerce_router import _is_shipping_fulfillment


def test_accepts_website_ship_value():
    assert _is_shipping_fulfillment("ship") is True


def test_accepts_legacy_shipping_value():
    assert _is_shipping_fulfillment("shipping") is True


def test_defaults_to_shipping_when_missing():
    assert _is_shipping_fulfillment("") is True


def test_rejects_pickup_and_local_delivery():
    for ft in ("pickup_east", "pickup_west", "local_delivery"):
        assert _is_shipping_fulfillment(ft) is False
