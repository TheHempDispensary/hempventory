from app.routers.ecommerce_router import (
    _build_image_lookups,
    _build_family_image_urls,
    _family_key,
)


def test_family_key_precedence():
    assert _family_key("CBD/CBG/CBN PET TINCTURE") == "Tinctures"
    assert _family_key("THC MOON ROCK PRE ROLLED JOINT") == "PreRoll"
    assert _family_key("GRAIN FREE CBD PET TREATS BACON") == "PetTreats"
    assert _family_key("THC FLOWER SMALLS DIVINE SATIVA") == "Flower"
    assert _family_key("DELTA 9 THC VANILLA ICE CREAM") == "IceCream"
    assert _family_key("Gift card") is None
    assert _family_key("RANDYS HEMPWICK") is None


def test_family_image_donor_prefers_latest_then_lowest_sku():
    rows = [
        ("SKU-OLD", "THC FLOWER SMALLS OLD", "2024-01-01 00:00:00"),
        ("SKU-Z", "THC FLOWER SMALLS Z", "2024-02-01 00:00:00"),
        ("SKU-A", "THC FLOWER SMALLS A", "2024-02-01 00:00:00"),
        ("LF-STRAIN", "THC FLOWER SMALLS STRAIN", "2025-01-01 00:00:00"),
        ("SKU-NAMELESS", None, "2025-02-01 00:00:00"),
    ]

    family_urls = _build_family_image_urls(
        rows, "https://inventory.example/images"
    )

    assert family_urls["Flower"].startswith(
        "https://inventory.example/images/SKU-A?v=2&bg=1&t=2024-02-01_00:00:00"
    )
    assert "SKU-NAMELESS" not in family_urls.values()
    assert not _build_family_image_urls(
        [("LF-ONLY", "THC FLOWER SMALLS STRAIN", "2025-01-01 00:00:00")],
        "https://inventory.example/images",
    )


def test_product_with_own_image_keeps_sku_image():
    rows = [
        ("SKU-OWN", "THC FLOWER SMALLS OWN", "2024-01-01 00:00:00"),
        ("SKU-DONOR", "THC FLOWER SMALLS DONOR", "2024-02-01 00:00:00"),
    ]

    image_by_sku, _, family_urls = _build_image_lookups(
        rows, "https://inventory.example/images"
    )

    assert image_by_sku["SKU-OWN"].startswith(
        "https://inventory.example/images/SKU-OWN?v=2&bg=1&t=2024-01-01_00:00:00"
    )
    assert image_by_sku["SKU-OWN"] != family_urls["Flower"]
