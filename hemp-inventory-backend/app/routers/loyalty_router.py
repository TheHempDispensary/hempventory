from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import aiosqlite
import asyncio
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/loyalty", tags=["loyalty"])


# ── Models ──────────────────────────────────────────────

class CustomerCreate(BaseModel):
    first_name: str
    last_name: Optional[str] = ""
    phone: Optional[str] = None
    email: Optional[str] = None
    birthday: Optional[str] = None
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    birthday: Optional[str] = None
    notes: Optional[str] = None


class AwardPoints(BaseModel):
    points: int
    description: Optional[str] = "Manual points award"
    order_id: Optional[str] = None
    location_name: Optional[str] = None


class RedeemReward(BaseModel):
    reward_id: int
    location_name: Optional[str] = None


class RewardCreate(BaseModel):
    name: str
    points_required: int
    reward_type: str = "discount"
    reward_value: float
    description: Optional[str] = None


class RewardUpdate(BaseModel):
    name: Optional[str] = None
    points_required: Optional[int] = None
    reward_type: Optional[str] = None
    reward_value: Optional[float] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class LoyaltySettingsUpdate(BaseModel):
    points_per_dollar: Optional[str] = None
    signup_bonus: Optional[str] = None
    birthday_bonus: Optional[str] = None
    program_name: Optional[str] = None


# ── Helper ──────────────────────────────────────────────

# SQL expression that strips common separators from a stored phone number so it
# can be compared against a digits-only value regardless of how it was entered.
_PHONE_DIGITS_SQL = "REPLACE(REPLACE(REPLACE(REPLACE(phone, '-', ''), ' ', ''), '(', ''), ')', '')"

# Both stores are in Florida; staff read every timestamp as Eastern.
_EASTERN = ZoneInfo("America/New_York")


def _normalize_phone(raw: Optional[str]) -> str:
    """Return the last 10 digits of a phone number (digits only).

    Ensures phone numbers are stored and compared consistently regardless of
    whether the customer typed dashes, spaces, or parentheses.
    """
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _normalize_name(first: Optional[str], last: Optional[str]) -> str:
    """A comparable form of a person's name.

    Staff routinely decorate the loyalty record with a tag the register never
    has — "Jaime Crews (Military)" vs Clover's "Jaime Crews" — and those extra
    characters used to make the name lookup miss, so the member's in-store
    purchases were never credited. Parenthesised/bracketed notes and
    punctuation are dropped.
    """
    raw = f"{(first or '').strip()} {(last or '').strip()}".lower()
    cleaned: list[str] = []
    depth = 0
    for ch in raw:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0:
            cleaned.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    return " ".join("".join(cleaned).split())


_PLACEHOLDER_NAMES = ("", "customer", "guest", "unknown")


def _is_placeholder_name(first: Optional[str], last: Optional[str]) -> bool:
    """True when a member has no real name — only the "Customer" stand-in."""
    return (first or "").strip().lower() in _PLACEHOLDER_NAMES and not (last or "").strip()


async def _adopt_real_name(
    db: aiosqlite.Connection,
    customer_id: int,
    stored_first: Optional[str],
    stored_last: Optional[str],
    clover_first: Optional[str],
    clover_last: Optional[str],
) -> bool:
    """Replace the "Customer" stand-in with the name Clover knows.

    Members created from a Clover record that carried only a phone number are
    stored as "Customer"; once the register learns the real name, staff should
    see it instead of having to retype it.
    """
    if not _is_placeholder_name(stored_first, stored_last):
        return False
    first = (clover_first or "").strip()
    last = (clover_last or "").strip()
    if not first and not last:
        return False
    await db.execute(
        """UPDATE loyalty_customers
           SET first_name = ?, last_name = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (first or last, last if first else "", customer_id),
    )
    print(f"[loyalty] Named member {customer_id} '{f'{first} {last}'.strip()}' from Clover")
    return True


async def _push_customer_to_clover(
    db: aiosqlite.Connection,
    customer_id: int,
    first_name: str,
    last_name: str,
    phone: str,
    email: str,
    existing_by_merchant: Optional[dict[str, dict[str, str]]] = None,
) -> list[str]:
    """Create this loyalty customer at every Clover location and record the mapping.

    Without a Clover record an online signup is invisible at the register, so the
    customer's in-store purchases carry no customer and can never be matched back
    to their loyalty account. Returns the location names that were pushed.

    `existing_by_merchant` maps merchant_id → {normalized phone: clover customer
    id}; when the register already knows the number the existing record is linked
    instead of creating a duplicate.
    """
    if not phone:
        return []
    norm_phone = _normalize_phone(phone)

    loc_cursor = await db.execute("SELECT name, merchant_id, api_token FROM locations")
    locations = await loc_cursor.fetchall()

    mapped_cursor = await db.execute(
        "SELECT merchant_id FROM loyalty_clover_id_map WHERE loyalty_customer_id = ?",
        (customer_id,),
    )
    already_mapped = {row[0] for row in await mapped_cursor.fetchall()}

    pushed: list[str] = []
    for loc_name, merchant_id, api_token in locations:
        if merchant_id in already_mapped:
            continue
        try:
            client = CloverClient(merchant_id, api_token)
            clover_id = (existing_by_merchant or {}).get(merchant_id, {}).get(norm_phone, "")
            if not clover_id:
                created = await client.create_customer(first_name, last_name, phone, email)
                clover_id = created.get("id", "")
            if not clover_id:
                continue
            await db.execute(
                """INSERT OR IGNORE INTO loyalty_clover_id_map
                   (loyalty_customer_id, clover_customer_id, merchant_id, location_name)
                   VALUES (?, ?, ?, ?)""",
                (customer_id, clover_id, merchant_id, loc_name),
            )
            await db.execute(
                """UPDATE loyalty_customers SET clover_customer_id = ?
                   WHERE id = ? AND (clover_customer_id IS NULL OR clover_customer_id = '')""",
                (clover_id, customer_id),
            )
            await db.commit()
            pushed.append(loc_name)
        except Exception as e:
            print(f"[loyalty-clover-push] Failed to create customer {customer_id} at {loc_name}: {e}")
    return pushed


async def _sync_balance_to_clover(db: aiosqlite.Connection, customer_id: int) -> list[str]:
    """Write the member's HempVentory balance onto their Clover customer record.

    Clover Rewards keeps its own per-merchant point count (East and West disagree
    with each other and with HempVentory) and the Platform API can't write it, so
    the customer note — visible on the register's customer screen — is where a
    budtender can read the one true cross-store balance. Returns the locations updated.
    """
    cursor = await db.execute(
        "SELECT points_balance FROM loyalty_customers WHERE id = ?",
        (customer_id,),
    )
    customer = await cursor.fetchone()
    if not customer:
        return []

    settings = await _get_settings(db)
    program = settings.get("program_name") or "HempVentory Rewards"
    stamp = datetime.now(_EASTERN).strftime("%-m/%-d/%y %-I:%M %p ET")
    note = (
        f"{program}: {customer[0]:,} points available "
        f"(all stores + online, as of {stamp}). "
        "This is the balance to use — Clover's own Rewards count is per-store and out of date."
    )

    cursor = await db.execute(
        """SELECT m.clover_customer_id, m.merchant_id, l.name, l.api_token
           FROM loyalty_clover_id_map m
           JOIN locations l ON l.merchant_id = m.merchant_id
           WHERE m.loyalty_customer_id = ?""",
        (customer_id,),
    )
    updated: list[str] = []
    for clover_customer_id, merchant_id, loc_name, api_token in await cursor.fetchall():
        try:
            await CloverClient(merchant_id, api_token).update_customer_note(
                clover_customer_id, note
            )
            updated.append(loc_name)
        except Exception as e:
            print(f"[loyalty-balance-note] {customer_id} at {loc_name} failed: {e}")
    return updated


async def _sync_balance_to_clover_quietly(db: aiosqlite.Connection, customer_id: int) -> None:
    """Best-effort balance push: a register that's unreachable must not fail the sale."""
    try:
        await _sync_balance_to_clover(db, customer_id)
    except Exception as e:
        print(f"[loyalty-balance-note] Could not update customer {customer_id}: {e}")


async def _get_settings(db: aiosqlite.Connection) -> dict:
    cursor = await db.execute("SELECT key, value FROM loyalty_settings")
    rows = await cursor.fetchall()
    return {row[0]: row[1] for row in rows}


async def _customer_row_to_dict(row: aiosqlite.Row) -> dict:
    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2] or "",
        "phone": row[3] or "",
        "email": row[4] or "",
        "birthday": row[5] or "",
        "points_balance": row[6],
        "lifetime_points": row[7],
        "lifetime_redeemed": row[8],
        "clover_customer_id": row[9] or "",
        "notes": row[10] or "",
        "created_at": row[11],
        "updated_at": row[12],
    }


