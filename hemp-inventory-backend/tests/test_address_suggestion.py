"""Shippo's standardized address is surfaced as a one-click suggestion.

Order for 64 Groshon Road, Fort Bridger WY failed USPS validation because USPS
knows the road as "Groshons Rd". Shippo returns the corrected form on the
address object; we hand it to the admin instead of making them guess.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_addr_suggest_test.db"))

from app.routers import shipping_router as sr

SUBMITTED = {
    "street1": "64 Groshon Road",
    "street2": "Lot 12",
    "city": "Fort Bridger",
    "state": "WY",
    "zip": "82933",
}


def test_material_correction_is_suggested():
    returned = {**SUBMITTED, "street1": "64 Groshons Rd"}
    assert sr._suggested_address(SUBMITTED, returned) == {
        "street1": "64 Groshons Rd",
        "street2": "Lot 12",
        "city": "Fort Bridger",
        "state": "WY",
        "zip": "82933",
    }


def test_cosmetic_differences_are_not_suggested():
    returned = {
        "street1": "64 GROSHON ROAD",
        "street2": "LOT 12",
        "city": "FORT BRIDGER",
        "state": "WY",
        "zip": "82933-1234",
    }
    assert sr._suggested_address(SUBMITTED, returned) is None


def test_incomplete_or_missing_address_is_not_suggested():
    assert sr._suggested_address(SUBMITTED, {}) is None
    assert sr._suggested_address(SUBMITTED, {"street1": "64 Groshons Rd", "city": "", "zip": "82933"}) is None
