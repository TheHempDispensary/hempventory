"""Match packaged products to the HQ bulk they are made from and net that bulk
out of Smart PAR reorder quantities.

Mirrors the name-matching used by the Production board (frontend
`bulkMatchesProduct`) so both pages agree on which bulk feeds which product.
"""
import re

from app.catalog import is_bulk_name

# Words present in nearly every product/bulk name; they can't identify a bulk.
_GENERIC_TOKENS = frozenset({
    "bulk", "thc", "cbd", "cbg", "cbn", "delta", "flower", "smalls", "shake", "bigs",
    "gram", "grams", "g", "oz", "mg", "ct", "count", "pack", "pk", "piece", "pieces",
    "pre", "roll", "rolled", "joint", "gummy", "gummies", "the", "and", "of", "with",
    "for", "a", "in", "by", "per", "each", "hybrid", "sativa", "indica", "variety",
})

# Order matters: "PRE ROLLED JOINT ... FLOWER" is a pre-roll, not flower.
_FORMS = (
    ("preroll", re.compile(r"pre[\s-]?roll|\bjoint\b|\bbaby\s*j\b|\bblunt\b|\bdog\s*walker\b")),
    ("vapor", re.compile(r"\bvape\b|\bcart\b|\bcartridge\b|\bdisposable\b|\bpod\b")),
    ("concentrate", re.compile(
        r"\bdab\b|\bwax\b|\brosin\b|\bresin\b|\bshatter\b|\bbadder\b|\bconcentrate\b|\bhash\b|\bkief\b|\bmoon\s*rock"
    )),
    ("edible", re.compile(
        r"\bgumm|\bedible|\bchocolate|\bcookie|\bbrownie|\bcoffee|\bdrink|\bbeverage|\bsyrup|\bhoney|\bcaramel|\bchew|\bmint"
    )),
    ("flower", re.compile(r"\bflower\b|\bsmalls\b|\bshake\b|\bbud\b|\bpopcorn\b")),
)

# Flower grades: bulk smalls only make smalls, ground only makes ground, etc.
_GRADES = ("smalls", "ground", "shake", "popcorn", "bigs")

# Cannabinoid words: a CBD product is never made from Delta 8 bulk even when
# every other word lines up.
_CANNABINOIDS = frozenset({"cbd", "cbg", "cbn", "cbc", "cbda", "thca", "thcp", "thcv", "hhc", "thc"})
_DELTA_RE = re.compile(r"\b(?:delta|d) ?(8|9|10|11|eight|nine|ten)\b")
_DELTA_WORDS = {"eight": "8", "nine": "9", "ten": "10"}

_GRAMS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:G|GRAMS?)\b")
_WORD_GRAMS = (("HALF GRAM", 0.5), ("ONE GRAM", 1.0), ("TWO GRAM", 2.0), ("THREE GRAM", 3.0), ("FOUR GRAM", 4.0))


def tokenize(name: str) -> set[str]:
    return set(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def product_form(name: str) -> str | None:
    low = name.lower()
    for form, rx in _FORMS:
        if rx.search(low):
            return form
    return None


def _ignorable(tok: str) -> bool:
    return tok in _GENERIC_TOKENS or tok.isdigit()


def _grade(tokens: set[str]) -> frozenset[str]:
    return frozenset(g for g in _GRADES if g in tokens)


def cannabinoid_signature(name: str) -> frozenset[str]:
    """Which cannabinoid(s) a name is for: {"cbd"}, {"delta8"}, {"thc"}, ...
    A delta number implies THC, so "Delta 8 THC" and "Delta 8" both give
    {"delta8"}. Empty when the name doesn't say."""
    low = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("∆", " delta "))
    sig = {t for t in low.split() if t in _CANNABINOIDS}
    deltas = {
        f"delta{_DELTA_WORDS.get(m.group(1), m.group(1))}"
        for m in _DELTA_RE.finditer(low)
    }
    if deltas:
        sig.discard("thc")
        sig |= deltas
    return frozenset(sig)


def _cannabinoids_compatible(bulk_sig: frozenset[str], product_sig: frozenset[str]) -> bool:
    """Every cannabinoid the bulk names must be in the product. Plain "THC"
    on either side stands in for any delta variant (a "THC WAX" can be made
    from "DELTA 8 THC WAX" bulk), but CBD/CBG/CBN never match THC bulk."""
    product_has_thc = "thc" in product_sig or any(c.startswith("delta") for c in product_sig)
    for c in bulk_sig:
        if c in product_sig:
            continue
        if c == "thc" and product_has_thc:
            continue
        if c.startswith("delta") and "thc" in product_sig:
            continue
        return False
    return True


def bulk_matches_product(bulk: str, product: str) -> bool:
    """A bulk feeds a product when both are the same form, grade and
    cannabinoid, and every distinguishing word of the bulk name appears in
    the product name."""
    form = product_form(bulk)
    if not form or form != product_form(product):
        return False
    bulk_tokens = tokenize(bulk)
    if all(_ignorable(t) for t in bulk_tokens):
        return False
    product_tokens = tokenize(product)
    if _grade(bulk_tokens) != _grade(product_tokens):
        return False
    if not _cannabinoids_compatible(cannabinoid_signature(bulk), cannabinoid_signature(product)):
        return False
    return all(_ignorable(t) or t in product_tokens for t in bulk_tokens)


