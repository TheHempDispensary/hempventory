from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional
from pydantic import BaseModel
import aiosqlite
import os

from app.database import get_db

router = APIRouter(prefix="/api/discounts", tags=["discounts"])


def _verify_admin(request: Request):
    """Verify admin JWT token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    import jwt
    token = auth.split(" ", 1)[1]
    jwt_secret = os.environ.get("JWT_SECRET", "hemp-inventory-secret-key")
    try:
        jwt.decode(token, jwt_secret, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


class CreateDiscountRequest(BaseModel):
    code: str
    discount_type: str = "percentage"
    discount_value: float
    description: str = ""
    min_order_amount: int = 0
    max_uses: int = 0
    starts_at: Optional[str] = None
    expires_at: Optional[str] = None


class UpdateDiscountRequest(BaseModel):
    code: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    description: Optional[str] = None
    min_order_amount: Optional[int] = None
    max_uses: Optional[int] = None
    is_active: Optional[bool] = None
    starts_at: Optional[str] = None
    expires_at: Optional[str] = None


@router.get("")
async def get_discounts(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all discount codes (requires admin auth)."""
    _verify_admin(request)

    cursor = await db.execute(
        "SELECT * FROM discount_codes ORDER BY created_at DESC"
    )
    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    discounts = [dict(zip(columns, row)) for row in rows]
    return {"discounts": discounts}


@router.post("")
async def create_discount(
    data: CreateDiscountRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create a new discount code (requires admin auth)."""
    _verify_admin(request)

    code = data.code.strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Code is required")
    if data.discount_type not in ("percentage", "fixed"):
        raise HTTPException(status_code=400, detail="Type must be 'percentage' or 'fixed'")
    if data.discount_value <= 0:
        raise HTTPException(status_code=400, detail="Value must be greater than 0")

    try:
        cursor = await db.execute(
            """INSERT INTO discount_codes (code, discount_type, discount_value, description, min_order_amount, max_uses, starts_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, data.discount_type, data.discount_value, data.description,
             data.min_order_amount, data.max_uses, data.starts_at, data.expires_at),
        )
        await db.commit()
        return {"success": True, "id": cursor.lastrowid, "code": code}
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Discount code '{code}' already exists")


@router.patch("/{discount_id}")
async def update_discount(
    discount_id: int,
    data: UpdateDiscountRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update an existing discount code (requires admin auth)."""
    _verify_admin(request)

    updates = []
    params = []
    if data.code is not None:
        updates.append("code = ?")
        params.append(data.code.strip().upper())
    if data.discount_type is not None:
        updates.append("discount_type = ?")
        params.append(data.discount_type)
    if data.discount_value is not None:
        updates.append("discount_value = ?")
        params.append(data.discount_value)
    if data.description is not None:
        updates.append("description = ?")
        params.append(data.description)
    if data.min_order_amount is not None:
        updates.append("min_order_amount = ?")
        params.append(data.min_order_amount)
    if data.max_uses is not None:
        updates.append("max_uses = ?")
        params.append(data.max_uses)
    if data.is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if data.is_active else 0)
    if data.starts_at is not None:
        updates.append("starts_at = ?")
        params.append(data.starts_at)
    if data.expires_at is not None:
        updates.append("expires_at = ?")
        params.append(data.expires_at)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(discount_id)
    await db.execute(
        f"UPDATE discount_codes SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    await db.commit()
    return {"success": True, "id": discount_id}


@router.delete("/{discount_id}")
async def delete_discount(
    discount_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Delete a discount code (requires admin auth)."""
    _verify_admin(request)

    await db.execute("DELETE FROM discount_codes WHERE id = ?", (discount_id,))
    await db.commit()
    return {"success": True, "id": discount_id}


@router.get("/validate/{code}")
async def validate_discount(
    code: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Validate a discount code for the e-commerce site."""
    code = code.strip().upper()
    cursor = await db.execute(
        "SELECT * FROM discount_codes WHERE code = ? AND is_active = 1",
        (code,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()

    if not row:
        return {"valid": False, "message": "Invalid promo code"}

    discount = dict(zip(columns, row))

    # Check max uses
    if discount["max_uses"] > 0 and discount["times_used"] >= discount["max_uses"]:
        return {"valid": False, "message": "This code has been fully redeemed"}

    # Check expiration
    if discount["expires_at"]:
        from datetime import datetime
        try:
            expires = datetime.fromisoformat(discount["expires_at"])
            if datetime.now() > expires:
                return {"valid": False, "message": "This code has expired"}
        except Exception:
            pass

    return {
        "valid": True,
        "code": discount["code"],
        "discount_type": discount["discount_type"],
        "discount_value": discount["discount_value"],
        "description": discount["description"],
    }
