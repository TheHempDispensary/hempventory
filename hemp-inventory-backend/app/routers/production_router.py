"""Production planning & tracking.

Combines the two Google Sheets the shop used to keep by hand (a "what's coming
up" production plan and a "what's done" QA log) into one board, and auto-derives
the plan from Smart PAR: for any product flagged as *made in-house*, Smart PAR's
"order quantity" (needed − in stock) is exactly how much to produce.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import re
import aiosqlite

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient
from app.routers.inventory_router import smart_par, _do_sync

router = APIRouter(prefix="/api/production", tags=["production"])


async def _add_to_hq_inventory(sku: str, qty: float) -> dict:
    """Add `qty` units to a product's stock at HQ (Clover). Returns a result dict.

    Matches on Clover SKU first, then Clover item id (Smart PAR uses the raw SKU
    when present, otherwise the item id). Never lowers stock.
    """
    from app.routers.ecommerce_router import (
        HQ_MERCHANT_ID, HQ_API_TOKEN, invalidate_product_cache,
    )
    if not sku:
        return {"ok": False, "reason": "batch has no linked product/SKU"}
    if qty <= 0:
        return {"ok": False, "reason": "quantity must be greater than 0"}
    if not HQ_MERCHANT_ID or not HQ_API_TOKEN:
        return {"ok": False, "reason": "HQ Clover credentials not configured"}

    client = CloverClient(HQ_MERCHANT_ID, HQ_API_TOKEN)
    data = await client.get_items(expand="itemStock")
    match = None
    for it in data.get("elements", []):
        if (it.get("sku") or "") == sku or it.get("id") == sku:
            match = it
            break
    if not match:
        return {"ok": False, "reason": f"'{sku}' not found in HQ inventory"}

    stock = match.get("itemStock") or {}
    current = stock.get("quantity", 0) or 0
    new_q = current + qty
    await client.update_item_stock(match["id"], new_q)
    invalidate_product_cache()
    return {"ok": True, "item_id": match["id"], "previous": current, "new": new_q, "added": qty}

# Valid batch lifecycle stages.
_STATUSES = {"planned", "in_production", "ready", "done"}

# Item names copied from the two production Google Sheets, used only to
# pre-seed the "made in-house" flag against the live catalog. Matching is
# best-effort (fuzzy); the user reviews/adjusts afterward.
_SEED_NAMES: list[str] = [
    "After Hours CBD/CBG/CBN Lubricant",
    "After Hours Delta 9 THC Lubricant",
    "Apples and Bananas Pre roll 1 gram",
    "Baby Js pre roll OG Kush",
    "Baby Js pre roll banana runtz",
    "Banana Runtz Baby J",
    "Banana Runtz 1 Gram pre roll",
    "Banana Runtz Bigs flower",
    "Banana Runtz Bigs shake",
    "Banana Runtz Pre roll",
    "Banana Runtz THC Flower",
    "Blue dream snow caps flower",
    "CBD Coffee Syrup",
    "CBD rub",
    "CBD/CBG 15 Mg gummies",
    "CBD/CBG/CBN 150mg Gummies watermelon",
    "CBD/CBG/CBN 150mg Gummies Mango",
    "CBD/CBG/CBN 30mg Fruit Variety Gummies",
    "CBD/CBG/CBN tincture",
    "CBG 10mg gummy",
    "CBG Tincture",
    "CBG Chronic Fruit Gushers Gummies",
    "CBN Gummies",
    "Cherry Lemonade",
    "Cold Brew",
    "Cold Brew 2 oz",
    "Cold Brew 8 oz",
    "Cold Brew Liter",
    "Delta 8 100mg gummy",
    "Delta 8 50 mg variety gummy",
    "Delta 8 90mg gummy",
    "Delta 8 THC 90mg Gummies",
    "Delta 9 10 mg variety gummy",
    "Delta 9 5 mg variety gummy",
    "Euphoria Temptation Lubricant",
    "Forbidden Fruit wax 3 grams",
    "Galactic Gas Disp vape 1 Gram",
    "Galactic Gas Disp vape 2 Gram",
    "Grand daddy Purple wax 3 grams",
    "Grape Goji Vape disp",
    "Green Crack THC Flower",
    "Green Crack THC Baby J Pre Roll",
    "Green Crack THC Shake",
    "Green Crack THC Smalls",
    "Guava Flower Smalls",
    "Guava Flower Smalls shake",
    "Guava Snow caps",
    "ICC moonrock flower",
    "Ice Cream cookies moon rocks",
    "Ice Cream cookies moon rocks Pre roll",
    "Lemon Cherry Gelato THC Flower",
    "Lemon Octane",
    "Lemonade",
    "Lemonade 2 oz",
    "Lemonade 8 oz",
    "Lemonade 16 oz",
    "Lemonade 1 Liter",
    "Mango 120MG Delta 9/CBG",
    "Mango 150mg CBD/CBG/CBN gummy",
    "Nerds THC Flower",
    "Nerds THC Shake",
    "Nerds THC Smalls",
    "OG Kush Bigs Shake",
    "OG Kush Pre roll",
    "OG Kush Smalls Flower",
    "Rasp Kush Disp vape",
    "Rice crispy treats",
    "Rice Crispy Treat THC",
    "Skywalker THC Flower",
    "Skywalker THC Pre Roll",
    "Skywalker THC Shake",
    "Skywalker THC Smalls",
    "Sour Tangie disp vape",
    "Strawberry syrup",
    "THC Cartridges 9 pound Hammer Indica",
    "THC Cartridges Grand Daddy Purple Indica",
    "THC Cartridges Grapefruit Romulan Hybrid",
    "THC Cartridges Jack Herer Sativa",
    "THC Cartridges OG Kush Hybrid",
    "THC Cartridges Super Lemon Haze Sativa",
    "THC Chocolate Bar",
    "THC Chocolate Bar Blueberry",
    "THC Chocolate Bar Sea Salt",
    "THC Coffee Syrup",
    "Watermelon Disp vape 1 Gram",
    "Watermelon Disp vape 2 Gram",
]

# Tokens that carry no discriminating meaning when comparing product names.
_STOPWORDS = {
    "the", "and", "a", "of", "with", "for", "disp", "ct", "count", "pack",
    "oz", "mg", "gram", "grams", "g", "variety",
}


def _tokens(name: str) -> set[str]:
    """Significant word tokens of a product name for fuzzy matching."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return {t for t in cleaned.split() if t and t not in _STOPWORDS}


