import pytest

from app.catalog import (
    is_bulk_name,
    name_with_strain_type,
    strain_phrase,
    strain_types_by_phrase,
)

RETAIL_NAMES = [
    "THC FLOWER GREEN CRACK Sativa 3.5 GRAMS",
    "THC FLOWER SMALLS GREEN CRACK Sativa 28 GRAMS",
    "THC PRE ROLLED JOINT BABY J GREEN CRACK Sativa 7 COUNT",
    "THC FLOWER SKYWALKER OG Indica 1 GRAM",
    "THC GROUND FLOWER NERDS Hybrid 2 GRAMS",
    "THC FLOWER PEANUT BUTTER BREATH SMALLS INDICA 3.5 GRAMS",
    "CBG/CBD FLOWER GELATO HYBRID 3.5 GRAMS",
    "THC FLOWER LEMON CHERRY GELATO Hybrid 28 GRAMS",
    "DELTA 9 THC UGLY GUMMIES 120MG MANGO 20 COUNT",
]


@pytest.fixture
def strain_types():
    return strain_types_by_phrase(RETAIL_NAMES)


@pytest.mark.parametrize(
    ("name", "phrase"),
    [
        ("Bulk - Green Crack THC Ground Flower Grams", "Green Crack"),
        ("Bulk - Skywalker OG Baby Js", "Skywalker OG"),
        ("Bulk - Peanut Butter Breath THC Smalls Flower Grams", "Peanut Butter Breath"),
        ("Bulk - CBD/CBG Gelato 1.5g Pre Rolls (in tubes)", "Gelato"),
        ("Bulk - Delta 9 THC 30mg Gummies", ""),
        ("Bulk - ∆9 THC/CBD/CBN Gummies", ""),
    ],
)
def test_strain_phrase_ignores_cannabinoid_form_and_size_words(name, phrase):
    assert strain_phrase(name) == phrase


def test_learns_strain_types_from_retail_names(strain_types):
    assert strain_types == {
        "GREEN CRACK": "Sativa",
        "SKYWALKER OG": "Indica",
        "NERDS": "Hybrid",
        "PEANUT BUTTER BREATH": "Indica",
        "GELATO": "Hybrid",
        "LEMON CHERRY GELATO": "Hybrid",
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Bulk - Green Crack THC Flower Grams",
            "Bulk - Green Crack Sativa THC Flower Grams",
        ),
        ("Bulk - Green Crack Baby Js", "Bulk - Green Crack Sativa Baby Js"),
        (
            "Bulk - Peanut Butter Breath THC Smalls Flower Grams",
            "Bulk - Peanut Butter Breath Indica THC Smalls Flower Grams",
        ),
        (
            "Bulk - CBD/CBG Gelato 1.5g Pre Rolls (in tubes)",
            "Bulk - CBD/CBG Gelato Hybrid 1.5g Pre Rolls (in tubes)",
        ),
        # A partial strain name still matches its retail product.
        (
            "Bulk - Skywalker 1.5g Pre Rolled Joints",
            "Bulk - Skywalker Indica 1.5g Pre Rolled Joints",
        ),
    ],
)
def test_copies_strain_type_from_the_matching_retail_product(name, expected, strain_types):
    assert name_with_strain_type(name, strain_types) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Bulk - Sour Berries CBD Hybrid Flower Grams",  # already labelled
        "Bulk - Delta 9 THC 30mg Gummies",  # no strain
        "Bulk - CBD/CBG/CBN Gummies 150mg Mango UGLY",  # edible, not a strain
        "Bulk - Divine THC Smalls Flower Grams",  # no retail counterpart here
        "Bulk - Wholesale Bag",  # not a flower product
    ],
)
def test_leaves_names_alone_without_a_confident_match(name, strain_types):
    assert name_with_strain_type(name, strain_types) == name


def test_ambiguous_strains_are_left_unlabelled():
    strain_types = strain_types_by_phrase(
        ["THC FLOWER GUAVA Hybrid 1 GRAM", "THC FLOWER GUAVA Sativa 1 GRAM"]
    )
    assert "GUAVA" not in strain_types
    assert (
        name_with_strain_type("Bulk - Guava THC Flower Grams", strain_types)
        == "Bulk - Guava THC Flower Grams"
    )


def test_bulk_names_are_detected_case_insensitively():
    assert is_bulk_name("Bulk - Nerds THC Flower Grams")
    assert is_bulk_name("BULK- Nerds THC Flower Grams")
    assert not is_bulk_name("THC FLOWER NERDS Hybrid 1 GRAM")