def bulk_is_weight(bulk_name: str) -> bool:
    """Bulk flower/concentrate is tracked in grams; vapes, pre-rolls and
    edibles are counted by the piece even when the name carries a size
    ("Bulk - THC Disposable Vape Two Grams ...")."""
    if product_form(bulk_name) not in ("flower", "concentrate"):
        return False
    return bool(re.search(r"\bGRAMS?\b|\bOZ\b|\bPOUND", bulk_name.upper()))


def grams_per_package(product_name: str) -> float:
    """Grams in one retail package, parsed from the product name (0 if none)."""
    up = product_name.upper()
    m = _GRAMS_RE.search(up)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    for word, val in _WORD_GRAMS:
        if word in up:
            return val
    return 0.0


def collect_bulk_pool(items: list[dict]) -> dict[str, float]:
    """HQ stock of every bulk item, summed across duplicate Clover records."""
    pool: dict[str, float] = {}
    for it in items:
        name = " ".join((it.get("name") or "").split())
        if not is_bulk_name(name):
            continue
        stock = sum(
            (loc.get("stock", 0) or 0) for loc in (it.get("locations") or {}).values()
        )
        pool[name] = pool.get(name, 0.0) + stock
    return pool


def bulk_per_unit_for(product_name: str, bulk_name: str, recipe_per_unit: float | None) -> float:
    """Bulk consumed per finished unit. For bulk tracked in grams the weight in
    the packaged name is authoritative (a "3.5 GRAMS" jar always uses 3.5g);
    the saved recipe value is only used when the name carries no weight, and
    count-based bulk (pre-rolls, vapes, gummies) defaults to one piece."""
    weight = bulk_is_weight(bulk_name)
    if weight:
        grams = grams_per_package(product_name)
        if grams > 0:
            return grams
    if recipe_per_unit and recipe_per_unit > 0:
        return recipe_per_unit
    return 0.0 if weight else 1.0


def apply_bulk_netting(
    results: list[dict],
    bulk_pool: dict[str, float],
    recipes: dict[str, tuple[str, float]],
) -> None:
    """Annotate each Smart PAR row with its bulk source and net the bulk out of
    `order_qty`.

    `recipes` maps a normalised packaged name -> (bulk_name, bulk_per_unit) from
    saved production recipes; other rows fall back to name matching. Products
    sharing one bulk split it in proportion to the bulk each needs, so the same
    grams are never counted twice. Sets on each row:

        bulk_name, bulk_stock, bulk_unit ("g"|"units"), bulk_per_unit,
        bulk_shared_by (number of packaged products drawing on this bulk),
        bulk_covers  (packages this row can make from its share, <= gross order),
        gross_order_qty (pre-netting), order_qty (netted)
    """
    tokenised = sorted(
        ((name, tokenize(name)) for name in bulk_pool),
        key=lambda x: -len(x[1]),
    )
    remaining = dict(bulk_pool)
    assigned: list[tuple[dict, str, float]] = []

    for r in results:
        r["gross_order_qty"] = r["order_qty"]
        r["bulk_name"] = None
        r["bulk_stock"] = 0
        r["bulk_unit"] = None
        r["bulk_per_unit"] = 0
        r["bulk_covers"] = 0
        r["bulk_shared_by"] = 0

        key = " ".join(r["name"].lower().split())
        recipe = recipes.get(key)
        bulk_name: str | None = None
        recipe_per_unit: float | None = None
        if recipe and recipe[0] in bulk_pool:
            bulk_name, recipe_per_unit = recipe
        else:
            for name, _tokens in tokenised:
                if bulk_matches_product(name, r["name"]):
                    bulk_name = name
                    break
        if not bulk_name:
            continue
        per_unit = bulk_per_unit_for(r["name"], bulk_name, recipe_per_unit)
        r["bulk_name"] = bulk_name
        r["bulk_stock"] = bulk_pool[bulk_name]
        r["bulk_unit"] = "g" if bulk_is_weight(bulk_name) else "units"
        r["bulk_per_unit"] = per_unit
        if per_unit > 0:
            assigned.append((r, bulk_name, per_unit))

    by_bulk: dict[str, list[tuple[dict, float]]] = {}
    for r, bulk_name, per_unit in assigned:
        by_bulk.setdefault(bulk_name, []).append((r, per_unit))

    for bulk_name, rows in by_bulk.items():
        for r, _ in rows:
            r["bulk_shared_by"] = len(rows)
        avail = remaining.get(bulk_name, 0.0)
        short = [(r, pu) for r, pu in rows if r["gross_order_qty"] > 0]
        need = sum(r["gross_order_qty"] * pu for r, pu in short)
        if avail <= 0 or need <= 0:
            continue
        # Each size gets its share of the pool in proportion to the bulk it
        # needs, so an oz jar isn't starved by many small jars (or vice versa).
        ratio = min(1.0, avail / need)
        for r, pu in short:
            covers = min(r["gross_order_qty"], int(r["gross_order_qty"] * ratio))
            r["bulk_covers"] = covers
            avail -= covers * pu
        # Rounding leftovers go to the largest remaining shortfall first.
        for r, pu in sorted(short, key=lambda t: -(t[0]["gross_order_qty"] - t[0]["bulk_covers"])):
            extra = min(r["gross_order_qty"] - r["bulk_covers"], int(avail // pu))
            if extra > 0:
                r["bulk_covers"] += extra
                avail -= extra * pu
        for r, _ in short:
            r["order_qty"] = r["gross_order_qty"] - r["bulk_covers"]
        remaining[bulk_name] = avail
