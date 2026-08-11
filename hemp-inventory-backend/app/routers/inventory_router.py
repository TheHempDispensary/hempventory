from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
import asyncio
import aiosqlite
import base64
import csv
from collections import deque
import os
import json
import io
import itertools
import time
import re
import uuid
import httpx
from urllib.parse import quote
from PIL import Image as PILImage

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient
from app.routers.ecommerce_router import invalidate_product_cache

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# In-memory cache for inventory data
_inventory_cache: dict = {"items": [], "locations": [], "updated_at": 0}
_cache_lock = asyncio.Lock()

# In-memory cache for processed images (nobg + resize results)
# Key: (sku, w, nobg) -> (image_bytes, media_type)
_image_cache: dict[tuple[str, int | None, int | None], tuple[bytes, str]] = {}
_IMAGE_CACHE_MAX = 500


async def _invalidate_cache():
    """Clear inventory cache so next /cached call triggers a fresh sync."""
    async with _cache_lock:
        _inventory_cache["updated_at"] = 0


async def _remove_from_cache(skus: list):
    """Remove items with given SKUs directly from cache (no re-sync needed)."""
    async with _cache_lock:
        if _inventory_cache["items"]:
            sku_set = set(skus)
            _inventory_cache["items"] = [
                item for item in _inventory_cache["items"]
                if item["sku"] not in sku_set
            ]
            _inventory_cache["updated_at"] = time.time()


async def _remove_from_cache_by_name(name: str):
    """Remove items matching an exact name from cache (for no-SKU items merged by name)."""
    async with _cache_lock:
        if _inventory_cache["items"]:
            _inventory_cache["items"] = [
                item for item in _inventory_cache["items"]
                if item.get("name") != name
            ]
            _inventory_cache["updated_at"] = time.time()


class LocationStockInput(BaseModel):
    location_id: int
    quantity: float


class ItemCreate(BaseModel):
    name: str
    price: int  # in cents
    sku: Optional[str] = None
    category: Optional[str] = None
    initial_stock: Optional[float] = 0
    locations: Optional[list[int]] = None  # location IDs to push to; None = all
    stock_per_location: Optional[list[LocationStockInput]] = None
    par_per_location: Optional[list[dict]] = None  # [{location_id, par_level}]
    # New Clover fields
    price_type: Optional[str] = "FIXED"  # FIXED, VARIABLE, PER_UNIT
    cost: Optional[int] = None  # item cost in cents
    product_code: Optional[str] = None  # itemCode in Clover
    alternate_name: Optional[str] = None  # online name
    description: Optional[str] = None  # item description for online
    color_code: Optional[str] = None  # hex color code
    is_revenue: Optional[bool] = True
    is_age_restricted: Optional[bool] = False
    age_restriction_type: Optional[str] = None  # e.g. "Vitamin & Supplements", "Tobacco"
    age_restriction_min_age: Optional[int] = None  # e.g. 21
    available: Optional[bool] = True
    hidden: Optional[bool] = False  # hidden from POS
    auto_manage: Optional[bool] = False  # disabled by default – Clover auto-hides items at 0 stock, blocking POS scanning
    default_tax_rates: Optional[bool] = True


class StockUpdate(BaseModel):
    location_id: int
    quantity: float


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[int] = None
    sku: Optional[str] = None
    stock_updates: Optional[list[StockUpdate]] = None
    # Extended Clover fields
    price_type: Optional[str] = None  # FIXED, VARIABLE, PER_UNIT
    cost: Optional[int] = None  # item cost in cents
    product_code: Optional[str] = None  # itemCode in Clover
    alternate_name: Optional[str] = None  # online name
    description: Optional[str] = None
    color_code: Optional[str] = None
    is_revenue: Optional[bool] = None
    is_age_restricted: Optional[bool] = None
    age_restriction_type: Optional[str] = None
    age_restriction_min_age: Optional[int] = None
    available: Optional[bool] = None
    hidden: Optional[bool] = None
    auto_manage: Optional[bool] = None
    default_tax_rates: Optional[bool] = None
    # Product attributes for ecommerce (stored locally, not in Clover)
    effect: Optional[str] = None  # Relax, Sleep, Energy, Focus
    strength: Optional[str] = None  # High, Medium, Low
    product_type: Optional[str] = None  # Hybrid, Indica, Sativa


async def _get_locations(db: aiosqlite.Connection, location_ids: Optional[list[int]] = None):
    if location_ids:
        placeholders = ",".join("?" for _ in location_ids)
        cursor = await db.execute(
            f"SELECT id, name, merchant_id, api_token FROM locations WHERE id IN ({placeholders})",
            location_ids,
        )
    else:
        cursor = await db.execute("SELECT id, name, merchant_id, api_token FROM locations")
    return await cursor.fetchall()


async def _get_par_levels(db: aiosqlite.Connection) -> dict:
    """Returns dict of (sku, location_id) -> par_level."""
    cursor = await db.execute("SELECT sku, location_id, par_level FROM par_levels")
    rows = await cursor.fetchall()
    return {(row[0], row[1]): row[2] for row in rows}


_BATCH_SUFFIX_RE = re.compile(r"\s+batch\s+[\w-]+\s*$", re.IGNORECASE)


def _strip_batch_suffix(name: str) -> str:
    """Drop a trailing 'BATCH <code>' from a product name.

    Production/staff sometimes append a lot code (e.g. "... PINEAPPLE EXPRESS
    BATCH 01182515") to the Clover item at one store but not another, so the
    same product (same SKU) would otherwise split into duplicate rows. Removing
    the batch suffix lets those merge back into a single per-location row.
    Applied repeatedly in case more than one suffix was appended.
    """
    prev = None
    out = name
    while out != prev:
        prev = out
        out = _BATCH_SUFFIX_RE.sub("", out).strip()
    return out or name