def _compact(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _is_match(seed_tokens: set[str], seed_compact: str, prod_name: str) -> bool:
    """True if a live product name confidently matches a seed phrase."""
    prod_tokens = _tokens(prod_name)
    if not prod_tokens or not seed_tokens:
        return False
    prod_compact = _compact(prod_name)
    # Strong signal: one compacted name contains the other.
    if len(seed_compact) >= 6 and (seed_compact in prod_compact or prod_compact in seed_compact):
        return True
    inter = seed_tokens & prod_tokens
    if len(inter) < 2:
        return False
    union = seed_tokens | prod_tokens
    return (len(inter) / len(union)) >= 0.55


# ── Made-in-house flags ──────────────────────────────────────────────────────

class FlagSet(BaseModel):
    made_in_house: bool
    product_name: Optional[str] = None


@router.get("/flags")
async def list_flags(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute(
        "SELECT sku, product_name FROM production_flags ORDER BY product_name"
    )
    rows = await cursor.fetchall()
    return {"flags": [{"sku": r[0], "product_name": r[1]} for r in rows]}


@router.put("/flags/{sku}")
async def set_flag(
    sku: str,
    body: FlagSet,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    if body.made_in_house:
        await db.execute(
            """INSERT INTO production_flags (sku, product_name)
               VALUES (?, ?)
               ON CONFLICT(sku) DO UPDATE SET product_name = excluded.product_name""",
            (sku, body.product_name or ""),
        )
    else:
        await db.execute("DELETE FROM production_flags WHERE sku = ?", (sku,))
    await db.commit()
    return {"sku": sku, "made_in_house": body.made_in_house}


@router.post("/seed-flags")
async def seed_flags(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Match live catalog products against the sheet item names and flag matches."""
    inv = await _do_sync(db)
    items_list = inv.get("items", [])

    seeds = [(_tokens(s), _compact(s)) for s in _SEED_NAMES]

    cursor = await db.execute("SELECT sku FROM production_flags")
    existing = {r[0] for r in await cursor.fetchall()}

    added: list[dict] = []
    already = 0
    for item in items_list:
        sku = item.get("sku") or ""
        name = item.get("name") or ""
        if not sku or not name:
            continue
        if (sku).upper().startswith("LF-"):  # LeafLife = supplier, not in-house
            continue
        matched = any(_is_match(st, sc, name) for st, sc in seeds)
        if not matched:
            continue
        if sku in existing:
            already += 1
            continue
        await db.execute(
            "INSERT OR IGNORE INTO production_flags (sku, product_name) VALUES (?, ?)",
            (sku, name),
        )
        existing.add(sku)
        added.append({"sku": sku, "product_name": name})
    await db.commit()
    return {
        "added": len(added),
        "already_flagged": already,
        "matched": sorted(added, key=lambda x: x["product_name"]),
    }


# ── Production plan (derived from Smart PAR) ─────────────────────────────────

@router.get("/plan")
async def production_plan(
    months: int = 3,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """What to produce, per in-house product, derived from Smart PAR.

    needed        = Smart PAR order qty (par − current stock)
    already_planned = open batch quantity not yet finished
    to_produce    = max(needed − already_planned, 0)
    """
    cursor = await db.execute("SELECT sku, product_name FROM production_flags")
    flag_rows = await cursor.fetchall()
    flags = {r[0]: r[1] for r in flag_rows}

    if not flags:
        return {"items": [], "meta": {"months": months, "flagged": 0}}

    par = await smart_par(months=months, user=user, db=db)
    par_by_sku = {p["sku"]: p for p in par.get("products", [])}

    # Open (not-yet-done) batch quantities already in the pipeline, per sku.
    cursor = await db.execute(
        """SELECT sku, COALESCE(SUM(planned_qty), 0)
           FROM production_batches
           WHERE status != 'done' AND sku IS NOT NULL AND sku != ''
           GROUP BY sku"""
    )
    planned_by_sku = {r[0]: r[1] for r in await cursor.fetchall()}

    items: list[dict] = []
    for sku, name in flags.items():
        p = par_by_sku.get(sku)
        needed = int(p["order_qty"]) if p else 0
        in_stock = p["total_stock"] if p else 0
        units_sold = p["units_sold"] if p else 0
        units_per_month = p["units_per_month"] if p else 0
        categories = p["categories"] if p else []
        already_planned = planned_by_sku.get(sku, 0)
        to_produce = max(needed - already_planned, 0)
        items.append({
            "sku": sku,
            "name": p["name"] if p else name,
            "categories": categories,
            "in_stock": in_stock,
            "units_sold": units_sold,
            "units_per_month": units_per_month,
            "needed": needed,
            "already_planned": already_planned,
            "to_produce": to_produce,
        })

    items.sort(key=lambda x: (-x["to_produce"], x["name"]))
    return {
        "items": items,
        "meta": {
            "months": months,
            "flagged": len(flags),
            "days_of_data": par.get("meta", {}).get("days_of_data"),
        },
    }


# ── Batch tracking ───────────────────────────────────────────────────────────

class BatchCreate(BaseModel):
    product_name: str
    sku: Optional[str] = None
    size: Optional[str] = None
    planned_qty: float = 0
    produced_qty: float = 0
    status: str = "planned"
    batch_no: Optional[str] = None
    expiration_date: Optional[str] = None
    made_by: Optional[str] = None
    qa_check: bool = False
    label_ordered: bool = False
    label_qty: Optional[int] = None
    notes: Optional[str] = None
    source: str = "manual"
    plan_date: Optional[str] = None
    add_to_inventory: Optional[bool] = None


class BatchUpdate(BaseModel):
    product_name: Optional[str] = None
    sku: Optional[str] = None
    size: Optional[str] = None
    planned_qty: Optional[float] = None
    produced_qty: Optional[float] = None
    status: Optional[str] = None
    batch_no: Optional[str] = None
    expiration_date: Optional[str] = None
    made_by: Optional[str] = None
    qa_check: Optional[bool] = None
    label_ordered: Optional[bool] = None
    label_qty: Optional[int] = None
    notes: Optional[str] = None
    plan_date: Optional[str] = None
    # When a batch first becomes 'done', its output is added to HQ Clover stock.
    # Set False to skip (e.g. moving stock rather than making new units).
    add_to_inventory: Optional[bool] = None


def _batch_row(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "sku": row["sku"],
        "product_name": row["product_name"],
        "size": row["size"],
        "planned_qty": row["planned_qty"],
        "produced_qty": row["produced_qty"],
        "status": row["status"],
        "batch_no": row["batch_no"],
        "expiration_date": row["expiration_date"],
        "made_by": row["made_by"],
        "qa_check": bool(row["qa_check"]),
        "label_ordered": bool(row["label_ordered"]),
        "label_qty": row["label_qty"],
        "notes": row["notes"],
        "source": row["source"],
        "plan_date": row["plan_date"],
        "completed_at": row["completed_at"],
        "inventoried": bool(row["inventoried"]),
        "inventoried_at": row["inventoried_at"],
        "inventoried_qty": row["inventoried_qty"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/batches")
async def list_batches(
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    if status:
        cursor = await db.execute(
            "SELECT * FROM production_batches WHERE status = ? ORDER BY updated_at DESC",
            (status,),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM production_batches ORDER BY updated_at DESC"
        )
    rows = await cursor.fetchall()
    return {"batches": [_batch_row(r) for r in rows]}


@router.post("/batches")
async def create_batch(
    body: BatchCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    if body.status not in _STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{body.status}'")
    completed_expr = "CURRENT_TIMESTAMP" if body.status == "done" else "NULL"
    cursor = await db.execute(
        f"""INSERT INTO production_batches
           (sku, product_name, size, planned_qty, produced_qty, status, batch_no,
            expiration_date, made_by, qa_check, label_ordered, label_qty, notes,
            source, plan_date, completed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {completed_expr})""",
        (
            body.sku, body.product_name, body.size, body.planned_qty, body.produced_qty,
            body.status, body.batch_no, body.expiration_date, body.made_by,
            int(body.qa_check), int(body.label_ordered), body.label_qty, body.notes,
            body.source, body.plan_date,
        ),
    )
    await db.commit()
    new_id = cursor.lastrowid
    cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (new_id,))
    row = await cursor.fetchone()

    inventory_result = None
    if body.status == "done" and body.add_to_inventory is not False and body.sku:
        qty = row["produced_qty"] or row["planned_qty"] or 0
        inventory_result = await _add_to_hq_inventory(body.sku, qty)
        if inventory_result.get("ok"):
            await db.execute(
                """UPDATE production_batches
                   SET inventoried = 1, inventoried_at = CURRENT_TIMESTAMP, inventoried_qty = ?
                   WHERE id = ?""",
                (qty, new_id),
            )
            await db.commit()
            cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (new_id,))
            row = await cursor.fetchone()

    result = _batch_row(row)
    if inventory_result is not None:
        result["inventory_result"] = inventory_result
    return result


@router.put("/batches/{batch_id}")
async def update_batch(
    batch_id: int,
    body: BatchUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (batch_id,))
    existing = await cursor.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Batch not found")

    fields: dict = {}
    for col in [
        "product_name", "sku", "size", "planned_qty", "produced_qty", "status",
        "batch_no", "expiration_date", "made_by", "label_qty", "notes", "plan_date",
    ]:
        val = getattr(body, col)
        if val is not None:
            fields[col] = val
    if body.qa_check is not None:
        fields["qa_check"] = int(body.qa_check)
    if body.label_ordered is not None:
        fields["label_ordered"] = int(body.label_ordered)

    if "status" in fields and fields["status"] not in _STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{fields['status']}'")

    set_parts = [f"{col} = ?" for col in fields]
    params = list(fields.values())
    set_parts.append("updated_at = CURRENT_TIMESTAMP")

    # Stamp completion time when transitioning into 'done'.
    if fields.get("status") == "done" and existing["status"] != "done":
        set_parts.append("completed_at = CURRENT_TIMESTAMP")
    elif "status" in fields and fields["status"] != "done":
        set_parts.append("completed_at = NULL")

    params.append(batch_id)
    await db.execute(
        f"UPDATE production_batches SET {', '.join(set_parts)} WHERE id = ?",
        params,
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (batch_id,))
    row = await cursor.fetchone()

    # On first transition into 'done', add the produced output to HQ stock
    # (unless explicitly skipped or already inventoried).
    inventory_result = None
    became_done = fields.get("status") == "done" and existing["status"] != "done"
    if became_done and body.add_to_inventory is not False and not row["inventoried"] and row["sku"]:
        qty = row["produced_qty"] or row["planned_qty"] or 0
        inventory_result = await _add_to_hq_inventory(row["sku"], qty)
        if inventory_result.get("ok"):
            await db.execute(
                """UPDATE production_batches
                   SET inventoried = 1, inventoried_at = CURRENT_TIMESTAMP, inventoried_qty = ?
                   WHERE id = ?""",
                (qty, batch_id),
            )
            await db.commit()
            cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (batch_id,))
            row = await cursor.fetchone()

    result = _batch_row(row)
    if inventory_result is not None:
        result["inventory_result"] = inventory_result
    return result


@router.post("/batches/{batch_id}/add-to-inventory")
async def add_batch_to_inventory(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Manually push a batch's produced qty into HQ stock (retry / ad-hoc)."""
    cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (batch_id,))
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Batch not found")
    if row["inventoried"]:
        raise HTTPException(status_code=400, detail="Batch already added to inventory")
    qty = row["produced_qty"] or row["planned_qty"] or 0
    inv = await _add_to_hq_inventory(row["sku"] or "", qty)
    if not inv.get("ok"):
        raise HTTPException(status_code=400, detail=inv.get("reason", "Could not add to inventory"))
    await db.execute(
        """UPDATE production_batches
           SET inventoried = 1, inventoried_at = CURRENT_TIMESTAMP, inventoried_qty = ?
           WHERE id = ?""",
        (qty, batch_id),
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM production_batches WHERE id = ?", (batch_id,))
    result = _batch_row(await cursor.fetchone())
    result["inventory_result"] = inv
    return result


@router.delete("/batches/{batch_id}")
async def delete_batch(
    batch_id: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM production_batches WHERE id = ?", (batch_id,))
    await db.commit()
    return {"deleted": batch_id}
