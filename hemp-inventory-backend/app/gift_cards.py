"""Online gift cards: fixed-denomination virtual products sold through the
storefront and delivered by email as a code that spends down at checkout."""
import secrets
from typing import Optional

import aiosqlite

GIFT_CARD_DENOMINATIONS = (2500, 5000, 10000)
GIFT_CARD_SKU_PREFIX = "GIFTCARD-"
GIFT_CARD_CATEGORY = "Gift Cards"
GIFT_CARD_IMAGE_URL = "/images/gift-card.svg"

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def gift_card_sku(amount_cents: int) -> str:
    return f"{GIFT_CARD_SKU_PREFIX}{amount_cents // 100}"


def gift_card_products() -> list[dict]:
    """Virtual storefront products, one per denomination, shaped like Clover items."""
    products = []
    for amount in GIFT_CARD_DENOMINATIONS:
        dollars = amount // 100
        sku = gift_card_sku(amount)
        products.append({
            "id": sku,
            "name": f"${dollars} Gift Card",
            "online_name": f"${dollars} Gift Card",
            "slug": f"{dollars}-gift-card",
            "sku": sku,
            "price": amount,
            "description": (
                f"A ${dollars} Hemp Dispensary gift card, emailed to you as a code right after "
                "purchase. Redeem it at checkout on our website or show the code at either store."
            ),
            "categories": [GIFT_CARD_CATEGORY],
            "stock": 999,
            "stock_hq": 999,
            "stock_west": 999,
            "stock_east": 999,
            "available": True,
            "image_url": GIFT_CARD_IMAGE_URL,
            "is_age_restricted": False,
            "shipping_only": False,
            "is_gift_card": True,
            "effect": None,
            "strength": None,
            "product_type": None,
            "modified_time": 0,
            "lab_results": [],
        })
    return products


def add_gift_card_products(products: list[dict], categories: list[str]) -> tuple[list[dict], list[str]]:
    """Merge the virtual gift card products into a catalog listing (idempotent)."""
    rest = [p for p in products if not is_gift_card_sku(p.get("sku", ""))]
    merged = rest + gift_card_products()
    merged.sort(key=lambda p: p["name"])
    cats = sorted(set(categories) | {GIFT_CARD_CATEGORY})
    return merged, cats


def is_gift_card_sku(sku: Optional[str]) -> bool:
    return isinstance(sku, str) and sku.startswith(GIFT_CARD_SKU_PREFIX)


def gift_card_amount(sku: Optional[str]) -> Optional[int]:
    """Denomination (cents) for a gift card SKU, or None if it isn't a valid one."""
    if not is_gift_card_sku(sku):
        return None
    try:
        amount = int(sku[len(GIFT_CARD_SKU_PREFIX):]) * 100
    except ValueError:
        return None
    return amount if amount in GIFT_CARD_DENOMINATIONS else None


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def format_code(raw: str) -> str:
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(16))


