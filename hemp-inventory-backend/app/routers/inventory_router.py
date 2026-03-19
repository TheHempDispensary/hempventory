from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
import asyncio
import aiosqlite
import base64
import os
import json
import io
import itertools
import time
import httpx
from PIL import Image as PILImage

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# In-memory cache for inventory data
_inventory_cache: dict = {"items": [], "locations": [], "updated_at": 0}
_cache_lock = asyncio.Lock()


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
    auto_manage: Optional[bool] = True  # auto manage stock
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


async def _do_sync(db: aiosqlite.Connection) -> dict:
    """Core sync logic: pull latest inventory from all Clover locations."""
    locations = await _get_locations(db)
    if not locations:
        return {"items": [], "locations": []}

    par_levels = await _get_par_levels(db)

    # Build a unified inventory keyed by composite key
    inventory: dict[str, dict] = {}
    location_list = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        location_list.append({"id": loc_id, "name": loc_name, "merchant_id": merchant_id})

        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items(expand="itemStock,categories,ageRestricted,itemGroup")
            items = data.get("elements", [])
        except Exception as e:
            print(f"Error syncing {loc_name}: {e}")
            continue

        for item in items:
            raw_sku = item.get("sku", "") or ""
            clover_id = item.get("id", "")
            item_name = " ".join((item.get("name", "") or "").split())  # normalize whitespace
            display_sku = raw_sku or clover_id
            merge_key = f"{display_sku}::{item_name}"

            item_stock = item.get("itemStock", {})
            quantity = item_stock.get("quantity", 0) if item_stock else 0

            categories = item.get("categories", {}).get("elements", [])
            category_names = [c.get("name", "") for c in categories]

            par = par_levels.get((display_sku, loc_id), None)

            if merge_key not in inventory:
                inventory[merge_key] = {
                    "sku": display_sku,
                    "name": item_name,
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
                }

            inventory[merge_key]["locations"][loc_name] = {
                "location_id": loc_id,
                "stock": quantity,
                "par_level": par,
                "status": _stock_status(quantity, par),
                "clover_item_id": clover_id,
            }
            inventory[merge_key]["clover_ids"][loc_name] = clover_id

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

    items_list = sorted(inventory.values(), key=lambda x: x["name"])
    result = {"items": items_list, "locations": location_list}

    # Update cache
    async with _cache_lock:
        _inventory_cache["items"] = result["items"]
        _inventory_cache["locations"] = result["locations"]
        _inventory_cache["updated_at"] = time.time()

    return result


