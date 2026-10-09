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


def _restock_units(par_level: float, quantity: float) -> int:
    return int(par_level - quantity + par_level * 0.5)


def _recommendation(loc_name: str, is_hq: bool, restock: int, hq_stock: float) -> str:
    if is_hq:
        return f"Produce or reorder {restock} units for HQ"
    if hq_stock <= 0:
        return f"HQ is out: produce or reorder {restock} units for {loc_name}"
    if hq_stock < restock:
        have = int(hq_stock)
        return f"Send {have} units from HQ to {loc_name} (all HQ has), then produce or reorder {restock - have} more"
    return f"Send {restock} units from HQ to {loc_name}"


async def build_par_alerts(db: aiosqlite.Connection) -> list[dict]:
    """Items at or below PAR at each location, biggest monthly sales first.

    Store alerts only suggest sending from HQ what HQ actually holds; the rest
    (and every HQ alert) is a produce/reorder. Sales come from Smart PAR's
    cached history, so this never triggers a Clover order pull.
    """
    from app.routers import inventory_router as inv
    from app.routers.ecommerce_router import HQ_MERCHANT_ID

    cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    locations = await cursor.fetchall()

    cursor = await db.execute("SELECT sku, location_id, par_level FROM par_levels")
    par_map: dict[tuple[str, int], float] = {
        (row[0], row[1]): row[2] for row in await cursor.fetchall()
    }
    if not par_map:
        return []

    loaded = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            data = await CloverClient(merchant_id, api_token).get_items()
        except Exception as e:
            print(f"Error checking PAR for {loc_name}: {e}")
            continue
        is_hq = inv._is_hq(loc_name) or str(merchant_id) == str(HQ_MERCHANT_ID)
        loaded.append((loc_id, loc_name, is_hq, data.get("elements", [])))

    def _qty(item: dict) -> float:
        return (item.get("itemStock") or {}).get("quantity") or 0

    hq_stock: dict[str, float] = {}
    for _, _, is_hq, items in loaded:
        if is_hq:
            for item in items:
                sku = item.get("sku", "") or item.get("id", "")
                hq_stock[sku] = hq_stock.get(sku, 0) + _qty(item)

    cached = inv._usable_sales_cache()
    tally = inv._SalesTally.from_cache(cached) if cached else None
    days_of_data = inv._days_of_data(tally.earliest_ts, tally.latest_ts) if tally else 1.0

    alerts = []
    for loc_id, loc_name, is_hq, items in loaded:
        for item in items:
            sku = item.get("sku", "") or item.get("id", "")
            par_level = par_map.get((sku, loc_id))
            if par_level is None:
                continue
            quantity = _qty(item)
            if quantity > par_level:
                continue

            units_per_month = 0.0
            if tally:
                units, first_ts = tally.product_sales(item.get("name", ""), [item.get("id")])
                days = inv._product_days_of_data(first_ts, tally.latest_ts, days_of_data)
                units_per_month = units / days * 30.44
            price = (item.get("price") or 0) / 100
            restock = _restock_units(par_level, quantity)
            hq_qty = hq_stock.get(sku, 0)
            alerts.append({
                "sku": sku,
                "product_name": item.get("name", ""),
                "location": loc_name,
                "location_name": loc_name,
                "location_id": loc_id,
                "current_stock": quantity,
                "par_level": par_level,
                "deficit": par_level - quantity,
                "hq_stock": None if is_hq else hq_qty,
                "restock_qty": restock,
                "units_per_month": round(units_per_month, 1),
                "monthly_sales": round(units_per_month * price, 2),
                "recommendation": _recommendation(loc_name, is_hq, restock, hq_qty),
            })

    alerts.sort(key=lambda a: (a["monthly_sales"], a["deficit"]), reverse=True)
    return alerts


@router.get("/alerts")
async def get_par_alerts(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Check current inventory against PAR levels and return alerts."""
    cursor = await db.execute("SELECT COUNT(*) FROM par_levels")
    if not (await cursor.fetchone())[0]:
        return {"alerts": [], "message": "No PAR levels configured"}
    alerts = await build_par_alerts(db)
    return {"alerts": alerts, "total": len(alerts)}