async def ensure_tables(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS gift_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            initial_amount INTEGER NOT NULL,
            balance INTEGER NOT NULL,
            purchaser_name TEXT DEFAULT '',
            purchaser_email TEXT DEFAULT '',
            recipient_email TEXT DEFAULT '',
            order_number TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_redeemed_at TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS gift_card_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_card_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            order_number TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gift_card_id) REFERENCES gift_cards(id)
        )
    """)


async def lookup(db: aiosqlite.Connection, code: str) -> Optional[dict]:
    raw = normalize_code(code)
    if len(raw) != 16:
        return None
    cursor = await db.execute(
        "SELECT id, code, initial_amount, balance, purchaser_name, purchaser_email, "
        "recipient_email, order_number, is_active, created_at, last_redeemed_at "
        "FROM gift_cards WHERE code = ?",
        (raw,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "code": row[1],
        "initial_amount": row[2],
        "balance": row[3],
        "purchaser_name": row[4],
        "purchaser_email": row[5],
        "recipient_email": row[6],
        "order_number": row[7],
        "is_active": bool(row[8]),
        "created_at": row[9],
        "last_redeemed_at": row[10],
    }


async def issue(
    db: aiosqlite.Connection,
    amount: int,
    purchaser_name: str = "",
    purchaser_email: str = "",
    recipient_email: str = "",
    order_number: str = "",
    note: str = "",
) -> dict:
    """Create a new gift card and return it (code is the raw 16-char form)."""
    if amount <= 0:
        raise ValueError("Gift card amount must be positive")
    for _ in range(10):
        code = generate_code()
        try:
            cursor = await db.execute(
                """INSERT INTO gift_cards
                   (code, initial_amount, balance, purchaser_name, purchaser_email,
                    recipient_email, order_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (code, amount, amount, purchaser_name, purchaser_email, recipient_email, order_number),
            )
        except aiosqlite.IntegrityError:
            continue
        card_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO gift_card_transactions (gift_card_id, amount, order_number, note) VALUES (?, ?, ?, ?)",
            (card_id, amount, order_number, note or "Issued"),
        )
        await db.commit()
        return {"id": card_id, "code": code, "initial_amount": amount, "balance": amount}
    raise RuntimeError("Could not generate a unique gift card code")


async def redeem(
    db: aiosqlite.Connection,
    code: str,
    amount: int,
    order_number: str = "",
    note: str = "",
) -> Optional[int]:
    """Atomically deduct `amount` (cents). Returns the remaining balance, or
    None when the card is missing, inactive, or doesn't have enough balance."""
    raw = normalize_code(code)
    if amount <= 0:
        return None
    cursor = await db.execute(
        """UPDATE gift_cards
           SET balance = balance - ?, last_redeemed_at = CURRENT_TIMESTAMP
           WHERE code = ? AND is_active = 1 AND balance >= ?""",
        (amount, raw, amount),
    )
    if cursor.rowcount != 1:
        return None
    cursor = await db.execute("SELECT id, balance FROM gift_cards WHERE code = ?", (raw,))
    row = await cursor.fetchone()
    await db.execute(
        "INSERT INTO gift_card_transactions (gift_card_id, amount, order_number, note) VALUES (?, ?, ?, ?)",
        (row[0], -amount, order_number, note),
    )
    await db.commit()
    return row[1]


async def reverse_order(db: aiosqlite.Connection, order_number: str) -> dict:
    """Undo an order's gift card effects when it is cancelled: credit back any
    balance it spent and deactivate any cards it issued. Idempotent."""
    if not order_number:
        return {"refunded": 0, "deactivated": 0}
    cursor = await db.execute(
        "SELECT 1 FROM gift_card_transactions WHERE order_number = ? AND note = 'Order cancelled' LIMIT 1",
        (order_number,),
    )
    if await cursor.fetchone():
        return {"refunded": 0, "deactivated": 0}

    cursor = await db.execute(
        "SELECT gift_card_id, SUM(amount) FROM gift_card_transactions "
        "WHERE order_number = ? AND amount < 0 GROUP BY gift_card_id",
        (order_number,),
    )
    refunded = 0
    for card_id, spent in await cursor.fetchall():
        credit = -spent
        await db.execute(
            "UPDATE gift_cards SET balance = balance + ? WHERE id = ?", (credit, card_id)
        )
        await db.execute(
            "INSERT INTO gift_card_transactions (gift_card_id, amount, order_number, note) VALUES (?, ?, ?, ?)",
            (card_id, credit, order_number, "Order cancelled"),
        )
        refunded += credit

    cursor = await db.execute(
        "UPDATE gift_cards SET is_active = 0 WHERE order_number = ? AND is_active = 1",
        (order_number,),
    )
    deactivated = cursor.rowcount
    await db.commit()
    return {"refunded": refunded, "deactivated": deactivated}