@router.get("/sync")
async def sync_inventory(
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Pull latest inventory from all Clover locations (full sync)."""
    return await _do_sync(db)


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
    item_name: Optional[str] = None


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
            # If item_name provided, filter by name to disambiguate shared SKUs
            if matching and req.item_name:
                norm_name = " ".join(req.item_name.split())
                name_matched = [
                    i for i in matching
                    if " ".join((i.get("name", "") or "").split()) == norm_name
                ]
                if name_matched:
                    matching = name_matched
                else:
                    # Wrong-name match; skip this location and try the next
                    continue
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
    # For items without a real SKU (barcode), use the source Clover ID as SKU
    # so the sync merges the new item with the original across locations
    source_sku = source_item.get("sku", "") or ""
    source_clover_id = source_item.get("id", "")
    if source_sku:
        item_data["sku"] = source_sku
    elif source_clover_id:
        item_data["sku"] = source_clover_id
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


class BulkStockUpdateItem(BaseModel):
    sku: str
    location_id: int
    quantity: float


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
        matching = [i for i in clover_items if (i.get("sku") or i.get("id", "")) == upd.sku]
        if not matching:
            results.append({"sku": upd.sku, "location": loc_name, "status": "not_found"})
            continue

        try:
            for match in matching:
                await client.update_item_stock(match["id"], int(upd.quantity))
            results.append({"sku": upd.sku, "location": loc_name, "status": "updated", "quantity": upd.quantity})
        except Exception as e:
            results.append({"sku": upd.sku, "location": loc_name, "status": "error", "error": str(e)})

    await _invalidate_cache()
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


@router.delete("/items/{sku}")
async def delete_item(
    sku: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Delete an item from all locations by SKU."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    results = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            data = await client.get_items()
            matching = [i for i in data.get("elements", []) if i.get("sku") == sku]
            if not matching:
                matching = [i for i in data.get("elements", []) if i.get("id") == sku]
            if not matching:
                results.append({"location": loc_name, "status": "not_found"})
                continue
            for match in matching:
                await client.delete_item(match["id"])
            results.append({"location": loc_name, "status": "deleted", "count": len(matching)})
        except Exception as e:
            results.append({"location": loc_name, "status": "error", "error": str(e)})

    # Also remove PAR levels
    await db.execute("DELETE FROM par_levels WHERE sku = ?", (sku,))
    await db.commit()

    await _remove_from_cache([sku])
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

    results = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            # Find the item by SKU
            data = await client.get_items()
            matching = [i for i in data.get("elements", []) if i.get("sku") == sku]
            if not matching:
                # Also try matching by Clover item ID
                matching = [i for i in data.get("elements", []) if i.get("id") == sku]
            if not matching:
                results.append({"location": loc_name, "status": "not_found"})
                continue

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
                    await client.update_item_stock(match["id"], stock_map[loc_id])
            results.append({"location": loc_name, "status": "updated", "count": len(matching)})
        except httpx.HTTPStatusError as e:
            error_detail = str(e)
            try:
                error_body = e.response.json()
                error_detail = error_body.get("message", str(e))
            except Exception:
                pass
            results.append({"location": loc_name, "status": "error", "error": error_detail})
        except Exception as e:
            results.append({"location": loc_name, "status": "error", "error": str(e)})

    await _invalidate_cache()
    return {"results": results}


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
    # Update cache in-place to reflect the new image without full re-sync
    async with _cache_lock:
        for item in _inventory_cache.get("items", []):
            if item["sku"] == sku:
                item["has_image"] = True
    return {"status": "ok", "sku": sku}


@router.get("/images/{sku}")
async def get_image(
    sku: str,
    w: Optional[int] = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get a product image by SKU. Returns the raw image bytes.
    Optional ?w=300 parameter to get a resized thumbnail for faster loading."""
    cursor = await db.execute(
        "SELECT image_data, content_type, updated_at FROM product_images WHERE sku = ?", (sku,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="No image found for this SKU")

    image_bytes = base64.b64decode(row[0])

    # If width parameter provided, resize the image for faster loading
    if w and 50 <= w <= 1200:
        try:
            img = PILImage.open(io.BytesIO(image_bytes))
            ratio = w / img.width
            new_height = int(img.height * ratio)
            img = img.resize((w, new_height), PILImage.LANCZOS)
            buf = io.BytesIO()
            # Save as WebP for smaller file size, fall back to original format
            try:
                img.save(buf, format="WEBP", quality=80)
                media_type = "image/webp"
            except Exception:
                fmt = "PNG" if row[1] == "image/png" else "JPEG"
                img.save(buf, format=fmt, quality=85)
                media_type = row[1]
            image_bytes = buf.getvalue()
        except Exception:
            pass  # Fall back to original image if resize fails

    return Response(
        content=image_bytes,
        media_type=row[1] if not w else media_type if w and 50 <= w <= 1200 else row[1],
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
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
    return {"status": "ok", "sku": sku}


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


@router.post("/transfer-stock")
async def transfer_stock(
    req: StockTransferRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Transfer stock of an item from one location to another.
    Deducts from source and adds to destination via Clover API."""
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

    if not source_item:
        raise HTTPException(status_code=404, detail=f"Item with SKU '{req.sku}' not found at {from_name}")

    current_stock = (source_item.get("itemStock") or {}).get("quantity", 0)
    if current_stock < req.quantity:
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

    if not dest_item:
        raise HTTPException(
            status_code=404,
            detail=f"Item with SKU '{req.sku}' not found at {to_name}. Push the item to that location first."
        )

    dest_stock = (dest_item.get("itemStock") or {}).get("quantity", 0)

    # Execute transfer: deduct from source, add to destination
    new_from_stock = current_stock - req.quantity
    new_to_stock = dest_stock + req.quantity

    try:
        await from_client.update_item_stock(source_item["id"], new_from_stock)
        await to_client.update_item_stock(dest_item["id"], new_to_stock)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transfer failed: {str(e)}")

    return {
        "status": "transferred",
        "sku": req.sku,
        "item_name": source_item.get("name", ""),
        "quantity": req.quantity,
        "from_location": from_name,
        "to_location": to_name,
        "from_stock_before": current_stock,
        "from_stock_after": new_from_stock,
        "to_stock_before": dest_stock,
        "to_stock_after": new_to_stock,
    }
    await _invalidate_cache()
    return result


def _remove_white_background(image_bytes: bytes, threshold: int = 240, edge_softness: int = 20) -> tuple[bytes, str]:
    """Remove white/near-white background from an image, returning transparent PNG bytes and content_type."""
    img = PILImage.open(io.BytesIO(image_bytes)).convert("RGBA")
    pixels = img.load()
    width, height = img.size

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r > threshold and g > threshold and b > threshold:
                pixels[x, y] = (r, g, b, 0)
            elif r > (threshold - edge_softness) and g > (threshold - edge_softness) and b > (threshold - edge_softness):
                min_c = min(r, g, b)
                new_alpha = int(255 * (1 - (min_c - (threshold - edge_softness)) / edge_softness))
                pixels[x, y] = (r, g, b, min(a, max(0, new_alpha)))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


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
        async with _cache_lock:
            for item in _inventory_cache.get("items", []):
                if item["sku"] in assigned_skus:
                    item["has_image"] = True

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
    auto_manage: Optional[bool] = True
    default_tax_rates: Optional[bool] = True


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

    results = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Step 1: Create the item group
            group = await client.create_item_group(req.name)
            group_id = group["id"]

            # Step 2 & 3: Create attributes and their options
            attribute_options: list[list[dict]] = []  # list of lists of option dicts
            for variant in req.variants:
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


class AddVariantsRequest(BaseModel):
    variants: list[VariantOption]
    sku_prefix: Optional[str] = None


@router.post("/items/{sku}/add-variants")
async def add_variants_to_item(
    sku: str,
    req: AddVariantsRequest,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Add variants to an existing item across all locations.

    Flow per Clover API:
    1. Find the existing item on each location by SKU
    2. Create an item group using the item's name
    3. Create attributes and options
    4. Move the original item into the group
    5. Generate variant items for all option combos
    6. Associate options with each item
    """
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    results = []

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)

            # Find the existing item by SKU
            items_data = await client.get_items()
            all_items = items_data.get("elements", [])
            matching = [i for i in all_items if i.get("sku") == sku]
            if not matching:
                matching = [i for i in all_items if i.get("id") == sku]
            if not matching:
                results.append({
                    "location": loc_name,
                    "status": "skipped",
                    "error": f"Item with SKU {sku} not found",
                })
                continue

            source_item = matching[0]
            source_item_id = source_item["id"]
            item_name = source_item.get("name", "")
            item_price = source_item.get("price", 0)

            # Step 1: Create the item group
            group = await client.create_item_group(item_name)
            group_id = group["id"]

            # Step 2: Move the original item into the group
            await client.update_item(source_item_id, {"itemGroup": {"id": group_id}})

            # Step 3 & 4: Create attributes and their options
            attribute_options: list[list[dict]] = []
            for variant in req.variants:
                attr = await client.create_attribute(variant.attribute_name, group_id)
                attr_id = attr["id"]

                options_for_attr: list[dict] = []
                for opt_name in variant.option_names:
                    opt = await client.create_option(attr_id, opt_name)
                    options_for_attr.append({"id": opt["id"], "name": opt_name, "attr_name": variant.attribute_name})
                attribute_options.append(options_for_attr)

            # Step 5: Generate cartesian product of all options
            option_combos = list(itertools.product(*attribute_options))

            # Step 6: Create variant items for each combination
            created_items = []
            for combo in option_combos:
                item_data: dict = {
                    "name": item_name,
                    "price": item_price,
                    "itemGroup": {"id": group_id},
                }
                if req.sku_prefix:
                    option_suffix = "-".join(o["name"][:3].upper() for o in combo)
                    item_data["sku"] = f"{req.sku_prefix}-{option_suffix}"

                # Copy properties from the original item
                for field_map in [
                    ("priceType", "priceType"), ("cost", "cost"),
                    ("isRevenue", "isRevenue"), ("hidden", "hidden"),
                    ("autoManage", "autoManage"), ("available", "available"),
                    ("defaultTaxRates", "defaultTaxRates"),
                ]:
                    src_field, dst_field = field_map
                    if src_field in source_item:
                        item_data[dst_field] = source_item[src_field]

                created_item = await client.create_item(item_data)
                new_item_id = created_item.get("id", "")

                # Associate options with this item
                for opt in combo:
                    await client.associate_option_with_item(opt["id"], new_item_id)

                combo_desc = " / ".join(o["name"] for o in combo)
                created_items.append({
                    "item_id": new_item_id,
                    "variant": combo_desc,
                    "name": created_item.get("name", ""),
                })

            # Copy category from original item to new variants
            orig_cats = source_item.get("categories", {}).get("elements", [])
            if orig_cats:
                cat_id = orig_cats[0].get("id", "")
                if cat_id:
                    for ci in created_items:
                        try:
                            await client.assign_category(ci["item_id"], cat_id)
                        except Exception:
                            pass

            results.append({
                "location": loc_name,
                "status": "created",
                "group_id": group_id,
                "original_item_id": source_item_id,
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

    await _invalidate_cache(db)
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