# ── Dashboard / Stats ──────────────────────────────────

@router.get("/dashboard")
async def loyalty_dashboard(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    settings = await _get_settings(db)

    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_customers")
    total_customers = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COALESCE(SUM(points_balance), 0) FROM loyalty_customers")
    total_outstanding = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COALESCE(SUM(lifetime_points), 0) FROM loyalty_customers")
    total_awarded = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COALESCE(SUM(lifetime_redeemed), 0) FROM loyalty_customers")
    total_redeemed = (await cursor.fetchone())[0]

    # Recent transactions
    cursor = await db.execute("""
        SELECT t.id, t.customer_id, t.type, t.points, t.description, t.order_id,
               t.location_name, t.created_at, c.first_name, c.last_name
        FROM loyalty_transactions t
        JOIN loyalty_customers c ON t.customer_id = c.id
        ORDER BY t.created_at DESC LIMIT 20
    """)
    recent = await cursor.fetchall()
    recent_txns = [{
        "id": r[0], "customer_id": r[1], "type": r[2], "points": r[3],
        "description": r[4], "order_id": r[5], "location_name": r[6],
        "created_at": r[7], "customer_name": f"{r[8]} {r[9] or ''}".strip()
    } for r in recent]

    # Top customers
    cursor = await db.execute("""
        SELECT id, first_name, last_name, phone, points_balance, lifetime_points
        FROM loyalty_customers ORDER BY lifetime_points DESC LIMIT 10
    """)
    top = await cursor.fetchall()
    top_customers = [{
        "id": t[0], "first_name": t[1], "last_name": t[2] or "",
        "phone": t[3] or "", "points_balance": t[4], "lifetime_points": t[5]
    } for t in top]

    return {
        "settings": settings,
        "stats": {
            "total_customers": total_customers,
            "total_outstanding_points": total_outstanding,
            "total_awarded_points": total_awarded,
            "total_redeemed_points": total_redeemed,
        },
        "recent_transactions": recent_txns,
        "top_customers": top_customers,
    }


# ── Customers ──────────────────────────────────────────

@router.get("/customers")
async def list_customers(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    offset = (page - 1) * per_page

    if search:
        like = f"%{search}%"
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, birthday,
                      points_balance, lifetime_points, lifetime_redeemed,
                      clover_customer_id, notes, created_at, updated_at
               FROM loyalty_customers
               WHERE first_name LIKE ? OR last_name LIKE ? OR phone LIKE ? OR email LIKE ?
               ORDER BY first_name ASC LIMIT ? OFFSET ?""",
            (like, like, like, like, per_page, offset),
        )
        count_cursor = await db.execute(
            """SELECT COUNT(*) FROM loyalty_customers
               WHERE first_name LIKE ? OR last_name LIKE ? OR phone LIKE ? OR email LIKE ?""",
            (like, like, like, like),
        )
    else:
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, birthday,
                      points_balance, lifetime_points, lifetime_redeemed,
                      clover_customer_id, notes, created_at, updated_at
               FROM loyalty_customers ORDER BY first_name ASC LIMIT ? OFFSET ?""",
            (per_page, offset),
        )
        count_cursor = await db.execute("SELECT COUNT(*) FROM loyalty_customers")

    rows = await cursor.fetchall()
    total = (await count_cursor.fetchone())[0]
    customers = [await _customer_row_to_dict(r) for r in rows]

    return {"customers": customers, "total": total, "page": page, "per_page": per_page}


@router.post("/customers")
async def create_customer(
    data: CustomerCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    settings = await _get_settings(db)
    signup_bonus = int(settings.get("signup_bonus", "0"))

    try:
        cursor = await db.execute(
            """INSERT INTO loyalty_customers (first_name, last_name, phone, email, birthday, notes,
                                             points_balance, lifetime_points)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.first_name, data.last_name or "", _normalize_phone(data.phone), data.email,
             data.birthday, data.notes, signup_bonus, signup_bonus),
        )
        customer_id = cursor.lastrowid

        # Record signup bonus transaction
        if signup_bonus > 0:
            await db.execute(
                """INSERT INTO loyalty_transactions (customer_id, type, points, description)
                   VALUES (?, 'earn', ?, 'Sign-up bonus')""",
                (customer_id, signup_bonus),
            )

        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail="A customer with this phone number already exists")

    await _push_customer_to_clover(
        db, customer_id, data.first_name, data.last_name or "",
        _normalize_phone(data.phone), data.email or "",
    )

    cursor = await db.execute(
        """SELECT id, first_name, last_name, phone, email, birthday,
                  points_balance, lifetime_points, lifetime_redeemed,
                  clover_customer_id, notes, created_at, updated_at
           FROM loyalty_customers WHERE id = ?""",
        (customer_id,),
    )
    row = await cursor.fetchone()
    return await _customer_row_to_dict(row)


@router.get("/customers/{customer_id}")
async def get_customer(
    customer_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute(
        """SELECT id, first_name, last_name, phone, email, birthday,
                  points_balance, lifetime_points, lifetime_redeemed,
                  clover_customer_id, notes, created_at, updated_at
           FROM loyalty_customers WHERE id = ?""",
        (customer_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found")

    customer = await _customer_row_to_dict(row)

    # Get transaction history
    tx_cursor = await db.execute(
        """SELECT id, type, points, description, order_id, location_name, created_at
           FROM loyalty_transactions WHERE customer_id = ?
           ORDER BY created_at DESC LIMIT 50""",
        (customer_id,),
    )
    txns = await tx_cursor.fetchall()
    customer["transactions"] = [{
        "id": t[0], "type": t[1], "points": t[2], "description": t[3],
        "order_id": t[4], "location_name": t[5], "created_at": t[6]
    } for t in txns]

    # Get redemption history
    rd_cursor = await db.execute(
        """SELECT r.id, r.points_spent, r.location_name, r.created_at, rw.name
           FROM loyalty_redemptions r
           JOIN loyalty_rewards rw ON r.reward_id = rw.id
           WHERE r.customer_id = ?
           ORDER BY r.created_at DESC LIMIT 20""",
        (customer_id,),
    )
    redemptions = await rd_cursor.fetchall()
    customer["redemptions"] = [{
        "id": rd[0], "points_spent": rd[1], "location_name": rd[2],
        "created_at": rd[3], "reward_name": rd[4]
    } for rd in redemptions]

    return customer


@router.put("/customers/{customer_id}")
async def update_customer(
    customer_id: int,
    data: CustomerUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    updates = []
    params = []
    if data.first_name is not None:
        updates.append("first_name = ?")
        params.append(data.first_name)
    if data.last_name is not None:
        updates.append("last_name = ?")
        params.append(data.last_name)
    if data.phone is not None:
        updates.append("phone = ?")
        params.append(_normalize_phone(data.phone))
    if data.email is not None:
        updates.append("email = ?")
        params.append(data.email)
    if data.birthday is not None:
        updates.append("birthday = ?")
        params.append(data.birthday)
    if data.notes is not None:
        updates.append("notes = ?")
        params.append(data.notes)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(customer_id)

    try:
        await db.execute(
            f"UPDATE loyalty_customers SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail="A customer with this phone number already exists")

    return await get_customer(customer_id, user, db)


@router.delete("/customers/{customer_id}")
async def delete_customer(
    customer_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM loyalty_transactions WHERE customer_id = ?", (customer_id,))
    await db.execute("DELETE FROM loyalty_redemptions WHERE customer_id = ?", (customer_id,))
    await db.execute("DELETE FROM loyalty_customers WHERE id = ?", (customer_id,))
    await db.commit()
    return {"status": "deleted"}


# ── Points Operations ──────────────────────────────────

@router.post("/customers/{customer_id}/award")
async def award_points(
    customer_id: int,
    data: AwardPoints,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    if data.points <= 0:
        raise HTTPException(status_code=400, detail="Points must be positive")

    cursor = await db.execute("SELECT id FROM loyalty_customers WHERE id = ?", (customer_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Customer not found")

    await db.execute(
        """UPDATE loyalty_customers
           SET points_balance = points_balance + ?,
               lifetime_points = lifetime_points + ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (data.points, data.points, customer_id),
    )
    await db.execute(
        """INSERT INTO loyalty_transactions (customer_id, type, points, description, order_id, location_name)
           VALUES (?, 'earn', ?, ?, ?, ?)""",
        (customer_id, data.points, data.description, data.order_id, data.location_name),
    )
    await db.commit()

    cursor = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    balance = (await cursor.fetchone())[0]
    await _sync_balance_to_clover_quietly(db, customer_id)
    return {"points_balance": balance, "points_awarded": data.points}


@router.post("/customers/{customer_id}/deduct")
async def deduct_points(
    customer_id: int,
    data: AwardPoints,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    if data.points <= 0:
        raise HTTPException(status_code=400, detail="Points must be positive")

    cursor = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found")
    if row[0] < data.points:
        raise HTTPException(status_code=400, detail="Insufficient points balance")

    await db.execute(
        """UPDATE loyalty_customers
           SET points_balance = points_balance - ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (data.points, customer_id),
    )
    await db.execute(
        """INSERT INTO loyalty_transactions (customer_id, type, points, description, order_id, location_name)
           VALUES (?, 'deduct', ?, ?, ?, ?)""",
        (customer_id, -data.points, data.description or "Manual deduction", data.order_id, data.location_name),
    )
    await db.commit()

    cursor = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    balance = (await cursor.fetchone())[0]
    await _sync_balance_to_clover_quietly(db, customer_id)
    return {"points_balance": balance, "points_deducted": data.points}


@router.post("/customers/{customer_id}/redeem")
async def redeem_reward(
    customer_id: int,
    data: RedeemReward,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    cust = await cursor.fetchone()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")

    cursor = await db.execute(
        "SELECT id, name, points_required, reward_value FROM loyalty_rewards WHERE id = ? AND is_active = 1",
        (data.reward_id,),
    )
    reward = await cursor.fetchone()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found or inactive")

    if cust[0] < reward[2]:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient points. Need {reward[2]}, have {cust[0]}"
        )

    await db.execute(
        """UPDATE loyalty_customers
           SET points_balance = points_balance - ?,
               lifetime_redeemed = lifetime_redeemed + ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (reward[2], reward[2], customer_id),
    )
    await db.execute(
        """INSERT INTO loyalty_transactions (customer_id, type, points, description, location_name)
           VALUES (?, 'redeem', ?, ?, ?)""",
        (customer_id, -reward[2], f"Redeemed: {reward[1]}", data.location_name),
    )
    await db.execute(
        """INSERT INTO loyalty_redemptions (customer_id, reward_id, points_spent, location_name)
           VALUES (?, ?, ?, ?)""",
        (customer_id, data.reward_id, reward[2], data.location_name),
    )
    await db.commit()

    cursor = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    balance = (await cursor.fetchone())[0]
    await _sync_balance_to_clover_quietly(db, customer_id)
    return {
        "points_balance": balance,
        "reward_redeemed": reward[1],
        "points_spent": reward[2],
        "discount_value": reward[3],
    }


# ── Rewards Management ──────────────────────────────────

@router.get("/rewards")
async def list_rewards(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute(
        "SELECT id, name, points_required, reward_type, reward_value, description, is_active, created_at FROM loyalty_rewards ORDER BY points_required ASC"
    )
    rows = await cursor.fetchall()
    return {"rewards": [{
        "id": r[0], "name": r[1], "points_required": r[2], "reward_type": r[3],
        "reward_value": r[4], "description": r[5], "is_active": bool(r[6]), "created_at": r[7]
    } for r in rows]}


@router.get("/rewards/public")
async def public_list_rewards(
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint for the Website to fetch active rewards (no auth required)."""
    cursor = await db.execute(
        "SELECT id, name, points_required, reward_type, reward_value, description, is_active FROM loyalty_rewards WHERE is_active = 1 ORDER BY points_required ASC"
    )
    rows = await cursor.fetchall()
    return {"rewards": [{
        "id": r[0], "name": r[1], "points_required": r[2], "reward_type": r[3],
        "reward_value": r[4], "description": r[5], "is_active": bool(r[6])
    } for r in rows]}


@router.post("/rewards")
async def create_reward(
    data: RewardCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute(
        """INSERT INTO loyalty_rewards (name, points_required, reward_type, reward_value, description)
           VALUES (?, ?, ?, ?, ?)""",
        (data.name, data.points_required, data.reward_type, data.reward_value, data.description),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "status": "created"}


@router.put("/rewards/{reward_id}")
async def update_reward(
    reward_id: int,
    data: RewardUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    updates = []
    params = []
    if data.name is not None:
        updates.append("name = ?")
        params.append(data.name)
    if data.points_required is not None:
        updates.append("points_required = ?")
        params.append(data.points_required)
    if data.reward_type is not None:
        updates.append("reward_type = ?")
        params.append(data.reward_type)
    if data.reward_value is not None:
        updates.append("reward_value = ?")
        params.append(data.reward_value)
    if data.description is not None:
        updates.append("description = ?")
        params.append(data.description)
    if data.is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if data.is_active else 0)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(reward_id)
    await db.execute(f"UPDATE loyalty_rewards SET {', '.join(updates)} WHERE id = ?", params)
    await db.commit()
    return {"status": "updated"}


@router.delete("/rewards/{reward_id}")
async def delete_reward(
    reward_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM loyalty_rewards WHERE id = ?", (reward_id,))
    await db.commit()
    return {"status": "deleted"}


# ── Settings ──────────────────────────────────────────

@router.get("/settings")
async def get_loyalty_settings(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    return await _get_settings(db)


@router.put("/settings")
async def update_loyalty_settings(
    data: LoyaltySettingsUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    for key, value in data.model_dump(exclude_none=True).items():
        await db.execute(
            "INSERT INTO loyalty_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
    await db.commit()
    return await _get_settings(db)


# ── Push Loyalty Customers → Clover ───────────────────

@router.post("/push-to-clover")
async def push_customers_to_clover(
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create loyalty customers that only exist online in Clover, so the register
    recognises them and their in-store purchases earn points.

    Picks members missing a Clover record at *any* location — a member created
    at one store is still unknown to the other register — oldest first, in
    batches (Clover rate limits); call again until `remaining` is 0."""
    loc_cursor = await db.execute("SELECT COUNT(*) FROM locations")
    location_count = (await loc_cursor.fetchone())[0]
    if not location_count:
        return {"status": "error", "detail": "No locations configured", "pushed": 0}

    cursor = await db.execute(
        """SELECT id, first_name, last_name, phone, email
           FROM loyalty_customers
           WHERE phone IS NOT NULL AND phone != ''
             AND (SELECT COUNT(DISTINCT m.merchant_id) FROM loyalty_clover_id_map m
                  WHERE m.loyalty_customer_id = loyalty_customers.id) < ?
           ORDER BY id ASC
           LIMIT ?""",
        (location_count, limit),
    )
    rows = await cursor.fetchall()

    # One pass over each register's customer list so members who already exist
    # there get linked rather than duplicated.
    existing_by_merchant: dict[str, dict[str, str]] = {}
    loc_rows = await (await db.execute("SELECT merchant_id, api_token FROM locations")).fetchall()
    for merchant_id, api_token in loc_rows:
        try:
            data = await CloverClient(merchant_id, api_token).get_customers(limit=100)
        except Exception as e:
            print(f"[loyalty-clover-push] Could not list customers for {merchant_id}: {e}")
            continue
        index: dict[str, str] = {}
        for cc in data.get("elements", []):
            elements = cc.get("phoneNumbers", {}).get("elements", []) if cc.get("phoneNumbers") else []
            for pe in elements:
                norm = _normalize_phone(pe.get("phoneNumber", ""))
                if len(norm) == 10 and cc.get("id"):
                    index.setdefault(norm, cc["id"])
                    break
        existing_by_merchant[merchant_id] = index

    pushed = 0
    failed = 0
    for row in rows:
        locations_pushed = await _push_customer_to_clover(
            db, row[0], row[1] or "Customer", row[2] or "", row[3] or "", row[4] or "",
            existing_by_merchant=existing_by_merchant,
        )
        if locations_pushed:
            pushed += 1
        else:
            failed += 1
        await asyncio.sleep(0.3)

    remaining_cursor = await db.execute(
        """SELECT COUNT(*) FROM loyalty_customers
           WHERE phone IS NOT NULL AND phone != ''
             AND (SELECT COUNT(DISTINCT m.merchant_id) FROM loyalty_clover_id_map m
                  WHERE m.loyalty_customer_id = loyalty_customers.id) < ?""",
        (location_count,),
    )
    remaining = (await remaining_cursor.fetchone())[0]

    return {
        "status": "done",
        "pushed": pushed,
        "failed": failed,
        "remaining": remaining,
    }


# ── Bulk Import Clover Customers → Loyalty ─────────────

@router.post("/bulk-import")
async def bulk_import_clover_customers(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Import all Clover customers (with phone numbers) into the loyalty program.
    Skips customers already enrolled (matched by phone number).
    Links Clover customer IDs for future order matching."""
    settings = await _get_settings(db)
    signup_bonus = int(settings.get("signup_bonus", "0"))

    # Get all locations
    loc_cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await loc_cursor.fetchall()
    if not locations:
        return {"status": "error", "detail": "No locations configured", "imported": 0}

    # Get existing loyalty customers by phone for dedup
    existing_cursor = await db.execute("SELECT phone FROM loyalty_customers WHERE phone IS NOT NULL AND phone != ''")
    existing_phones = set()
    for row in await existing_cursor.fetchall():
        raw = row[0] or ""
        norm = "".join(ch for ch in raw if ch.isdigit())
        if len(norm) >= 10:
            existing_phones.add(norm[-10:])

    total_imported = 0
    total_skipped = 0
    total_failed = 0
    total_clover_customers = 0
    details: list[dict] = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            cust_data = await client.get_customers(limit=100)
            clover_customers = cust_data.get("elements", [])
            total_clover_customers += len(clover_customers)

            for cc in clover_customers:
                cc_id = cc.get("id", "")
                first_name = (cc.get("firstName") or "").strip()
                last_name = (cc.get("lastName") or "").strip()

                # Extract phone
                phone_elements = cc.get("phoneNumbers", {}).get("elements", []) if cc.get("phoneNumbers") else []
                phone = ""
                for pe in phone_elements:
                    ph = pe.get("phoneNumber", "")
                    if ph:
                        phone = ph
                        break

                # Extract email
                email_elements = cc.get("emailAddresses", {}).get("elements", []) if cc.get("emailAddresses") else []
                email = ""
                for ee in email_elements:
                    em = ee.get("emailAddress", "")
                    if em:
                        email = em
                        break

                # Skip if no phone number (can't match for loyalty)
                if not phone:
                    total_skipped += 1
                    continue

                # Normalize phone
                norm_phone = "".join(ch for ch in phone if ch.isdigit())
                if len(norm_phone) >= 10:
                    norm_phone = norm_phone[-10:]
                else:
                    total_skipped += 1
                    continue

                # Skip if already exists
                if norm_phone in existing_phones:
                    # Still link the Clover ID if not already linked
                    cust_cursor = await db.execute(
                        "SELECT id, first_name, last_name FROM loyalty_customers WHERE phone LIKE ?",
                        (f"%{norm_phone}%",),
                    )
                    existing_row = await cust_cursor.fetchone()
                    if existing_row:
                        await _adopt_real_name(
                            db, existing_row[0], existing_row[1], existing_row[2], first_name, last_name
                        )
                    if existing_row and cc_id:
                        try:
                            await db.execute(
                                "INSERT OR IGNORE INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name) VALUES (?, ?, ?, ?)",
                                (existing_row[0], cc_id, merchant_id, loc_name),
                            )
                        except Exception:
                            pass
                    total_skipped += 1
                    continue

                # Import new customer
                if not first_name:
                    first_name = "Customer"

                try:
                    cursor = await db.execute(
                        """INSERT INTO loyalty_customers (first_name, last_name, phone, email, clover_customer_id,
                                                         points_balance, lifetime_points)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (first_name, last_name, norm_phone, email, cc_id,
                         signup_bonus, signup_bonus),
                    )
                    new_id = cursor.lastrowid

                    # Record signup bonus transaction
                    if signup_bonus > 0:
                        await db.execute(
                            """INSERT INTO loyalty_transactions (customer_id, type, points, description)
                               VALUES (?, 'earn', ?, 'Sign-up bonus (bulk import)')""",
                            (new_id, signup_bonus),
                        )

                    # Link Clover ID in mapping table
                    if cc_id:
                        try:
                            await db.execute(
                                "INSERT OR IGNORE INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name) VALUES (?, ?, ?, ?)",
                                (new_id, cc_id, merchant_id, loc_name),
                            )
                        except Exception:
                            pass

                    existing_phones.add(norm_phone)
                    total_imported += 1
                    details.append({
                        "name": f"{first_name} {last_name}".strip(),
                        "phone": norm_phone,
                        "location": loc_name,
                    })
                except aiosqlite.IntegrityError:
                    total_skipped += 1
                except Exception as e:
                    total_failed += 1

        except Exception as e:
            details.append({"location": loc_name, "error": str(e)})

        await asyncio.sleep(1)  # Rate limit between locations

    await db.commit()
    return {
        "status": "done",
        "total_clover_customers": total_clover_customers,
        "imported": total_imported,
        "skipped": total_skipped,
        "failed": total_failed,
        "details": details,
    }


# ── Clover Order Sync (POS → Loyalty) ──────────────────


async def _do_bulk_import_customers(db: aiosqlite.Connection) -> dict:
    """Core logic for importing Clover customers into loyalty. Callable from scheduler."""
    settings = await _get_settings(db)
    signup_bonus = int(settings.get("signup_bonus", "0"))

    loc_cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await loc_cursor.fetchall()
    if not locations:
        return {"status": "error", "detail": "No locations configured", "imported": 0}

    existing_cursor = await db.execute("SELECT phone FROM loyalty_customers WHERE phone IS NOT NULL AND phone != ''")
    existing_phones = set()
    for row in await existing_cursor.fetchall():
        raw = row[0] or ""
        norm = "".join(ch for ch in raw if ch.isdigit())
        if len(norm) >= 10:
            existing_phones.add(norm[-10:])

    total_imported = 0
    total_skipped = 0

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            cust_data = await client.get_customers(limit=100)
            clover_customers = cust_data.get("elements", [])

            for cc in clover_customers:
                cc_id = cc.get("id", "")
                first_name = (cc.get("firstName") or "").strip()
                last_name = (cc.get("lastName") or "").strip()

                phone_elements = cc.get("phoneNumbers", {}).get("elements", []) if cc.get("phoneNumbers") else []
                phone = ""
                for pe in phone_elements:
                    ph = pe.get("phoneNumber", "")
                    if ph:
                        phone = ph
                        break

                email_elements = cc.get("emailAddresses", {}).get("elements", []) if cc.get("emailAddresses") else []
                email = ""
                for ee in email_elements:
                    em = ee.get("emailAddress", "")
                    if em:
                        email = em
                        break

                if not phone:
                    total_skipped += 1
                    continue

                norm_phone = "".join(ch for ch in phone if ch.isdigit())
                if len(norm_phone) >= 10:
                    norm_phone = norm_phone[-10:]
                else:
                    total_skipped += 1
                    continue

                if norm_phone in existing_phones:
                    cust_cursor = await db.execute(
                        "SELECT id, first_name, last_name FROM loyalty_customers WHERE phone LIKE ?",
                        (f"%{norm_phone}%",),
                    )
                    existing_row = await cust_cursor.fetchone()
                    if existing_row:
                        await _adopt_real_name(
                            db, existing_row[0], existing_row[1], existing_row[2], first_name, last_name
                        )
                    if existing_row and cc_id:
                        try:
                            await db.execute(
                                "INSERT OR IGNORE INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name) VALUES (?, ?, ?, ?)",
                                (existing_row[0], cc_id, merchant_id, loc_name),
                            )
                        except Exception:
                            pass
                    total_skipped += 1
                    continue

                if not first_name:
                    first_name = "Customer"

                try:
                    cursor = await db.execute(
                        """INSERT INTO loyalty_customers (first_name, last_name, phone, email, clover_customer_id,
                                                         points_balance, lifetime_points)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (first_name, last_name, norm_phone, email, cc_id,
                         signup_bonus, signup_bonus),
                    )
                    new_id = cursor.lastrowid

                    if signup_bonus > 0:
                        await db.execute(
                            """INSERT INTO loyalty_transactions (customer_id, type, points, description)
                               VALUES (?, 'earn', ?, 'Sign-up bonus (bulk import)')""",
                            (new_id, signup_bonus),
                        )

                    if cc_id:
                        try:
                            await db.execute(
                                "INSERT OR IGNORE INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name) VALUES (?, ?, ?, ?)",
                                (new_id, cc_id, merchant_id, loc_name),
                            )
                        except Exception:
                            pass

                    existing_phones.add(norm_phone)
                    total_imported += 1
                except aiosqlite.IntegrityError:
                    total_skipped += 1
                except Exception:
                    pass

        except Exception as e:
            print(f"[loyalty-import] Error importing from {loc_name}: {e}")

        await asyncio.sleep(1)

    await db.commit()
    return {"status": "done", "imported": total_imported, "skipped": total_skipped}


LOYALTY_ORDER_LOOKBACK_DAYS = 30
_MAX_ORDERS_PER_LOCATION = 5000

# Register discounts whose name means "the member spent points", used when the
# setting is missing. Clover's own reward button writes a discount named
# "Rewards", which is what budtenders have been applying all along.
_DEFAULT_REDEMPTION_DISCOUNT_NAMES = ("rewards", "loyalty", "smoken token")


def _redemption_discount_names(settings: dict) -> tuple[str, ...]:
    """Lowercased fragments that identify a points-funded register discount."""
    configured = [
        fragment.strip().lower()
        for fragment in (settings.get("redemption_discount_names") or "").split(",")
        if fragment.strip()
    ]
    return tuple(configured) or _DEFAULT_REDEMPTION_DISCOUNT_NAMES


def _order_discounts(order: dict) -> list[dict]:
    """Every discount on a Clover order, whether applied to the whole ticket or a line."""
    discounts = list((order.get("discounts") or {}).get("elements", []) or [])
    for line in (order.get("lineItems") or {}).get("elements", []) or []:
        discounts.extend((line.get("discounts") or {}).get("elements", []) or [])
    return discounts


def _reward_for_discount(
    discount: dict,
    rewards: list[dict],
    name_fragments: tuple[str, ...],
) -> Optional[dict]:
    """The reward a register discount paid for, or None when it wasn't points.

    A percentage discount is a sale or a military/employee courtesy, never a
    reward, so only a fixed dollar amount matching a reward's value counts.
    """
    name = (discount.get("name") or "").strip().lower()
    if not name or not any(fragment in name for fragment in name_fragments):
        return None

    amount = discount.get("amount")
    if amount is None:
        return None

    dollars_off = abs(int(amount)) / 100.0
    candidates = [
        reward for reward in rewards
        if abs(float(reward["reward_value"]) - dollars_off) < 0.005
    ]
    if not candidates:
        return None
    # Cheapest reward of that value, so a member never overpays for the discount.
    return min(candidates, key=lambda reward: reward["points_required"])


async def _already_redeemed_for_order(
    db: aiosqlite.Connection,
    customer_id: int,
    clover_order_id: str,
) -> bool:
    cursor = await db.execute(
        """SELECT 1 FROM loyalty_transactions
           WHERE customer_id = ? AND order_id = ? AND type = 'redeem' LIMIT 1""",
        (customer_id, clover_order_id),
    )
    return await cursor.fetchone() is not None


async def _redeem_pos_discounts(
    db: aiosqlite.Connection,
    customer: dict,
    order: dict,
    clover_order_id: str,
    loc_name: str,
    rewards: list[dict],
    name_fragments: tuple[str, ...],
) -> int:
    """Spend the points behind reward discounts a budtender applied at the register.

    Redeeming in the dashboard was the only way to take points off, so a member
    kept the balance they had just spent in store. Returns the points removed.
    """
    if not rewards:
        return 0

    matched = [
        reward for discount in _order_discounts(order)
        if (reward := _reward_for_discount(discount, rewards, name_fragments))
    ]
    if not matched:
        return 0

    if await _already_redeemed_for_order(db, customer["id"], clover_order_id):
        return 0

    cursor = await db.execute(
        "SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer["id"],)
    )
    row = await cursor.fetchone()
    balance = row[0] if row else 0

    spent = 0
    for reward in matched:
        cost = reward["points_required"]
        if balance < cost:
            # The discount was already given away at the register; going negative
            # would only hide that, so leave it for staff to sort out.
            print(
                f"[loyalty-orders] {loc_name} order {clover_order_id}: member "
                f"{customer['id']} redeemed '{reward['name']}' with {balance} pts, "
                f"needs {cost} — points left alone"
            )
            continue

        balance -= cost
        spent += cost
        await db.execute(
            """UPDATE loyalty_customers
               SET points_balance = points_balance - ?,
                   lifetime_redeemed = lifetime_redeemed + ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (cost, cost, customer["id"]),
        )
        await db.execute(
            """INSERT INTO loyalty_transactions
               (customer_id, type, points, description, order_id, location_name)
               VALUES (?, 'redeem', ?, ?, ?, ?)""",
            (customer["id"], -cost,
             f"Redeemed at register: {reward['name']}", clover_order_id, loc_name),
        )
        await db.execute(
            """INSERT INTO loyalty_redemptions (customer_id, reward_id, points_spent, location_name)
               VALUES (?, ?, ?, ?)""",
            (customer["id"], reward["id"], cost, loc_name),
        )
    return spent


async def _fetch_recent_paid_orders(client: CloverClient, lookback_days: int) -> list:
    """Every paid order in the lookback window, paged.

    Clover caps a page at 100 and doesn't promise newest-first, so asking for a
    bare `limit=100` silently hid orders once a location passed 100 in the window.
    """
    start_ms = int((time.time() - lookback_days * 86400) * 1000)
    orders: list = []
    offset = 0
    limit = 100
    while True:
        data = await client.get_orders(
            limit=limit,
            offset=offset,
            filters=["payType!=NULL", f"createdTime>={start_ms}"],
            expand="lineItems,lineItems.discounts,customers,discounts",
        )
        elements = data.get("elements", [])
        orders.extend(elements)
        if len(elements) < limit or len(orders) >= _MAX_ORDERS_PER_LOCATION:
            break
        offset += limit
        await asyncio.sleep(0.3)
    return orders


async def _record_synced_order(
    db: aiosqlite.Connection,
    order_id: str,
    merchant_id: str,
    loc_name: str,
    order_total: int,
    status: str,
    customer_id: Optional[int] = None,
    points_awarded: int = 0,
    points_redeemed: int = 0,
    is_retry: bool = False,
) -> None:
    """Store the outcome for a Clover order, updating the row on a retry."""
    if is_retry:
        await db.execute(
            """UPDATE loyalty_synced_orders
               SET customer_id = ?, points_awarded = ?, points_redeemed = ?, status = ?,
                   order_total = ?, location_name = ?, synced_at = CURRENT_TIMESTAMP
               WHERE clover_order_id = ? AND location_merchant_id = ?""",
            (customer_id, points_awarded, points_redeemed, status, order_total,
             loc_name, order_id, merchant_id),
        )
        return
    await db.execute(
        """INSERT INTO loyalty_synced_orders
           (clover_order_id, location_merchant_id, location_name, order_total,
            customer_id, points_awarded, points_redeemed, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (order_id, merchant_id, loc_name, order_total, customer_id,
         points_awarded, points_redeemed, status),
    )


async def _do_sync_orders(
    db: aiosqlite.Connection,
    lookback_days: int = LOYALTY_ORDER_LOOKBACK_DAYS,
) -> dict:
    """Award loyalty points for Clover (POS) orders. Callable from the scheduler.

    Orders previously recorded as `no_match` are re-examined on every run: the
    customer often signs up (or gets linked to their Clover record) after the
    purchase, and their points would otherwise never arrive. `lookback_days` can
    be widened to recover purchases that fell out of the default window while
    matching was broken — a retry is only possible while the order is still
    inside the window.
    """
    settings = await _get_settings(db)
    points_per_dollar = int(settings.get("points_per_dollar", "1"))
    redemption_names = _redemption_discount_names(settings)

    reward_cursor = await db.execute(
        """SELECT id, name, points_required, reward_value FROM loyalty_rewards
           WHERE is_active = 1 AND reward_type = 'discount'"""
    )
    active_rewards = [
        {"id": r[0], "name": r[1], "points_required": r[2], "reward_value": r[3]}
        for r in await reward_cursor.fetchall()
    ]

    loc_cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await loc_cursor.fetchall()
    if not locations:
        return {"status": "no_locations", "orders_processed": 0, "points_awarded": 0}

    # Index loyalty customers by phone, name, and Clover customer id
    cust_cursor = await db.execute(
        "SELECT id, first_name, last_name, phone, email, clover_customer_id FROM loyalty_customers"
    )
    cust_rows = await cust_cursor.fetchall()

    phone_to_customer: dict[str, dict] = {}
    name_to_customer: dict[str, dict] = {}
    email_to_customer: dict[str, dict] = {}
    clover_id_to_customer: dict[str, dict] = {}
    customers_by_id: dict[int, dict] = {}

    for c in cust_rows:
        cust_dict = {
            "id": c[0], "first_name": c[1], "last_name": c[2],
            "phone": c[3], "email": c[4],
        }
        customers_by_id[c[0]] = cust_dict

        normalized = _normalize_phone(c[3])
        if len(normalized) == 10:
            phone_to_customer[normalized] = cust_dict

        name_key = _normalize_name(c[1], c[2])
        if name_key:
            name_to_customer[name_key] = cust_dict

        email_key = (c[4] or "").strip().lower()
        if email_key:
            email_to_customer[email_key] = cust_dict

        if c[5]:
            clover_id_to_customer[c[5]] = cust_dict

    map_cursor = await db.execute(
        "SELECT loyalty_customer_id, clover_customer_id FROM loyalty_clover_id_map"
    )
    for mapped_loyalty_id, mapped_clover_id in await map_cursor.fetchall():
        mapped = customers_by_id.get(mapped_loyalty_id)
        if mapped:
            clover_id_to_customer[mapped_clover_id] = mapped

    total_processed = 0
    total_points_awarded = 0
    total_skipped = 0
    total_no_match = 0
    total_retried = 0
    total_points_redeemed = 0
    details: list[dict] = []
    credited_customers: set[int] = set()

    for loc in locations:
        loc_name, merchant_id, api_token = loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Clover orders carry only customer ids, so map ids → phone/name first.
            clover_cust_data = await client.get_customers(limit=100)
            clover_id_to_phone: dict[str, str] = {}
            clover_id_to_name: dict[str, str] = {}
            clover_id_to_email: dict[str, str] = {}
            clover_id_to_raw_name: dict[str, tuple[str, str]] = {}
            for cc in clover_cust_data.get("elements", []):
                cc_id = cc.get("id", "")
                phone_elements = cc.get("phoneNumbers", {}).get("elements", []) if cc.get("phoneNumbers") else []
                for pe in phone_elements:
                    ph = pe.get("phoneNumber", "")
                    if ph:
                        clover_id_to_phone[cc_id] = ph
                        break
                email_elements = cc.get("emailAddresses", {}).get("elements", []) if cc.get("emailAddresses") else []
                for ee in email_elements:
                    em = (ee.get("emailAddress") or "").strip().lower()
                    if em:
                        clover_id_to_email[cc_id] = em
                        break
                cc_name = _normalize_name(cc.get("firstName"), cc.get("lastName"))
                if cc_name:
                    clover_id_to_name[cc_id] = cc_name
                    clover_id_to_raw_name[cc_id] = (
                        (cc.get("firstName") or "").strip(),
                        (cc.get("lastName") or "").strip(),
                    )

            recorded_cursor = await db.execute(
                "SELECT clover_order_id, status FROM loyalty_synced_orders WHERE location_merchant_id = ?",
                (merchant_id,),
            )
            recorded_status = {row[0]: row[1] for row in await recorded_cursor.fetchall()}

            orders = await _fetch_recent_paid_orders(client, lookback_days)

            for order in orders:
                order_id = order.get("id", "")
                if not order_id:
                    continue

                previous_status = recorded_status.get(order_id)
                if previous_status is not None and previous_status != "no_match":
                    total_skipped += 1
                    continue
                is_retry = previous_status == "no_match"

                order_total = order.get("total", 0)  # cents
                if order_total <= 0:
                    await _record_synced_order(
                        db, order_id, merchant_id, loc_name, order_total,
                        "zero_total", is_retry=is_retry,
                    )
                    total_skipped += 1
                    continue

                matched_customer = None
                order_customers = order.get("customers", {})
                order_cust_elements = (order_customers.get("elements", []) if order_customers else [])

                for oc in order_cust_elements:
                    clover_cust_id = oc.get("id", "")

                    if clover_cust_id and clover_cust_id in clover_id_to_customer:
                        matched_customer = clover_id_to_customer[clover_cust_id]
                        known_first, known_last = clover_id_to_raw_name.get(clover_cust_id, ("", ""))
                        if await _adopt_real_name(
                            db, matched_customer["id"], matched_customer.get("first_name"),
                            matched_customer.get("last_name"), known_first, known_last,
                        ):
                            matched_customer["first_name"] = known_first or known_last
                            matched_customer["last_name"] = known_last if known_first else ""
                        break

                    customer_phone = oc.get("phoneNumber") or oc.get("phone", "")
                    if not customer_phone and clover_cust_id:
                        customer_phone = clover_id_to_phone.get(clover_cust_id, "")

                    norm_phone = _normalize_phone(customer_phone)
                    if len(norm_phone) == 10:
                        matched_customer = phone_to_customer.get(norm_phone)

                    if not matched_customer:
                        order_name = _normalize_name(oc.get("firstName"), oc.get("lastName"))
                        clover_name = clover_id_to_name.get(clover_cust_id, "") if clover_cust_id else ""
                        for candidate in (clover_name, order_name):
                            if candidate:
                                matched_customer = name_to_customer.get(candidate)
                                if matched_customer:
                                    break

                    if not matched_customer:
                        order_email = (oc.get("email") or "").strip().lower()
                        clover_email = clover_id_to_email.get(clover_cust_id, "") if clover_cust_id else ""
                        for candidate in (clover_email, order_email):
                            if candidate:
                                matched_customer = email_to_customer.get(candidate)
                                if matched_customer:
                                    break

                    if matched_customer:
                        raw_first, raw_last = clover_id_to_raw_name.get(
                            clover_cust_id, ((oc.get("firstName") or "").strip(), (oc.get("lastName") or "").strip())
                        )
                        if await _adopt_real_name(
                            db, matched_customer["id"], matched_customer.get("first_name"),
                            matched_customer.get("last_name"), raw_first, raw_last,
                        ):
                            matched_customer["first_name"] = raw_first or raw_last
                            matched_customer["last_name"] = raw_last if raw_first else ""
                        if clover_cust_id:
                            await db.execute(
                                """INSERT OR IGNORE INTO loyalty_clover_id_map
                                   (loyalty_customer_id, clover_customer_id, merchant_id, location_name)
                                   VALUES (?, ?, ?, ?)""",
                                (matched_customer["id"], clover_cust_id, merchant_id, loc_name),
                            )
                            clover_id_to_customer[clover_cust_id] = matched_customer
                        break

                if not matched_customer:
                    if not is_retry:
                        await _record_synced_order(
                            db, order_id, merchant_id, loc_name, order_total, "no_match",
                        )
                        total_no_match += 1
                    continue

                points_redeemed = await _redeem_pos_discounts(
                    db, matched_customer, order, order_id, loc_name,
                    active_rewards, redemption_names,
                )
                total_points_redeemed += points_redeemed

                order_dollars = order_total / 100.0
                points_to_award = math.floor(order_dollars * points_per_dollar)
                if points_to_award <= 0:
                    await _record_synced_order(
                        db, order_id, merchant_id, loc_name, order_total, "zero_points",
                        customer_id=matched_customer["id"],
                        points_redeemed=points_redeemed, is_retry=is_retry,
                    )
                    total_skipped += 1
                    if points_redeemed:
                        credited_customers.add(matched_customer["id"])
                    continue

                await db.execute(
                    """UPDATE loyalty_customers
                       SET points_balance = points_balance + ?,
                           lifetime_points = lifetime_points + ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (points_to_award, points_to_award, matched_customer["id"]),
                )
                await db.execute(
                    """INSERT INTO loyalty_transactions (customer_id, type, points, description, order_id, location_name)
                       VALUES (?, 'earn', ?, ?, ?, ?)""",
                    (matched_customer["id"], points_to_award,
                     f"POS purchase ${order_dollars:.2f} at {loc_name}",
                     order_id, loc_name),
                )
                await _record_synced_order(
                    db, order_id, merchant_id, loc_name, order_total, "awarded",
                    customer_id=matched_customer["id"], points_awarded=points_to_award,
                    points_redeemed=points_redeemed, is_retry=is_retry,
                )

                total_processed += 1
                total_points_awarded += points_to_award
                credited_customers.add(matched_customer["id"])
                if is_retry:
                    total_retried += 1
                details.append({
                    "order_id": order_id,
                    "location": loc_name,
                    "customer": f"{matched_customer['first_name']} {matched_customer.get('last_name', '') or ''}".strip(),
                    "order_total": order_dollars,
                    "points_awarded": points_to_award,
                    "points_redeemed": points_redeemed,
                    "late_match": is_retry,
                })

        except Exception as e:
            print(f"[loyalty-orders] Error syncing orders from {loc_name}: {e}")
            details.append({"location": loc_name, "error": str(e)})

        await db.commit()
        # Delay between locations to avoid Clover API rate limiting
        await asyncio.sleep(2)

    await db.commit()

    # Refresh the POS-visible balance so the register shows the new total.
    for credited_id in credited_customers:
        await _sync_balance_to_clover_quietly(db, credited_id)

    return {
        "status": "done",
        "lookback_days": lookback_days,
        "balances_pushed": len(credited_customers),
        "orders_processed": total_processed,
        "points_awarded": total_points_awarded,
        "points_redeemed": total_points_redeemed,
        "orders_skipped": total_skipped,
        "orders_no_match": total_no_match,
        "orders_late_matched": total_retried,
        "details": details,
    }


@router.post("/sync-orders")
async def sync_clover_orders(
    lookback_days: int = Query(LOYALTY_ORDER_LOOKBACK_DAYS, ge=1, le=400),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Poll Clover orders and award loyalty points for matched customers.
    Matches on the order's Clover customer id, then phone, then name, then email."""
    return await _do_sync_orders(db, lookback_days=lookback_days)


def _register_discount_name(reward: dict) -> str:
    """The register button's label. "Rewards" is what the sync watches for."""
    return f"Rewards ${float(reward['reward_value']):.0f} off ({reward['points_required']} pts)"


@router.post("/push-reward-discounts")
async def push_reward_discounts(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Give every active reward a discount button at both registers.

    A budtender can only hand over a reward they can tap on the ticket, and the
    order sync recognises these names, so the points come off automatically.
    """
    reward_cursor = await db.execute(
        """SELECT id, name, points_required, reward_value FROM loyalty_rewards
           WHERE is_active = 1 AND reward_type = 'discount' ORDER BY points_required"""
    )
    rewards = [
        {"id": r[0], "name": r[1], "points_required": r[2], "reward_value": r[3]}
        for r in await reward_cursor.fetchall()
    ]

    redemption_names = _redemption_discount_names(await _get_settings(db))

    loc_cursor = await db.execute("SELECT name, merchant_id, api_token FROM locations")
    locations = await loc_cursor.fetchall()

    created: list[str] = []
    existing: list[str] = []
    errors: list[str] = []

    for loc_name, merchant_id, api_token in locations:
        try:
            client = CloverClient(merchant_id, api_token)
            discounts = (await client.get_discounts()).get("elements", [])
            # A reward is only covered by a button the sync will recognise: an
            # unrelated "$10 PREROLL" discount must not stand in for a reward.
            amounts_present = {
                abs(int(d["amount"]))
                for d in discounts
                if d.get("amount") is not None
                and any(f in (d.get("name") or "").lower() for f in redemption_names)
            }
            for reward in rewards:
                cents = round(float(reward["reward_value"]) * 100)
                label = f"{loc_name}: {_register_discount_name(reward)}"
                if cents in amounts_present:
                    existing.append(label)
                    continue
                try:
                    # A reward is a fixed dollar amount, never a percentage.
                    await client.create_discount(
                        name=_register_discount_name(reward), amount=cents
                    )
                    created.append(label)
                except Exception as e:
                    errors.append(f"{label}: {e}")
                await asyncio.sleep(0.3)
        except Exception as e:
            errors.append(f"{loc_name}: {e}")

    return {
        "status": "done" if not errors else "partial",
        "created": created,
        "already_present": existing,
        "errors": errors,
    }


@router.post("/push-balances")
async def push_balances_to_clover(
    limit: int = Query(500, ge=1, le=5000),
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Write every member's current balance onto their Clover customer record.

    Use after a backfill so budtenders see the corrected totals right away
    instead of waiting for each member's next purchase.
    """
    cursor = await db.execute(
        """SELECT DISTINCT loyalty_customer_id FROM loyalty_clover_id_map
           ORDER BY loyalty_customer_id ASC LIMIT ?""",
        (limit,),
    )
    members = [row[0] for row in await cursor.fetchall()]

    updated = 0
    failed = 0
    for member_id in members:
        if await _sync_balance_to_clover(db, member_id):
            updated += 1
        else:
            failed += 1
        await asyncio.sleep(0.2)  # stay under Clover's rate limit

    return {
        "status": "done",
        "members_considered": len(members),
        "members_updated": updated,
        "members_failed": failed,
    }


@router.get("/sync-status")
async def get_sync_status(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get the status of order sync — last sync time, total synced, etc."""
    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_synced_orders")
    total = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_synced_orders WHERE status = 'awarded'")
    awarded = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT MAX(synced_at) FROM loyalty_synced_orders")
    last_sync = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COALESCE(SUM(points_awarded), 0) FROM loyalty_synced_orders WHERE status = 'awarded'")
    total_points = (await cursor.fetchone())[0]

    # Recent synced orders
    cursor = await db.execute("""
        SELECT s.clover_order_id, s.location_name, s.order_total, s.points_awarded,
               s.status, s.synced_at, c.first_name, c.last_name
        FROM loyalty_synced_orders s
        LEFT JOIN loyalty_customers c ON s.customer_id = c.id
        ORDER BY s.synced_at DESC LIMIT 20
    """)
    recent = await cursor.fetchall()

    return {
        "total_orders_synced": total,
        "total_orders_awarded": awarded,
        "total_points_awarded": total_points,
        "last_sync": last_sync,
        "recent": [{
            "order_id": r[0], "location": r[1],
            "order_total": (r[2] or 0) / 100.0,
            "points_awarded": r[3] or 0,
            "status": r[4], "synced_at": r[5],
            "customer_name": f"{r[6] or ''} {r[7] or ''}".strip() if r[6] else "",
        } for r in recent],
    }


@router.post("/sync-reset")
async def reset_sync(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Reset all synced orders so they can be re-processed with updated matching logic."""
    await db.execute("DELETE FROM loyalty_synced_orders")
    await db.commit()
    return {"status": "reset", "message": "All synced orders cleared. Run sync again to re-process."}


# ── Public lookup (for e-commerce, no auth required) ───

class PublicSignup(BaseModel):
    first_name: str
    last_name: Optional[str] = ""
    phone: str
    email: Optional[str] = None
    birthday: Optional[str] = None


class ReferralRequest(BaseModel):
    referrer_phone: str
    friend_name: str
    friend_email: str


@router.post("/signup")
async def public_signup(
    data: PublicSignup,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint for customers to sign up for loyalty online."""
    return await _do_signup(data.phone, data.first_name, data.last_name or "", data.email or "", db, data.birthday or "")


@router.get("/signup")
async def public_signup_get(
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    birthday: str = "",
    db: aiosqlite.Connection = Depends(get_db),
):
    """GET-based signup endpoint (avoids CORS preflight for cross-origin calls)."""
    return await _do_signup(phone, first_name, last_name, email, db, birthday)


async def _do_signup(phone: str, first_name: str, last_name: str, email: str, db: aiosqlite.Connection, birthday: str = ""):
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")
    if not first_name:
        raise HTTPException(status_code=400, detail="First name is required")

    # Check if customer already exists (match on normalized digits so a number
    # entered with dashes isn't treated as a different account than one without).
    norm_phone = _normalize_phone(phone)
    if norm_phone:
        cursor = await db.execute(
            f"SELECT id, first_name, points_balance FROM loyalty_customers WHERE {_PHONE_DIGITS_SQL} LIKE ?",
            (f"%{norm_phone}",),
        )
    else:
        cursor = await db.execute(
            "SELECT id, first_name, points_balance FROM loyalty_customers WHERE phone = ?",
            (phone,),
        )
    existing = await cursor.fetchone()
    if existing:
        return {
            "status": "existing",
            "message": f"Welcome back, {existing[1]}! You already have an account.",
            "points": existing[2],
        }

    settings = await _get_settings(db)
    signup_bonus = int(settings.get("signup_bonus", "0"))

    try:
        cursor = await db.execute(
            """INSERT INTO loyalty_customers (first_name, last_name, phone, email, birthday,
                                             points_balance, lifetime_points)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (first_name, last_name, norm_phone or phone, email, birthday,
             signup_bonus, signup_bonus),
        )
        customer_id = cursor.lastrowid

        if signup_bonus > 0:
            await db.execute(
                """INSERT INTO loyalty_transactions (customer_id, type, points, description)
                   VALUES (?, 'earn', ?, 'Sign-up bonus (online)')""",
                (customer_id, signup_bonus),
            )

        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail="A customer with this phone number already exists")

    await _push_customer_to_clover(
        db, customer_id, first_name, last_name, norm_phone or phone, email
    )

    return {
        "status": "created",
        "message": f"Welcome to Hemp Rewards, {first_name}!",
        "points": signup_bonus,
        "signup_bonus": signup_bonus,
    }


@router.get("/lookup")
async def lookup_customer(
    phone: Optional[str] = None,
    email: Optional[str] = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint for e-commerce loyalty lookup by phone or email."""
    if not phone and not email:
        raise HTTPException(status_code=400, detail="Provide phone or email")

    if phone:
        norm_phone = _normalize_phone(phone)
        if norm_phone:
            cursor = await db.execute(
                f"""SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
                   FROM loyalty_customers WHERE {_PHONE_DIGITS_SQL} LIKE ?""",
                (f"%{norm_phone}",),
            )
        else:
            cursor = await db.execute(
                """SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
                   FROM loyalty_customers WHERE phone = ?""",
                (phone,),
            )
    else:
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
               FROM loyalty_customers WHERE email = ? COLLATE NOCASE""",
            (email,),
        )
    row = await cursor.fetchone()
    if not row:
        return {"found": False}

    # Get available rewards
    rw_cursor = await db.execute(
        "SELECT id, name, points_required, reward_type, reward_value, description FROM loyalty_rewards WHERE is_active = 1 ORDER BY points_required ASC"
    )
    rewards = await rw_cursor.fetchall()

    # Get transaction history
    tx_cursor = await db.execute(
        """SELECT type, points, description, created_at
           FROM loyalty_transactions WHERE customer_id = ?
           ORDER BY created_at DESC LIMIT 50""",
        (row[0],),
    )
    txns = await tx_cursor.fetchall()

    # Get birthday
    bday_cursor = await db.execute(
        "SELECT birthday FROM loyalty_customers WHERE id = ?", (row[0],)
    )
    bday_row = await bday_cursor.fetchone()

    # Get lifetime spend from Clover order syncs (sum of earn transactions with order_id)
    spend_cursor = await db.execute(
        """SELECT COALESCE(SUM(points), 0) FROM loyalty_transactions
           WHERE customer_id = ? AND type = 'earn'""",
        (row[0],),
    )
    lifetime_earned = (await spend_cursor.fetchone())[0]

    return {
        "found": True,
        "customer": {
            "id": row[0], "first_name": row[1], "last_name": row[2] or "",
            "phone": row[3] or "", "email": row[4] or "",
            "points_balance": row[5], "lifetime_points": row[6],
            "birthday": bday_row[0] or "" if bday_row else "",
        },
        "transactions": [{
            "type": t[0], "points": t[1], "description": t[2] or "", "created_at": t[3]
        } for t in txns],
        "available_rewards": [{
            "id": r[0], "name": r[1], "points_required": r[2],
            "reward_type": r[3], "reward_value": r[4], "description": r[5],
            "can_redeem": row[5] >= r[2],
        } for r in rewards],
    }
