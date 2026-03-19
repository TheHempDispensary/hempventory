from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import aiosqlite

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/locations", tags=["locations"])


class LocationCreate(BaseModel):
    name: str
    merchant_id: str
    api_token: str
    is_virtual: bool = False


class LocationUpdate(BaseModel):
    name: Optional[str] = None
    merchant_id: Optional[str] = None
    api_token: Optional[str] = None


class LocationResponse(BaseModel):
    id: int
    name: str
    merchant_id: str
    created_at: str


@router.get("/")
async def list_locations(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("SELECT id, name, merchant_id, created_at FROM locations ORDER BY name")
    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "merchant_id": row[2],
            "created_at": row[3],
        }
        for row in rows
    ]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_location(
    location: LocationCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    clover_name = ""
    if location.is_virtual:
        # Virtual/placeholder location — skip Clover validation
        if not location.merchant_id:
            location.merchant_id = f"virtual-{location.name.lower().replace(' ', '-')}"
        if not location.api_token:
            location.api_token = "pending"
    else:
        # Validate the token works
        try:
            client = CloverClient(location.merchant_id, location.api_token)
            info = await client.get_merchant_info()
            clover_name = info.get("name", "")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not connect to Clover with the provided credentials. Check merchant_id and api_token.",
            )

    try:
        await db.execute(
            "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)",
            (location.name, location.merchant_id, location.api_token),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Location with merchant_id {location.merchant_id} already exists",
        )

    cursor = await db.execute(
        "SELECT id, name, merchant_id, created_at FROM locations WHERE merchant_id = ?",
        (location.merchant_id,),
    )
    row = await cursor.fetchone()
    return {
        "id": row[0],
        "name": row[1],
        "merchant_id": row[2],
        "created_at": row[3],
        "clover_name": clover_name,
    }


@router.put("/{location_id}")
async def update_location(
    location_id: int,
    location: LocationUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("SELECT id FROM locations WHERE id = ?", (location_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Location not found")

    if location.name:
        await db.execute("UPDATE locations SET name = ? WHERE id = ?", (location.name, location_id))
    if location.merchant_id:
        await db.execute("UPDATE locations SET merchant_id = ? WHERE id = ?", (location.merchant_id, location_id))
    if location.api_token:
        await db.execute("UPDATE locations SET api_token = ? WHERE id = ?", (location.api_token, location_id))
    await db.commit()
    return {"message": "Location updated"}


@router.delete("/{location_id}")
async def delete_location(
    location_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("SELECT id FROM locations WHERE id = ?", (location_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Location not found")

    await db.execute("DELETE FROM par_levels WHERE location_id = ?", (location_id,))
    await db.execute("DELETE FROM alert_history WHERE location_id = ?", (location_id,))
    await db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    await db.commit()
    return {"message": "Location deleted"}
