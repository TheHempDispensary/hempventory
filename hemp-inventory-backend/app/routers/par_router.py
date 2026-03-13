from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import aiosqlite

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/par", tags=["par"])


class ParLevelSet(BaseModel):
    par_level: float


class BulkParLevel(BaseModel):
    sku: str
    location_id: int
    par_level: float


@router.get("/")
async def list_par_levels(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("""
        SELECT p.id, p.sku, p.location_id, p.par_level, p.updated_at, l.name as location_name
        FROM par_levels p
        JOIN locations l ON p.location_id = l.id
        ORDER BY p.sku, l.name
    """)
    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "sku": row[1],
            "location_id": row[2],
            "par_level": row[3],
            "updated_at": row[4],
            "location_name": row[5],
        }
        for row in rows
    ]


@router.put("/{sku}/{location_id}")
async def set_par_level(
    sku: str,
    location_id: int,
    par: ParLevelSet,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    # Validate location exists
    cursor = await db.execute("SELECT id FROM locations WHERE id = ?", (location_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Location not found")

    await db.execute(
        """INSERT INTO par_levels (sku, location_id, par_level, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(sku, location_id)
           DO UPDATE SET par_level = ?, updated_at = CURRENT_TIMESTAMP""",
        (sku, location_id, par.par_level, par.par_level),
    )
    await db.commit()
    return {"message": "PAR level set", "sku": sku, "location_id": location_id, "par_level": par.par_level}


@router.post("/bulk")
async def set_bulk_par_levels(
    levels: list[BulkParLevel],
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    for level in levels:
        await db.execute(
            """INSERT INTO par_levels (sku, location_id, par_level, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(sku, location_id)
               DO UPDATE SET par_level = ?, updated_at = CURRENT_TIMESTAMP""",
            (level.sku, level.location_id, level.par_level, level.par_level),
        )
    await db.commit()
    return {"message": f"Set {len(levels)} PAR levels"}


@router.delete("/{sku}/{location_id}")
async def delete_par_level(
    sku: str,
    location_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute(
        "DELETE FROM par_levels WHERE sku = ? AND location_id = ?",
        (sku, location_id),
    )
    await db.commit()
    return {"message": "PAR level removed"}


@router.get("/alerts")
async def get_par_alerts(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Check current inventory against PAR levels and return alerts."""
    # Get all locations
    cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await cursor.fetchall()

    # Get all PAR levels
    cursor = await db.execute("SELECT sku, location_id, par_level FROM par_levels")
    par_rows = await cursor.fetchall()
    par_map: dict[tuple[str, int], float] = {(row[0], row[1]): row[2] for row in par_rows}

    if not par_map:
        return {"alerts": [], "message": "No PAR levels configured"}

    alerts = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            items = data.get("elements", [])
        except Exception as e:
            print(f"Error checking PAR for {loc_name}: {e}")
            continue

        for item in items:
            sku = item.get("sku", "") or item.get("id", "")
            par_level = par_map.get((sku, loc_id))
            if par_level is None:
                continue

            item_stock = item.get("itemStock", {})
            quantity = item_stock.get("quantity", 0) if item_stock else 0

            if quantity <= par_level:
                deficit = par_level - quantity
                alerts.append({
                    "sku": sku,
                    "product_name": item.get("name", ""),
                    "location": loc_name,
                    "location_id": loc_id,
                    "current_stock": quantity,
                    "par_level": par_level,
                    "deficit": deficit,
                    "recommendation": f"Send {int(deficit + par_level * 0.5)} units from HQ to {loc_name}",
                })

    alerts.sort(key=lambda a: a["deficit"], reverse=True)
    return {"alerts": alerts, "total": len(alerts)}
