"""Catalog naming helpers shared by the storefront and the inventory app.

Clover leaves many items without a category and bulk (production) items are
named without the strain type its retail counterparts carry, so both surfaces
derive that information from the product name.
"""
import re
from collections import Counter
from typing import Iterable, Optional

APPAREL_PATTERN = re.compile(r"\b(hoodie|t-shirt|shirt|tee|jersey|hat|beanie|shoes|socks)\b", re.I)


def infer_categories(name: str) -> list[str]:
    """Infer a catalog category from a product name when Clover has none."""
    name_upper = name.upper()
    if any(keyword in name_upper for keyword in ("GIFT CARD", "SHIPPING", "PROMOTIONAL", "DEPOSIT")):
        return []
    if re.search(r"\bPETS?\b(?!-)", name_upper):
        return ["Pets"]
    if any(keyword in name_upper for keyword in ("CONTAINER", "MYLAR", "LINER", "JAR", "POP TOP", "ZIP BAG", "WHOLESALE BAG")):
        return ["Packaging"]
    if APPAREL_PATTERN.search(name):
        return ["Apparel"]
    if "TINCTURE" in name_upper:
        return ["Tinctures"]
    if (
        any(keyword in name_upper for keyword in ("VAPE", "CARTRIDGE", "DISPOSABLE", "ALL IN ONE", "AIO"))
        or re.search(r"\bCARTS?\b(?!-)", name_upper)
        or re.search(r"\bPODS?\b(?!-)", name_upper)
    ):
        return ["Vapor"]
    if any(keyword in name_upper for keyword in ("BALM", "SALVE", "LOTION", "ROLL ON", "ROLL-ON", "PATCH", "MUSCLE")):
        return ["Topicals"]
    if "MOON ROCK" in name_upper or "MOONROCK" in name_upper:
        return ["MoonRocks"]
    if any(keyword in name_upper for keyword in ("GUMM", "CHOCOLATE", "AGAVE", "HONEY", "SYRUP", "SELTZER", "DRINK", "LOLLIPOP", "TAFFY", "CAPSULE", "BROWNIE", "RICE KRISPY", "ICE CREAM")):
        return ["Edibles"]
    if any(keyword in name_upper for keyword in ("STICKER", "POSTER", "GLASS", "PIPE", "GRINDER", "LIGHTER", "TRAY", "CARB CAP", "TORCH", "BUTANE", "BANGER", "ROLLING PAPER", "BATTERY")):
        return ["Accessories"]
    if (
        any(keyword in name_upper for keyword in ("FLOWER", "PRE ROLL", "PRE-ROLL", "PREROLL", "JOINT", "SHAKE", "SMALLS", "BLUNT", "SNOW CAP"))
        or re.search(r"\bBABY ?JS?\b", name_upper)
    ):
        return ["Flower"]
    if (
        any(keyword in name_upper for keyword in ("WAX", "ISOLATE", "SHATTER", "ROSIN", "RESIN", "DISTILLATE", "BADDER", "CRUMBLE", "DIAMOND", "SYRINGE", "SAUCE"))
        or re.search(r"\bBATTERS?\b(?!-)", name_upper)
        or re.search(r"\bHASHS?\b(?!-)", name_upper)
        or re.search(r"\bDABS?\b(?!-)", name_upper)
    ):
        return ["Concentrates"]
    return []


def resolve_categories(name: str, clover_categories: list[str]) -> list[str]:
    """The categories an item should show: Clover's, with apparel remapped and
    a name-inferred fallback when Clover has none."""
    if APPAREL_PATTERN.search(name):
        categories = [c if c != "Accessories" else "Apparel" for c in clover_categories]
        if categories:
            return categories
    elif clover_categories:
        return clover_categories
    return infer_categories(name)


# ── Bulk strain types ────────────────────────────────────────────────────────

STRAIN_TYPES = ("Indica", "Sativa", "Hybrid")

# Strain type only makes sense for flower-form products; bulk gummies and
# packaging must never be labelled.
_STRAIN_FORM_KEYWORDS = (
    "FLOWER", "PRE ROLL", "PRE-ROLL", "PREROLL", "PRE ROLLED", "JOINT",
    "SMALLS", "SHAKE", "TRIM", "BABY J", "MOON ROCK", "SNOW CAP", "BLUNT",
)