async def _do_sync(db: aiosqlite.Connection) -> dict:
    """Core sync logic: pull latest inventory from all Clover locations."""
    locations = await _get_locations(db)
    if not locations:
        return {"items": [], "locations": []}

    par_levels = await _get_par_levels(db)

    # Build a unified inventory keyed by composite key
    inventory: dict[str, dict] = {}
    location_list = []

    # Pre-fetch item groups from first location to build clover_item_id -> group name map
    item_id_to_group_name: dict[str, str] = {}
    for loc in locations:
        try:
            client = CloverClient(loc[2], loc[3])
            groups_data = await client.get_item_groups()
            for group in groups_data.get("elements", []):
                group_name = group.get("name", "")
                for gi in (group.get("items", {}) or {}).get("elements", []):
                    item_id_to_group_name[gi.get("id", "")] = group_name
        except Exception as e:
            print(f"Error fetching item groups for {loc[1]}: {e}")
        break  # Only need groups from one location (names are the same across locations)

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        location_list.append({"id": loc_id, "name": loc_name, "merchant_id": merchant_id})

        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items(expand="itemStock,categories,ageRestricted")
            items = data.get("elements", [])
        except Exception as e:
            print(f"Error syncing {loc_name}: {e}")
            continue

        for item in items:
            raw_sku = item.get("sku", "") or ""
            clover_id = item.get("id", "")
            item_name = " ".join((item.get("name", "") or "").split())  # normalize whitespace
            # Ignore a per-store "BATCH <code>" lot suffix so the same product
            # (identified by SKU + base name) merges into ONE row even when one
            # location appended a batch number to its name and another didn't.
            base_name = _strip_batch_suffix(item_name)
            # Case-fold the name in the merge key so the same product still
            # merges when locations differ only by capitalization (e.g.
            # "... BLUE DREAM Sativa 28 GRAMS" vs "... BLUE DREAM SATIVA 28 GRAMS").
            name_key = base_name.upper()
            display_sku = raw_sku or clover_id
            # When an item has no user-assigned SKU, merge by name so that
            # the same product (e.g. item-group variants) created across
            # multiple locations shows as ONE row with stock at each location
            # instead of separate rows per location.
            if raw_sku:
                merge_key = f"{raw_sku}::{name_key}"
            else:
                merge_key = f"name::{name_key}"

            item_stock = item.get("itemStock", {})
            quantity = item_stock.get("quantity", 0) if item_stock else 0

            categories = item.get("categories", {}).get("elements", [])
            category_names = [c.get("name", "") for c in categories]

            # Remap apparel items (hoodies, t-shirts, shirts) to "Apparel" category
            name_lower = item_name.lower()
            if re.search(r'\b(hoodie|t-shirt|shirt|tee|jersey|hat|beanie)\b', name_lower):
                category_names = [c if c != "Accessories" else "Apparel" for c in category_names]
                if not category_names:
                    category_names = ["Apparel"]

            par = par_levels.get((display_sku, loc_id), None)

            if merge_key not in inventory:
                inventory[merge_key] = {
                    "sku": display_sku,
                    "name": base_name,
                    "price": item.get("price", 0),
                    "categories": category_names,
                    "locations": {},
                    "clover_ids": {},
                    "price_type": item.get("priceType", "FIXED"),
                    "cost": item.get("cost", 0),
                    "product_code": item.get("code", ""),
                    "alternate_name": item.get("alternateName", ""),
                    "description": item.get("description", ""),
                    "color_code": item.get("colorCode", ""),
                    "is_revenue": item.get("isRevenue", True),
                    "is_age_restricted": item.get("isAgeRestricted", False),
                    "age_restriction_type": (item.get("ageRestrictedObj") or {}).get("name", ""),
                    "age_restriction_min_age": (item.get("ageRestrictedObj") or {}).get("minimumAge", 21),
                    "available": item.get("available", True),
                    "hidden": item.get("hidden", False),
                    "auto_manage": item.get("autoManage", False),
                    "default_tax_rates": item.get("defaultTaxRates", True),
                    "item_group_name": item_id_to_group_name.get(clover_id, ""),
                    "modified_time": item.get("modifiedTime", 0),
                }

            existing_loc = inventory[merge_key]["locations"].get(loc_name)
            if existing_loc:
                # Same product already recorded at this location under a
                # different name (e.g. a separate batch item) — add the stock
                # together instead of overwriting so the total is correct.
                quantity = (existing_loc.get("stock") or 0) + quantity
                if par is None:
                    par = existing_loc.get("par_level")
            inventory[merge_key]["locations"][loc_name] = {
                "location_id": loc_id,
                "stock": quantity,
                "par_level": par,
                "status": _stock_status(quantity, par),
                "clover_item_id": existing_loc.get("clover_item_id") if existing_loc else clover_id,
            }
            if loc_name not in inventory[merge_key]["clover_ids"]:
                inventory[merge_key]["clover_ids"][loc_name] = clover_id

    # Ensure LeafLife / HQ-only products appear at ALL locations with 0 stock
    # (LeafLife items only exist on HQ Clover, so East/West show "—" without this)
    all_loc_names = [loc[1] for loc in locations]
    for _key, item_data in inventory.items():
        sku = item_data.get("sku", "")
        # LeafLife products (SKU starts with LF-) should show at every location
        if isinstance(sku, str) and sku.startswith("LF-"):
            for loc in locations:
                loc_name = loc[1]
                loc_id = loc[0]
                if loc_name not in item_data["locations"]:
                    item_data["locations"][loc_name] = {
                        "location_id": loc_id,
                        "stock": 0,
                        "par_level": None,
                        "status": "out_of_stock",
                        "clover_item_id": "",
                    }

    # Attach stored product images (only fetch SKU, not the heavy image_data blob)
    cursor = await db.execute("SELECT sku FROM product_images")
    image_rows = await cursor.fetchall()
    image_map = {row[0] for row in image_rows}
    for _key, item_data in inventory.items():
        if item_data["sku"] in image_map:
            item_data["has_image"] = True
        else:
            item_data["has_image"] = False

    # Add a unique id to each item for frontend selection
    for key, item_data in inventory.items():
        item_data["id"] = key  # composite key "sku::name"

    # Mark items that are hidden from inventory view
    cursor = await db.execute("SELECT sku FROM hidden_items")
    hidden_skus = {row[0] for row in await cursor.fetchall()}
    for _key, item_data in inventory.items():
        item_data["is_hidden"] = item_data["sku"] in hidden_skus

    items_list = sorted(inventory.values(), key=lambda x: x["name"])
    result = {"items": items_list, "locations": location_list}

    # Track inventory changes: compare new data against persistent DB snapshot
    # (survives server restarts unlike the in-memory cache)
    cursor = await db.execute("SELECT sku, location_name, stock FROM inventory_snapshots")
    snapshot_rows = await cursor.fetchall()
    old_snapshot: dict[tuple[str, str], float] = {
        (row[0], row[1]): row[2] for row in snapshot_rows
    }

    changes = []
    snapshot_upserts = []
    for ni in items_list:
        sku = ni.get("sku", "")
        name = ni.get("name", "")
        for loc_name, loc_data in ni.get("locations", {}).items():
            new_stock = loc_data.get("stock", 0)
            old_stock = old_snapshot.get((sku, loc_name))
            # Record change if we have a previous snapshot and stock differs
            if old_stock is not None and new_stock != old_stock:
                changes.append((
                    sku,
                    name,
                    loc_name,
                    old_stock,
                    new_stock,
                    new_stock - old_stock,
                    "sync",
                ))
            # Always upsert the current stock into snapshot
            snapshot_upserts.append((sku, loc_name, new_stock, name))

    if changes:
        await db.executemany(
            """INSERT INTO inventory_changes
               (sku, product_name, location_name, old_stock, new_stock, change_amount, change_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            changes,
        )

    if snapshot_upserts:
        await db.executemany(
            """INSERT INTO inventory_snapshots (sku, location_name, stock, product_name, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(sku, location_name)
               DO UPDATE SET stock = excluded.stock,
                             product_name = excluded.product_name,
                             updated_at = CURRENT_TIMESTAMP""",
            snapshot_upserts,
        )

    await db.commit()

    # Auto-delete LeafLife products (SKU starts with LF-) that have 0 stock
    # across ALL locations. These are supplier-shipped items that should be
    # removed from inventory once depleted.
    leaflife_to_delete: list[str] = []
    for key, item_data in list(inventory.items()):
        sku = item_data.get("sku", "")
        if not (isinstance(sku, str) and sku.startswith("LF-")):
            continue
        total_stock = sum(
            loc_data.get("stock", 0)
            for loc_data in item_data.get("locations", {}).values()
        )
        if total_stock <= 0:
            leaflife_to_delete.append(key)

    if leaflife_to_delete:
        for key in leaflife_to_delete:
            item_data = inventory.pop(key)
            sku = item_data.get("sku", "")
            clover_ids = item_data.get("clover_ids", {})
            # Delete from each Clover location where it exists
            for loc in locations:
                loc_name = loc[1]
                merchant_id, api_token = loc[2], loc[3]
                clover_id = clover_ids.get(loc_name, "")
                if clover_id:
                    try:
                        client = CloverClient(merchant_id, api_token)
                        await client.delete_item(clover_id)
                    except Exception as e:
                        print(f"[leaflife-cleanup] Failed to delete {sku} from {loc_name}: {e}")
            # Clean up DB records
            await db.execute(
                "DELETE FROM par_levels WHERE sku = ?", (sku,)
            )
            await db.execute(
                "DELETE FROM inventory_snapshots WHERE sku = ?", (sku,)
            )
            print(f"[leaflife-cleanup] Deleted {sku} ({item_data.get('name', '')}) - 0 stock")
        await db.commit()
        # Rebuild items_list after removals
        items_list = sorted(inventory.values(), key=lambda x: x["name"])
        result = {"items": items_list, "locations": location_list}

    # Update cache – but never overwrite a good cache with empty data
    # (protects against temporary Clover API failures wiping the cache)
    async with _cache_lock:
        if items_list or not _inventory_cache["items"]:
            _inventory_cache["items"] = result["items"]
            _inventory_cache["locations"] = result["locations"]
            _inventory_cache["updated_at"] = time.time()
        else:
            print("[sync] Skipping cache update: sync returned 0 items but cache has data")

    return result


@router.get("/sync")
async def sync_inventory(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Pull latest inventory from all Clover locations (full sync)."""
    result = await _do_sync(db)
    invalidate_product_cache()
    return result


@router.get("/cached")
async def get_cached_inventory(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return cached inventory if available, otherwise do a full sync."""
    async with _cache_lock:
        if _inventory_cache["updated_at"] > 0 and _inventory_cache["items"]:
            return {
                "items": _inventory_cache["items"],
                "locations": _inventory_cache["locations"],
                "cached": True,
                "updated_at": _inventory_cache["updated_at"],
            }
    # No cache yet, do a full sync
    result = await _do_sync(db)
    result["cached"] = False
    result["updated_at"] = _inventory_cache["updated_at"]
    return result


def _stock_status(stock: float, par: Optional[float]) -> str:
    if par is None:
        return "no_par"
    if stock <= 0:
        return "out_of_stock"
    if stock <= par:
        return "below_par"
    if stock <= par * 1.5:
        return "low"
    return "ok"


@router.post("/items")
async def create_item(
    item: ItemCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create an item and push it to specified (or all) locations."""
    locations = await _get_locations(db, item.locations)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    # Build per-location stock map
    stock_map: dict[int, float] = {}
    if item.stock_per_location:
        for sl in item.stock_per_location:
            stock_map[sl.location_id] = sl.quantity

    # Build per-location PAR map
    par_map: dict[int, float] = {}
    if item.par_per_location:
        for pl in item.par_per_location:
            par_map[pl["location_id"]] = pl["par_level"]

    results = []
    item_data: dict = {"name": item.name, "price": item.price}
    if item.sku:
        item_data["sku"] = item.sku
    if item.price_type:
        item_data["priceType"] = item.price_type
    if item.cost is not None:
        item_data["cost"] = item.cost
    if item.product_code:
        item_data["code"] = item.product_code
    if item.alternate_name:
        item_data["alternateName"] = item.alternate_name
    if item.description:
        item_data["description"] = item.description
    if item.color_code:
        item_data["colorCode"] = item.color_code
    # Always send these boolean fields explicitly
    item_data["isRevenue"] = item.is_revenue
    item_data["hidden"] = item.hidden
    item_data["autoManage"] = item.auto_manage
    item_data["available"] = item.available
    item_data["defaultTaxRates"] = item.default_tax_rates
    # Age restriction: Clover requires ageRestrictedObj with id, name, minimumAge
    if item.is_age_restricted and item.age_restriction_type:
        item_data["isAgeRestricted"] = True
    else:
        item_data["isAgeRestricted"] = False

    first_created_sku = None
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # If age restricted, look up or create the ageRestrictedObj for this merchant
            loc_item_data = dict(item_data)
            if item.is_age_restricted and item.age_restriction_type:
                age_obj = await _get_age_restriction_obj(
                    client, item.age_restriction_type,
                    item.age_restriction_min_age or 21
                )
                if age_obj:
                    loc_item_data["ageRestrictedObj"] = age_obj
                else:
                    # Can't find age restriction obj, skip the flag
                    loc_item_data["isAgeRestricted"] = False

            created = await client.create_item(loc_item_data)
            clover_id = created.get("id", "")
            # Track the first created SKU/ID for image storage
            if not first_created_sku:
                first_created_sku = item.sku or clover_id

            # Set stock: per-location amount takes priority, then initial_stock fallback
            loc_stock = stock_map.get(loc_id, item.initial_stock or 0)
            if loc_stock > 0:
                await client.update_item_stock(clover_id, loc_stock)

            # Assign category if provided
            if item.category:
                try:
                    cats = await client.get_categories()
                    existing = [c for c in cats.get("elements", []) if c.get("name") == item.category]
                    if existing:
                        cat_id = existing[0]["id"]
                    else:
                        new_cat = await client.create_category(item.category)
                        cat_id = new_cat["id"]
                    await client.assign_category(clover_id, cat_id)
                except Exception as cat_err:
                    print(f"Error assigning category at {loc_name}: {cat_err}")

            # Save PAR level if provided
            sku_for_par = item.sku or clover_id
            if loc_id in par_map and par_map[loc_id] > 0:
                await db.execute(
                    "INSERT OR REPLACE INTO par_levels (sku, location_id, par_level) VALUES (?, ?, ?)",
                    (sku_for_par, loc_id, par_map[loc_id]),
                )
                await db.commit()

            result_entry: dict = {
                "location": loc_name,
                "clover_id": clover_id,
                "status": "created",
            }
            results.append(result_entry)
        except httpx.HTTPStatusError as e:
            # Capture the actual Clover error response body
            error_detail = str(e)
            try:
                error_body = e.response.json()
                error_detail = error_body.get("message", str(e))
            except Exception:
                pass
            results.append({
                "location": loc_name,
                "status": "error",
                "error": error_detail,
            })
        except Exception as e:
            results.append({
                "location": loc_name,
                "status": "error",
                "error": str(e),
            })

    await _invalidate_cache()
    return {"results": results, "sku": first_created_sku}


# Clover fixed age restriction type IDs (these are universal across all merchants)
AGE_RESTRICTION_TYPE_IDS = {
    "Alcohol": "K2PM5DPQGBQEJ",
    "Tobacco": "DHXH8XT6CHZKA",
    "OTC drugs": "KH9G35W3YZ5YE",
    "Vitamin & Supplements": "4GJEQRKG7X370",
}


async def _get_age_restriction_obj(client: CloverClient, restriction_type: str, min_age: int) -> Optional[dict]:
    """Build the ageRestrictedObj using Clover's fixed type IDs."""
    type_id = AGE_RESTRICTION_TYPE_IDS.get(restriction_type)
    if type_id:
        return {"id": type_id, "name": restriction_type, "minimumAge": min_age}
    # Fallback: try to look up from merchant's existing items
    try:
        data = await client.get_items(expand="ageRestricted")
        for item in data.get("elements", []):
            obj = item.get("ageRestrictedObj")
            if obj and obj.get("name") == restriction_type:
                return {"id": obj["id"], "name": obj["name"], "minimumAge": min_age}
    except Exception as e:
        print(f"Error looking up age restriction: {e}")
    return None


@router.get("/age-restriction-types")
async def get_age_restriction_types(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get available age restriction types. Uses Clover's fixed type IDs."""
    return {
        "types": [
            {"id": type_id, "name": name, "minimumAge": 21}
            for name, type_id in AGE_RESTRICTION_TYPE_IDS.items()
        ]
    }


class BulkAutoManageRequest(BaseModel):
    enable: bool = True  # True to enable, False to disable
    skus: Optional[list[str]] = None  # None = all items


@router.post("/bulk-auto-manage")
async def bulk_auto_manage(
    req: BulkAutoManageRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Enable or disable autoManage on all (or selected) items across all locations.
    WARNING: Enabling autoManage causes Clover to auto-hide items when stock=0,
    which blocks POS scanning. When enabling, we also force available=true and hidden=false
    to mitigate, but items may become unscannable again as stock depletes.
    Consider using fix-pos endpoint instead to ensure all items stay scannable."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    results = []
    total_updated = 0
    total_failed = 0

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        loc_updated = 0
        loc_failed = 0
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            items = data.get("elements", [])

            for item in items:
                item_sku = item.get("sku") or item.get("id", "")
                # If specific SKUs requested, skip items not in the list
                if req.skus and item_sku not in req.skus:
                    continue

                update_data: dict = {}
                if item.get("autoManage", False) != req.enable:
                    update_data["autoManage"] = req.enable
                # When enabling autoManage, also ensure item is visible/scannable
                if req.enable:
                    if not item.get("available", True):
                        update_data["available"] = True
                    if item.get("hidden", False):
                        update_data["hidden"] = False
                # When disabling, also ensure items are available
                else:
                    if not item.get("available", True):
                        update_data["available"] = True
                    if item.get("hidden", False):
                        update_data["hidden"] = False

                if not update_data:
                    loc_updated += 1
                    continue

                try:
                    await client.update_item(item["id"], update_data)
                    loc_updated += 1
                except Exception as e:
                    print(f"Error updating {item.get('name', '')} at {loc_name}: {e}")
                    loc_failed += 1

            total_updated += loc_updated
            total_failed += loc_failed
            results.append({
                "location": loc_name,
                "updated": loc_updated,
                "failed": loc_failed,
                "status": "done",
            })
        except Exception as e:
            results.append({
                "location": loc_name,
                "status": "error",
                "error": str(e),
            })

    return {
        "results": results,
        "total_updated": total_updated,
        "total_failed": total_failed,
        "auto_manage_enabled": req.enable,
    }


@router.post("/fix-pos")
async def fix_pos_scanning(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Fix POS scanning issues: disable autoManage on all items and ensure
    every item is available=true and hidden=false so they can be scanned.
    Clover's autoManage feature auto-hides items when stock=0, which blocks
    POS scanning. This endpoint reverses that damage."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    results = []
    total_fixed = 0
    total_already_ok = 0
    total_failed = 0

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        loc_fixed = 0
        loc_ok = 0
        loc_failed = 0
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            items = data.get("elements", [])

            for item in items:
                update_data: dict = {}
                if item.get("autoManage", False):
                    update_data["autoManage"] = False
                if not item.get("available", True):
                    update_data["available"] = True
                if item.get("hidden", False):
                    update_data["hidden"] = False

                if not update_data:
                    loc_ok += 1
                    continue

                try:
                    await client.update_item(item["id"], update_data)
                    loc_fixed += 1
                except Exception as e:
                    print(f"Error fixing {item.get('name', '')} at {loc_name}: {e}")
                    loc_failed += 1

            total_fixed += loc_fixed
            total_already_ok += loc_ok
            total_failed += loc_failed
            results.append({
                "location": loc_name,
                "fixed": loc_fixed,
                "already_ok": loc_ok,
                "failed": loc_failed,
                "status": "done",
            })
        except Exception as e:
            results.append({
                "location": loc_name,
                "status": "error",
                "error": str(e),
            })

    return {
        "status": "done",
        "results": results,
        "total_fixed": total_fixed,
        "total_already_ok": total_already_ok,
        "total_failed": total_failed,
        "message": f"Fixed {total_fixed} items across {len(results)} location(s). All items now scannable at POS.",
    }


class PushToLocationRequest(BaseModel):
    location_id: int
    initial_stock: Optional[float] = 0


@router.post("/items/{sku}/push-to-location")
async def push_item_to_location(
    sku: str,
    req: PushToLocationRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Push an existing item to a location where it doesn't exist yet.
    Copies item details from a location where it does exist and creates it at the target location."""
    # Get target location
    target_locations = await _get_locations(db, [req.location_id])
    if not target_locations:
        raise HTTPException(status_code=400, detail="Target location not found")
    target_loc = target_locations[0]
    target_loc_id, target_loc_name, target_merchant_id, target_api_token = (
        target_loc[0], target_loc[1], target_loc[2], target_loc[3]
    )

    # Get all locations to find the item in a source location
    all_locations = await _get_locations(db)
    source_item = None
    source_categories = []

    for loc in all_locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        if loc_id == req.location_id:
            continue  # Skip target location
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items(expand="itemStock,categories,ageRestricted")
            matching = [i for i in data.get("elements", []) if i.get("sku") == sku]
            if not matching:
                matching = [i for i in data.get("elements", []) if i.get("id") == sku]
            if matching:
                source_item = matching[0]
                source_categories = [
                    c.get("name", "") for c in source_item.get("categories", {}).get("elements", [])
                ]
                break
        except Exception:
            continue

    if not source_item:
        raise HTTPException(status_code=404, detail=f"Item with SKU '{sku}' not found in any location")

    # Build item data from source
    item_data: dict = {
        "name": source_item.get("name", ""),
        "price": source_item.get("price", 0),
    }
    if source_item.get("sku"):
        item_data["sku"] = source_item["sku"]
    if source_item.get("priceType"):
        item_data["priceType"] = source_item["priceType"]
    if source_item.get("cost"):
        item_data["cost"] = source_item["cost"]
    if source_item.get("code"):
        item_data["code"] = source_item["code"]
    if source_item.get("alternateName"):
        item_data["alternateName"] = source_item["alternateName"]
    if source_item.get("description"):
        item_data["description"] = source_item["description"]
    if source_item.get("colorCode"):
        item_data["colorCode"] = source_item["colorCode"]
    item_data["isRevenue"] = source_item.get("isRevenue", True)
    item_data["hidden"] = source_item.get("hidden", False)
    item_data["autoManage"] = source_item.get("autoManage", False)
    item_data["available"] = source_item.get("available", True)
    item_data["defaultTaxRates"] = source_item.get("defaultTaxRates", True)

    # Handle age restriction
    age_obj = source_item.get("ageRestrictedObj")
    if source_item.get("isAgeRestricted") and age_obj:
        item_data["isAgeRestricted"] = True
        item_data["ageRestrictedObj"] = {
            "id": age_obj.get("id"),
            "name": age_obj.get("name"),
            "minimumAge": age_obj.get("minimumAge", 21),
        }
    else:
        item_data["isAgeRestricted"] = False

    # Create at target location
    try:
        target_client = CloverClient(target_merchant_id, target_api_token)
        created = await target_client.create_item(item_data)
        clover_id = created.get("id", "")

        # Set initial stock if provided
        if req.initial_stock and req.initial_stock > 0:
            await target_client.update_item_stock(clover_id, req.initial_stock)

        # Assign categories
        for cat_name in source_categories:
            if cat_name:
                try:
                    cats = await target_client.get_categories()
                    existing = [c for c in cats.get("elements", []) if c.get("name") == cat_name]
                    if existing:
                        cat_id = existing[0]["id"]
                    else:
                        new_cat = await target_client.create_category(cat_name)
                        cat_id = new_cat["id"]
                    await target_client.assign_category(clover_id, cat_id)
                except Exception as cat_err:
                    print(f"Error assigning category '{cat_name}' at {target_loc_name}: {cat_err}")

        return {
            "status": "created",
            "location": target_loc_name,
            "clover_id": clover_id,
            "item_name": source_item.get("name", ""),
        }
    except httpx.HTTPStatusError as e:
        error_detail = str(e)
        try:
            error_body = e.response.json()
            error_detail = error_body.get("message", str(e))
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Failed to create item at {target_loc_name}: {error_detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create item at {target_loc_name}: {str(e)}")


class BulkCategoryRequest(BaseModel):
    skus: list[str]
    category_name: str


@router.post("/bulk-assign-category")
async def bulk_assign_category(
    req: BulkCategoryRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Assign a category to multiple items across all Clover locations."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    results: list[dict] = []
    total_assigned = 0

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Get or create the category in this merchant account
            cat_data = await client.get_categories()
            cat_elements = cat_data.get("elements", [])
            existing = [c for c in cat_elements if c.get("name", "").lower() == req.category_name.lower()]
            if existing:
                cat_id = existing[0]["id"]
            else:
                new_cat = await client.create_category(req.category_name)
                cat_id = new_cat["id"]

            # Get all items to find matching ones
            all_items_data = await client.get_items()
            all_items = all_items_data.get("elements", [])

            assigned_count = 0
            for sku in req.skus:
                # Match by SKU or by Clover item ID (for items with no SKU)
                matching = [i for i in all_items if i.get("sku") == sku or i.get("id") == sku]
                for item in matching:
                    # Check if category is already assigned
                    item_cats = item.get("categories", {}).get("elements", [])
                    already_has = any(c.get("id") == cat_id for c in item_cats)
                    if already_has:
                        continue
                    try:
                        await client.assign_category(item["id"], cat_id)
                        assigned_count += 1
                    except Exception:
                        pass  # skip individual failures

            total_assigned += assigned_count
            results.append({"location": loc_name, "assigned": assigned_count, "status": "ok"})
        except Exception as e:
            results.append({"location": loc_name, "assigned": 0, "status": "error", "error": str(e)})

    await _invalidate_cache()
    return {"category": req.category_name, "total_assigned": total_assigned, "results": results}


class SetItemCategoryRequest(BaseModel):
    sku: str
    category_name: Optional[str] = None  # empty string clears the category
    # Preferred: the exact set of categories the item should end up in.
    # An empty list clears every category.
    category_names: Optional[list[str]] = None


@router.post("/set-item-category")
async def set_item_category(
    req: SetItemCategoryRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Set a single item's categories across all Clover locations.

    Unlike bulk-assign (which only adds), this makes the item's categories match
    the request exactly: any category not in the request is unassigned, so an
    item wrongly tagged (e.g. Butane under "Edibles") can be corrected, and
    passing no categories clears them all.
    """
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    if req.category_names is not None:
        targets = [c.strip() for c in req.category_names if c and c.strip()]
    else:
        single = (req.category_name or "").strip()
        targets = [single] if single else []
    targets_lower = {t.lower() for t in targets}
    results: list[dict] = []
    errors: list[str] = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            all_items_data = await client.get_items()
            all_items = all_items_data.get("elements", [])
            matching = [i for i in all_items if i.get("sku") == req.sku or i.get("id") == req.sku]

            # Resolve (creating when needed) a Clover category id per target name
            target_ids: dict[str, str] = {}
            if targets:
                cat_data = await client.get_categories()
                cat_elements = cat_data.get("elements", [])
                by_name = {c.get("name", "").lower(): c for c in cat_elements}
                for t in targets:
                    existing = by_name.get(t.lower())
                    target_ids[t.lower()] = (
                        existing["id"] if existing else (await client.create_category(t))["id"]
                    )

            for item in matching:
                item_cats = item.get("categories", {}).get("elements", [])
                for c in item_cats:
                    if c.get("id") and c.get("name", "").lower() not in targets_lower:
                        try:
                            await client.unassign_category(item["id"], c["id"])
                        except Exception as ue:
                            errors.append(f"{c.get('name', '')}: {ue}")
                current_ids = {c.get("id") for c in item_cats}
                for cat_id in target_ids.values():
                    if cat_id not in current_ids:
                        await client.assign_category(item["id"], cat_id)

            results.append({"location": loc_name, "status": "ok"})
        except Exception as e:
            results.append({"location": loc_name, "status": "error", "error": str(e)})

    await _invalidate_cache()
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Clover rejected {len(errors)} category removal(s): {errors[0]}",
        )
    return {"categories": targets, "category": targets[0] if targets else "", "results": results}


@router.post("/bulk-remove-category")
async def bulk_remove_category(
    req: BulkCategoryRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Remove one category from multiple items across all Clover locations.

    The inverse of bulk-assign-category: it only detaches the named category and
    leaves the item's other categories intact, so a batch of mis-tagged products
    (e.g. concentrates that also carry "Edibles") can be cleaned up at once.
    """
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    target = (req.category_name or "").strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="category_name is required")

    results: list[dict] = []
    errors: list[str] = []
    total_removed = 0
    matched = 0
    sku_set = set(req.skus)

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            all_items_data = await client.get_items(expand="categories")
            all_items = all_items_data.get("elements", [])

            removed_count = 0
            for item in all_items:
                if item.get("sku") not in sku_set and item.get("id") not in sku_set:
                    continue
                matched += 1
                for c in item.get("categories", {}).get("elements", []):
                    if c.get("id") and c.get("name", "").lower() == target:
                        try:
                            await client.unassign_category(item["id"], c["id"])
                            removed_count += 1
                        except Exception as ue:
                            errors.append(f"{item.get('sku') or item.get('id')}: {ue}")

            total_removed += removed_count
            results.append({"location": loc_name, "removed": removed_count, "status": "ok"})
        except Exception as e:
            results.append({"location": loc_name, "removed": 0, "status": "error", "error": str(e)})

    await _invalidate_cache()
    # A bare "removed from 0 item(s)" hides two different problems — nothing
    # matched, or Clover refused the removals — so surface the rejection.
    if errors:
        raise HTTPException(
            status_code=502,
            detail=f"Clover rejected {len(errors)} removal(s): {errors[0]}",
        )
    return {
        "category": req.category_name,
        "total_removed": total_removed,
        "matched_items": matched,
        "results": results,
    }


async def _set_consolidated_stock(client, matching: list[dict], quantity) -> None:
    """Set a product's stock to an absolute total across duplicate Clover items.

    A single logical product can resolve to more than one Clover item at a
    location (e.g. blank-SKU bulk items created once per production batch). The
    inventory view merges them by name and sums their stock, so writing the same
    quantity to every duplicate double-counts and makes it impossible to lower
    the total (the untouched duplicates act as a hidden floor). Instead we set
    the first item to the requested total and zero the rest, so the merged total
    equals exactly what was entered. For a normal single item this is just a
    plain set.
    """
    if not matching:
        return
    await client.update_item_stock(matching[0]["id"], quantity)
    for extra in matching[1:]:
        await client.update_item_stock(extra["id"], 0)


class BulkStockUpdateItem(BaseModel):
    sku: str
    location_id: int
    quantity: float
    item_name: str | None = None
    clover_item_id: str | None = None


class BulkStockUpdateRequest(BaseModel):
    updates: list[BulkStockUpdateItem]


@router.post("/items/bulk-stock-update")
async def bulk_stock_update(
    req: BulkStockUpdateRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update stock for multiple items across locations in one call."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    # Build lookup: location_id -> (merchant_id, api_token, name)
    loc_map: dict[int, tuple] = {}
    for loc in locations:
        loc_map[loc[0]] = (loc[2], loc[3], loc[1])

    # Cache Clover items per location to avoid repeated API calls
    items_cache: dict[int, list] = {}
    results = []

    for upd in req.updates:
        if upd.location_id not in loc_map:
            results.append({"sku": upd.sku, "location_id": upd.location_id, "status": "location_not_found"})
            continue

        merchant_id, api_token, loc_name = loc_map[upd.location_id]
        client = CloverClient(merchant_id, api_token)

        # Fetch and cache items for this location
        if upd.location_id not in items_cache:
            try:
                data = await client.get_items(expand="itemStock")
                items_cache[upd.location_id] = data.get("elements", [])
            except Exception as e:
                results.append({"sku": upd.sku, "location": loc_name, "status": "error", "error": str(e)})
                continue

        clover_items = items_cache[upd.location_id]
        # Try matching by SKU first
        matching = [i for i in clover_items if i.get("sku") and i.get("sku") == upd.sku]
        # Then try matching by location-specific Clover item ID
        if not matching and upd.clover_item_id:
            matching = [i for i in clover_items if i.get("id") == upd.clover_item_id]
        # Then try matching by Clover ID (sku field may be the clover_id from another location)
        if not matching:
            matching = [i for i in clover_items if i.get("id") == upd.sku]
        # Fallback: match by normalized item name
        if not matching and upd.item_name:
            norm_name = " ".join(upd.item_name.split()).lower()
            matching = [
                i for i in clover_items
                if " ".join((i.get("name", "") or "").split()).lower() == norm_name
            ]
        if not matching:
            results.append({"sku": upd.sku, "location": loc_name, "status": "not_found"})
            continue

        # A single logical product can map to several Clover items at one
        # location (e.g. blank-SKU bulk items created per production batch).
        # Pull in every same-name item so we can consolidate onto one, otherwise
        # the untouched duplicates keep inflating the merged total.
        primary_name = " ".join((matching[0].get("name", "") or "").split()).lower()
        if primary_name:
            matched_ids = {m["id"] for m in matching}
            for i in clover_items:
                if i["id"] in matched_ids:
                    continue
                if " ".join((i.get("name", "") or "").split()).lower() == primary_name:
                    matching.append(i)

        try:
            await _set_consolidated_stock(client, matching, int(upd.quantity))
            results.append({"sku": upd.sku, "location": loc_name, "status": "updated", "quantity": upd.quantity})
        except Exception as e:
            results.append({"sku": upd.sku, "location": loc_name, "status": "error", "error": str(e)})

    await _invalidate_cache()
    invalidate_product_cache()
    return {"results": results, "total_updated": sum(1 for r in results if r.get("status") == "updated")}


class BulkDeleteRequest(BaseModel):
    skus: list[str]


@router.post("/items/bulk-delete")
async def bulk_delete_items(
    req: BulkDeleteRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Delete multiple items from all locations by SKU list."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    all_results = []
    for sku in req.skus:
        sku_results = []
        for loc in locations:
            loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
            try:
                client = CloverClient(merchant_id, api_token)
                data = await client.get_items()
                matching = [i for i in data.get("elements", []) if i.get("sku") == sku]
                if not matching:
                    matching = [i for i in data.get("elements", []) if i.get("id") == sku]
                if not matching:
                    sku_results.append({"location": loc_name, "status": "not_found"})
                    continue
                for match in matching:
                    await client.delete_item(match["id"])
                sku_results.append({"location": loc_name, "status": "deleted", "count": len(matching)})
            except Exception as e:
                sku_results.append({"location": loc_name, "status": "error", "error": str(e)})

        await db.execute("DELETE FROM par_levels WHERE sku = ?", (sku,))
        await db.commit()
        all_results.append({"sku": sku, "results": sku_results})

    await _remove_from_cache(req.skus)
    return {"results": all_results}


class BulkHideRequest(BaseModel):
    skus: list[str]


@router.post("/items/bulk-hide")
async def bulk_hide_items(
    req: BulkHideRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Hide items from the inventory view (keeps them for sales data)."""
    hidden = 0
    for sku in req.skus:
        await db.execute(
            "INSERT OR IGNORE INTO hidden_items (sku) VALUES (?)", (sku,)
        )
        hidden += 1
    await db.commit()
    return {"hidden": hidden, "skus": req.skus}


@router.post("/items/bulk-unhide")
async def bulk_unhide_items(
    req: BulkHideRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Unhide items so they appear in the inventory view again."""
    for sku in req.skus:
        await db.execute("DELETE FROM hidden_items WHERE sku = ?", (sku,))
    await db.commit()
    return {"unhidden": len(req.skus), "skus": req.skus}


@router.delete("/items/{sku}")
async def delete_item(
    sku: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Delete an item from all locations by SKU.

    When the SKU is actually a Clover ID (no user-assigned SKU), the same
    product may have *different* Clover IDs in each location.  To handle this
    we first try matching by SKU / Clover ID, and if that only finds the item
    in one location we also try matching by the resolved item name so the
    product is removed from *every* location in a single call.
    """
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    # First pass: find the item name so we can do a name-based fallback
    resolved_name: str | None = None
    all_deleted_clover_ids: list[str] = []

    results = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            elements = data.get("elements", [])

            # Try matching by user-assigned SKU first
            matching = [i for i in elements if i.get("sku") == sku]
            # Then try matching by Clover ID
            if not matching:
                matching = [i for i in elements if i.get("id") == sku]
            # Capture the name from the first match for fallback
            if matching and not resolved_name:
                resolved_name = " ".join((matching[0].get("name", "") or "").split())
            # If no SKU/ID match but we know the name, match by name
            # (handles items with no user-SKU that have different Clover IDs per location)
            if not matching and resolved_name:
                matching = [
                    i for i in elements
                    if " ".join((i.get("name", "") or "").split()) == resolved_name
                ]
            if not matching:
                results.append({"location": loc_name, "status": "not_found"})
                continue
            for match in matching:
                await client.delete_item(match["id"])
                all_deleted_clover_ids.append(match["id"])
            results.append({"location": loc_name, "status": "deleted", "count": len(matching)})
        except Exception as e:
            results.append({"location": loc_name, "status": "error", "error": str(e)})

    # Also remove PAR levels
    await db.execute("DELETE FROM par_levels WHERE sku = ?", (sku,))
    # Remove inventory snapshots so the item doesn't linger in change tracking
    await db.execute("DELETE FROM inventory_snapshots WHERE sku = ?", (sku,))
    await db.commit()

    # Remove from cache: the displayed SKU plus any Clover IDs we deleted
    skus_to_remove = [sku] + all_deleted_clover_ids
    await _remove_from_cache(skus_to_remove)
    # Also remove by name from cache (for name-merged items)
    if resolved_name:
        await _remove_from_cache_by_name(resolved_name)
    return {"results": results}


@router.put("/items/{sku}")
async def update_item(
    sku: str,
    item: ItemUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update an item across all locations by SKU."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    update_data: dict = {}
    if item.name is not None:
        update_data["name"] = item.name
    if item.price is not None:
        update_data["price"] = item.price
    if item.sku is not None:
        update_data["sku"] = item.sku
    if item.price_type is not None:
        update_data["priceType"] = item.price_type
    if item.cost is not None:
        update_data["cost"] = item.cost
    if item.product_code is not None:
        update_data["code"] = item.product_code
    if item.alternate_name is not None:
        update_data["alternateName"] = item.alternate_name
    if item.description is not None:
        update_data["description"] = item.description
    if item.color_code is not None:
        update_data["colorCode"] = item.color_code
    if item.is_revenue is not None:
        update_data["isRevenue"] = item.is_revenue
    if item.hidden is not None:
        update_data["hidden"] = item.hidden
    if item.auto_manage is not None:
        update_data["autoManage"] = item.auto_manage
    if item.available is not None:
        update_data["available"] = item.available
    if item.default_tax_rates is not None:
        update_data["defaultTaxRates"] = item.default_tax_rates
    # Age restriction handling
    if item.is_age_restricted is not None:
        if item.is_age_restricted and item.age_restriction_type:
            update_data["isAgeRestricted"] = True
        else:
            update_data["isAgeRestricted"] = False

    has_field_updates = bool(update_data)
    has_stock_updates = bool(item.stock_updates)
    needs_age_obj = item.is_age_restricted and item.age_restriction_type

    if not has_field_updates and not has_stock_updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build a map of location_id -> desired stock quantity
    stock_map: dict[int, float] = {}
    if item.stock_updates:
        for su in item.stock_updates:
            stock_map[su.location_id] = su.quantity

    # Pre-fetch all location item lists so we can resolve cross-location matching.
    # Items without user-assigned SKUs have different Clover IDs at each location,
    # so we need to fall back to matching by normalized item name.
    loc_items_cache: dict[int, list[dict]] = {}
    item_name_fallback: str | None = None

    results = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            elements = data.get("elements", [])
            loc_items_cache[loc_id] = elements

            # Find the item by SKU
            matching = [i for i in elements if i.get("sku") == sku]
            if not matching:
                # Also try matching by Clover item ID
                matching = [i for i in elements if i.get("id") == sku]
            if not matching and item_name_fallback:
                # Fallback: match by normalized name (for items without user-assigned SKUs
                # that have different Clover IDs at each location)
                matching = [
                    i for i in elements
                    if " ".join((i.get("name", "") or "").split()).lower() == item_name_fallback.lower()
                ]
            if not matching:
                results.append({"location": loc_name, "status": "not_found"})
                continue

            # Remember the item name for cross-location fallback matching
            if not item_name_fallback and matching:
                item_name_fallback = " ".join((matching[0].get("name", "") or "").split())

            # Build per-location update data (may need age restriction obj lookup)
            loc_update_data = dict(update_data)
            if needs_age_obj:
                age_obj = await _get_age_restriction_obj(
                    client, item.age_restriction_type,
                    item.age_restriction_min_age or 21
                )
                if age_obj:
                    loc_update_data["ageRestrictedObj"] = age_obj
                else:
                    loc_update_data["isAgeRestricted"] = False

            for match in matching:
                if has_field_updates:
                    await client.update_item(match["id"], loc_update_data)
            if loc_id in stock_map:
                await _set_consolidated_stock(client, matching, stock_map[loc_id])
            results.append({"location": loc_name, "status": "updated", "count": len(matching)})
        except httpx.HTTPStatusError as e:
            error_detail = str(e)
            try:
                error_body = e.response.json()
                error_detail = error_body.get("message", str(e))
            except Exception:
                pass
            import logging
            logging.error(f"Clover update error at {loc_name}: {error_detail} | data sent: {loc_update_data}")
            results.append({"location": loc_name, "status": "error", "error": error_detail})
        except Exception as e:
            import logging
            logging.error(f"Update error at {loc_name}: {str(e)}")
            results.append({"location": loc_name, "status": "error", "error": str(e)})

    # Retry any "not_found" locations now that we may have learned the item name
    if item_name_fallback:
        for idx, r in enumerate(results):
            if r.get("status") != "not_found":
                continue
            loc_name = r["location"]
            loc = next((l for l in locations if l[1] == loc_name), None)
            if not loc:
                continue
            loc_id, _, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
            elements = loc_items_cache.get(loc_id, [])
            matching = [
                i for i in elements
                if " ".join((i.get("name", "") or "").split()).lower() == item_name_fallback.lower()
            ]
            if not matching:
                continue
            try:
                client = CloverClient(merchant_id, api_token)
                loc_update_data = dict(update_data)
                if needs_age_obj:
                    age_obj = await _get_age_restriction_obj(
                        client, item.age_restriction_type,
                        item.age_restriction_min_age or 21
                    )
                    if age_obj:
                        loc_update_data["ageRestrictedObj"] = age_obj
                    else:
                        loc_update_data["isAgeRestricted"] = False
                for match in matching:
                    if has_field_updates:
                        await client.update_item(match["id"], loc_update_data)
                if loc_id in stock_map:
                    await _set_consolidated_stock(client, matching, stock_map[loc_id])
                results[idx] = {"location": loc_name, "status": "updated", "count": len(matching)}
            except Exception:
                pass  # keep original not_found

    # Also persist description to local SQLite DB (Clover silently ignores it)
    if item.description is not None:
        product_name = item.name  # may be None if only description changed
        await db.execute(
            """INSERT INTO product_descriptions (sku, product_name, description, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(sku, product_name) DO UPDATE SET
                   description = excluded.description,
                   updated_at = CURRENT_TIMESTAMP""",
            (sku, product_name, item.description),
        )
        await db.commit()

    # Persist effect/strength/type to local SQLite DB
    if item.effect is not None or item.strength is not None or item.product_type is not None:
        product_name = item.name
        await db.execute(
            """INSERT INTO product_attributes (sku, product_name, effect, strength, product_type, updated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(sku) DO UPDATE SET
                   effect = COALESCE(?, product_attributes.effect),
                   strength = COALESCE(?, product_attributes.strength),
                   product_type = COALESCE(?, product_attributes.product_type),
                   product_name = COALESCE(?, product_attributes.product_name),
                   updated_at = CURRENT_TIMESTAMP""",
            (sku, product_name, item.effect, item.strength, item.product_type,
             item.effect, item.strength, item.product_type, product_name),
        )
        await db.commit()

    await _invalidate_cache()
    return {"results": results}


class BulkDescriptionItem(BaseModel):
    sku: str
    description: str
    product_name: Optional[str] = None


class BulkDescriptionRequest(BaseModel):
    items: list[BulkDescriptionItem]


@router.post("/bulk-descriptions")
async def bulk_update_descriptions(
    req: BulkDescriptionRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Store descriptions in local DB (Clover API does not persist descriptions)."""
    updated = 0
    errors = 0

    for item in req.items:
        try:
            await db.execute(
                """INSERT INTO product_descriptions (sku, product_name, description, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(sku, product_name) DO UPDATE SET
                       description = excluded.description,
                       updated_at = CURRENT_TIMESTAMP""",
                (item.sku, item.product_name, item.description),
            )
            updated += 1
        except Exception as e:
            errors += 1

    await db.commit()
    await _invalidate_cache()
    return {
        "summary": {"updated": updated, "errors": errors, "total": len(req.items)},
    }


@router.get("/descriptions")
async def get_descriptions(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """List all stored product descriptions."""
    cursor = await db.execute("SELECT sku, product_name, description, updated_at FROM product_descriptions")
    rows = await cursor.fetchall()
    return {
        "total": len(rows),
        "descriptions": [
            {"sku": r[0], "product_name": r[1], "description": r[2], "updated_at": r[3]}
            for r in rows
        ],
    }


@router.get("/product-attributes")
async def get_product_attributes(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """List all stored product attributes (effect, strength & type)."""
    cursor = await db.execute("SELECT sku, product_name, effect, strength, product_type, updated_at FROM product_attributes")
    rows = await cursor.fetchall()
    return {
        "total": len(rows),
        "attributes": [
            {"sku": r[0], "product_name": r[1], "effect": r[2], "strength": r[3], "product_type": r[4], "updated_at": r[5]}
            for r in rows
        ],
    }


class ProductAttributeUpdate(BaseModel):
    effect: Optional[str] = None  # Relax, Sleep, Energy, Focus
    strength: Optional[str] = None  # High, Medium, Low
    product_type: Optional[str] = None  # Hybrid, Indica, Sativa
    product_name: Optional[str] = None


@router.put("/product-attributes/{sku}")
async def update_product_attributes(
    sku: str,
    attrs: ProductAttributeUpdate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Set effect, strength and/or type for a product by SKU."""
    await db.execute(
        """INSERT INTO product_attributes (sku, product_name, effect, strength, product_type, updated_at)
           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(sku) DO UPDATE SET
               effect = COALESCE(?, product_attributes.effect),
               strength = COALESCE(?, product_attributes.strength),
               product_type = COALESCE(?, product_attributes.product_type),
               product_name = COALESCE(?, product_attributes.product_name),
               updated_at = CURRENT_TIMESTAMP""",
        (sku, attrs.product_name, attrs.effect, attrs.strength, attrs.product_type,
         attrs.effect, attrs.strength, attrs.product_type, attrs.product_name),
    )
    await db.commit()
    invalidate_product_cache()
    return {"status": "ok", "sku": sku, "effect": attrs.effect, "strength": attrs.strength, "product_type": attrs.product_type}


class ImageUpload(BaseModel):
    image_data: str  # base64 encoded image data
    content_type: str = "image/png"
    product_name: Optional[str] = None


@router.post("/images/{sku}")
async def upload_image(
    sku: str,
    data: ImageUpload,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Upload or replace a product image (base64 encoded)."""
    try:
        decoded = base64.b64decode(data.image_data)
        if len(decoded) > 10 * 1024 * 1024:  # 10MB limit
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {e}")

    await db.execute(
        """INSERT INTO product_images (sku, image_data, content_type, product_name, updated_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(sku) DO UPDATE SET
             image_data = excluded.image_data,
             content_type = excluded.content_type,
             product_name = COALESCE(excluded.product_name, product_images.product_name),
             updated_at = CURRENT_TIMESTAMP""",
        (sku, data.image_data, data.content_type, data.product_name),
    )
    await db.commit()
    # Invalidate processed image cache for this SKU
    for key in [k for k in _image_cache if k[0] == sku]:
        del _image_cache[key]
    # Update cache in-place to reflect the new image without full re-sync
    async with _cache_lock:
        for item in _inventory_cache.get("items", []):
            if item["sku"] == sku:
                item["has_image"] = True
    # Invalidate e-commerce product cache so website picks up new image URL
    invalidate_product_cache()
    return {"status": "ok", "sku": sku}


@router.get("/images/{sku}")
async def get_image(
    sku: str,
    w: Optional[int] = None,
    nobg: Optional[int] = None,
    bg: Optional[int] = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get a product image by SKU. Returns the raw image bytes.
    Optional ?w=300 parameter to get a resized thumbnail for faster loading.
    Optional ?nobg=1 parameter to remove white background (returns transparent PNG).
    Optional ?bg=1 parameter to whiten a dark studio background (subject preserved).
    Results are cached in-memory so expensive processing only runs once per SKU."""
    cache_key = (sku, w, nobg, bg)
    cached = _image_cache.get(cache_key)
    if cached is not None:
        return Response(
            content=cached[0],
            media_type=cached[1],
            headers={"Cache-Control": "public, max-age=86400"},
        )

    cursor = await db.execute(
        "SELECT image_data, content_type, updated_at FROM product_images WHERE sku = ?", (sku,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No image found for this SKU")

    image_bytes = base64.b64decode(row[0])
    final_media_type = row[1]

    # Run CPU-intensive image processing in a thread pool to avoid blocking the event loop
    orig_media_type = row[1]

    def _process_image(raw_bytes: bytes) -> tuple[bytes, str]:
        result_bytes = raw_bytes
        media = orig_media_type
        # Remove white background if requested
        if nobg and nobg == 1:
            try:
                result_bytes, media = _remove_white_background(result_bytes)
            except Exception:
                pass
        # Whiten a dark studio background if requested (leaves image untouched
        # when the subject can't be separated from a dark background)
        elif bg and bg == 1:
            try:
                whitened, wmedia = _whiten_background(result_bytes)
                if whitened is not result_bytes:
                    result_bytes, media = whitened, wmedia
            except Exception:
                pass
        # Resize if width parameter provided
        if w and 50 <= w <= 1200:
            try:
                img = PILImage.open(io.BytesIO(result_bytes))
                ratio = w / img.width
                new_height = int(img.height * ratio)
                img = img.resize((w, new_height), PILImage.LANCZOS)
                buf = io.BytesIO()
                try:
                    if img.mode == "RGBA":
                        bg_img = PILImage.new("RGBA", img.size, (255, 255, 255, 255))
                        bg_img.paste(img, mask=img.split()[3])
                        img = bg_img.convert("RGB")
                    else:
                        img = img.convert("RGB")
                    img.save(buf, format="WEBP", quality=95)
                    media = "image/webp"
                except Exception:
                    fmt = "PNG" if orig_media_type == "image/png" else "JPEG"
                    img.save(buf, format=fmt, quality=95)
                    media = orig_media_type
                result_bytes = buf.getvalue()
            except Exception:
                pass
        return result_bytes, media

    loop = asyncio.get_event_loop()
    image_bytes, final_media_type = await loop.run_in_executor(None, _process_image, image_bytes)

    # Cache the processed result (evict oldest if full)
    if len(_image_cache) >= _IMAGE_CACHE_MAX:
        oldest_key = next(iter(_image_cache))
        del _image_cache[oldest_key]
    _image_cache[cache_key] = (image_bytes, final_media_type)

    return Response(
        content=image_bytes,
        media_type=final_media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/images-list")
async def list_all_images(
    db: aiosqlite.Connection = Depends(get_db),
):
    """List all SKUs that have images stored. Public endpoint for e-commerce."""
    cursor = await db.execute(
        "SELECT sku, content_type, product_name, created_at, updated_at FROM product_images"
    )
    rows = await cursor.fetchall()
    return {
        "images": [
            {
                "sku": row[0],
                "content_type": row[1],
                "product_name": row[2],
                "created_at": row[3],
                "updated_at": row[4],
            }
            for row in rows
        ],
        "count": len(rows),
    }


@router.get("/images-map")
async def get_images_map(
    db: aiosqlite.Connection = Depends(get_db),
):
    """Return a mapping of product names to image URLs.
    Public endpoint for e-commerce sites to know which products have custom images.
    Falls back to Clover API to resolve SKU -> product name if not stored locally."""
    base_url = "https://thd-inventory-api.fly.dev/api/inventory/images"

    # Get all images with their product names
    cursor = await db.execute(
        "SELECT sku, product_name FROM product_images"
    )
    rows = await cursor.fetchall()

    # Build the mapping
    name_to_url = {}
    skus_without_names = []

    for row in rows:
        sku = row[0]
        product_name = row[1]
        if product_name:
            name_to_url[product_name.upper()] = f"{base_url}/{sku}"
        else:
            skus_without_names.append(sku)

    # For SKUs without stored names, look up in Clover
    if skus_without_names:
        try:
            locations = await _get_locations(db)
            for loc in locations:
                merchant_id = loc[2]
                api_token = loc[3]
                try:
                    client = CloverClient(merchant_id, api_token)
                    items = await client.get_items()
                    for item in items:
                        item_sku = item.get("sku") or item.get("id")
                        if item_sku in skus_without_names:
                            item_name = item.get("name", "")
                            if item_name:
                                name_to_url[item_name.upper()] = f"{base_url}/{item_sku}"
                                # Also update the stored product name for future lookups
                                await db.execute(
                                    "UPDATE product_images SET product_name = ? WHERE sku = ?",
                                    (item_name, item_sku),
                                )
                except Exception:
                    continue
            await db.commit()
        except Exception:
            pass

    return {
        "map": name_to_url,
        "count": len(name_to_url),
    }


@router.get("/images-by-name")
async def get_image_by_name(
    name: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Search for a product image by product name. Returns the raw image bytes.
    Tries exact match first, then case-insensitive contains match."""
    # First get all locations to search Clover-synced data
    cursor = await db.execute("SELECT id, merchant_id, api_token FROM locations")
    locations = await cursor.fetchall()

    # Search for SKU matching the product name in our synced inventory
    matched_sku = None
    for loc in locations:
        loc_id = loc[0]
        merchant_id = loc[1]
        api_token = loc[2]
        try:
            client = CloverClient(merchant_id, api_token)
            items = await client.get_items()
            for item in items:
                item_name = item.get("name", "")
                if item_name.upper() == name.upper():
                    matched_sku = item.get("sku") or item.get("id")
                    break
            if matched_sku:
                break
        except Exception:
            continue

    if not matched_sku:
        raise HTTPException(status_code=404, detail="No product found with that name")

    # Now check if we have an image for this SKU
    cursor = await db.execute(
        "SELECT image_data, content_type FROM product_images WHERE sku = ?",
        (matched_sku,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Product found (SKU: {matched_sku}) but no image stored",
        )

    image_bytes = base64.b64decode(row[0])
    return Response(content=image_bytes, media_type=row[1])


@router.delete("/images/{sku}")
async def delete_image(
    sku: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Delete a product image by SKU."""
    await db.execute("DELETE FROM product_images WHERE sku = ?", (sku,))
    await db.commit()
    # Invalidate processed image cache for this SKU
    for key in [k for k in _image_cache if k[0] == sku]:
        del _image_cache[key]
    return {"status": "ok", "sku": sku}


# ─── Product Image Gallery (multiple images per product) ──────────────────

@router.get("/images/{sku}/gallery")
async def get_image_gallery(
    sku: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all gallery images for a product (returns metadata, not image data)."""
    cursor = await db.execute(
        "SELECT id, position, content_type, created_at FROM product_image_gallery WHERE sku = ? ORDER BY position",
        (sku,),
    )
    rows = await cursor.fetchall()
    return [
        {"id": row[0], "position": row[1], "content_type": row[2], "created_at": row[3]}
        for row in rows
    ]


@router.post("/images/{sku}/gallery")
async def upload_gallery_image(
    sku: str,
    data: ImageUpload,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Upload an additional image to the product gallery."""
    try:
        decoded = base64.b64decode(data.image_data)
        if len(decoded) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {e}")

    # Find next available position
    cursor = await db.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM product_image_gallery WHERE sku = ?", (sku,)
    )
    next_pos = (await cursor.fetchone())[0]

    await db.execute(
        """INSERT INTO product_image_gallery (sku, position, image_data, content_type)
           VALUES (?, ?, ?, ?)""",
        (sku, next_pos, data.image_data, data.content_type),
    )
    await db.commit()
    return {"status": "ok", "sku": sku, "position": next_pos}


@router.get("/images/{sku}/gallery/{position}")
async def get_gallery_image(
    sku: str,
    position: int,
    w: Optional[int] = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get a specific gallery image by SKU and position."""
    cursor = await db.execute(
        "SELECT image_data, content_type FROM product_image_gallery WHERE sku = ? AND position = ?",
        (sku, position),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Gallery image not found")

    image_bytes = base64.b64decode(row[0])
    media_type = row[1]

    if w and w > 0:
        try:
            from PIL import Image as PILImage
            import io
            img = PILImage.open(io.BytesIO(image_bytes))
            ratio = w / img.width
            new_h = int(img.height * ratio)
            img = img.resize((w, new_h), PILImage.LANCZOS)
            buf = io.BytesIO()
            fmt = "WEBP" if "webp" in media_type else "PNG"
            img.save(buf, format=fmt, quality=85)
            image_bytes = buf.getvalue()
            media_type = f"image/{fmt.lower()}"
        except Exception:
            pass

    return Response(
        content=image_bytes,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/images/{sku}/gallery/{position}")
async def delete_gallery_image(
    sku: str,
    position: int,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Delete a specific gallery image."""
    await db.execute(
        "DELETE FROM product_image_gallery WHERE sku = ? AND position = ?", (sku, position)
    )
    await db.commit()
    return {"status": "ok", "sku": sku, "position": position}


@router.post("/sync-refunds")
async def sync_refunds(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Sync refunds from Clover POS and update inventory stock accordingly.
    When a refund is processed at POS, the returned items should be added back to stock."""
    locations = await _get_locations(db)
    if not locations:
        return {"status": "no_locations", "refunds_processed": 0}

    total_processed = 0
    total_skipped = 0
    details: list[dict] = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Get all orders and look for refunds
            orders_data = await client.get_orders(limit=200, filter_str="payType!=NULL")
            orders = orders_data.get("elements", [])

            for order in orders:
                order_id = order.get("id", "")
                if not order_id:
                    continue

                # Check if this is a refund (negative total or has refund markers)
                order_total = order.get("total", 0)
                is_refund = order_total < 0

                # Also check line items for individual refunded items
                line_items = order.get("lineItems", {}).get("elements", []) if order.get("lineItems") else []
                refunded_items = []
                for li in line_items:
                    if li.get("refunded") or li.get("isRefund"):
                        refunded_items.append(li)

                if not is_refund and not refunded_items:
                    continue

                # Check if already synced
                synced_cursor = await db.execute(
                    "SELECT id FROM synced_refunds WHERE clover_order_id = ? AND location_merchant_id = ?",
                    (order_id, merchant_id),
                )
                if await synced_cursor.fetchone():
                    total_skipped += 1
                    continue

                # Process refund - for full refunds, all line items are returned
                items_to_return = refunded_items if refunded_items else line_items
                returned_info = []

                for li in items_to_return:
                    item_ref = li.get("item", {})
                    item_id = item_ref.get("id", "") if item_ref else ""
                    item_name = li.get("name", "Unknown")
                    qty = 1  # Each refunded line item = 1 unit returned

                    if item_id:
                        try:
                            # Get current stock and add the returned quantity
                            item_data = await client.get_item(item_id)
                            current_stock = (item_data.get("itemStock") or {}).get("quantity", 0)
                            new_stock = current_stock + qty
                            await client.update_item_stock(item_id, new_stock)
                            returned_info.append({
                                "item_name": item_name,
                                "item_id": item_id,
                                "qty_returned": qty,
                                "new_stock": new_stock,
                            })
                        except Exception as item_err:
                            print(f"Error updating stock for refunded item {item_name}: {item_err}")
                            returned_info.append({
                                "item_name": item_name,
                                "item_id": item_id,
                                "error": str(item_err),
                            })

                # Mark as synced
                await db.execute(
                    """INSERT INTO synced_refunds (clover_order_id, location_merchant_id, location_name, refund_total, items_returned, status)
                       VALUES (?, ?, ?, ?, ?, 'processed')""",
                    (order_id, merchant_id, loc_name, abs(order_total), json.dumps(returned_info)),
                )

                total_processed += 1
                details.append({
                    "order_id": order_id,
                    "location": loc_name,
                    "refund_total": abs(order_total) / 100.0,
                    "items_returned": returned_info,
                })

        except Exception as e:
            details.append({"location": loc_name, "error": str(e)})

    await db.commit()
    return {
        "status": "done",
        "refunds_processed": total_processed,
        "refunds_skipped": total_skipped,
        "details": details,
    }


@router.get("/refund-history")
async def get_refund_history(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get history of synced refunds."""
    cursor = await db.execute("""
        SELECT clover_order_id, location_name, refund_total, items_returned, status, synced_at
        FROM synced_refunds
        ORDER BY synced_at DESC LIMIT 50
    """)
    rows = await cursor.fetchall()
    return {
        "refunds": [{
            "order_id": r[0],
            "location": r[1],
            "refund_total": (r[2] or 0) / 100.0,
            "items_returned": json.loads(r[3]) if r[3] else [],
            "status": r[4],
            "synced_at": r[5],
        } for r in rows],
    }


class StockTransferRequest(BaseModel):
    sku: str
    from_location_id: int
    to_location_id: int
    quantity: float
    transfer_group_id: Optional[str] = None
    item_name: Optional[str] = None


@router.post("/transfer-stock")
async def transfer_stock(
    req: StockTransferRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Transfer stock of an item from one location to another.
    Deducts from source and adds to destination via Clover API.
    Logs every attempt (success or failure) to transfer_history."""
    transfer_group_id = req.transfer_group_id or str(uuid.uuid4())
    transferred_by = user.get("username", "unknown")

    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")

    # Get both locations
    from_locs = await _get_locations(db, [req.from_location_id])
    to_locs = await _get_locations(db, [req.to_location_id])
    if not from_locs:
        raise HTTPException(status_code=400, detail="Source location not found")
    if not to_locs:
        raise HTTPException(status_code=400, detail="Destination location not found")

    from_loc = from_locs[0]
    to_loc = to_locs[0]
    from_name = from_loc[1]
    to_name = to_loc[1]

    # Find item at source location
    from_client = CloverClient(from_loc[2], from_loc[3])
    from_data = await from_client.get_items(expand="itemStock")
    from_items = from_data.get("elements", [])
    source_item = None
    for item in from_items:
        if (item.get("sku") or item.get("id", "")) == req.sku:
            source_item = item
            break
    # Fallback: match by Clover item ID directly
    if not source_item:
        for item in from_items:
            if item.get("id", "") == req.sku:
                source_item = item
                break
    # Fallback: match by name (handles items with different Clover IDs per location)
    if not source_item and req.item_name:
        normalized_name = " ".join(req.item_name.split())
        for item in from_items:
            if " ".join((item.get("name") or "").split()) == normalized_name:
                source_item = item
                break

    if not source_item:
        # Log failed lookup
        try:
            await db.execute(
                """INSERT INTO transfer_history
                   (transfer_group_id, sku, item_name, quantity, from_location_id, from_location_name,
                    to_location_id, to_location_name, status, error_message, transferred_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?)""",
                (transfer_group_id, req.sku, req.sku, req.quantity,
                 req.from_location_id, from_name, req.to_location_id, to_name,
                 f"Item not found at {from_name}", transferred_by),
            )
            await db.commit()
        except Exception as log_err:
            print(f"[transfer] Failed to log transfer history: {log_err}")
        raise HTTPException(status_code=404, detail=f"Item with SKU '{req.sku}' not found at {from_name}")

    item_name = source_item.get("name", req.sku)
    current_stock = (source_item.get("itemStock") or {}).get("quantity", 0)
    if current_stock < req.quantity:
        try:
            await db.execute(
                """INSERT INTO transfer_history
                   (transfer_group_id, sku, item_name, quantity, from_location_id, from_location_name,
                    to_location_id, to_location_name, status, error_message, from_stock_before, transferred_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)""",
                (transfer_group_id, req.sku, item_name, req.quantity,
                 req.from_location_id, from_name, req.to_location_id, to_name,
                 f"Insufficient stock: {current_stock} available, {req.quantity} requested",
                 current_stock, transferred_by),
            )
            await db.commit()
        except Exception as log_err:
            print(f"[transfer] Failed to log transfer history: {log_err}")
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock at {from_name}: {current_stock} available, {req.quantity} requested"
        )

    # Find item at destination location
    to_client = CloverClient(to_loc[2], to_loc[3])
    to_data = await to_client.get_items(expand="itemStock")
    to_items = to_data.get("elements", [])
    dest_item = None
    for item in to_items:
        if (item.get("sku") or item.get("id", "")) == req.sku:
            dest_item = item
            break
    # Fallback: match by Clover item ID directly
    if not dest_item:
        for item in to_items:
            if item.get("id", "") == req.sku:
                dest_item = item
                break
    # Fallback: match by name (handles items with different Clover IDs per location)
    if not dest_item and req.item_name:
        normalized_name = " ".join(req.item_name.split())
        for item in to_items:
            if " ".join((item.get("name") or "").split()) == normalized_name:
                dest_item = item
                break

    if not dest_item:
        try:
            await db.execute(
                """INSERT INTO transfer_history
                   (transfer_group_id, sku, item_name, quantity, from_location_id, from_location_name,
                    to_location_id, to_location_name, status, error_message, from_stock_before, transferred_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)""",
                (transfer_group_id, req.sku, item_name, req.quantity,
                 req.from_location_id, from_name, req.to_location_id, to_name,
                 f"Item not found at {to_name}", current_stock, transferred_by),
            )
            await db.commit()
        except Exception as log_err:
            print(f"[transfer] Failed to log transfer history: {log_err}")
        raise HTTPException(
            status_code=404,
            detail=f"Item with SKU '{req.sku}' not found at {to_name}. Push the item to that location first."
        )

    dest_stock = (dest_item.get("itemStock") or {}).get("quantity", 0)

    # Execute transfer: deduct from source, then add to destination
    new_from_stock = current_stock - req.quantity
    new_to_stock = dest_stock + req.quantity
    source_deducted = False

    # Step 1: Deduct from source
    try:
        await from_client.update_item_stock(source_item["id"], new_from_stock)
        source_deducted = True
    except Exception as e:
        # Source deduction failed — nothing changed
        try:
            await db.execute(
                """INSERT INTO transfer_history
                   (transfer_group_id, sku, item_name, quantity, from_location_id, from_location_name,
                    to_location_id, to_location_name, status, error_message,
                    from_stock_before, to_stock_before, transferred_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?)""",
                (transfer_group_id, req.sku, item_name, req.quantity,
                 req.from_location_id, from_name, req.to_location_id, to_name,
                 f"Source deduction failed: {e}", current_stock, dest_stock, transferred_by),
            )
            await db.commit()
        except Exception as log_err:
            print(f"[transfer] Failed to log transfer history: {log_err}")
        raise HTTPException(status_code=500, detail=f"Transfer failed (source deduction): {str(e)}")

    # Step 2: Add to destination
    try:
        await to_client.update_item_stock(dest_item["id"], new_to_stock)
    except Exception as e:
        # Source was deducted but destination failed — partial transfer / stock loss
        # Attempt to roll back the source deduction
        rollback_msg = ""
        try:
            await from_client.update_item_stock(source_item["id"], current_stock)
            rollback_msg = " (source rollback succeeded)"
        except Exception as rb_err:
            rollback_msg = f" (source rollback also failed: {rb_err})"

        try:
            await db.execute(
                """INSERT INTO transfer_history
                   (transfer_group_id, sku, item_name, quantity, from_location_id, from_location_name,
                    to_location_id, to_location_name, status, error_message,
                    from_stock_before, from_stock_after, to_stock_before, transferred_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'partial', ?, ?, ?, ?, ?)""",
                (transfer_group_id, req.sku, item_name, req.quantity,
                 req.from_location_id, from_name, req.to_location_id, to_name,
                 f"Destination add failed: {e}{rollback_msg}",
                 current_stock, new_from_stock, dest_stock, transferred_by),
            )
            await db.commit()
        except Exception as log_err:
            print(f"[transfer] Failed to log transfer history: {log_err}")
        raise HTTPException(
            status_code=500,
            detail=f"Transfer partially failed: source deducted but destination add failed: {str(e)}{rollback_msg}"
        )

    # Both calls succeeded — log full success
    try:
        await db.execute(
            """INSERT INTO transfer_history
               (transfer_group_id, sku, item_name, quantity, from_location_id, from_location_name,
                to_location_id, to_location_name, status,
                from_stock_before, from_stock_after, to_stock_before, to_stock_after, transferred_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'success', ?, ?, ?, ?, ?)""",
            (transfer_group_id, req.sku, item_name, req.quantity,
             req.from_location_id, from_name, req.to_location_id, to_name,
             current_stock, new_from_stock, dest_stock, new_to_stock, transferred_by),
        )
        await db.commit()
    except Exception as log_err:
        print(f"[transfer] Failed to log transfer history: {log_err}")

    result = {
        "status": "transferred",
        "sku": req.sku,
        "item_name": item_name,
        "quantity": req.quantity,
        "from_location": from_name,
        "to_location": to_name,
        "from_stock_before": current_stock,
        "from_stock_after": new_from_stock,
        "to_stock_before": dest_stock,
        "to_stock_after": new_to_stock,
        "transfer_group_id": transfer_group_id,
    }
    await _invalidate_cache()
    return result


@router.get("/transfer-history")
async def get_transfer_history(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """Return transfer history grouped by transfer_group_id, newest first."""
    # Get distinct transfer groups
    cursor = await db.execute(
        """SELECT DISTINCT transfer_group_id,
                  MIN(created_at) as started_at,
                  MIN(from_location_name) as from_location,
                  MIN(to_location_name) as to_location,
                  MIN(transferred_by) as transferred_by,
                  COUNT(*) as item_count,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_count
           FROM transfer_history
           GROUP BY transfer_group_id
           ORDER BY started_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )
    groups = await cursor.fetchall()

    # Get total count
    count_cursor = await db.execute(
        "SELECT COUNT(DISTINCT transfer_group_id) FROM transfer_history"
    )
    total = (await count_cursor.fetchone())[0]

    result = []
    for g in groups:
        group_id = g[0]
        # Get individual items for this transfer group
        items_cursor = await db.execute(
            """SELECT sku, item_name, quantity, status, error_message,
                      from_stock_before, from_stock_after, to_stock_before, to_stock_after,
                      created_at
               FROM transfer_history
               WHERE transfer_group_id = ?
               ORDER BY created_at ASC""",
            (group_id,),
        )
        items = await items_cursor.fetchall()
        result.append({
            "transfer_group_id": group_id,
            "created_at": g[1],
            "from_location": g[2],
            "to_location": g[3],
            "transferred_by": g[4],
            "item_count": g[5],
            "success_count": g[6],
            "failed_count": g[7],
            "items": [{
                "sku": item[0],
                "item_name": item[1],
                "quantity": item[2],
                "status": item[3],
                "error_message": item[4],
                "from_stock_before": item[5],
                "from_stock_after": item[6],
                "to_stock_before": item[7],
                "to_stock_after": item[8],
                "created_at": item[9],
            } for item in items],
        })

    return {"transfers": result, "total": total}


def _remove_white_background(image_bytes: bytes, threshold: int = 240, edge_softness: int = 20) -> tuple[bytes, str]:
    """Remove white/near-white backgrounds (transparent) and replace dark/black backgrounds with white."""
    img = PILImage.open(io.BytesIO(image_bytes)).convert("RGBA")
    pixels = img.load()
    width, height = img.size

    dark_threshold = 35
    dark_edge_softness = 25

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            # Remove white/near-white pixels (make transparent)
            if r > threshold and g > threshold and b > threshold:
                pixels[x, y] = (255, 255, 255, 0)
            elif r > (threshold - edge_softness) and g > (threshold - edge_softness) and b > (threshold - edge_softness):
                min_c = min(r, g, b)
                new_alpha = int(255 * (1 - (min_c - (threshold - edge_softness)) / edge_softness))
                pixels[x, y] = (r, g, b, min(a, max(0, new_alpha)))
            # Replace black/near-black pixels with white
            elif r < dark_threshold and g < dark_threshold and b < dark_threshold:
                pixels[x, y] = (255, 255, 255, 255)
            elif r < (dark_threshold + dark_edge_softness) and g < (dark_threshold + dark_edge_softness) and b < (dark_threshold + dark_edge_softness):
                max_c = max(r, g, b)
                blend = max_c / (dark_threshold + dark_edge_softness)
                new_r = int(r * blend + 255 * (1 - blend))
                new_g = int(g * blend + 255 * (1 - blend))
                new_b = int(b * blend + 255 * (1 - blend))
                pixels[x, y] = (new_r, new_g, new_b, a)

    # Flatten to white background and save as JPEG for smaller size
    background = PILImage.new("RGBA", img.size, (255, 255, 255, 255))
    background.paste(img, mask=img.split()[3])
    rgb_img = background.convert("RGB")
    buf = io.BytesIO()
    rgb_img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def _whiten_background(
    image_bytes: bytes, dark_thr: int = 60, min_fg: float = 0.12, mask_dim: int = 480
) -> tuple[bytes, str]:
    """Replace a dark studio background with white while preserving the subject.

    Uses an edge flood-fill so only dark pixels connected to the image border are
    whitened — a light/colorful subject in the center is kept intact. If the
    remaining foreground is tiny (a dark subject on a dark background, e.g. a
    black vape), the image is left untouched to avoid erasing the product.
    Returns the original bytes unchanged when there is nothing safe to do.
    """
    img = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
    W, H = img.size
    scale = min(1.0, mask_dim / max(W, H))
    sw, sh = max(1, int(W * scale)), max(1, int(H * scale))
    gpx = img.resize((sw, sh), PILImage.BILINEAR).convert("L").load()

    visited = bytearray(sw * sh)
    dq: deque = deque()

    def _dark(x: int, y: int) -> bool:
        return gpx[x, y] < dark_thr

    for x in range(sw):
        for y in (0, sh - 1):
            if not visited[y * sw + x] and _dark(x, y):
                visited[y * sw + x] = 1
                dq.append((x, y))
    for y in range(sh):
        for x in (0, sw - 1):
            if not visited[y * sw + x] and _dark(x, y):
                visited[y * sw + x] = 1
                dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < sw and 0 <= ny < sh and not visited[ny * sw + nx] and _dark(nx, ny):
                visited[ny * sw + nx] = 1
                dq.append((nx, ny))

    bg_count = sum(visited)
    fg_frac = 1 - bg_count / (sw * sh)
    # Nothing to whiten, or subject indistinguishable from a dark background.
    if bg_count == 0 or fg_frac < min_fg:
        return image_bytes, "image/jpeg"

    mask = PILImage.frombytes(
        "L", (sw, sh), bytes(255 if v else 0 for v in visited)
    ).resize((W, H), PILImage.BILINEAR)
    img.paste((255, 255, 255), (0, 0), mask)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


class BulkImageAssignRequest(BaseModel):
    keyword: str  # e.g., "gummies"
    image_data: str  # base64 encoded image
    content_type: str = "image/png"
    remove_bg: bool = False  # whether to remove white background
    skus: list[str] | None = None  # optional: only assign to these specific SKUs


@router.post("/bulk-assign-images")
async def bulk_assign_images(
    req: BulkImageAssignRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Assign the same image to all products whose name contains the keyword.
    For example, keyword='gummies' assigns the image to all gummy products."""
    if not req.keyword or len(req.keyword) < 2:
        raise HTTPException(status_code=400, detail="Keyword must be at least 2 characters")

    # Validate image
    try:
        decoded = base64.b64decode(req.image_data)
        if len(decoded) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large (max 10MB)")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {e}")

    # Remove white background if requested
    final_image_data = req.image_data
    final_content_type = req.content_type
    if req.remove_bg:
        try:
            processed_bytes, final_content_type = _remove_white_background(decoded)
            final_image_data = base64.b64encode(processed_bytes).decode("utf-8")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to remove background: {e}")

    # Get all items from all locations to find matching products
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    matching_skus: dict[str, str] = {}  # sku -> product name
    keyword_lower = req.keyword.lower()

    for loc in locations:
        try:
            client = CloverClient(loc[2], loc[3])
            data = await client.get_items()
            for item in data.get("elements", []):
                name = item.get("name", "")
                sku = item.get("sku") or item.get("id", "")
                if keyword_lower in name.lower() and sku not in matching_skus:
                    matching_skus[sku] = name
        except Exception:
            continue

    if not matching_skus:
        return {"status": "no_matches", "keyword": req.keyword, "assigned": 0, "products": []}

    # If specific SKUs provided, filter to only those
    if req.skus is not None:
        filtered = {sku: name for sku, name in matching_skus.items() if sku in req.skus}
        matching_skus = filtered
        if not matching_skus:
            return {"status": "no_matches", "keyword": req.keyword, "assigned": 0, "products": []}

    # Assign image to selected products
    assigned = 0
    skipped = 0
    for sku, product_name in matching_skus.items():
        try:
            await db.execute(
                """INSERT INTO product_images (sku, image_data, content_type, product_name, updated_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(sku) DO UPDATE SET
                     image_data = excluded.image_data,
                     content_type = excluded.content_type,
                     product_name = COALESCE(excluded.product_name, product_images.product_name),
                     updated_at = CURRENT_TIMESTAMP""",
                (sku, final_image_data, final_content_type, product_name),
            )
            assigned += 1
        except Exception:
            skipped += 1

    await db.commit()

    # Update cache in-place to reflect new images without full re-sync
    if assigned > 0:
        assigned_skus = set(matching_skus.keys())
        # Clear processed image cache for all assigned SKUs so fresh images are served
        for sku in assigned_skus:
            for key in [k for k in _image_cache if k[0] == sku]:
                del _image_cache[key]
        async with _cache_lock:
            for item in _inventory_cache.get("items", []):
                if item["sku"] in assigned_skus:
                    item["has_image"] = True
        # Invalidate e-commerce product cache so website picks up new image URLs
        invalidate_product_cache()

    return {
        "status": "done",
        "keyword": req.keyword,
        "assigned": assigned,
        "skipped": skipped,
        "products": [{"sku": sku, "name": name} for sku, name in matching_skus.items()],
    }


# === Item Groups / Variants ===


class VariantOption(BaseModel):
    attribute_name: str  # e.g., "Size", "Color", "Flavor"
    option_names: list[str]  # e.g., ["Small", "Medium", "Large"]


class ItemGroupCreate(BaseModel):
    name: str  # Item group name (e.g., "CBD Gummies")
    price: int  # Base price in cents
    sku_prefix: Optional[str] = None
    category: Optional[str] = None
    variants: list[VariantOption]  # Attributes with their options
    # Optional fields same as regular items
    price_type: Optional[str] = "FIXED"
    cost: Optional[int] = None
    description: Optional[str] = None
    is_revenue: Optional[bool] = True
    is_age_restricted: Optional[bool] = False
    age_restriction_type: Optional[str] = None
    age_restriction_min_age: Optional[int] = None
    available: Optional[bool] = True
    hidden: Optional[bool] = False
    auto_manage: Optional[bool] = False  # disabled by default – Clover auto-hides items at 0 stock, blocking POS scanning
    default_tax_rates: Optional[bool] = True


class ItemGroupRename(BaseModel):
    current_name: str
    new_name: str


# LeafLife retail flower is tagged with these pricing tiers in the group name.
# We never rename LeafLife products, so groups ending in one of these are skipped
# (belt-and-suspenders alongside the LF- SKU guard).
_LEAFLIFE_TIER_WORDS = ("EVERYDAY", "PREMIUM", "ESSENTIAL")


def _is_leaflife_group(name: str, variant_skus: list[str]) -> bool:
    """A group is LeafLife if any variant SKU is LF- or its name ends in a tier word."""
    if any((s or "").upper().startswith("LF-") for s in variant_skus):
        return True
    up = " ".join((name or "").upper().split())
    return any(up.endswith(" " + t) or up == t for t in _LEAFLIFE_TIER_WORDS)


@router.post("/item-groups/rename")
async def rename_item_group(
    req: ItemGroupRename,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Rename a variant group across all locations.

    Clover forbids editing the name of an item that belongs to a group; the
    supported path is to rename the *group*, which regenerates every variant
    item's name (group name + size option). LeafLife (LF-) groups are refused.
    Returns each location's resulting variant item names so the cascade can be
    verified.
    """
    current = " ".join((req.current_name or "").split())
    new_name = " ".join((req.new_name or "").split())
    if not current or not new_name:
        raise HTTPException(status_code=400, detail="current_name and new_name are required")
    if current == new_name:
        raise HTTPException(status_code=400, detail="New name is the same as the current name")

    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    norm_current = _normalise_name(current)
    results: list[dict] = []
    renamed_any = False

    for loc in locations:
        loc_name, merchant_id, api_token = loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_item_groups()
            groups = data.get("elements", [])
            # Clover often holds several groups with the same name (stale
            # duplicates + the active one); rename every match so the live
            # group is always covered.
            matches = [
                g for g in groups
                if _normalise_name(g.get("name", "")) == norm_current
            ]
            if not matches:
                results.append({"location": loc_name, "status": "not_found"})
                continue

            loc_item_names: list[str] = []
            loc_renamed = 0
            loc_skipped_ll = False
            for match in matches:
                variant_skus = [
                    it.get("sku", "")
                    for it in match.get("items", {}).get("elements", [])
                ]
                if _is_leaflife_group(match.get("name", ""), variant_skus):
                    loc_skipped_ll = True
                    continue
                await client.update_item_group(match.get("id", ""), new_name)
                # Re-fetch to confirm Clover cascaded the rename to the items.
                refreshed = await client.get_item_group(match.get("id", ""))
                loc_item_names.extend(
                    " ".join((it.get("name", "") or "").split())
                    for it in refreshed.get("items", {}).get("elements", [])
                )
                loc_renamed += 1

            if loc_renamed:
                renamed_any = True
                results.append({
                    "location": loc_name,
                    "status": "renamed",
                    "group_name": new_name,
                    "groups_renamed": loc_renamed,
                    "item_names": loc_item_names,
                })
            elif loc_skipped_ll:
                results.append({"location": loc_name, "status": "skipped_leaflife"})
            else:
                results.append({"location": loc_name, "status": "not_found"})
        except httpx.HTTPStatusError as e:
            error_detail = str(e)
            try:
                error_detail = e.response.json().get("message", str(e))
            except Exception:
                pass
            results.append({"location": loc_name, "status": "error", "error": error_detail})
        except Exception as e:
            results.append({"location": loc_name, "status": "error", "error": str(e)})

    if any(r["status"] == "skipped_leaflife" for r in results) and not renamed_any:
        raise HTTPException(
            status_code=400,
            detail="This is a LeafLife product and cannot be renamed here.",
        )

    return {"new_name": new_name, "results": results}


@router.get("/item-groups")
async def get_item_groups(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all item groups (items with variants) from all locations."""
    locations = await _get_locations(db)
    if not locations:
        return {"item_groups": []}

    all_groups: dict[str, dict] = {}

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_item_groups()
            groups = data.get("elements", [])
        except Exception as e:
            print(f"Error getting item groups from {loc_name}: {e}")
            continue

        for group in groups:
            group_id = group.get("id", "")
            group_name = group.get("name", "")
            if group_name not in all_groups:
                all_groups[group_name] = {
                    "name": group_name,
                    "clover_ids": {},
                    "attributes": [],
                    "items": [],
                }
            all_groups[group_name]["clover_ids"][loc_name] = group_id

            # Parse attributes and options
            attrs = group.get("attributes", {}).get("elements", [])
            if attrs and not all_groups[group_name]["attributes"]:
                for attr in attrs:
                    attr_data = {
                        "id": attr.get("id", ""),
                        "name": attr.get("name", ""),
                        "options": [],
                    }
                    options = attr.get("options", {}).get("elements", [])
                    for opt in options:
                        attr_data["options"].append({
                            "id": opt.get("id", ""),
                            "name": opt.get("name", ""),
                        })
                    all_groups[group_name]["attributes"].append(attr_data)

            # Parse variant items
            items = group.get("items", {}).get("elements", [])
            existing_item_names = {i["name"] for i in all_groups[group_name]["items"]}
            for item in items:
                item_name = item.get("name", "")
                if item_name not in existing_item_names:
                    all_groups[group_name]["items"].append({
                        "id": item.get("id", ""),
                        "name": item_name,
                        "sku": item.get("sku", ""),
                        "price": item.get("price", 0),
                    })
                    existing_item_names.add(item_name)

    return {"item_groups": list(all_groups.values())}


@router.post("/item-groups")
async def create_item_group(
    req: ItemGroupCreate,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create an item group with variants across all locations.

    Flow per Clover API:
    1. Create item group
    2. Create attributes (Size, Color, etc.) linked to item group
    3. Create options for each attribute (Small, Medium, Large, etc.)
    4. Generate all option combinations (cartesian product)
    5. Create individual items with itemGroup.id set
    6. Associate options with each item
    """
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    # Merge duplicate attribute names (case-insensitive): combine options from attributes with the same name.
    # This prevents accidental cartesian explosion (e.g. two "Size" attributes with 3 opts each → 9 combos).
    merged_variants: dict[str, list[str]] = {}  # keyed by lowercase name
    merged_display_names: dict[str, str] = {}  # lowercase → first-seen original-case name
    for v in req.variants:
        attr_name = v.attribute_name.strip()
        if not attr_name:
            continue
        key = attr_name.lower()
        if key not in merged_variants:
            merged_variants[key] = []
            merged_display_names[key] = attr_name  # preserve first-seen casing for Clover
        for o in v.option_names:
            o_stripped = o.strip()
            if o_stripped and o_stripped not in merged_variants[key]:
                merged_variants[key].append(o_stripped)
    merged_variant_list = [VariantOption(attribute_name=merged_display_names[k], option_names=v) for k, v in merged_variants.items() if v]
    if not merged_variant_list:
        raise HTTPException(status_code=400, detail="At least one attribute with options is required for variants.")

    total_combos = 1
    for v in merged_variant_list:
        total_combos *= len(v.option_names)
    print(f"[create-item-group] name={req.name!r}, {len(merged_variant_list)} attribute(s), {total_combos} combo(s)")

    results = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Step 1: Create the item group
            group = await client.create_item_group(req.name)
            group_id = group["id"]

            # Step 2 & 3: Create attributes and their options (using merged/deduped variants)
            attribute_options: list[list[dict]] = []  # list of lists of option dicts
            for variant in merged_variant_list:
                attr = await client.create_attribute(variant.attribute_name, group_id)
                attr_id = attr["id"]

                options_for_attr: list[dict] = []
                for opt_name in variant.option_names:
                    opt = await client.create_option(attr_id, opt_name)
                    options_for_attr.append({"id": opt["id"], "name": opt_name, "attr_name": variant.attribute_name})
                attribute_options.append(options_for_attr)

            # Step 4: Generate cartesian product of all options
            option_combos = list(itertools.product(*attribute_options))

            # Step 5 & 6: Create items for each combination and associate options
            created_items = []
            for combo in option_combos:
                # Build item data
                item_data: dict = {
                    "name": req.name,  # Clover auto-generates the full name from group + options
                    "price": req.price,
                    "itemGroup": {"id": group_id},
                }
                if req.sku_prefix:
                    # Generate SKU from prefix + option names
                    option_suffix = "-".join(o["name"][:3].upper() for o in combo)
                    item_data["sku"] = f"{req.sku_prefix}-{option_suffix}"
                if req.price_type:
                    item_data["priceType"] = req.price_type
                if req.cost is not None:
                    item_data["cost"] = req.cost
                if req.description:
                    item_data["description"] = req.description
                item_data["isRevenue"] = req.is_revenue
                item_data["hidden"] = req.hidden
                item_data["autoManage"] = req.auto_manage
                item_data["available"] = req.available
                item_data["defaultTaxRates"] = req.default_tax_rates

                # Handle age restriction
                if req.is_age_restricted and req.age_restriction_type:
                    item_data["isAgeRestricted"] = True
                    age_obj = await _get_age_restriction_obj(
                        client, req.age_restriction_type,
                        req.age_restriction_min_age or 21
                    )
                    if age_obj:
                        item_data["ageRestrictedObj"] = age_obj
                else:
                    item_data["isAgeRestricted"] = False

                # Create the variant item
                created_item = await client.create_item(item_data)
                item_id = created_item.get("id", "")

                # Associate options with this item
                for opt in combo:
                    await client.associate_option_with_item(opt["id"], item_id)

                combo_desc = " / ".join(o["name"] for o in combo)
                created_items.append({
                    "item_id": item_id,
                    "variant": combo_desc,
                    "name": created_item.get("name", ""),
                })

            # Assign category to all variant items if provided
            if req.category:
                try:
                    cats = await client.get_categories()
                    existing = [c for c in cats.get("elements", []) if c.get("name") == req.category]
                    if existing:
                        cat_id = existing[0]["id"]
                    else:
                        new_cat = await client.create_category(req.category)
                        cat_id = new_cat["id"]
                    for ci in created_items:
                        await client.assign_category(ci["item_id"], cat_id)
                except Exception as cat_err:
                    print(f"Error assigning category at {loc_name}: {cat_err}")

            results.append({
                "location": loc_name,
                "status": "created",
                "group_id": group_id,
                "items_created": len(created_items),
                "items": created_items,
            })

        except Exception as e:
            error_detail = str(e)
            try:
                if hasattr(e, "response"):
                    error_body = e.response.json()
                    error_detail = error_body.get("message", str(e))
            except Exception:
                pass
            results.append({
                "location": loc_name,
                "status": "error",
                "error": error_detail,
            })

    return {"results": results}


class AddVariantsToItem(BaseModel):
    item_name: str  # Name of the existing item
    item_sku: Optional[str] = None  # SKU of the existing item (for lookup)
    price: int  # Price in cents for the new variant items
    sku_prefix: Optional[str] = None
    variants: list[VariantOption]  # Attributes with their options
    keep_original: Optional[bool] = False  # Keep the original non-variant item


@router.post("/add-variants")
async def add_variants_to_existing_item(
    req: AddVariantsToItem,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Add variants to an existing item by creating an item group and variant items.

    Flow:
    1. Find the existing item by name/SKU at each location
    2. Create an item group with the item's name
    3. Create attributes and options
    4. Generate variant combinations (cartesian product)
    5. Create new variant items linked to the group
    6. Associate options with each variant item
    7. Optionally delete the original item (if keep_original is False)
    """
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    if not req.variants or not any(v.attribute_name.strip() and any(o.strip() for o in v.option_names) for v in req.variants):
        raise HTTPException(status_code=400, detail="At least one attribute with options is required")

    # Merge duplicate attribute names (case-insensitive): combine options from attributes with the same name
    merged_variants: dict[str, list[str]] = {}  # keyed by lowercase name
    merged_display_names: dict[str, str] = {}  # lowercase → first-seen original-case name
    for v in req.variants:
        attr_name = v.attribute_name.strip()
        if not attr_name:
            continue
        key = attr_name.lower()
        if key not in merged_variants:
            merged_variants[key] = []
            merged_display_names[key] = attr_name  # preserve first-seen casing for Clover
        for o in v.option_names:
            o_stripped = o.strip()
            if o_stripped and o_stripped not in merged_variants[key]:
                merged_variants[key].append(o_stripped)
    # Replace req.variants with merged version for processing
    merged_variant_list = [VariantOption(attribute_name=merged_display_names[k], option_names=v) for k, v in merged_variants.items() if v]

    # Derive a clean base name by stripping any option values from the end of item_name.
    # e.g. "Lemon Cherry Gelato Smalls 28 Grams" with option "28 Grams" → "Lemon Cherry Gelato Smalls"
    all_option_names = [o for opts in merged_variants.values() for o in opts]
    base_name = req.item_name.strip()
    for opt in sorted(all_option_names, key=len, reverse=True):
        if base_name.lower().endswith(opt.lower()):
            base_name = base_name[: len(base_name) - len(opt)].strip()
            break  # Only strip one trailing option
    if not base_name:
        base_name = req.item_name.strip()  # Safety: never use an empty name
    print(f"[add-variants] Original name: {req.item_name!r}, base name: {base_name!r}, options: {all_option_names}")

    results = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Find the existing item
            existing_item_id = None
            category_ids = []
            items_data = await client.get_items()
            for item in items_data.get("elements", []):
                if req.item_sku and item.get("sku") == req.item_sku:
                    existing_item_id = item.get("id")
                    # Get categories from existing item
                    cats = item.get("categories", {}).get("elements", [])
                    category_ids = [c.get("id") for c in cats if c.get("id")]
                    break
                if item.get("name") == req.item_name:
                    existing_item_id = item.get("id")
                    cats = item.get("categories", {}).get("elements", [])
                    category_ids = [c.get("id") for c in cats if c.get("id")]
                    break

            # Step 1: Create the item group using the CLEAN base name (no size suffix)
            group = await client.create_item_group(base_name)
            group_id = group["id"]

            # Step 2 & 3: Create attributes and their options (using merged/deduped variants)
            attribute_options: list[list[dict]] = []
            for variant in merged_variant_list:
                attr = await client.create_attribute(variant.attribute_name, group_id)
                attr_id = attr["id"]

                options_for_attr: list[dict] = []
                for opt_name in variant.option_names:
                    opt = await client.create_option(attr_id, opt_name)
                    options_for_attr.append({"id": opt["id"], "name": opt_name, "attr_name": variant.attribute_name})
                attribute_options.append(options_for_attr)

            if not attribute_options:
                results.append({"location": loc_name, "status": "error", "error": "No valid attributes/options provided"})
                continue

            # Step 4: Generate cartesian product of all options
            option_combos = list(itertools.product(*attribute_options))

            # Step 5 & 6: Create items for each combination
            created_items = []
            for combo in option_combos:
                item_data: dict = {
                    "name": base_name,
                    "price": req.price,
                    "itemGroup": {"id": group_id},
                }
                if req.sku_prefix:
                    option_suffix = "-".join(o["name"][:3].upper() for o in combo)
                    item_data["sku"] = f"{req.sku_prefix}-{option_suffix}"
                # Ensure variant items are always scannable at POS
                item_data["autoManage"] = False
                item_data["available"] = True
                item_data["hidden"] = False

                created_item = await client.create_item(item_data)
                item_id = created_item.get("id", "")

                for opt in combo:
                    await client.associate_option_with_item(opt["id"], item_id)

                # Assign same categories as original item
                for cat_id in category_ids:
                    try:
                        await client.assign_category(item_id, cat_id)
                    except Exception:
                        pass

                combo_desc = " / ".join(o["name"] for o in combo)
                created_items.append({
                    "item_id": item_id,
                    "variant": combo_desc,
                    "name": created_item.get("name", ""),
                })

            # Delete original non-variant item if requested (always delete — it's replaced by variants)
            if not req.keep_original and existing_item_id:
                try:
                    await client.delete_item(existing_item_id)
                except Exception as del_err:
                    print(f"Warning: Could not delete original item at {loc_name}: {del_err}")

            results.append({
                "location": loc_name,
                "status": "created",
                "group_id": group_id,
                "items_created": len(created_items),
                "items": created_items,
                "original_deleted": not req.keep_original and existing_item_id is not None,
            })

        except Exception as e:
            error_detail = str(e)
            try:
                if hasattr(e, "response"):
                    error_body = e.response.json()
                    error_detail = error_body.get("message", str(e))
            except Exception:
                pass
            results.append({"location": loc_name, "status": "error", "error": error_detail})

    return {"results": results}


@router.get("/attributes")
async def get_attributes(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all existing attributes (Size, Color, Flavor, etc.) from the first location."""
    locations = await _get_locations(db)
    if not locations:
        return {"attributes": []}

    # Get attributes from the first location (they should be consistent)
    loc = locations[0]
    merchant_id, api_token = loc[2], loc[3]
    try:
        client = CloverClient(merchant_id, api_token)
        data = await client.get_attributes()
        attrs = data.get("elements", [])
        result = []
        for attr in attrs:
            options = attr.get("options", {}).get("elements", [])
            result.append({
                "id": attr.get("id", ""),
                "name": attr.get("name", ""),
                "options": [{"id": o.get("id", ""), "name": o.get("name", "")} for o in options],
            })
        return {"attributes": result}
    except Exception as e:
        print(f"Error getting attributes: {e}")
        return {"attributes": []}


@router.get("/changes")
async def get_inventory_changes(
    sku: str = "",
    location: str = "",
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get inventory change history. Filter by SKU and/or location."""
    query = "SELECT * FROM inventory_changes WHERE 1=1"
    params: list = []
    if sku:
        query += " AND sku = ?"
        params.append(sku)
    if location:
        query += " AND location_name = ?"
        params.append(location)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    changes = [dict(zip(cols, row)) for row in rows]

    # Also get total count
    count_query = "SELECT COUNT(*) FROM inventory_changes WHERE 1=1"
    count_params: list = []
    if sku:
        count_query += " AND sku = ?"
        count_params.append(sku)
    if location:
        count_query += " AND location_name = ?"
        count_params.append(location)
    cursor2 = await db.execute(count_query, count_params)
    total = (await cursor2.fetchone())[0]

    return {"changes": changes, "total": total}


# --------------- Smart PAR (sales-velocity reorder calculator) ---------------

_smart_par_cache: dict = {"data": None, "updated_at": 0}
_SMART_PAR_TTL = 3600  # 1 hour cache


async def _fetch_all_clover_orders(client: CloverClient) -> list[dict]:
    """Paginate through all paid Clover orders with lineItems expanded.

    Only paid orders are fetched (payType!=NULL) so open/unpaid tabs don't count
    as sales; deleted and refunded orders/line items are filtered out at the call
    site so velocity matches Clover's "Sold" figures.
    """
    all_orders: list[dict] = []
    offset = 0
    limit = 100
    while True:
        try:
            data = await client.get_orders(
                limit=limit,
                offset=offset,
                expand="lineItems",
                filters=["payType!=NULL"],
            )
        except Exception as e:
            print(f"Error fetching Clover orders at offset {offset}: {e}")
            break
        elements = data.get("elements", [])
        all_orders.extend(elements)
        if len(elements) < limit:
            break
        offset += limit
        await asyncio.sleep(0.3)
    return all_orders


def _normalise_name(name: str) -> str:
    """Lowercase, collapse whitespace, strip for fuzzy matching."""
    return " ".join(name.lower().split())


_STRAIN_TYPE_WORDS = frozenset({"indica", "sativa", "hybrid"})


def _normalise_sales_name(name: str) -> str:
    """Normalise for sales-velocity matching, ignoring the strain-type label.

    The strain type (indica/sativa/hybrid) is a display label added to product
    titles; it is not present in older sales records. Dropping it here keeps a
    product connected to its historical sales even after its title is renamed to
    include the type (e.g. "THC FLOWER TAHOE OG 3.5 GRAMS" and
    "THC FLOWER TAHOE OG INDICA 3.5 GRAMS" match to the same sales bucket).
    """
    return " ".join(
        w for w in name.lower().split() if w not in _STRAIN_TYPE_WORDS
    )


def _order_group_cannabinoid(up: str) -> str:
    """Derive the cannabinoid family from an upper-cased product name."""
    if up.startswith("DELTA 8"):
        return "Delta 8 THC"
    if up.startswith("DELTA 9"):
        return "Delta 9 THC"
    if "CBD/CBG/CBN" in up:
        return "CBD/CBG/CBN"
    if "CBG/CBD" in up or "CBD/CBG" in up:
        return "CBD/CBG"
    if up.startswith("CBN"):
        return "CBN"
    if up.startswith("CBG"):
        return "CBG"
    if up.startswith("CBD"):
        return "CBD"
    return "THC"


_FLOWER_KEYWORDS = (
    "FLOWER", "SNOW CAPS", "SNOWCAPS", "MOON ROCK", "MOONROCK",
    "PRE ROLLED", "PRE-ROLL", "PRE ROLL", "SHAKE",
)

# Categories that should not use the flower/vape name-keyword matching (a
# "glass pipe flower" accessory must not be treated as flower). They still get
# a catch-all order group based on their category.
_ORDER_GROUP_SKIP_CATEGORIES = {"Accessories", "Packaging", "Apparel", "Pets"}

# Products excluded from order totals entirely (promo/loss-leader dabs).
_ORDER_GROUP_EXCLUDE_KEYWORDS = ("PROMOTIONAL DAB", "$5 DAB")

# Concentrate detection for items outside the Concentrates category (some wax
# products carry no category in Clover).
_CONCENTRATE_KEYWORDS = (
    "WAX", "ROSIN", "RESIN", "BADDER", "BATTER", "SHATTER",
    "DIAMONDS", "DISTILLATE", "SYRINGE", "HASH", "ISOLATE",
)


def _concentrate_form(up: str) -> str:
    """Concentrate form (Wax, Rosin, Diamonds, ...) parsed from the name."""
    if "SYRINGE" in up:
        return "Distillate Syringe"
    if "LIVE RESIN" in up:
        return "Live Resin"
    if "ROSIN" in up:
        return "Rosin"
    if "RESIN" in up:
        return "Resin"
    if "BADDER" in up or "BATTER" in up:
        return "Badder"
    if "SHATTER" in up:
        return "Shatter"
    if "DIAMONDS" in up and "SAUCE" in up:
        return "Diamonds & Sauce"
    if "DIAMONDS" in up:
        return "Diamonds"
    if "SUGAR" in up:
        return "Sugar"
    if "HASH" in up:
        return "Hash"
    if "WAX" in up:
        return "Wax"
    if "ISOLATE" in up:
        return "Isolate"
    if "CAPSULE" in up:
        return "Capsules"
    if "DISTILLATE" in up:
        return "Distillate"
    return "Concentrate"


# Spelled-out gram sizes seen in vape names ("ONE GRAM", "TWO GRAMS").
_WORD_GRAMS = (
    ("HALF GRAM", 0.5),
    ("ONE GRAM", 1.0),
    ("TWO GRAM", 2.0),
    ("THREE GRAM", 3.0),
    ("FOUR GRAM", 4.0),
)


def _strain_from_name(up: str, strain_type: str) -> str:
    """Strain from the name (vapes carry it) falling back to the tagged type."""
    if "HYBRID" in up:
        return "Hybrid"
    if "SATIVA" in up:
        return "Sativa"
    if "INDICA" in up:
        return "Indica"
    st = (strain_type or "").strip().title()
    return st if st in ("Hybrid", "Sativa", "Indica") else "Unclassified"


def _vape_size(up: str) -> str:
    """Size label for a vape ("1g", "2g", ...) parsed from the name ('' if none)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*GRAMS?\b", up)
    grams = 0.0
    if m:
        try:
            grams = float(m.group(1))
        except ValueError:
            grams = 0.0
    else:
        for word, val in _WORD_GRAMS:
            if word in up:
                grams = val
                break
    return f"{grams:g}g" if grams > 0 else ""


def _order_group(name: str, categories: list[str], strain_type: str) -> tuple[str | None, str | None]:
    """Classify a product into the order-line the store reorders by.

    Gummies group by cannabinoid + strength (e.g. "Delta 9 THC Gummies 10mg").
    Flower groups by cannabinoid + form + strain type
    (e.g. "THC Flower Smalls — Indica"). Vapes group by cannabinoid + form +
    size + strain (e.g. "THC Disposable Vape 2g — Indica"). Everything else
    falls back to a catch-all group named after its Clover category so no
    product is hidden from the order totals.
    """
    up = " ".join((name or "").upper().split())
    is_skip = any(c in _ORDER_GROUP_SKIP_CATEGORIES for c in categories)

    if any(x in up for x in _ORDER_GROUP_EXCLUDE_KEYWORDS):
        return None, None

    if "GUMMIES" in up or "GUMMY" in up:
        strength_match = re.search(r"(\d+)\s*MG", up)
        strength = f"{strength_match.group(1)}mg" if strength_match else "Unspecified"
        return "Gummies", f"{_order_group_cannabinoid(up)} Gummies {strength}"

    if not is_skip and ("VAPE" in up or "Vapor" in categories):
        if "DISPOSABLE" in up:
            form = "Disposable Vape"
        elif "CARTRIDGE" in up or "CART" in up:
            form = "Vape Cartridge"
        else:
            form = "Vape"
        size = _vape_size(up)
        size_part = f" {size}" if size else ""
        strain = _strain_from_name(up, strain_type)
        return "Vape", f"{_order_group_cannabinoid(up)} {form}{size_part} \u2014 {strain}"

    if not is_skip and ("Concentrates" in categories or any(k in up for k in _CONCENTRATE_KEYWORDS)):
        form = _concentrate_form(up)
        size = _vape_size(up)
        size_part = f" {size}" if size else ""
        strain = _strain_from_name(up, strain_type)
        return "Concentrate", f"{_order_group_cannabinoid(up)} {form}{size_part} \u2014 {strain}"

    if not is_skip and ("Flower" in categories or any(k in up for k in _FLOWER_KEYWORDS)):
        if "SMALLS" in up:
            form = "Smalls"
        elif "TRIM" in up:
            form = "Trim"
        elif "GROUND" in up:
            form = "Ground"
        elif "SNOW CAPS" in up or "SNOWCAPS" in up:
            form = "Snow Caps"
        elif "MOON ROCK" in up or "MOONROCK" in up:
            form = "Moon Rock"
        elif "PRE ROLL" in up or "PRE-ROLL" in up:
            form = "Baby J Pre-Rolls" if "BABY J" in up else "Pre-Rolls"
        elif "SHAKE" in up:
            form = "Shake"
        elif "EXOTIC" in up:
            form = "Exotic"
        else:
            form = "Flower"
        strain = _strain_from_name(up, strain_type)
        return "Flower", f"{_order_group_cannabinoid(up)} {form} \u2014 {strain}"

    # No cannabinoid/piece rule for this category, so grouping a whole category
    # ("all Concentrates") into one line isn't actionable. Give each distinct
    # product its own order line; the same product across stores/online still
    # merges by name.
    label = " ".join((name or "").split())
    return "Other", label or "Uncategorized"


# Grams per pound used to roll flower reorder amounts up to pounds.
# The store buys flower at 448 g (16 oz) per pound.
_GRAMS_PER_POUND = 448.0


def _flower_grams(name: str) -> float:
    """Grams of flower in one package, parsed from the product name (0 if none)."""
    up = name.upper()
    mp = re.search(r"(\d+(?:\.\d+)?)\s*(?:POUNDS?|LBS?)\b", up)
    if mp:
        try:
            return float(mp.group(1)) * _GRAMS_PER_POUND
        except ValueError:
            return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*GRAMS?\b", up)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*G\b", up)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _pack_count(name: str) -> int:
    """Units (gummies/joints) in one package, parsed from the name (default 1)."""
    m = re.search(r"(\d+)\s*(?:COUNT|CT|PACK)\b", name.upper())
    if not m:
        return 1
    try:
        return max(int(m.group(1)), 1)
    except ValueError:
        return 1


def _order_unit_basis(kind: str, group_label: str, name: str) -> tuple[str, float]:
    """How a grouped item is counted for ordering.

    Returns (basis, per_package) where basis is:
        "weight" – flower buds, per_package = grams (rolled up to pounds)
        "count"  – gummies / pre-rolls, per_package = units per package
        "package" – fallback, per_package = 1
    """
    if kind == "Gummies":
        return "count", float(_pack_count(name))
    if kind in ("Vape", "Concentrate"):
        return "count", 1.0
    if kind == "Flower":
        if "Pre-Rolls" in group_label:
            return "count", float(_pack_count(name))
        grams = _flower_grams(name)
        if grams > 0:
            return "weight", grams
    return "package", 1.0


def _build_order_groups(results: list[dict]) -> list[dict]:
    """Roll grouped products up into order-line totals in their buying unit.

    Flower buds report the reorder amount in pounds (summed grams / lb),
    gummies and pre-rolls report total units (package count x units/package).
    """
    groups: dict[str, dict] = {}
    for r in results:
        label = r.get("group")
        if not label:
            continue
        kind = r.get("group_kind") or ""
        g = groups.get(label)
        if g is None:
            g = {
                "group": label,
                "kind": kind,
                "item_count": 0,
                "packages_sold": 0,
                "packages_in_stock": 0,
                "packages_par": 0,
                "packages_order_qty": 0,
                "grams_sold": 0.0,
                "grams_in_stock": 0.0,
                "grams_order": 0.0,
                "each_sold": 0,
                "each_in_stock": 0,
                "each_order": 0,
                "basis": "package",
            }
            groups[label] = g

        basis, per = _order_unit_basis(kind, label, r["name"])
        g["item_count"] += 1
        g["packages_sold"] += r["units_sold"]
        g["packages_in_stock"] += r["total_stock"]
        g["packages_par"] += r["par_level"]
        g["packages_order_qty"] += r["order_qty"]

        if basis == "weight":
            g["basis"] = "weight"
            g["grams_sold"] += r["units_sold"] * per
            g["grams_in_stock"] += r["total_stock"] * per
            g["grams_order"] += r["order_qty"] * per
        elif basis == "count":
            if g["basis"] != "weight":
                g["basis"] = "count"
            g["each_sold"] += int(round(r["units_sold"] * per))
            g["each_in_stock"] += int(round(r["total_stock"] * per))
            g["each_order"] += int(round(r["order_qty"] * per))

    out: list[dict] = []
    for g in groups.values():
        basis = g["basis"]
        if basis == "weight":
            unit = "lb"
            order_amount = round(g["grams_order"] / _GRAMS_PER_POUND, 2)
            sold_amount = round(g["grams_sold"] / _GRAMS_PER_POUND, 2)
            stock_amount = round(g["grams_in_stock"] / _GRAMS_PER_POUND, 2)
        elif basis == "count":
            if g["kind"] == "Flower":
                unit = "joints"
            elif g["kind"] == "Gummies":
                unit = "gummies"
            elif g["kind"] == "Vape":
                unit = "vapes"
            elif g["kind"] == "Concentrate":
                unit = "units"
            else:
                unit = "units"
            order_amount = g["each_order"]
            sold_amount = g["each_sold"]
            stock_amount = g["each_in_stock"]
        else:
            unit = "packages"
            order_amount = g["packages_order_qty"]
            sold_amount = g["packages_sold"]
            stock_amount = g["packages_in_stock"]

        out.append({
            "group": g["group"],
            "kind": g["kind"],
            "item_count": g["item_count"],
            "order_unit": unit,
            "order_amount": order_amount,
            "sold_amount": sold_amount,
            "stock_amount": stock_amount,
            "packages_sold": g["packages_sold"],
            "packages_in_stock": g["packages_in_stock"],
            "packages_par": g["packages_par"],
            "packages_order_qty": g["packages_order_qty"],
        })

    out.sort(key=lambda x: (x["kind"], -x["order_amount"]))
    return out


@router.get("/smart-par")
async def smart_par(
    months: int = 3,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Calculate recommended reorder quantities based on historical sales velocity.

    Query params:
        months – supply window (default 3).  Accepts 1-24.
    """
    months = max(1, min(months, 24))

    # ----- 1. Get current inventory (from cache or fresh sync) -----
    inv = await _do_sync(db)
    items_list = inv.get("items", [])

    # Build lookup: normalised name -> item info
    product_lookup: dict[str, dict] = {}
    for item in items_list:
        norm = _normalise_name(item["name"])
        total_stock = sum(
            loc_data.get("stock", 0)
            for loc_data in item.get("locations", {}).values()
        )
        product_lookup[norm] = {
            "name": item["name"],
            "sku": item["sku"],
            "categories": item.get("categories", []),
            "price": item.get("price", 0),
            "total_stock": total_stock,
            "locations": {
                loc_name: loc_data.get("stock", 0)
                for loc_name, loc_data in item.get("locations", {}).items()
            },
        }

    # ----- 2. Gather sales data (check cache first) -----
    now = time.time()
    if _smart_par_cache["data"] and (now - _smart_par_cache["updated_at"]) < _SMART_PAR_TTL:
        sales_by_product = _smart_par_cache["data"]["sales_by_product"]
        earliest_ts = _smart_par_cache["data"]["earliest_ts"]
        latest_ts = _smart_par_cache["data"]["latest_ts"]
    else:
        sales_by_product: dict[str, int] = {}  # normalised name -> total units
        earliest_ts = float("inf")
        latest_ts = 0.0

        # 2a. Clover POS orders (all locations)
        locations = await _get_locations(db)
        for loc in locations:
            merchant_id, api_token = loc[2], loc[3]
            client = CloverClient(merchant_id, api_token)
            orders = await _fetch_all_clover_orders(client)
            for order in orders:
                # Skip deleted orders and full refunds/voids so they don't count as sales
                if order.get("deletedTime") or order.get("isRefund"):
                    continue
                if order.get("total", 0) < 0:
                    continue
                order_ts = order.get("createdTime", 0) / 1000  # ms -> s
                if order_ts > 0:
                    earliest_ts = min(earliest_ts, order_ts)
                    latest_ts = max(latest_ts, order_ts)
                line_items = (order.get("lineItems") or {}).get("elements", [])
                for li in line_items:
                    # Skip refunded/returned line items to match Clover's "Sold" count
                    if li.get("refunded") or li.get("isRefund"):
                        continue
                    li_name = " ".join((li.get("name") or "").split())
                    if not li_name:
                        continue
                    raw_qty = li.get("unitQty", 1000)
                    qty = max(round(raw_qty / 1000), 1)
                    norm = _normalise_sales_name(li_name)
                    sales_by_product[norm] = sales_by_product.get(norm, 0) + qty

        # 2b. Ecommerce orders (website)
        cursor = await db.execute(
            """SELECT oi.product_name, oi.quantity, eo.created_at
               FROM ecommerce_order_items oi
               JOIN ecommerce_orders eo ON oi.order_id = eo.id
               WHERE eo.status NOT IN ('cancelled', 'refunded')"""
        )
        rows = await cursor.fetchall()
        for row in rows:
            p_name, qty, created_at = row[0], row[1], row[2]
            if not p_name:
                continue
            norm = _normalise_sales_name(p_name)
            sales_by_product[norm] = sales_by_product.get(norm, 0) + (qty or 1)
            # Parse created_at for date range
            if created_at:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    ts = dt.timestamp()
                    earliest_ts = min(earliest_ts, ts)
                    latest_ts = max(latest_ts, ts)
                except Exception:
                    pass

        _smart_par_cache["data"] = {
            "sales_by_product": sales_by_product,
            "earliest_ts": earliest_ts,
            "latest_ts": latest_ts,
        }
        _smart_par_cache["updated_at"] = now

    # ----- 3. Compute velocity & PAR -----
    if earliest_ts >= latest_ts or earliest_ts == float("inf"):
        days_of_data = 1
    else:
        days_of_data = max((latest_ts - earliest_ts) / 86400, 1)

    # Strain type (Sativa/Indica/Hybrid) per product, used to group flower.
    strain_by_sku: dict[str, str] = {}
    strain_by_name: dict[str, str] = {}
    attr_cursor = await db.execute(
        "SELECT sku, product_name, product_type FROM product_attributes"
    )
    for row in await attr_cursor.fetchall():
        attr_sku, attr_name, attr_type = row[0], row[1], row[2]
        if not attr_type:
            continue
        if attr_sku:
            strain_by_sku[attr_sku] = attr_type
        if attr_name:
            strain_by_name[attr_name.upper()] = attr_type

    results: list[dict] = []

    for item in items_list:
        # Exclude LeafLife products (SKU prefix "LF-") — they ship from the
        # partner and shouldn't drive our reorder recommendations.
        if (item.get("sku") or "").upper().startswith("LF-"):
            continue
        norm = _normalise_sales_name(item["name"])
        units_sold = sales_by_product.get(norm, 0)
        units_per_day = units_sold / days_of_data
        units_per_month = units_per_day * 30.44  # avg days/month
        par_level = round(units_per_month * months)

        total_stock = sum(
            loc_data.get("stock", 0)
            for loc_data in item.get("locations", {}).values()
        )

        stock_by_location = {
            loc_name: loc_data.get("stock", 0)
            for loc_name, loc_data in item.get("locations", {}).items()
        }

        strain = (
            strain_by_sku.get(item["sku"])
            or strain_by_name.get(item["name"].upper())
            or ""
        )
        group_kind, group_label = _order_group(
            item["name"], item.get("categories", []), strain
        )

        results.append({
            "name": item["name"],
            "sku": item["sku"],
            "categories": item.get("categories", []),
            "price": item.get("price", 0) / 100,  # cents -> dollars
            "total_stock": total_stock,
            "stock_by_location": stock_by_location,
            "units_sold": units_sold,
            "units_per_month": round(units_per_month, 1),
            "par_level": par_level,
            "order_qty": max(par_level - total_stock, 0),
            "group": group_label,
            "group_kind": group_kind,
        })

    groups = _build_order_groups(results)

    return {
        "products": results,
        "groups": groups,
        "meta": {
            "months": months,
            "days_of_data": round(days_of_data, 1),
            "safety_buffer": 1.0,
            "total_products": len(results),
            "total_units_sold": sum(r["units_sold"] for r in results),
        },
    }


class AutoSetParRequest(BaseModel):
    months: float = 1.0  # supply window each PAR level should cover


@router.post("/auto-set-par")
async def auto_set_par(
    req: AutoSetParRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Set every item's PAR level, per location, from that location's own sales velocity.

    For each Clover location we tally its paid, non-refunded POS sales by product
    name, derive units/day over the location's own order history, and store
    PAR = round(units_per_month * months) for each item at that location. Re-running
    recomputes from the latest sales, so as an item sells faster its PAR (and the
    reorder/production need it drives) rises automatically.

    LeafLife (LF-) items are skipped — they ship from the partner and don't sit on
    our shelves.
    """
    from datetime import datetime
    from app.routers.ecommerce_router import HQ_MERCHANT_ID

    months = max(0.25, min(req.months, 24))

    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=500, detail="No locations configured")

    par_rows: list[tuple[str, int, float]] = []  # (sku, location_id, par_level)
    per_location_summary: list[dict] = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        client = CloverClient(merchant_id, api_token)

        # 1. Tally this location's own sales by normalised product name.
        sales_by_name: dict[str, int] = {}
        earliest_ts = float("inf")
        latest_ts = 0.0
        try:
            orders = await _fetch_all_clover_orders(client)
        except Exception as e:
            print(f"[auto-set-par] order fetch failed for {loc_name}: {e}")
            orders = []
        for order in orders:
            if order.get("deletedTime") or order.get("isRefund"):
                continue
            if order.get("total", 0) < 0:
                continue
            order_ts = order.get("createdTime", 0) / 1000
            if order_ts > 0:
                earliest_ts = min(earliest_ts, order_ts)
                latest_ts = max(latest_ts, order_ts)
            for li in (order.get("lineItems") or {}).get("elements", []):
                if li.get("refunded") or li.get("isRefund"):
                    continue
                li_name = " ".join((li.get("name") or "").split())
                if not li_name:
                    continue
                qty = max(round(li.get("unitQty", 1000) / 1000), 1)
                norm = _normalise_sales_name(li_name)
                sales_by_name[norm] = sales_by_name.get(norm, 0) + qty

        # HQ is the e-commerce/warehouse location: its real per-product demand
        # lives in the website order tables. Clover records online sales as
        # generic "item 1" lines with no product name, so without this every HQ
        # item would get PAR 0. Fold the e-commerce sales in (same source Smart
        # PAR uses) so HQ PAR reflects actual online sales.
        if str(merchant_id) == str(HQ_MERCHANT_ID):
            ec_cursor = await db.execute(
                """SELECT oi.product_name, oi.quantity, eo.created_at
                   FROM ecommerce_order_items oi
                   JOIN ecommerce_orders eo ON oi.order_id = eo.id
                   WHERE eo.status NOT IN ('cancelled', 'refunded')"""
            )
            for p_name, qty, created_at in await ec_cursor.fetchall():
                if not p_name:
                    continue
                norm = _normalise_sales_name(" ".join(str(p_name).split()))
                sales_by_name[norm] = sales_by_name.get(norm, 0) + (qty or 1)
                if created_at:
                    try:
                        dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                        ts = dt.timestamp()
                        earliest_ts = min(earliest_ts, ts)
                        latest_ts = max(latest_ts, ts)
                    except (ValueError, TypeError):
                        pass

        if earliest_ts >= latest_ts or earliest_ts == float("inf"):
            days_of_data = 1.0
        else:
            days_of_data = max((latest_ts - earliest_ts) / 86400, 1.0)

        # 2. Compute PAR for each item currently at this location.
        try:
            data = await client.get_items(expand="itemStock")
            items = data.get("elements", [])
        except Exception as e:
            print(f"[auto-set-par] item fetch failed for {loc_name}: {e}")
            items = []

        loc_items = 0
        loc_with_par = 0
        for item in items:
            raw_sku = item.get("sku", "") or ""
            clover_id = item.get("id", "")
            if raw_sku.upper().startswith("LF-"):
                continue
            display_sku = raw_sku or clover_id
            if not display_sku:
                continue
            name = " ".join((item.get("name") or "").split())
            norm = _normalise_sales_name(name)
            units_sold = sales_by_name.get(norm, 0)
            units_per_month = (units_sold / days_of_data) * 30.44
            par_level = round(units_per_month * months)
            par_rows.append((display_sku, loc_id, float(par_level)))
            loc_items += 1
            if par_level > 0:
                loc_with_par += 1

        per_location_summary.append({
            "location": loc_name,
            "items": loc_items,
            "with_par": loc_with_par,
            "days_of_data": round(days_of_data, 1),
        })

    # 3. Persist. Upsert so re-running refreshes existing PAR levels.
    for sku, loc_id, par_level in par_rows:
        await db.execute(
            """INSERT INTO par_levels (sku, location_id, par_level, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(sku, location_id)
               DO UPDATE SET par_level = ?, updated_at = CURRENT_TIMESTAMP""",
            (sku, loc_id, par_level, par_level),
        )
    await db.commit()

    return {
        "message": f"Set PAR levels for {len(par_rows)} item/location pairs from {months}-month sales velocity",
        "months": months,
        "total_set": len(par_rows),
        "by_location": per_location_summary,
    }


# ── LeafLife Product Import ──────────────────────────────────────────────────

# Standard retail prices (in cents) by tier and weight
_LEAFLIFE_TIER_PRICES: dict[str, dict[str, int]] = {
    "Everyday": {"3.5": 2500, "7": 5500, "14": 9500, "28": 12000},
    "Premium": {"3.5": 3500, "7": 6500, "14": 12000, "28": 18000},
    "Snowcaps": {"3.5": 3500, "7": 6500, "14": 12000},
    "Smalls": {"3.5": 1500, "7": 3000, "14": 5500, "28": 8000},
}

# SKU suffix format per weight (matches existing Clover patterns)
_WEIGHT_SKU_SUFFIX: dict[str, str] = {
    "3.5": "3.5",
    "7": "7 G",
    "14": "14",
    "28": "28",
}


class LeafLifeStrain(BaseModel):
    strain_name: str        # e.g. "Blue Nerdz"
    tier: str               # Everyday, Premium, Snowcaps, Smalls
    inventory_grams: float  # total grams available from supplier
    weights: Optional[list[str]] = None  # override default sizes, e.g. ["3.5", "7", "14", "28"]


class LeafLifeImportRequest(BaseModel):
    strains: list[LeafLifeStrain]
    dry_run: bool = False  # if True, show what would be created without creating


@router.post("/leaflife-import")
async def leaflife_import(
    req: LeafLifeImportRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Batch-create LeafLife flower products in Clover HQ.

    For each strain, creates items for each weight size with proper SKU (LF-),
    pricing, stock, Flower category, and age restriction.
    Only creates at HQ location (LeafLife products ship from partner).
    """
    from app.routers.ecommerce_router import HQ_MERCHANT_ID, HQ_API_TOKEN, invalidate_product_cache

    if not HQ_MERCHANT_ID or not HQ_API_TOKEN:
        raise HTTPException(status_code=500, detail="HQ Clover credentials not configured")

    client = CloverClient(HQ_MERCHANT_ID, HQ_API_TOKEN)

    # Look up or cache the "Flower" category ID
    cats = await client.get_categories()
    flower_cat_id = None
    for c in cats.get("elements", []):
        if c.get("name", "").lower() == "flower":
            flower_cat_id = c["id"]
            break
    if not flower_cat_id:
        new_cat = await client.create_category("Flower")
        flower_cat_id = new_cat["id"]

    # Get age restriction obj
    age_obj = await _get_age_restriction_obj(client, "Vitamin & Supplements", 21)

    # Fetch existing LF- SKUs to avoid duplicates
    existing_items = await client.get_items(expand="itemStock")
    existing_skus: set[str] = set()
    for item in existing_items.get("elements", []):
        sku = item.get("sku", "") or ""
        if sku.startswith("LF-"):
            existing_skus.add(sku.upper())

    results: list[dict] = []

    for strain in req.strains:
        tier = strain.tier.strip().title()
        tier_prices = _LEAFLIFE_TIER_PRICES.get(tier)
        if not tier_prices:
            results.append({
                "strain": strain.strain_name,
                "status": "error",
                "error": f"Unknown tier '{strain.tier}'. Must be: Everyday, Premium, Snowcaps, or Smalls",
            })
            continue

        weights = strain.weights or list(tier_prices.keys())
        strain_upper = strain.strain_name.strip().upper()
        # Build SKU base: LF-{STRAIN}-  (replace spaces with hyphens)
        sku_base = "LF-" + re.sub(r"\s+", "-", strain_upper)

        created_items: list[dict] = []
        skipped_items: list[str] = []

        for weight in weights:
            weight_str = weight.strip()
            if weight_str not in tier_prices:
                skipped_items.append(f"{weight_str}g (no price for this size in {tier} tier)")
                continue

            sku_suffix = _WEIGHT_SKU_SUFFIX.get(weight_str, weight_str)
            sku = f"{sku_base}-{sku_suffix}"

            # Skip if already exists
            if sku.upper() in existing_skus:
                skipped_items.append(f"{weight_str}g (SKU {sku} already exists)")
                continue

            price = tier_prices[weight_str]
            weight_float = float(weight_str)
            stock_qty = int(strain.inventory_grams / weight_float) if weight_float > 0 else 0

            # Build item name: "{STRAIN} {TIER} {WEIGHT} GRAMS"
            weight_label = f"{weight_str} Gram{'s' if weight_float != 1 else ''}"
            item_name = f"{strain.strain_name.strip().upper()} {tier.upper()} {weight_label.upper()}"

            if req.dry_run:
                created_items.append({
                    "sku": sku,
                    "name": item_name,
                    "price": price,
                    "stock": stock_qty,
                    "dry_run": True,
                })
                continue

            # Create item in Clover HQ
            item_data: dict = {
                "name": item_name,
                "price": price,
                "sku": sku,
                "available": True,
                "hidden": False,
                "autoManage": False,
                "isRevenue": True,
                "defaultTaxRates": True,
                "isAgeRestricted": True,
            }
            if age_obj:
                item_data["ageRestrictedObj"] = age_obj

            try:
                created = await client.create_item(item_data)
                item_id = created.get("id", "")

                # Assign Flower category
                if flower_cat_id:
                    try:
                        await client.assign_category(item_id, flower_cat_id)
                    except Exception as cat_err:
                        print(f"[leaflife-import] Category assign failed for {sku}: {cat_err}")

                # Set stock
                if stock_qty > 0:
                    try:
                        await client.update_item_stock(item_id, stock_qty)
                    except Exception as stock_err:
                        print(f"[leaflife-import] Stock update failed for {sku}: {stock_err}")

                created_items.append({
                    "sku": sku,
                    "name": item_name,
                    "item_id": item_id,
                    "price": price,
                    "stock": stock_qty,
                })
                existing_skus.add(sku.upper())

                await asyncio.sleep(0.3)  # Rate limit

            except Exception as e:
                created_items.append({
                    "sku": sku,
                    "name": item_name,
                    "error": str(e),
                })

        results.append({
            "strain": strain.strain_name,
            "tier": tier,
            "inventory_grams": strain.inventory_grams,
            "created": [i for i in created_items if "error" not in i],
            "errors": [i for i in created_items if "error" in i],
            "skipped": skipped_items,
        })

    if not req.dry_run:
        # Invalidate caches so new products appear on the website
        invalidate_product_cache()
        await _invalidate_cache()

    total_created = sum(len(r.get("created", [])) for r in results)
    return {
        "status": "dry_run" if req.dry_run else "completed",
        "total_created": total_created,
        "results": results,
    }


# ── LeafLife Google Sheet Sync ───────────────────────────────────────────────
# Reads the LeafLife x THD partnership sheet directly (link-sharing enabled, no
# credentials needed) and reconciles LeafLife (LF-) products in Clover HQ:
# creates new strains, updates price/stock on existing ones, and removes strains
# that sold out or dropped off the sheet. Runs on a schedule and on demand.

LEAFLIFE_SHEET_ID = os.environ.get(
    "LEAFLIFE_SHEET_ID", "1gztJ_rdLf2EIbXWeRHu_GSexSJU1xObdXYKvkZ5TEV4"
)

# Per-tab config: which Clover category, and which sheet columns hold the retail
# price for each package weight (in grams). Column indexes match the sheet's
# gviz CSV header order.
_LEAFLIFE_TABS = [
    {
        "tab": "Retail Flower Menu",
        "category": "Flower",
        # weight (g) -> retail-price column index
        "price_cols": {"28": 12, "14": 13, "7": 14, "3.5": 15},
        "sku_suffix": {"28": "28", "14": "14", "7": "7 G", "3.5": "3.5"},
        # weight (g) -> minimum customer price in cents. Flat floor across all
        # grade tiers (SMALLS/EVERYDAY/PREMIUM) — higher tiers price above it.
        # Applied at sync time so Clover POS + Inventory show the same minimum
        # the website enforces, instead of the raw (lower) sheet price.
        "price_floor": {"28": 10000, "14": 9500, "7": 5500, "3.5": 2500},
    },
    {
        "tab": "Retail Concentrate Menu",
        "category": "Concentrates",
        "price_cols": {"1": 12, "2": 13, "4": 14},
        "sku_suffix": {"1": "1G", "2": "2G", "4": "4G"},
        # weight (g) -> minimum customer price in cents
        "price_floor": {"1": 2000, "2": 3500, "4": 6000},
    },
]

# Sheet column indexes shared by both tabs.
_LL_COL_INVENTORY = 1
_LL_COL_TIER = 2
_LL_COL_STRAIN = 3
_LL_COL_IHS = 8

_LEAFLIFE_SYNC_STATUS: dict = {
    "last_run": None,
    "status": "never",
    "created": 0,
    "updated": 0,
    "removed": 0,
    "strains": 0,
    "errors": [],
}


def _leaflife_strain_type(ihs: str) -> Optional[str]:
    """Map the sheet's I/H/S text to Hybrid / Indica / Sativa.

    Anything labelled a hybrid (incl. 'Indica-Dominant Hybrid') is Hybrid;
    pure Indica/Sativa map straight through. Blank/unknown returns None.
    """
    low = (ihs or "").lower()
    if "hybrid" in low:
        return "Hybrid"
    has_i = "indica" in low
    has_s = "sativa" in low
    if has_i and not has_s:
        return "Indica"
    if has_s and not has_i:
        return "Sativa"
    return None


def _leaflife_money_cents(raw: str) -> Optional[int]:
    """Parse a '$1,234.50' style price into integer cents. None if empty."""
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return None


async def _fetch_leaflife_sheet(tab: str) -> list[list[str]]:
    """Fetch a sheet tab as CSV rows (excluding the header). Raises on failure."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{LEAFLIFE_SHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}"
    )
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    text = resp.text
    if text.lstrip().startswith("<"):
        raise ValueError(f"Sheet tab '{tab}' not accessible (got HTML)")
    rows = list(csv.reader(io.StringIO(text)))
    return rows[1:] if rows else []


def _build_leaflife_desired(tab_rows: dict) -> dict:
    """Build the desired {sku_upper: {...}} set from parsed sheet rows.

    Only package sizes with stock >= 1 and a valid price are included.
    """
    desired: dict = {}
    for cfg in _LEAFLIFE_TABS:
        rows = tab_rows.get(cfg["tab"], [])
        category = cfg["category"]
        for row in rows:
            max_col = max(cfg["price_cols"].values())
            if len(row) <= max_col:
                continue
            strain = (row[_LL_COL_STRAIN] or "").strip()
            if not strain:
                continue
            try:
                inventory_grams = float(
                    (row[_LL_COL_INVENTORY] or "0").replace(",", "").strip() or 0
                )
            except ValueError:
                inventory_grams = 0.0
            if inventory_grams <= 0:
                continue
            tier = (row[_LL_COL_TIER] or "").strip()
            product_type = _leaflife_strain_type(row[_LL_COL_IHS])
            strain_upper = strain.upper()
            # Keep flower SKUs byte-identical to the legacy importer (letters,
            # spaces, hyphens, '#') while stripping non-ASCII (e.g. '×') that
            # only appears in concentrate strain names.
            sku_source = re.sub(r"[^A-Z0-9 #./&-]", "", strain_upper.replace("×", "X"))
            sku_base = "LF-" + re.sub(r"\s+", "-", sku_source)
            price_floor = cfg.get("price_floor", {})
            for weight, col in cfg["price_cols"].items():
                price = _leaflife_money_cents(row[col])
                if not price:
                    continue
                floor = price_floor.get(weight)
                if floor and price < floor:
                    price = floor
                weight_f = float(weight)
                stock = int(inventory_grams / weight_f) if weight_f > 0 else 0
                if stock < 1:
                    continue
                suffix = cfg["sku_suffix"][weight]
                sku = f"{sku_base}-{suffix}"
                label = f"{weight} Gram{'s' if weight_f != 1 else ''}".upper()
                if category == "Flower" and tier:
                    name = f"{strain_upper} {tier.upper()} {label}"
                else:
                    name = f"{strain_upper} {label}"
                desired[sku.upper()] = {
                    "sku": sku,
                    "name": name,
                    "price": price,
                    "stock": stock,
                    "category": category,
                    "product_type": product_type,
                }
    return desired


async def _upsert_leaflife_attrs(
    db: aiosqlite.Connection, sku: str, name: str, product_type: Optional[str]
):
    if not product_type:
        return
    await db.execute(
        """INSERT INTO product_attributes
               (sku, product_name, product_type, updated_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(sku) DO UPDATE SET
               product_type = excluded.product_type,
               product_name = COALESCE(excluded.product_name, product_attributes.product_name),
               updated_at = CURRENT_TIMESTAMP""",
        (sku, name, product_type),
    )


async def run_leaflife_sync(db: aiosqlite.Connection) -> dict:
    """Reconcile LeafLife products in Clover HQ against the partnership sheet."""
    from app.routers.ecommerce_router import (
        HQ_MERCHANT_ID, HQ_API_TOKEN, invalidate_product_cache,
    )
    if not HQ_MERCHANT_ID or not HQ_API_TOKEN:
        raise HTTPException(
            status_code=500, detail="HQ Clover credentials not configured"
        )
    client = CloverClient(HQ_MERCHANT_ID, HQ_API_TOKEN)
    errors: list[str] = []

    # 1. Read every configured tab. If any fails, we skip the delete phase so a
    #    transient sheet error can never wipe live products.
    tab_rows: dict = {}
    all_tabs_ok = True
    for cfg in _LEAFLIFE_TABS:
        try:
            tab_rows[cfg["tab"]] = await _fetch_leaflife_sheet(cfg["tab"])
        except Exception as e:
            all_tabs_ok = False
            errors.append(f"fetch '{cfg['tab']}': {e}")
            tab_rows[cfg["tab"]] = []

    desired = _build_leaflife_desired(tab_rows)

    # 2. Category IDs (create if missing).
    cats = await client.get_categories()
    cat_ids: dict = {}
    for c in cats.get("elements", []):
        cat_ids[c.get("name", "").lower()] = c["id"]
    for cfg in _LEAFLIFE_TABS:
        cname = cfg["category"]
        if cname.lower() not in cat_ids:
            new_cat = await client.create_category(cname)
            cat_ids[cname.lower()] = new_cat["id"]

    age_obj = await _get_age_restriction_obj(client, "Vitamin & Supplements", 21)

    # 3. Existing LF- items in Clover HQ, grouped by SKU. A SKU can map to more
    #    than one Clover item when duplicates were created previously; we keep a
    #    list so the update phase can collapse them into a single canonical item
    #    (deleting the extras) instead of silently touching just one — which is
    #    what left stale duplicates below the price floor and double-counted
    #    stock in the merged inventory view.
    existing_items = await client.get_items(expand="itemStock")
    existing: dict[str, list[dict]] = {}
    for item in existing_items.get("elements", []):
        sku = (item.get("sku") or "")
        if sku.upper().startswith("LF-"):
            stock = 0
            item_stock = item.get("itemStock") or {}
            if isinstance(item_stock, dict):
                stock = int(item_stock.get("quantity", 0) or 0)
            existing.setdefault(sku.upper(), []).append({
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "price": item.get("price", 0),
                "stock": stock,
            })

    created = updated = removed = 0

    # 4. Create / update. When a SKU has duplicate Clover items, keep the first
    #    as canonical and delete the rest so each LF- product is exactly one item.
    for sku_up, want in desired.items():
        cat_id = cat_ids.get(want["category"].lower())
        dupes = existing.get(sku_up) or []
        cur = dupes[0] if dupes else None
        try:
            if cur:
                for extra in dupes[1:]:
                    if not extra["id"]:
                        continue
                    try:
                        await client.delete_item(extra["id"])
                        removed += 1
                        await asyncio.sleep(0.2)
                    except Exception as de:
                        errors.append(f"dedupe {want['sku']}: {de}")
                payload: dict = {}
                if cur["price"] != want["price"]:
                    payload["price"] = want["price"]
                if cur["name"] != want["name"]:
                    payload["name"] = want["name"]
                if payload:
                    await client.update_item(cur["id"], payload)
                if cur["stock"] != want["stock"]:
                    await client.update_item_stock(cur["id"], want["stock"])
                if payload or cur["stock"] != want["stock"] or len(dupes) > 1:
                    updated += 1
                await _upsert_leaflife_attrs(
                    db, want["sku"], want["name"], want["product_type"]
                )
            else:
                item_data: dict = {
                    "name": want["name"],
                    "price": want["price"],
                    "sku": want["sku"],
                    "available": True,
                    "hidden": False,
                    "autoManage": False,
                    "isRevenue": True,
                    "defaultTaxRates": True,
                    "isAgeRestricted": True,
                }
                if age_obj:
                    item_data["ageRestrictedObj"] = age_obj
                new_item = await client.create_item(item_data)
                item_id = new_item.get("id", "")
                if cat_id:
                    try:
                        await client.assign_category(item_id, cat_id)
                    except Exception as ce:
                        errors.append(f"category {want['sku']}: {ce}")
                if want["stock"] > 0:
                    await client.update_item_stock(item_id, want["stock"])
                await _upsert_leaflife_attrs(
                    db, want["sku"], want["name"], want["product_type"]
                )
                created += 1
            await asyncio.sleep(0.2)
        except Exception as e:
            errors.append(f"{want['sku']}: {e}")

    # 5. Remove LF- items no longer on the sheet (only when every tab loaded
    #    cleanly, so a fetch error can't cascade into deletions).
    if all_tabs_ok and desired:
        for sku_up, dupes in existing.items():
            if sku_up in desired:
                continue
            try:
                for cur in dupes:
                    if cur["id"]:
                        await client.delete_item(cur["id"])
                        removed += 1
                        await asyncio.sleep(0.2)
                await db.execute("DELETE FROM par_levels WHERE UPPER(sku) = ?", (sku_up,))
                await db.execute(
                    "DELETE FROM inventory_snapshots WHERE UPPER(sku) = ?", (sku_up,)
                )
            except Exception as e:
                errors.append(f"delete {sku_up}: {e}")

    await db.commit()

    try:
        invalidate_product_cache()
        await _invalidate_cache()
    except Exception as e:
        errors.append(f"cache: {e}")

    _LEAFLIFE_SYNC_STATUS.update({
        "last_run": time.time(),
        "status": "ok" if not errors else "completed_with_errors",
        "created": created,
        "updated": updated,
        "removed": removed,
        "strains": len(desired),
        "errors": errors[:20],
    })
    return {
        "status": _LEAFLIFE_SYNC_STATUS["status"],
        "created": created,
        "updated": updated,
        "removed": removed,
        "strains": len(desired),
        "errors": errors[:20],
    }


@router.post("/leaflife-sync")
async def leaflife_sync(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Manually trigger a LeafLife sheet sync now."""
    return await run_leaflife_sync(db)


@router.get("/leaflife-sync/status")
async def leaflife_sync_status(
    user: dict = Depends(get_current_user),
):
    """Return the result of the most recent LeafLife sheet sync."""
    return _LEAFLIFE_SYNC_STATUS
