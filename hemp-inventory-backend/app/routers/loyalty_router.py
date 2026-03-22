from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import aiosqlite
import asyncio
import math

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
            (data.first_name, data.last_name or "", data.phone, data.email,
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
        params.append(data.phone)
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
                        "SELECT id FROM loyalty_customers WHERE phone LIKE ?",
                        (f"%{norm_phone}%",),
                    )
                    existing_row = await cust_cursor.fetchone()
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
                        "SELECT id FROM loyalty_customers WHERE phone LIKE ?",
                        (f"%{norm_phone}%",),
                    )
                    existing_row = await cust_cursor.fetchone()
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


async def _do_sync_orders(db: aiosqlite.Connection) -> dict:
    """Core logic for syncing Clover orders to loyalty points. Callable from scheduler."""
    settings = await _get_settings(db)
    points_per_dollar = int(settings.get("points_per_dollar", "1"))

    loc_cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await loc_cursor.fetchall()
    if not locations:
        return {"status": "no_locations", "orders_processed": 0, "points_awarded": 0}

    cust_cursor = await db.execute(
        "SELECT id, first_name, last_name, phone, email, clover_customer_id FROM loyalty_customers"
    )
    cust_rows = await cust_cursor.fetchall()

    phone_to_customer: dict[str, dict] = {}
    name_to_customer: dict[str, dict] = {}
    clover_id_to_customer: dict[str, dict] = {}

    for c in cust_rows:
        cust_dict = {
            "id": c[0], "first_name": c[1], "last_name": c[2],
            "phone": c[3], "email": c[4],
        }
        raw_phone = c[3] or ""
        normalized = "".join(ch for ch in raw_phone if ch.isdigit())
        if len(normalized) >= 10:
            normalized = normalized[-10:]
            phone_to_customer[normalized] = cust_dict

        name_key = f"{(c[1] or '').strip()} {(c[2] or '').strip()}".strip().lower()
        if name_key:
            name_to_customer[name_key] = cust_dict

        clover_cid = c[5] or ""
        if clover_cid:
            clover_id_to_customer[clover_cid] = cust_dict

    map_cursor = await db.execute(
        "SELECT loyalty_customer_id, clover_customer_id, merchant_id FROM loyalty_clover_id_map"
    )
    map_rows = await map_cursor.fetchall()
    for mr in map_rows:
        mapped_clover_id = mr[1]
        mapped_loyalty_id = mr[0]
        for c in cust_rows:
            if c[0] == mapped_loyalty_id:
                clover_id_to_customer[mapped_clover_id] = {
                    "id": c[0], "first_name": c[1], "last_name": c[2],
                    "phone": c[3], "email": c[4],
                }
                break

    total_processed = 0
    total_points_awarded = 0
    total_skipped = 0
    total_no_match = 0

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            clover_cust_data = await client.get_customers(limit=100)
            clover_id_to_phone: dict[str, str] = {}
            clover_id_to_name: dict[str, str] = {}
            for cc in clover_cust_data.get("elements", []):
                cc_id = cc.get("id", "")
                phone_elements = cc.get("phoneNumbers", {}).get("elements", []) if cc.get("phoneNumbers") else []
                for pe in phone_elements:
                    ph = pe.get("phoneNumber", "")
                    if ph:
                        clover_id_to_phone[cc_id] = ph
                        break
                cc_name = f"{(cc.get('firstName') or '').strip()} {(cc.get('lastName') or '').strip()}".strip().lower()
                if cc_name:
                    clover_id_to_name[cc_id] = cc_name

            orders_data = await client.get_orders(limit=100, filter_str="payType!=NULL", paginate_all=True)
            orders = orders_data.get("elements", [])

            for order in orders:
                order_id = order.get("id", "")
                if not order_id:
                    continue

                synced_cursor = await db.execute(
                    "SELECT id FROM loyalty_synced_orders WHERE clover_order_id = ? AND location_merchant_id = ?",
                    (order_id, merchant_id),
                )
                if await synced_cursor.fetchone():
                    total_skipped += 1
                    continue

                order_total = order.get("total", 0)
                if order_total <= 0:
                    await db.execute(
                        "INSERT INTO loyalty_synced_orders (clover_order_id, location_merchant_id, location_name, order_total, status) VALUES (?, ?, ?, ?, 'zero_total')",
                        (order_id, merchant_id, loc_name, order_total),
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
                        break

                    customer_phone = oc.get("phoneNumber") or oc.get("phone", "")
                    if not customer_phone and clover_cust_id and clover_cust_id in clover_id_to_phone:
                        customer_phone = clover_id_to_phone[clover_cust_id]

                    if customer_phone:
                        norm_phone = "".join(ch for ch in customer_phone if ch.isdigit())
                        if len(norm_phone) >= 10:
                            norm_phone = norm_phone[-10:]
                            matched_customer = phone_to_customer.get(norm_phone)
                            if matched_customer:
                                if clover_cust_id:
                                    try:
                                        await db.execute(
                                            "INSERT OR IGNORE INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name) VALUES (?, ?, ?, ?)",
                                            (matched_customer["id"], clover_cust_id, merchant_id, loc_name),
                                        )
                                        clover_id_to_customer[clover_cust_id] = matched_customer
                                    except Exception:
                                        pass
                                break

                    if clover_cust_id and clover_cust_id in clover_id_to_name:
                        clover_name = clover_id_to_name[clover_cust_id]
                        if clover_name and clover_name in name_to_customer:
                            matched_customer = name_to_customer[clover_name]
                            if clover_cust_id:
                                try:
                                    await db.execute(
                                        "INSERT OR IGNORE INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name) VALUES (?, ?, ?, ?)",
                                        (matched_customer["id"], clover_cust_id, merchant_id, loc_name),
                                    )
                                    clover_id_to_customer[clover_cust_id] = matched_customer
                                except Exception:
                                    pass
                            break

                if not matched_customer:
                    await db.execute(
                        "INSERT INTO loyalty_synced_orders (clover_order_id, location_merchant_id, location_name, order_total, status) VALUES (?, ?, ?, ?, 'no_match')",
                        (order_id, merchant_id, loc_name, order_total),
                    )
                    total_no_match += 1
                    continue

                order_dollars = order_total / 100.0
                points_to_award = math.floor(order_dollars * points_per_dollar)
                if points_to_award <= 0:
                    await db.execute(
                        "INSERT INTO loyalty_synced_orders (clover_order_id, location_merchant_id, location_name, order_total, customer_id, status) VALUES (?, ?, ?, ?, ?, 'zero_points')",
                        (order_id, merchant_id, loc_name, order_total, matched_customer["id"]),
                    )
                    total_skipped += 1
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
                await db.execute(
                    "INSERT INTO loyalty_synced_orders (clover_order_id, location_merchant_id, location_name, order_total, customer_id, points_awarded, status) VALUES (?, ?, ?, ?, ?, ?, 'awarded')",
                    (order_id, merchant_id, loc_name, order_total, matched_customer["id"], points_to_award),
                )

                total_processed += 1
                total_points_awarded += points_to_award

        except Exception as e:
            print(f"[loyalty-orders] Error syncing orders from {loc_name}: {e}")

        await asyncio.sleep(2)

    await db.commit()
    return {
        "status": "done",
        "orders_processed": total_processed,
        "points_awarded": total_points_awarded,
        "orders_skipped": total_skipped,
        "orders_no_match": total_no_match,
    }


@router.post("/sync-orders")
async def sync_clover_orders(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Poll Clover orders and auto-award loyalty points for matched customers."""
    return await _do_sync_orders(db)


@router.post("/rematch-orders")
async def rematch_unmatched_orders(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Re-attempt matching on all previously 'no_match' orders.
    Useful after importing new customers or fixing customer data."""
    # Delete all no_match records so they get re-processed on next sync
    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_synced_orders WHERE status = 'no_match'")
    no_match_count = (await cursor.fetchone())[0]

    if no_match_count == 0:
        return {"status": "done", "message": "No unmatched orders to retry.", "cleared": 0}

    await db.execute("DELETE FROM loyalty_synced_orders WHERE status = 'no_match'")
    await db.commit()

    # Now run the sync again to re-process those orders
    result = await _do_sync_orders(db)
    result["previously_unmatched"] = no_match_count
    return result


@router.get("/unmatched-report")
async def unmatched_orders_report(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Report on unmatched orders from the last 30 days."""
    # Total no_match orders
    cursor = await db.execute(
        "SELECT COUNT(*) FROM loyalty_synced_orders WHERE status = 'no_match'"
    )
    total_no_match = (await cursor.fetchone())[0]

    # No_match in last 30 days
    cursor = await db.execute(
        "SELECT COUNT(*) FROM loyalty_synced_orders WHERE status = 'no_match' AND synced_at >= datetime('now', '-30 days')"
    )
    no_match_30d = (await cursor.fetchone())[0]

    # Total dollar value of unmatched orders (last 30 days)
    cursor = await db.execute(
        "SELECT COALESCE(SUM(order_total), 0) FROM loyalty_synced_orders WHERE status = 'no_match' AND synced_at >= datetime('now', '-30 days')"
    )
    unmatched_value_cents = (await cursor.fetchone())[0]

    # Estimated lost points
    settings = await _get_settings(db)
    points_per_dollar = int(settings.get("points_per_dollar", "1"))
    estimated_lost_points = math.floor((unmatched_value_cents / 100.0) * points_per_dollar)

    # By location breakdown
    cursor = await db.execute(
        """SELECT location_name, COUNT(*), COALESCE(SUM(order_total), 0)
           FROM loyalty_synced_orders
           WHERE status = 'no_match' AND synced_at >= datetime('now', '-30 days')
           GROUP BY location_name"""
    )
    by_location = await cursor.fetchall()

    # Recent unmatched orders (last 20)
    cursor = await db.execute(
        """SELECT clover_order_id, location_name, order_total, synced_at
           FROM loyalty_synced_orders
           WHERE status = 'no_match'
           ORDER BY synced_at DESC LIMIT 20"""
    )
    recent = await cursor.fetchall()

    # Overall sync stats for context
    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_synced_orders WHERE status = 'awarded'")
    total_awarded = (await cursor.fetchone())[0]

    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_synced_orders")
    total_synced = (await cursor.fetchone())[0]

    match_rate = ((total_awarded / total_synced * 100) if total_synced > 0 else 0)

    return {
        "total_unmatched_orders": total_no_match,
        "unmatched_last_30_days": no_match_30d,
        "unmatched_value_dollars": round(unmatched_value_cents / 100.0, 2),
        "estimated_lost_points": estimated_lost_points,
        "total_awarded_orders": total_awarded,
        "total_synced_orders": total_synced,
        "match_rate_percent": round(match_rate, 1),
        "by_location": [{
            "location": r[0],
            "unmatched_count": r[1],
            "unmatched_value_dollars": round(r[2] / 100.0, 2),
        } for r in by_location],
        "recent_unmatched": [{
            "order_id": r[0], "location": r[1],
            "order_total_dollars": round((r[2] or 0) / 100.0, 2),
            "synced_at": r[3],
        } for r in recent],
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


@router.post("/signup")
async def public_signup(
    data: PublicSignup,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint for customers to sign up for loyalty online."""
    return await _do_signup(data.phone, data.first_name, data.last_name or "", data.email or "", db)


@router.get("/signup")
async def public_signup_get(
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    db: aiosqlite.Connection = Depends(get_db),
):
    """GET-based signup endpoint (avoids CORS preflight for cross-origin calls)."""
    return await _do_signup(phone, first_name, last_name, email, db)


async def _do_signup(phone: str, first_name: str, last_name: str, email: str, db: aiosqlite.Connection):
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")
    if not first_name:
        raise HTTPException(status_code=400, detail="First name is required")

    # Check if customer already exists
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
            """INSERT INTO loyalty_customers (first_name, last_name, phone, email,
                                             points_balance, lifetime_points)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (first_name, last_name, phone, email,
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
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
               FROM loyalty_customers WHERE phone = ?""",
            (phone,),
        )
    else:
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
               FROM loyalty_customers WHERE email = ?""",
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

    return {
        "found": True,
        "customer": {
            "id": row[0], "first_name": row[1], "last_name": row[2] or "",
            "phone": row[3] or "", "email": row[4] or "",
            "points_balance": row[5], "lifetime_points": row[6],
        },
        "available_rewards": [{
            "id": r[0], "name": r[1], "points_required": r[2],
            "reward_type": r[3], "reward_value": r[4], "description": r[5],
            "can_redeem": row[5] >= r[2],
        } for r in rewards],
    }