# Words that describe the cannabinoid, form, or size rather than the strain.
_NON_STRAIN_WORDS = frozenset({
    "THC", "CBD", "CBG", "CBN", "CBC", "DELTA", "D8", "D9",
    "FLOWER", "FLOWERS", "SMALLS", "GROUND", "SHAKE", "TRIM", "EXOTIC",
    "PRE", "ROLL", "ROLLS", "ROLLED", "JOINT", "JOINTS", "BABY", "J", "JS",
    "MOON", "ROCK", "ROCKS", "SNOW", "CAP", "CAPS", "WAX", "VAPE", "CART",
    "CARTRIDGE", "DISPOSABLE", "GUMMY", "GUMMIES", "TINCTURE", "UGLY",
    "GRAM", "GRAMS", "OZ", "POUND", "COUNT", "IN", "TUBE", "TUBES", "AND",
    "BULK", "WHOLESALE", "MG",
})

_BULK_PREFIX = re.compile(r"^\s*BULK\s*-\s*", re.I)


def strain_type_in_name(name: str) -> Optional[str]:
    """The strain type already present in a name, if any."""
    upper = name.upper()
    for strain_type in STRAIN_TYPES:
        if re.search(rf"\b{strain_type.upper()}\b", upper):
            return strain_type
    return None


def is_bulk_name(name: str) -> bool:
    return bool(_BULK_PREFIX.match(name or ""))


def _is_noise(word: str) -> bool:
    cleaned = re.sub(r"[^A-Z0-9./∆]", "", word.upper())
    if not cleaned or not re.search(r"[A-Z]", cleaned):
        return True  # numbers, sizes ("1.5G", "150MG"), stray punctuation
    if re.fullmatch(r"[\d.]+(G|MG|OZ|LB|CT)", cleaned):
        return True
    # Cannabinoid blends such as "CBD/CBG/CBN" or "∆9".
    parts = [p for p in cleaned.split("/") if p]
    return all(p.strip("∆") in _NON_STRAIN_WORDS or not p.strip("∆") for p in parts)


def strain_phrase(name: str) -> str:
    """The longest run of words in a name that isn't cannabinoid/form/size noise.

    "Bulk - Green Crack THC Ground Flower Grams" -> "Green Crack".
    """
    words = _BULK_PREFIX.sub("", name or "").split()
    best: list[str] = []
    run: list[str] = []
    for word in words:
        if _is_noise(word):
            run = []
            continue
        run.append(word.strip("()"))
        if len(" ".join(run)) > len(" ".join(best)):
            best = list(run)
    return " ".join(best)


def strain_types_by_phrase(names: Iterable[str]) -> dict[str, str]:
    """Map strain phrase -> strain type, learned from the retail catalog.

    Only retail (non-bulk) flower-form items that name their strain type are
    used, so bulk items inherit whatever their regular counterparts say.
    """
    votes: dict[str, Counter] = {}
    for name in names:
        if not name or is_bulk_name(name):
            continue
        upper = name.upper()
        if not any(keyword in upper for keyword in _STRAIN_FORM_KEYWORDS):
            continue
        strain_type = strain_type_in_name(name)
        phrase = strain_phrase(name)
        if not strain_type or not phrase:
            continue
        for word in STRAIN_TYPES:
            phrase = re.sub(rf"\b{word}\b\s*", "", phrase, flags=re.I).strip()
        if len(phrase) < 4:
            continue
        votes.setdefault(phrase.upper(), Counter())[strain_type] += 1
    return {
        phrase: counter.most_common(1)[0][0]
        for phrase, counter in votes.items()
        if len(counter) == 1  # ambiguous strains stay unlabelled
    }


def name_with_strain_type(name: str, strain_types_by_phrase_map: dict[str, str]) -> str:
    """Add the strain type of the matching retail product to a bulk item's name.

    Returns the name unchanged when it already states a strain type, isn't a
    flower-form product, or has no retail counterpart to copy from.
    """
    if not name or strain_type_in_name(name):
        return name
    upper = name.upper()
    if not any(keyword in upper for keyword in _STRAIN_FORM_KEYWORDS):
        return name
    phrase = strain_phrase(name)
    if len(phrase) < 4:
        return name
    phrase_upper = phrase.upper()
    strain_type = strain_types_by_phrase_map.get(phrase_upper)
    if not strain_type:
        # A shorter bulk phrase ("Skywalker") still matches its retail strain
        # ("Skywalker OG"); require a word-boundary match to avoid false hits.
        matches = {
            t for p, t in strain_types_by_phrase_map.items()
            if re.search(rf"\b{re.escape(phrase_upper)}\b", p)
        }
        if len(matches) != 1:
            return name
        strain_type = matches.pop()
    index = upper.index(phrase_upper) + len(phrase_upper)
    return f"{name[:index]} {strain_type}{name[index:]}"
