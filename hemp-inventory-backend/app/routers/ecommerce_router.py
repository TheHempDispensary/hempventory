from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from typing import Optional, List
from pydantic import BaseModel
import httpx
import aiosqlite
import time
import json
import smtplib
import asyncio
import os
import re
import math
import html as html_mod
from urllib.parse import quote as url_quote
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import get_db, DB_PATH
from app.clover_client import CloverClient
from app.routers.loyalty_router import _do_signup, _sync_balance_to_clover_quietly
from app import leaflife_orders

STORE_EMAIL = "Support@TheHempDispensary.com"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Model used for product auto-tagging; overridable so a retirement is an env change.
AUTOTAG_MODEL = os.environ.get("AUTOTAG_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"
# Kill switch: set AUTOTAG_ENABLED=0 to stop all AI tagging without a deploy.
AUTOTAG_ENABLED = os.environ.get("AUTOTAG_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
# Tagging costs money per product, so a pass is small, rate-limited, and rare.
AUTOTAG_MAX_PER_RUN = 25
AUTOTAG_MAX_ATTEMPTS = 3
AUTOTAG_RUN_INTERVAL = 21600  # 6 hours between passes
_autotag_last_run: float = 0.0
_autotag_running: bool = False

# SMTP env-var fallbacks (so emails work even if DB settings are empty)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = os.environ.get("SMTP_PORT", "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


class OrderItem(BaseModel):
    product_id: str
    name: str
    sku: str = ""
    price: int = 0
    quantity: int = 1


class OrderCustomer(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str = ""


class OrderShipping(BaseModel):
    address: str = ""
    apartment: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""


class CreateOrderRequest(BaseModel):
    customer: OrderCustomer
    shipping_address: OrderShipping
    items: List[OrderItem]
    subtotal: int = 0
    discount: int = 0
    volume_discount: int = 0
    sale_discount: int = 0
    loyalty_discount: int = 0
    shipping_cost: int = 0
    tax: int = 0
    total: int = 0
    notes: str = ""
    payment_token: str = ""
    loyalty_number: str = ""
    loyalty_reward_id: Optional[int] = None
    promo_code: Optional[str] = None
    shipping_service: str = ""
    fulfillment_type: str = "shipping"  # "shipping", "pickup_west", "pickup_east", "local_delivery"


class RecoverOrderRequest(CreateOrderRequest):
    """Body for manually recovering an order that was charged but failed to save."""
    order_number: str = ""
    charge_id: str = ""
    payment_status: str = "paid"


router = APIRouter(prefix="/api/ecommerce", tags=["ecommerce"])

# HQ location Clover credentials (public endpoint - no auth required)
HQ_MERCHANT_ID = os.environ.get("CLOVER_HQ_MERCHANT_ID", "0AJ4FF0G1YFM1")
HQ_API_TOKEN = os.environ.get("CLOVER_HQ_API_TOKEN", "9a06267a-6998-3f5a-521c-ca235f704856")
HQ_ECOMM_TOKEN = os.environ.get("CLOVER_HQ_ECOMM_TOKEN", "81e997e6-89d0-0ff7-522d-d195e6cd9138")
CLOVER_BASE_URL = "https://api.clover.com/v3"
CLOVER_CHARGES_URL = "https://scl.clover.com/v1/charges"

# Store location Clover credentials for pickup orders & stock lookup
WEST_MERCHANT_ID = os.environ.get("CLOVER_WEST_MERCHANT_ID", "")
WEST_API_TOKEN = os.environ.get("CLOVER_WEST_API_TOKEN", "")
EAST_MERCHANT_ID = os.environ.get("CLOVER_EAST_MERCHANT_ID", "")
EAST_API_TOKEN = os.environ.get("CLOVER_EAST_API_TOKEN", "")

# Local delivery constants
HQ_LAT = 28.4786  # Spring Hill HQ latitude
HQ_LON = -82.5277  # Spring Hill HQ longitude
DELIVERY_RADIUS_MILES = 30
DELIVERY_FEE_STANDARD = 1500  # $15.00 in cents
DELIVERY_FEE_DISCOUNTED = 500  # $5.00 in cents
DELIVERY_DISCOUNT_THRESHOLD = 15000  # $150.00 in cents — orders above this get $5 delivery


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon points using Haversine formula."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def _geocode_address(
    address: str, city: str, state: str, zip_code: str
) -> Optional[tuple[float, float]]:
    """Geocode an address to (lat, lon) via Nominatim.

    Rural street addresses often aren't in OpenStreetMap, which would make the
    exact-address lookup return nothing. Fall back to increasingly coarse queries
    (city/state/zip, then zip) so delivery eligibility can still be determined.
    """
    queries = []
    if address and city and state:
        queries.append(f"{address}, {city}, {state} {zip_code}".strip())
    if city and state:
        queries.append(f"{city}, {state} {zip_code}".strip())
    if zip_code:
        queries.append(f"{zip_code}, USA")

    async with httpx.AsyncClient(timeout=5.0) as client:
        for q in queries:
            try:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "limit": "1", "countrycodes": "us"},
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "THD-Website/1.0 (support@thehempdispensary.com)",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if data:
                    return float(data[0]["lat"]), float(data[0]["lon"])
            except Exception as e:
                print(f"[delivery] Geocode attempt failed for '{q}': {e}")
    return None


# ── In-memory product cache ──────────────────────────────────────────────────
_product_cache: dict = {}  # {"products": [...], "total": int, "categories": [...]}
_product_cache_json: bytes = b""  # Pre-serialized JSON for the full /products response
_cache_timestamp: float = 0.0
_refresh_in_progress: bool = False
CACHE_TTL = 300  # 5 minutes


# Sits beside the database. Deriving it by name substitution overwrote the
# database itself whenever DB_PATH wasn't literally named "app.db".
DISK_CACHE_PATH = os.path.join(os.path.dirname(DB_PATH) or "/tmp", "product_cache.json")


def _enforce_leaflife_price_floor(sku: str, name: str, price: int) -> int:
    """Enforce minimum price floors for LeafLife products by weight.
    Returns the price (in cents) with floor applied if applicable."""
    if not isinstance(sku, str) or not sku.startswith("LF-"):
        return price
    sku_upper = sku.upper()
    name_upper = name.upper()
    # Concentrate floors (SKUs end -1G/-2G/-4G, no space — distinct from flower "-7 G")
    if sku_upper.endswith("-1G"):
        return max(price, 2000)   # $20.00 minimum for 1g concentrate
    elif sku_upper.endswith("-2G"):
        return max(price, 3500)   # $35.00 minimum for 2g concentrate
    elif sku_upper.endswith("-4G"):
        return max(price, 6000)   # $60.00 minimum for 4g concentrate
    if sku_upper.endswith("-28") or "28 GRAM" in name_upper:
        return max(price, 10000)  # $100.00 minimum for 28g
    elif sku_upper.endswith("-14") or "14 GRAM" in name_upper:
        return max(price, 9500)   # $95.00 minimum for 14g
    elif sku_upper.endswith("-7 G") or sku_upper.endswith("-7G") or "7 GRAM" in name_upper:
        return max(price, 5500)   # $55.00 minimum for 7g
    elif sku_upper.endswith("-3.5") or "3.5 GRAM" in name_upper:
        return max(price, 2500)   # $25.00 minimum for 3.5g
    return price


def invalidate_product_cache():
    """Invalidate ALL product cache layers so the next request fetches fresh data.
    Called from inventory_router when images are uploaded/changed."""
    global _product_cache, _product_cache_json, _cache_timestamp
    _cache_timestamp = 0  # Force refresh on next request
    _product_cache_json = b""  # Clear pre-serialized JSON so fast path doesn't serve stale data
    _product_cache = {}  # Clear cached dict so _get_cached_products does a full re-fetch
    # Delete disk cache so it doesn't reload stale data
    try:
        if os.path.exists(DISK_CACHE_PATH):
            os.remove(DISK_CACHE_PATH)
            print("[cache] Disk cache deleted after image update")
    except Exception as e:
        print(f"[cache] Failed to delete disk cache: {e}")


async def _load_disk_cache() -> bool:
    """Load product cache from disk (survives restarts/deploys). Returns True if loaded.
    Always loads if file exists — no TTL expiry. This ensures products are always
    available instantly on startup, even if the file is hours old."""
    global _product_cache, _product_cache_json, _cache_timestamp
    try:
        if os.path.exists(DISK_CACHE_PATH):
            with open(DISK_CACHE_PATH, "r") as f:
                disk_data = json.load(f)
            saved_at = disk_data.get("timestamp", 0)
            age = time.time() - saved_at
            _product_cache = disk_data["data"]
            _cache_timestamp = saved_at
            _product_cache_json = json.dumps(
                {"products": _product_cache["products"], "total": _product_cache["total"], "categories": _product_cache["categories"]}
            ).encode()
            print(f"[cache] Loaded {_product_cache['total']} products from disk cache ({age:.0f}s old)")
            return True
    except Exception as e:
        print(f"[cache] Disk cache load failed: {e}")
    return False


def _save_disk_cache(result: dict) -> None:
    """Persist cache to disk so it survives restarts."""
    try:
        with open(DISK_CACHE_PATH, "w") as f:
            json.dump({"data": result, "timestamp": time.time()}, f)
    except Exception as e:
        print(f"[cache] Disk cache save failed: {e}")


_fetch_event: Optional[asyncio.Event] = None  # Signals when an in-flight fetch completes


async def _fetch_location_stock(client: httpx.AsyncClient, merchant_id: str, api_token: str, label: str) -> dict[str, int]:
    """Fetch stock quantities from a Clover location. Returns {sku_or_name: quantity}."""
    stock_map: dict[str, int] = {}
    try:
        base = f"{CLOVER_BASE_URL}/merchants/{merchant_id}"
        headers = {"Authorization": f"Bearer {api_token}"}
        offset = 0
        while True:
            resp = await client.get(
                f"{base}/items",
                headers=headers,
                params={"expand": "itemStock", "limit": 1000, "offset": offset, "filter": "deleted=false"},
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
            for item in elements:
                sku = item.get("sku", "") or ""
                name = " ".join((item.get("name", "") or "").split())
                key = sku if sku else name
                if not key:
                    continue
                stock_info = item.get("itemStock", {})
                qty = stock_info.get("quantity", 0) if stock_info else 0
                stock_map[key] = stock_map.get(key, 0) + qty
            if len(elements) < 1000:
                break
            offset += 1000
        print(f"[cache] {label} stock: {len(stock_map)} items")
    except Exception as e:
        print(f"[cache] Failed to fetch {label} stock: {e}")
    return stock_map


async def _fetch_and_cache_products() -> dict:
    """Fetch all products from Clover API + image DB and cache in memory."""
    global _product_cache, _product_cache_json, _cache_timestamp, _refresh_in_progress, _fetch_event
    if _refresh_in_progress and _fetch_event:
        # Another fetch is already running — wait for it instead of starting a duplicate
        await _fetch_event.wait()
        return _product_cache

    _refresh_in_progress = True
    _fetch_event = asyncio.Event()
    start_time = time.time()

    try:
        base = f"{CLOVER_BASE_URL}/merchants/{HQ_MERCHANT_ID}"
        headers = {"Authorization": f"Bearer {HQ_API_TOKEN}"}

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Fetch HQ items + West/East stock in parallel
            async def _fetch_hq() -> list:
                all_items: list = []
                current_offset = 0
                while True:
                    resp = await client.get(
                        f"{base}/items",
                        headers=headers,
                        params={"expand": "categories,itemStock", "limit": 1000, "offset": current_offset, "filter": "deleted=false"},
                    )
                    resp.raise_for_status()
                    elements = resp.json().get("elements", [])
                    all_items.extend(elements)
                    if len(elements) < 1000:
                        break
                    current_offset += 1000
                return all_items

            hq_task = asyncio.ensure_future(_fetch_hq())
            west_task = asyncio.ensure_future(_fetch_location_stock(client, WEST_MERCHANT_ID, WEST_API_TOKEN, "West")) if WEST_MERCHANT_ID and WEST_API_TOKEN else None
            east_task = asyncio.ensure_future(_fetch_location_stock(client, EAST_MERCHANT_ID, EAST_API_TOKEN, "East")) if EAST_MERCHANT_ID and EAST_API_TOKEN else None

            all_items = await hq_task
            west_stock = await west_task if west_task else {}
            east_stock = await east_task if east_task else {}

        fetch_time = time.time() - start_time
        print(f"[cache] Clover API fetched {len(all_items)} HQ items in {fetch_time:.1f}s")

        # Get image map from our database
        from app.database import DB_PATH
        image_base_url = os.environ.get("BASE_URL", "https://thd-inventory-api.fly.dev") + "/api/inventory/images"
        db = await aiosqlite.connect(DB_PATH)
        try:
            cursor = await db.execute("SELECT sku, product_name, updated_at FROM product_images")
            image_rows = await cursor.fetchall()
        finally:
            await db.close()
        image_by_sku = {row[0]: f"{image_base_url}/{url_quote(row[0], safe='')}?v=2&bg=1&t={str(row[2] or '').replace(' ', '_')}" for row in image_rows}
        image_by_name = {}
        for row in image_rows:
            if row[1]:
                image_by_name[row[1].upper()] = f"{image_base_url}/{url_quote(row[0], safe='')}?v=2&bg=1&t={str(row[2] or '').replace(' ', '_')}"
        # All gummy products share one canonical cube image (env-overridable).
        gummy_image_sku = os.environ.get("GUMMY_IMAGE_SKU", "2025754319138")
        gummy_image_url = f"{image_base_url}/{url_quote(gummy_image_sku, safe='')}?v=2&bg=1"

        # Load product descriptions from local DB (Clover API doesn't persist descriptions)
        desc_db = await aiosqlite.connect(DB_PATH)
        try:
            desc_cursor = await desc_db.execute("SELECT sku, product_name, description FROM product_descriptions")
            desc_rows = await desc_cursor.fetchall()
        finally:
            await desc_db.close()
        desc_by_sku: dict[str, str] = {row[0]: row[2] for row in desc_rows}
        # Build a name-based lookup for products that share a SKU (e.g. syringes)
        desc_by_name: dict[str, str] = {}
        for row in desc_rows:
            if row[1]:  # product_name
                desc_by_name[row[1].upper()] = row[2]

        # Load product attributes (effect & strength) from local DB
        attr_db = await aiosqlite.connect(DB_PATH)
        try:
            attr_cursor = await attr_db.execute("SELECT sku, effect, strength, product_type, product_name FROM product_attributes")
            attr_rows = await attr_cursor.fetchall()
        finally:
            await attr_db.close()
        attrs_by_sku: dict[str, dict] = {row[0]: {"effect": row[1], "strength": row[2], "product_type": row[3]} for row in attr_rows}
        # Name-based lookup for products sharing a SKU
        attrs_by_name: dict[str, dict] = {}
        for row in attr_rows:
            if row[4]:  # product_name
                attrs_by_name[row[4].upper()] = {"effect": row[1], "strength": row[2], "product_type": row[3]}

        # Load locally-hidden items
        hidden_db = await aiosqlite.connect(DB_PATH)
        try:
            hcur = await hidden_db.execute("SELECT sku FROM hidden_items")
            hidden_skus = {row[0] for row in await hcur.fetchall()}
        finally:
            await hidden_db.close()

        # Load COA lab results linked to product SKUs (with analyte details)
        coa_db = await aiosqlite.connect(DB_PATH)
        try:
            coa_cursor = await coa_db.execute(
                """SELECT csl.sku, cr.sample_accession, cr.description,
                          cr.batch_no, cr.sample_status, cr.coa_approved_date,
                          cr.coa_approved_filepath, cr.source
                   FROM coa_sku_links csl
                   JOIN coa_results cr ON csl.sample_accession = cr.sample_accession
                   ORDER BY cr.coa_approved_date DESC"""
            )
            coa_rows = await coa_cursor.fetchall()

            # Collect all linked accessions to fetch analytes in one query
            linked_accessions = list({row[1] for row in coa_rows})
            analyte_by_acc: dict[str, list[dict]] = {}
            if linked_accessions:
                placeholders = ",".join("?" for _ in linked_accessions)
                analyte_cursor = await coa_db.execute(
                    f"""SELECT sample_accession, panel_name, analyte_identifier,
                               concentration, conc_unit, result, result_unit,
                               analyte_remark, panel_remark
                        FROM coa_analyte_results
                        WHERE sample_accession IN ({placeholders})
                        ORDER BY panel_name, analyte_identifier""",
                    tuple(linked_accessions),
                )
                for arow in await analyte_cursor.fetchall():
                    acc_key = arow[0]
                    if acc_key not in analyte_by_acc:
                        analyte_by_acc[acc_key] = []
                    analyte_by_acc[acc_key].append({
                        "panel_name": arow[1],
                        "analyte": arow[2],
                        "concentration": arow[3],
                        "conc_unit": arow[4],
                        "result": arow[5],
                        "result_unit": arow[6],
                        "remark": arow[7],
                        "panel_remark": arow[8],
                    })
        finally:
            await coa_db.close()

        coa_by_sku: dict[str, list[dict]] = {}
        for row in coa_rows:
            sku_key = row[0]
            accession = row[1]
            if sku_key not in coa_by_sku:
                coa_by_sku[sku_key] = []

            # Build the COA PDF URL. Manually-added (non-ACS) COAs point to the
            # uploaded PDF (or a pasted external URL) — NOT the ACS portal, which
            # only knows ACS batches and returns "no COAs available" for them.
            filepath = row[6]
            source = (row[7] or "").lower()
            if source == "manual":
                if filepath and filepath.startswith(("http://", "https://")):
                    coa_pdf_url = filepath
                elif filepath:
                    coa_pdf_url = (
                        os.environ.get("BASE_URL", "https://thd-inventory-api.fly.dev")
                        + filepath
                    )
                else:
                    coa_pdf_url = ""
            else:
                coa_pdf_url = f"https://portal.acslabcannabis.com/reports/view-public-coa?orderids=%5B%22{url_quote(accession)}%22%5D&lang=en"

            # Group analytes by panel
            raw_analytes = analyte_by_acc.get(accession, [])
            panels: dict[str, list[dict]] = {}
            panel_remarks: dict[str, str] = {}
            for a in raw_analytes:
                pname = a["panel_name"] or "Other"
                if pname not in panels:
                    panels[pname] = []
                    panel_remarks[pname] = a.get("panel_remark", "")
                panels[pname].append({
                    "analyte": a["analyte"],
                    "result": a["result"],
                    "result_unit": a["result_unit"],
                    "concentration": a["concentration"],
                    "conc_unit": a["conc_unit"],
                    "remark": a["remark"],
                })
            analyte_panels = [
                {"panel_name": pname, "panel_remark": panel_remarks.get(pname, ""), "analytes": analytes_list}
                for pname, analytes_list in panels.items()
            ]

            coa_by_sku[sku_key].append({
                "sample_accession": accession,
                "description": row[2],
                "batch_no": row[3],
                "sample_status": row[4],
                "coa_approved_date": row[5],
                "coa_pdf_url": coa_pdf_url,
                "panels": analyte_panels,
            })

        products = []
        categories_set: set = set()

        for item in all_items:
            if item.get("hidden", False):
                continue
            sku_or_id = item.get("sku", "") or item.get("id", "")
            if sku_or_id in hidden_skus:
                continue

            name = item.get("name", "")
            sku = item.get("sku", "") or item.get("id", "")
            price = item.get("price", 0)
            item_categories = [c.get("name", "") for c in item.get("categories", {}).get("elements", [])]

            # Remap apparel items (hoodies, t-shirts, shirts) to "Apparel" category
            name_lower = name.lower()
            is_apparel = bool(re.search(r'\b(hoodie|t-shirt|shirt|tee|jersey|hat|beanie)\b', name_lower))
            if is_apparel:
                item_categories = [c if c != "Accessories" else "Apparel" for c in item_categories]
                if not item_categories:
                    item_categories = ["Apparel"]
            stock_info = item.get("itemStock", {})
            hq_stock = stock_info.get("quantity", 0) if stock_info else 0
            # Look up description by product name first (handles shared-SKU items like syringes),
            # then fall back to SKU-based lookup, then Clover's own description field
            description = desc_by_name.get(name.upper(), "") or desc_by_sku.get(sku, "") or item.get("description", "")
            online_name = item.get("onlineName", "") or name

            # Look up stock at West and East by SKU first, then by name
            lookup_key = sku if sku else name
            w_stock = west_stock.get(lookup_key, 0)
            e_stock = east_stock.get(lookup_key, 0)
            if w_stock == 0 and sku:
                normalized_name = " ".join(name.split())
                w_stock = west_stock.get(normalized_name, 0)
            if e_stock == 0 and sku:
                normalized_name = " ".join(name.split())
                e_stock = east_stock.get(normalized_name, 0)

            for cat in item_categories:
                categories_set.add(cat)

            image_url = image_by_sku.get(sku)
            if not image_url:
                image_url = image_by_name.get(name.upper())
            # Every gummy uses the single canonical cube image
            name_up = name.upper()
            if "GUMMIES" in name_up or "GUMMY" in name_up:
                image_url = gummy_image_url

            slug = name.lower()
            slug = slug.replace("/", "-").replace('"', "").replace("(", "").replace(")", "").replace("$", "").replace("'", "").replace("&", "-and-")
            slug = slug.replace(" ", "-").replace(",", "").replace(".", "")
            # Strip any remaining URL/filename-unsafe characters (e.g. # ? % :) so
            # slugs work as routes and as static file paths on the CDN.
            slug = re.sub(r"[^a-z0-9-]", "-", slug)
            slug = re.sub(r"-+", "-", slug).strip("-")
            slug = "-".join(slug.split())

            # LeafLife products (SKU starts with LF-) are shipped from supplier, not available for pickup
            is_shipping_only = sku.startswith("LF-") if isinstance(sku, str) else False

            # Enforce minimum price floors for LeafLife products by weight
            price = _enforce_leaflife_price_floor(sku, name, price)

            total_stock = max(hq_stock, w_stock, e_stock)

            # Look up stored effect/strength attributes (by name first for shared-SKU items, then SKU)
            sku_attrs = attrs_by_name.get(name.upper(), {}) or attrs_by_sku.get(sku, {})

            products.append({
                "id": item.get("id", ""),
                "name": name,
                "online_name": online_name,
                "slug": slug,
                "sku": sku,
                "price": price,
                "description": description,
                "categories": item_categories,
                "stock": total_stock,
                "stock_hq": hq_stock,
                "stock_west": w_stock,
                "stock_east": e_stock,
                "available": item.get("available", True) and total_stock > 0,
                "image_url": image_url,
                "is_age_restricted": item.get("isAgeRestricted", False),
                "shipping_only": is_shipping_only,
                "effect": sku_attrs.get("effect"),
                "strength": sku_attrs.get("strength"),
                "product_type": sku_attrs.get("product_type"),
                "modified_time": item.get("modifiedTime", 0),
                "lab_results": coa_by_sku.get(sku) or coa_by_sku.get(item.get("id", ""), []),
            })

        # Deduplicate products by SKU: Clover can return multiple items with the
        # same SKU (e.g. from item-group recreation). Prefer the entry with the
        # highest HQ stock (modified_time as tiebreaker) for metadata (price,
        # name, categories, etc.) and merge stock counts so nothing is lost.
        seen_skus: dict[str, int] = {}  # sku -> index in products list
        deduped: list[dict] = []
        for p in products:
            sku = p.get("sku", "")
            if sku and sku in seen_skus:
                idx = seen_skus[sku]
                existing = deduped[idx]
                # Decide which entry is the "primary" (better metadata source):
                # prefer higher HQ stock, then more recent modified_time.
                p_better = (
                    p["stock_hq"] > existing["stock_hq"]
                    or (p["stock_hq"] == existing["stock_hq"]
                        and (p.get("modified_time") or 0) > (existing.get("modified_time") or 0))
                )
                if p_better:
                    # Swap: use p as the base entry, merge stock from existing
                    primary, secondary = p, existing
                else:
                    primary, secondary = existing, p
                # Merge stock: take the max at each location
                primary["stock_hq"] = max(primary["stock_hq"], secondary["stock_hq"])
                primary["stock_west"] = max(primary["stock_west"], secondary["stock_west"])
                primary["stock_east"] = max(primary["stock_east"], secondary["stock_east"])
                primary["stock"] = max(primary["stock_hq"], primary["stock_west"], primary["stock_east"])
                primary["available"] = primary["available"] or secondary["available"]
                # Fill in missing image / description from either entry
                if not primary.get("image_url") and secondary.get("image_url"):
                    primary["image_url"] = secondary["image_url"]
                if not primary.get("description") and secondary.get("description"):
                    primary["description"] = secondary["description"]
                deduped[idx] = primary
            else:
                seen_skus[sku] = len(deduped)
                deduped.append(p)
        products = deduped

        products.sort(key=lambda p: p["name"])

        result = {
            "products": products,
            "total": len(products),
            "categories": sorted(categories_set),
        }

        _product_cache = result
        _cache_timestamp = time.time()
        # Pre-serialize JSON so /products endpoint returns bytes instantly
        _product_cache_json = json.dumps(
            {"products": products, "total": len(products), "categories": result["categories"]}
        ).encode()

        total_time = time.time() - start_time
        print(f"[cache] Product cache refreshed: {len(products)} products in {total_time:.1f}s")

        # Save to disk for fast recovery after restart
        _save_disk_cache(result)

        # Auto-tag new products that have no attributes or descriptions (background)
        asyncio.create_task(_auto_tag_new_products(products, attrs_by_sku, attrs_by_name, desc_by_sku, desc_by_name))

        return result
    except Exception as e:
        print(f"[cache] Refresh failed: {e}")
        if _product_cache:
            return _product_cache
        raise
    finally:
        _refresh_in_progress = False
        if _fetch_event:
            _fetch_event.set()


_AUTOTAG_SYSTEM_PROMPT = """You are an expert cannabis sommelier and budtender with 15+ years of experience. You have encyclopedic knowledge of hemp and cannabis strains, their genetics, terpene profiles, and effects.

For the given product, determine:
1. STRAIN_TYPE: Must be exactly one of: Sativa, Indica, Hybrid, or N/A (for products with no strain like tinctures, topicals, edibles with no strain name)
2. EFFECT_TAG: Must be exactly one of: Energy, Sleep, Relax, Focus

Guidelines for STRAIN_TYPE:
- If the product name contains a known strain name, identify it accurately
- Sativas: Blue Dream, Maui Wowie, Jack Herer, Sour Diesel, Green Crack, Durban Poison, Super Lemon Haze, Tropicana Cookies, Pineapple Express, Strawberry Cough, and similar uplifting strains
- Indicas: OG Kush, Tahoe OG, King Louis, Purple Punch, Granddaddy Purple, Bubba Kush, Gorilla Glue, Northern Lights, Blueberry, and similar relaxing strains
- Hybrids: Gelato, Wedding Cake, Runtz, Cereal Milk, Biscotti, Mimosa, Girl Scout Cookies, Banana Runtz, Watermelon, and similar balanced strains
- If the product explicitly says Sativa, Indica, or Hybrid in the name, use that
- For edibles, tinctures, distillates with no strain name: use N/A

Guidelines for EFFECT_TAG:
- Energy: Sativas and uplifting hybrids — daytime use, creative, focused, social
- Sleep: Heavy indicas, CBN products, products explicitly for sleep or nighttime
- Relax: Mid indicas, balanced hybrids, CBD-dominant products, topicals, muscle relief products
- Focus: CBG products, nootropic blends, microdose products, clarity-focused products
- For multi-cannabinoid products (CBD/CBG/CBN blends): use the dominant intended effect
- Delta-8 products: generally Relax unless strain suggests otherwise
- Delta-9 edibles with no strain: Relax
- THCA flower: follow the strain

3. DESCRIPTION: Write a 2-3 sentence SEO-optimized product description for The Hemp Dispensary, a licensed hemp retailer in Spring Hill, Florida.
Description rules:
- Be factual and specific — use the product name, category, and any attributes provided
- Do not make medical claims or say the product treats, cures, or prevents anything
- Do not use the words "marijuana", "cannabis", "medicate", "medication", "dose", or "dosing" — use "hemp", "enjoy", "experience", or "use" instead
- Mention that products are lab-tested and compliant with federal hemp regulations where natural
- Write in a friendly, knowledgeable tone — like a budtender talking to a customer
- 2-3 sentences maximum, no bullet points, plain text only

Respond ONLY with valid JSON in this exact format, nothing else:
{"strain_type": "Sativa|Indica|Hybrid|N/A", "effect_tag": "Energy|Sleep|Relax|Focus", "confidence": "high|medium|low", "description": "2-3 sentence SEO description"}"""

# Categories that are non-consumable and should not be tagged
_SKIP_CATEGORIES = {"Accessories", "Packaging", "Apparel"}


async def _auto_tag_new_products(
    products: list,
    attrs_by_sku: dict,
    attrs_by_name: dict,
    desc_by_sku: dict,
    desc_by_name: dict,
) -> None:
    """Background task: find products missing attributes or descriptions, call Anthropic API to tag them.

    The product cache refreshes every few minutes and on every inventory edit, so
    this runs at most once per AUTOTAG_RUN_INTERVAL and only on a small batch of
    products it has not already given up on — otherwise every refresh would re-send
    the same unclassifiable products to Anthropic forever.
    """
    global _autotag_last_run, _autotag_running
    if not ANTHROPIC_API_KEY or not AUTOTAG_ENABLED or _autotag_running:
        return
    now = time.time()
    if now - _autotag_last_run < AUTOTAG_RUN_INTERVAL:
        return
    _autotag_last_run = now
    _autotag_running = True
    try:
        await _run_auto_tag_pass(products, attrs_by_sku, attrs_by_name, desc_by_sku, desc_by_name)
    finally:
        _autotag_running = False


class _AutoTagUnavailable(Exception):
    """Anthropic refused the whole pass (auth, quota or rate limit)."""


def _autotag_key(sku: str, name: str) -> str:
    return sku or name.upper()


async def _load_autotag_attempts() -> dict[str, int]:
    """Attempt counts per product, so exhausted products are skipped."""
    from app.database import DB_PATH

    db = await aiosqlite.connect(DB_PATH)
    try:
        cursor = await db.execute("SELECT product_key, attempts FROM product_autotag_attempts")
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return {row[0]: row[1] for row in rows}


async def _record_autotag_attempt(db: aiosqlite.Connection, product_key: str) -> None:
    await db.execute(
        """INSERT INTO product_autotag_attempts (product_key, attempts)
           VALUES (?, 1)
           ON CONFLICT(product_key) DO UPDATE SET
               attempts = attempts + 1,
               last_attempt_at = CURRENT_TIMESTAMP""",
        (product_key,),
    )


async def _run_auto_tag_pass(
    products: list,
    attrs_by_sku: dict,
    attrs_by_name: dict,
    desc_by_sku: dict,
    desc_by_name: dict,
) -> None:
    attempts_by_key = await _load_autotag_attempts()

    # Collect products that need tagging (no attributes) or descriptions
    needs_tagging: list[dict] = []
    for p in products:
        sku = p.get("sku", "")
        name = p.get("name", "")
        cats = p.get("categories", [])

        if attempts_by_key.get(_autotag_key(sku, name), 0) >= AUTOTAG_MAX_ATTEMPTS:
            continue

        # Skip non-consumable categories
        if any(c in _SKIP_CATEGORIES for c in cats):
            continue
        # Skip pet products
        if "pet" in name.lower() or "Pets" in cats:
            continue

        has_attrs = bool(
            attrs_by_name.get(name.upper(), {}).get("effect")
            or attrs_by_sku.get(sku, {}).get("effect")
        )
        has_desc = bool(
            desc_by_name.get(name.upper())
            or desc_by_sku.get(sku)
            or p.get("description")
        )

        if not has_attrs or not has_desc:
            needs_tagging.append({
                "sku": sku,
                "name": name,
                "categories": cats,
                "description": p.get("description", ""),
                "needs_attrs": not has_attrs,
                "needs_desc": not has_desc,
            })

    if not needs_tagging:
        return

    pending = len(needs_tagging)
    needs_tagging = needs_tagging[:AUTOTAG_MAX_PER_RUN]
    print(f"[auto-tag] {pending} products need tagging/description; doing {len(needs_tagging)} this pass")

    from app.database import DB_PATH

    tagged = 0
    # Process in batches of 10
    for i in range(0, len(needs_tagging), 10):
        batch = needs_tagging[i : i + 10]
        for product in batch:
            try:
                result = await _call_anthropic_for_tag(product)

                db = await aiosqlite.connect(DB_PATH)
                try:
                    await _record_autotag_attempt(
                        db, _autotag_key(product["sku"], product["name"])
                    )
                    if not result:
                        await db.commit()
                        continue
                    # Save attributes if needed
                    if product["needs_attrs"] and result.get("effect_tag"):
                        strain = result.get("strain_type", "N/A")
                        effect = result.get("effect_tag", "Relax")
                        confidence = result.get("confidence", "low")
                        if confidence != "low":
                            await db.execute(
                                """INSERT INTO product_attributes (sku, product_name, effect, strength, product_type)
                                   VALUES (?, ?, ?, ?, ?)
                                   ON CONFLICT(sku) DO UPDATE SET
                                       product_name = excluded.product_name,
                                       effect = excluded.effect,
                                       product_type = excluded.product_type,
                                       updated_at = CURRENT_TIMESTAMP""",
                                (product["sku"], product["name"], effect, "", strain if strain != "N/A" else ""),
                            )

                    # Save description if needed
                    if product["needs_desc"] and result.get("description"):
                        await db.execute(
                            """INSERT INTO product_descriptions (sku, product_name, description)
                               VALUES (?, ?, ?)
                               ON CONFLICT(sku, product_name) DO UPDATE SET
                                   description = excluded.description,
                                   updated_at = CURRENT_TIMESTAMP""",
                            (product["sku"], product["name"], result["description"]),
                        )

                    await db.commit()
                    tagged += 1
                    print(f"[auto-tag] Tagged: {product['name']} -> {result.get('strain_type')}/{result.get('effect_tag')}")
                finally:
                    await db.close()

            except _AutoTagUnavailable as e:
                print(f"[auto-tag] Stopping pass — Anthropic unavailable: {e}")
                return
            except Exception as e:
                print(f"[auto-tag] Error tagging {product['name']}: {e}")

        # Brief pause between batches to avoid rate limits
        if i + 10 < len(needs_tagging):
            await asyncio.sleep(1)

    if tagged > 0:
        print(f"[auto-tag] Successfully tagged {tagged}/{len(needs_tagging)} products")
        # Invalidate cache so next request picks up new attributes
        invalidate_product_cache()


async def _call_anthropic_for_tag(product: dict) -> dict:
    """Call Anthropic API to get strain type, effect tag, and description for a product."""
    name = product["name"]
    cats = ", ".join(product["categories"]) if product["categories"] else "Unknown"
    desc = product.get("description", "") or ""

    user_msg = f"Product name: {name}\nCategory: {cats}"
    if desc:
        user_msg += f"\nDescription: {desc}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": AUTOTAG_MODEL,
                    "max_tokens": 512,
                    "system": _AUTOTAG_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )
            if resp.status_code != 200:
                print(f"[auto-tag] Anthropic API error {resp.status_code} for {name}: {resp.text[:200]}")
                # 400 covers a spend cap being hit, which applies to every product,
                # so keep these from burning through the whole batch one by one.
                if resp.status_code in (400, 401, 403, 429, 529):
                    raise _AutoTagUnavailable(f"HTTP {resp.status_code}")
                return {}

            data = resp.json()
            text = data.get("content", [{}])[0].get("text", "")
            # Parse JSON from response
            result = json.loads(text)
            return result
    except json.JSONDecodeError:
        print(f"[auto-tag] Failed to parse JSON for {name}: {text[:200]}")
        return {}
    except _AutoTagUnavailable:
        raise
    except Exception as e:
        print(f"[auto-tag] API call failed for {name}: {e}")
        return {}


async def _get_cached_products() -> dict:
    """Return cached products instantly. Trigger background refresh if stale."""
    global _product_cache, _cache_timestamp
    now = time.time()

    # Fresh cache — return immediately
    if _product_cache and (now - _cache_timestamp) < CACHE_TTL:
        return _product_cache

    # Stale cache — return it immediately but kick off background refresh
    if _product_cache:
        if not _refresh_in_progress:
            asyncio.create_task(_safe_refresh())
        return _product_cache

    # No cache at all — try disk cache first
    if await _load_disk_cache():
        if not _refresh_in_progress:
            asyncio.create_task(_safe_refresh())
        return _product_cache

    # No cache anywhere — must wait for first fetch
    return await _fetch_and_cache_products()


async def _safe_refresh():
    """Background refresh that won't duplicate or crash."""
    try:
        await _fetch_and_cache_products()
    except Exception as e:
        print(f"[cache] Background refresh failed: {e}")


@router.get("/products")
async def get_products(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Public endpoint: Get products from Clover eCommerce catalog (HQ location).
    Results are served from an in-memory cache that refreshes every 10 minutes."""

    # Fast path: no filters + pre-serialized JSON available → return raw bytes
    if not category and not search and _product_cache_json:
        return Response(
            content=_product_cache_json,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=120, stale-while-revalidate=300"},
        )

    cached = await _get_cached_products()
    products = cached["products"]

    if category and category.lower() != "all":
        products = [p for p in products if any(c.lower() == category.lower() for c in p["categories"])]
    if search:
        search_lower = search.lower()
        products = [p for p in products if search_lower in p["name"].lower() or search_lower in (p.get("description") or "").lower()]

    return JSONResponse(
        content={"products": products, "total": len(products), "categories": cached["categories"]},
        headers={"Cache-Control": "public, max-age=120, stale-while-revalidate=300"},
    )


@router.post("/products/refresh")
async def refresh_products():
    """Force refresh the product cache from Clover API."""
    global _product_cache, _product_cache_json, _cache_timestamp
    _product_cache = {}
    _product_cache_json = b""
    _cache_timestamp = 0.0
    # Delete disk cache too
    try:
        if os.path.exists(DISK_CACHE_PATH):
            os.remove(DISK_CACHE_PATH)
    except Exception:
        pass
    result = await _fetch_and_cache_products()
    return {"status": "refreshed", "total": result["total"], "categories": result["categories"]}


class ValidatePromoRequest(BaseModel):
    promo_code: str
    email: str
    phone: str = ""


async def _fetch_active_direct_discounts(db: aiosqlite.Connection) -> list:
    """Return every direct discount that is live online right now (US Eastern).

    A missing start or end date means "no bound", so a direct discount created
    without dates runs until it is deactivated.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_eastern = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M")
    cursor = await db.execute(
        """SELECT code, discount_pct, excluded_brands, starts_at, expires_at, applies_to, product_ids
           FROM promo_codes
           WHERE is_direct_discount = 1
             AND is_active = 1
             AND (in_store_only = 0 OR in_store_only IS NULL)
             AND discount_pct > 0
             AND (starts_at IS NULL OR starts_at = ''
                  OR (CASE WHEN LENGTH(starts_at) <= 10 THEN starts_at || 'T00:00' ELSE starts_at END) <= ?)
             AND (expires_at IS NULL OR expires_at = ''
                  OR (CASE WHEN LENGTH(expires_at) <= 10 THEN expires_at || 'T23:59' ELSE expires_at END) >= ?)
           ORDER BY discount_pct DESC""",
        (now_eastern, now_eastern),
    )
    return await cursor.fetchall()


def _is_sitewide_sale(row) -> bool:
    """True when a direct discount applies to the whole catalog (not select items)."""
    applies_to = row["applies_to"] if "applies_to" in row.keys() else "all"
    product_ids = (row["product_ids"] or "") if "product_ids" in row.keys() else ""
    return applies_to == "all" or not product_ids.strip()


@router.post("/validate-promo")
async def validate_promo(
    body: ValidatePromoRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Validate a promo code and check if customer can use it."""
    code = body.promo_code.strip().upper()
    email = body.email.strip().lower()

    from datetime import datetime
    from zoneinfo import ZoneInfo
    eastern = ZoneInfo("America/New_York")

    # Block promo codes only during a sitewide sale (loyalty rewards are still
    # allowed). Sales on select items leave the rest of the catalog eligible.
    for row in await _fetch_active_direct_discounts(db):
        if _is_sitewide_sale(row):
            pct = round(row["discount_pct"] * 100)
            return {"valid": False, "reason": f"Promo codes are disabled during our {pct}% OFF sale. Loyalty rewards can still be redeemed at checkout."}

    cursor = await db.execute("SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (code,))
    promo = await cursor.fetchone()
    if not promo:
        return {"valid": False, "reason": "Invalid promo code"}

    # Direct discounts have no customer-facing code — their name must never work
    # as one, or the discount would stack on top of the already-reduced price.
    if "is_direct_discount" in promo.keys() and promo["is_direct_discount"]:
        return {"valid": False, "reason": "Invalid promo code"}

    # In-store-only discounts can never be redeemed on the online store
    if "in_store_only" in promo.keys() and promo["in_store_only"]:
        return {"valid": False, "reason": "This discount is only available in store"}

    # Check expiration (dates are stored in Eastern time)
    now_et = datetime.now(eastern).strftime("%Y-%m-%dT%H:%M")
    if promo["expires_at"]:
        try:
            exp_str = promo["expires_at"]
            if "T" not in exp_str:
                exp_str += "T23:59"
            if now_et > exp_str:
                return {"valid": False, "reason": "This promo code has expired"}
        except Exception:
            pass

    # Check start date
    starts_at = promo["starts_at"] if "starts_at" in promo.keys() else None
    if starts_at:
        try:
            start_str = starts_at
            if "T" not in start_str:
                start_str += "T00:00"
            if now_et < start_str:
                return {"valid": False, "reason": "This promo code is not yet active"}
        except Exception:
            pass

    # Check max uses
    if promo["max_uses"] > 0 and promo["times_used"] >= promo["max_uses"]:
        return {"valid": False, "reason": "This promo code has reached its usage limit"}

    # Check single-use per customer (by email AND phone number to prevent multi-account abuse)
    if promo["single_use"]:
        if email:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ecommerce_orders WHERE LOWER(customer_email) = ? AND promo_code = ? AND payment_status != 'cancelled'",
                (email, code),
            )
            count = (await cursor.fetchone())[0]
            if count > 0:
                return {"valid": False, "reason": "This promo code has already been used with this email address"}
        # Also check by phone number to prevent creating new emails to reuse codes
        phone = body.phone.strip().replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if phone and len(phone) >= 10:
            # Normalize: take last 10 digits
            phone_normalized = phone[-10:]
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ecommerce_orders WHERE REPLACE(REPLACE(REPLACE(REPLACE(customer_phone, '-', ''), ' ', ''), '(', ''), ')', '') LIKE ? AND promo_code = ? AND payment_status != 'cancelled'",
                (f"%{phone_normalized}", code),
            )
            count = (await cursor.fetchone())[0]
            if count > 0:
                return {"valid": False, "reason": "This promo code has already been used with this phone number"}

    applies_to = promo["applies_to"] if "applies_to" in promo.keys() else "all"
    product_ids = promo["product_ids"] if "product_ids" in promo.keys() else ""
    exclude_coupons = bool(promo["exclude_from_other_coupons"]) if "exclude_from_other_coupons" in promo.keys() else False

    excluded_brands = promo["excluded_brands"] if "excluded_brands" in promo.keys() else ""

    return {
        "valid": True,
        "discount_pct": promo["discount_pct"],
        "discount_amount": promo["discount_amount"],
        "code": code,
        "applies_to": applies_to,
        "product_ids": product_ids,
        "exclude_from_other_coupons": exclude_coupons,
        "excluded_brands": excluded_brands,
    }


@router.get("/brands")
async def list_brands():
    """Return unique brand/category names from the product catalog."""
    cached = await _get_cached_products()
    brands: set = set()
    for product in cached.get("products", []):
        for cat in product.get("categories", []):
            if cat:
                brands.add(cat)
    return sorted(brands)


@router.get("/active-sale")
async def active_sale(db: aiosqlite.Connection = Depends(get_db)):
    """Public endpoint: Every Direct Discount that is live online now (US Eastern).

    Several direct discounts can run at once (e.g. one per product family), so
    the response carries them all in ``sales``; the top-level fields describe the
    deepest one for older clients.
    """
    rows = await _fetch_active_direct_discounts(db)
    if not rows:
        return {"active": False, "sales": [], "promos_disabled": False}

    def _serialize(row) -> dict:
        applies_to = row["applies_to"] if "applies_to" in row.keys() else "all"
        product_ids = (
            [pid.strip() for pid in (row["product_ids"] or "").split(",") if pid.strip()]
            if applies_to == "specific" else []
        )
        return {
            "name": row["code"],
            "discount_percent": round(row["discount_pct"] * 100, 2),
            "excluded_brands": [b.strip() for b in (row["excluded_brands"] or "").split(",") if b.strip()],
            "applies_to": applies_to,
            "product_ids": product_ids,
            "start_date": row["starts_at"],
            "end_date": row["expires_at"],
        }

    sales = [_serialize(row) for row in rows]
    return {
        "active": True,
        **{k: v for k, v in sales[0].items() if k != "name"},
        "sales": sales,
        "promos_disabled": any(_is_sitewide_sale(row) for row in rows),
    }


# ── Promo Code Management (Admin) ────────────────────────────────────────────


def _norm_discount_name(name: str) -> str:
    return " ".join((name or "").strip().upper().split())


def _extract_applied_discount_codes(order: dict, cdid_to_code: dict, name_to_code: dict) -> set:
    """Return the set of our promo codes applied to a Clover order.

    Register/POS discounts show up either at the order level or on individual
    line items, each carrying a name and (usually) a reference to the discount
    definition. Match by the definition id first, then fall back to the name.
    """
    codes: set = set()

    def _match(d: dict):
        ref_id = (d.get("discount") or {}).get("id") or d.get("id")
        if ref_id and ref_id in cdid_to_code:
            codes.add(cdid_to_code[ref_id])
            return
        code = name_to_code.get(_norm_discount_name(d.get("name", "")))
        if code:
            codes.add(code)

    for d in (order.get("discounts") or {}).get("elements", []):
        _match(d)
    for li in (order.get("lineItems") or {}).get("elements", []):
        for d in (li.get("discounts") or {}).get("elements", []):
            _match(d)
    return codes


async def _sync_clover_discount_uses(db: aiosqlite.Connection, max_orders_per_location: int = 5000) -> dict:
    """Scan Clover orders across all locations and record in-store redemptions
    of our promo codes into ``clover_discount_uses`` (idempotent upserts) so the
    promo "Uses" count reflects register use, not just website orders."""
    cursor = await db.execute("SELECT id, code FROM promo_codes")
    promo_rows = await cursor.fetchall()
    if not promo_rows:
        return {"recorded": 0, "scanned": 0}
    code_by_id = {row["id"]: row["code"] for row in promo_rows}
    name_to_code = {_norm_discount_name(row["code"]): row["code"] for row in promo_rows}

    cursor = await db.execute(
        "SELECT discount_id, clover_discount_id FROM clover_discount_map WHERE discount_type = 'promo'"
    )
    cdid_to_code = {}
    for row in await cursor.fetchall():
        code = code_by_id.get(row["discount_id"])
        if code and row["clover_discount_id"]:
            cdid_to_code[row["clover_discount_id"]] = code

    clients = await _get_all_location_clients(db)
    recorded = 0
    scanned = 0
    for merchant_id, loc_name, client in clients:
        try:
            offset = 0
            while offset < max_orders_per_location:
                data = await client.get_orders(
                    limit=100, offset=offset,
                    expand="discounts,lineItems.discounts",
                    filters=["payType!=NULL"],
                )
                orders = data.get("elements", [])
                if not orders:
                    break
                scanned += len(orders)
                for o in orders:
                    order_id = o.get("id", "")
                    if not order_id:
                        continue
                    ctime = o.get("createdTime", 0) or 0
                    for code in _extract_applied_discount_codes(o, cdid_to_code, name_to_code):
                        cur = await db.execute(
                            "INSERT OR IGNORE INTO clover_discount_uses "
                            "(order_id, merchant_id, discount_code, created_time) VALUES (?, ?, ?, ?)",
                            (order_id, merchant_id, code, ctime),
                        )
                        recorded += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                await db.commit()
                if len(orders) < 100:
                    break
                offset += 100
        except Exception as e:
            print(f"[discount-use-sync] {loc_name} ({merchant_id}): {e}")
    return {"recorded": recorded, "scanned": scanned}


@router.get("/promos")
async def list_promos(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: List all promo codes."""
    cursor = await db.execute("SELECT * FROM promo_codes ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    # Count usage from orders for each promo
    promos = []
    for row in rows:
        cursor2 = await db.execute(
            "SELECT COUNT(*) FROM ecommerce_orders WHERE promo_code = ? AND payment_status != 'cancelled'",
            (row["code"],),
        )
        order_count = (await cursor2.fetchone())[0]
        # In-store (Clover POS) redemptions, synced from Clover orders
        cursor_is = await db.execute(
            "SELECT COUNT(*) FROM clover_discount_uses WHERE discount_code = ?",
            (row["code"],),
        )
        instore_count = (await cursor_is.fetchone())[0]
        promos.append({
            "id": row["id"],
            "code": row["code"],
            "discount_pct": row["discount_pct"],
            "discount_amount": row["discount_amount"],
            "single_use": bool(row["single_use"]),
            "is_active": bool(row["is_active"]),
            "max_uses": row["max_uses"],
            "times_used": order_count + instore_count,
            "expires_at": row["expires_at"],
            "starts_at": row["starts_at"] if "starts_at" in row.keys() else None,
            "applies_to": row["applies_to"] if "applies_to" in row.keys() else "all",
            "product_ids": row["product_ids"] if "product_ids" in row.keys() else "",
            "exclude_from_other_coupons": bool(row["exclude_from_other_coupons"]) if "exclude_from_other_coupons" in row.keys() else False,
            "clover_discount_id": row["clover_discount_id"] if "clover_discount_id" in row.keys() else "",
            "is_direct_discount": bool(row["is_direct_discount"]) if "is_direct_discount" in row.keys() else False,
            "in_store_only": bool(row["in_store_only"]) if "in_store_only" in row.keys() else False,
            "excluded_brands": row["excluded_brands"] if "excluded_brands" in row.keys() else "",
            "sync_to_clover": bool(row["sync_to_clover"]) if "sync_to_clover" in row.keys() else False,
            "created_at": row["created_at"] or "",
        })
    return promos


@router.post("/promos/sync-uses")
async def sync_promo_uses(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: pull in-store (Clover POS) discount redemptions now so the promo
    "Uses" counts refresh immediately instead of waiting for the scheduled job."""
    result = await _sync_clover_discount_uses(db)
    return {"ok": True, **result}


class PromoCreateRequest(BaseModel):
    code: str = ""  # empty for direct discounts
    discount_pct: float = 0
    is_direct_discount: bool = False  # True = no promo code, applied directly to products
    discount_amount: int = 0
    single_use: bool = False
    max_uses: int = 0
    expires_at: Optional[str] = None
    starts_at: Optional[str] = None
    applies_to: str = "all"  # "all", "specific", or "individual"
    product_ids: str = ""  # comma-separated Clover item IDs
    exclude_from_other_coupons: bool = False
    sync_to_clover: bool = True
    excluded_brands: str = ""  # comma-separated brand/category names to exclude
    in_store_only: bool = False  # True = sync to Clover POS only, never apply on the online store


async def _get_hq_clover_client(db: aiosqlite.Connection) -> Optional[CloverClient]:
    """Get CloverClient for HQ location."""
    cursor = await db.execute("SELECT merchant_id, api_token FROM locations WHERE name LIKE '%HQ%' OR id = 1 LIMIT 1")
    row = await cursor.fetchone()
    if row and row["merchant_id"] and row["api_token"]:
        return CloverClient(row["merchant_id"], row["api_token"])
    return None


async def _get_all_location_clients(db: aiosqlite.Connection) -> list[tuple[str, str, "CloverClient"]]:
    """Get CloverClient instances for all store locations.

    Returns list of (merchant_id, location_name, CloverClient) tuples.
    First tries the locations table; falls back to environment variables
    so discount sync works even if the Locations page hasn't been configured.
    """
    seen_merchants: set[str] = set()
    clients: list[tuple[str, str, CloverClient]] = []

    # 1) From the locations table (skip virtual/placeholder entries)
    cursor = await db.execute(
        "SELECT name, merchant_id, api_token FROM locations "
        "WHERE merchant_id != '' AND api_token != '' AND api_token != 'pending' "
        "AND merchant_id NOT LIKE 'virtual-%'"
    )
    rows = await cursor.fetchall()
    for row in rows:
        mid = row["merchant_id"]
        if mid not in seen_merchants:
            seen_merchants.add(mid)
            clients.append((mid, row["name"], CloverClient(mid, row["api_token"])))

    # 2) Fall back to environment variables for any locations not already covered
    env_locations = [
        ("HQ", HQ_MERCHANT_ID, HQ_API_TOKEN),
        ("West", WEST_MERCHANT_ID, WEST_API_TOKEN),
        ("East", EAST_MERCHANT_ID, EAST_API_TOKEN),
    ]
    for name, mid, token in env_locations:
        if mid and token and mid not in seen_merchants:
            seen_merchants.add(mid)
            clients.append((mid, name, CloverClient(mid, token)))

    return clients


async def _sync_discount_to_all_locations(
    db: aiosqlite.Connection,
    discount_id: int,
    discount_type: str,
    name: str,
    percentage: int = 0,
    amount: int = 0,
) -> list[dict]:
    """Create a discount on all Clover locations. Returns list of results per location."""
    clients = await _get_all_location_clients(db)
    results: list[dict] = []
    for merchant_id, location_name, client in clients:
        try:
            resp = await client.create_discount(name=name, percentage=percentage, amount=amount)
            clover_discount_id = resp.get("id", "")
            if clover_discount_id:
                await db.execute(
                    """INSERT OR REPLACE INTO clover_discount_map
                       (discount_id, discount_type, merchant_id, clover_discount_id, location_name)
                       VALUES (?, ?, ?, ?, ?)""",
                    (discount_id, discount_type, merchant_id, clover_discount_id, location_name),
                )
            results.append({"merchant_id": merchant_id, "location": location_name, "clover_id": clover_discount_id})
        except Exception as e:
            print(f"[discount-sync] Create failed for {location_name} ({merchant_id}): {e}")
            results.append({"merchant_id": merchant_id, "location": location_name, "error": str(e)})
    if results:
        await db.commit()
    return results


async def _update_discount_on_all_locations(
    db: aiosqlite.Connection,
    discount_id: int,
    discount_type: str,
    name: str,
    percentage: int = 0,
    amount: int = 0,
) -> list[dict]:
    """Update a discount on all Clover locations. Creates if not yet synced to a location."""
    clients = await _get_all_location_clients(db)
    results: list[dict] = []
    for merchant_id, location_name, client in clients:
        try:
            cursor = await db.execute(
                "SELECT clover_discount_id FROM clover_discount_map "
                "WHERE discount_id = ? AND discount_type = ? AND merchant_id = ?",
                (discount_id, discount_type, merchant_id),
            )
            row = await cursor.fetchone()
            clover_id = row["clover_discount_id"] if row else ""

            if clover_id:
                await client.update_discount(clover_id, name=name, percentage=percentage, amount=amount)
                results.append({"merchant_id": merchant_id, "location": location_name, "clover_id": clover_id, "action": "updated"})
            else:
                resp = await client.create_discount(name=name, percentage=percentage, amount=amount)
                new_id = resp.get("id", "")
                if new_id:
                    await db.execute(
                        """INSERT OR REPLACE INTO clover_discount_map
                           (discount_id, discount_type, merchant_id, clover_discount_id, location_name)
                           VALUES (?, ?, ?, ?, ?)""",
                        (discount_id, discount_type, merchant_id, new_id, location_name),
                    )
                results.append({"merchant_id": merchant_id, "location": location_name, "clover_id": new_id, "action": "created"})
        except Exception as e:
            print(f"[discount-sync] Update failed for {location_name} ({merchant_id}): {e}")
            results.append({"merchant_id": merchant_id, "location": location_name, "error": str(e)})
    if results:
        await db.commit()
    return results


async def _delete_discount_from_all_locations(
    db: aiosqlite.Connection,
    discount_id: int,
    discount_type: str,
) -> list[dict]:
    """Delete a discount from all Clover locations."""
    cursor = await db.execute(
        "SELECT merchant_id, clover_discount_id, location_name "
        "FROM clover_discount_map "
        "WHERE discount_id = ? AND discount_type = ?",
        (discount_id, discount_type),
    )
    mappings = await cursor.fetchall()
    results: list[dict] = []
    clients = await _get_all_location_clients(db)
    client_map = {mid: c for mid, _, c in clients}

    for mapping in mappings:
        merchant_id = mapping["merchant_id"]
        clover_id = mapping["clover_discount_id"]
        location_name = mapping["location_name"] or merchant_id
        try:
            client = client_map.get(merchant_id)
            if client:
                await client.delete_discount(clover_id)
            results.append({
                "merchant_id": merchant_id,
                "location": location_name, "action": "deleted",
            })
        except Exception as e:
            print(f"[discount-sync] Delete failed for {location_name} ({merchant_id}): {e}")
            results.append({
                "merchant_id": merchant_id,
                "location": location_name, "error": str(e),
            })

    await db.execute(
        "DELETE FROM clover_discount_map "
        "WHERE discount_id = ? AND discount_type = ?",
        (discount_id, discount_type),
    )
    await db.commit()
    return results


async def _build_clover_promo_name(client: Optional[CloverClient], code: str,
                                    is_direct: bool, applies_to: str,
                                    product_ids: str, discount_pct: float,
                                    discount_amount: int) -> str:
    """Build a descriptive Clover discount name.

    For discounts targeting specific products (both promo codes and direct
    discounts), include the product names so POS staff knows which items
    the discount applies to and doesn't apply it to other items.
    Clover discount names can be up to 127 chars.
    """
    # If user gave a custom name (not auto-generated), use it directly
    has_custom_name = is_direct and code and not code.startswith("DIRECT-")
    if applies_to != "specific" or not product_ids or not client:
        return code

    # Look up product names from Clover
    ids = [pid.strip() for pid in product_ids.split(",") if pid.strip()]
    if not ids:
        return code

    names: list[str] = []
    try:
        cached = await _get_cached_products()
        id_to_name = {p["id"]: p.get("name", p["id"]) for p in cached.get("products", [])}
        for pid in ids:
            names.append(id_to_name.get(pid, pid))
    except Exception:
        names = ids  # fall back to raw IDs

    # Build discount description with product names
    if discount_pct > 0:
        desc = f"{int(round(discount_pct * 100))}% off"
    elif discount_amount > 0:
        desc = f"${discount_amount / 100:.2f} off"
    else:
        desc = "Discount"

    product_list = ", ".join(names)
    if has_custom_name:
        label = f"{code} ({desc}: {product_list})"
    elif is_direct:
        label = f"{desc}: {product_list}"
    else:
        label = f"{code} {desc}: {product_list} ONLY"
    # Truncate to 64 chars (Clover name limit)
    if len(label) > 64:
        label = label[:61] + "..."
    return label


@router.post("/promos")
async def create_promo(body: PromoCreateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Create a new promo code or direct discount."""
    if body.is_direct_discount:
        if body.code.strip():
            # User provided a custom name for the direct discount; it's only ever
            # displayed, never typed by a shopper, so keep the capitalization.
            code = body.code.strip()
        else:
            # Auto-generate an internal code for direct discounts
            import uuid
            code = "DIRECT-" + uuid.uuid4().hex[:8].upper()
    else:
        code = body.code.strip().upper()
        if not code:
            raise HTTPException(status_code=400, detail="Promo code is required")
    clover_discount_id = ""

    try:
        cursor = await db.execute(
            """INSERT INTO promo_codes (code, discount_pct, discount_amount, single_use, max_uses,
               expires_at, starts_at, applies_to, product_ids, exclude_from_other_coupons, clover_discount_id, is_direct_discount, excluded_brands, sync_to_clover, in_store_only)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, body.discount_pct, body.discount_amount, int(body.single_use), body.max_uses,
             body.expires_at, body.starts_at, body.applies_to, body.product_ids,
             int(body.exclude_from_other_coupons), clover_discount_id, int(body.is_direct_discount), body.excluded_brands, int(body.sync_to_clover), int(body.in_store_only)),
        )
        await db.commit()
        promo_id = cursor.lastrowid
    except Exception:
        raise HTTPException(status_code=400, detail=f"Promo code '{code}' already exists")

    # Sync to all Clover POS locations (non-blocking: errors logged, don't fail response)
    clover_sync_ok = False
    clover_sync_errors: list[str] = []
    if body.sync_to_clover:
        try:
            pct = int(round(body.discount_pct * 100)) if body.discount_pct > 0 else 0
            amt = body.discount_amount if body.discount_amount > 0 else 0
            clients = await _get_all_location_clients(db)
            if not clients:
                clover_sync_errors.append("No Clover locations configured")
            else:
                ref_client = clients[0][2]
                clover_name = await _build_clover_promo_name(
                    ref_client, code, body.is_direct_discount, body.applies_to,
                    body.product_ids, body.discount_pct, body.discount_amount,
                )
                sync_results = await _sync_discount_to_all_locations(
                    db, promo_id, "promo", name=clover_name,
                    percentage=pct, amount=amt,
                )
                # Store first successful Clover ID in legacy column for backward compat
                for r in sync_results:
                    if r.get("clover_id"):
                        clover_discount_id = r["clover_id"]
                        clover_sync_ok = True
                        await db.execute(
                            "UPDATE promo_codes SET clover_discount_id = ? WHERE id = ?",
                            (clover_discount_id, promo_id),
                        )
                        await db.commit()
                        break
                    elif r.get("error"):
                        clover_sync_errors.append(f"{r.get('location', '?')}: {r['error']}")
        except Exception as e:
            print(f"[promo] Clover sync failed: {e}")
            clover_sync_errors.append(str(e))

    return {
        "status": "created", "code": code,
        "clover_discount_id": clover_discount_id,
        "clover_synced": clover_sync_ok,
        "clover_sync_errors": clover_sync_errors,
    }


class PromoUpdateRequest(BaseModel):
    code: Optional[str] = None
    discount_pct: Optional[float] = None
    discount_amount: Optional[int] = None
    single_use: Optional[bool] = None
    is_active: Optional[bool] = None
    max_uses: Optional[int] = None
    expires_at: Optional[str] = None
    starts_at: Optional[str] = None
    applies_to: Optional[str] = None
    product_ids: Optional[str] = None
    exclude_from_other_coupons: Optional[bool] = None
    sync_to_clover: Optional[bool] = None
    excluded_brands: Optional[str] = None
    in_store_only: Optional[bool] = None


@router.put("/promos/{promo_id}")
async def update_promo(promo_id: int, body: PromoUpdateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Update an existing promo code."""
    updates = []
    params = []

    cursor = await db.execute(
        "SELECT code, is_direct_discount FROM promo_codes WHERE id = ?", (promo_id,)
    )
    current = await cursor.fetchone()
    if not current:
        raise HTTPException(status_code=404, detail="Discount not found")

    renamed = False
    if body.code is not None:
        # Promo codes are the string a shopper types, so they stay uppercase;
        # a direct discount's code is only a display name, so keep it as typed.
        new_code = body.code.strip()
        if not current["is_direct_discount"]:
            new_code = new_code.upper()
        if not new_code:
            raise HTTPException(status_code=400, detail="Name is required")
        if new_code != current["code"]:
            cursor = await db.execute(
                "SELECT id FROM promo_codes WHERE code = ? AND id != ?", (new_code, promo_id)
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail=f"'{new_code}' is already in use")
            updates.append("code = ?")
            params.append(new_code)
            renamed = True

    if body.discount_pct is not None:
        updates.append("discount_pct = ?")
        params.append(body.discount_pct)
    if body.discount_amount is not None:
        updates.append("discount_amount = ?")
        params.append(body.discount_amount)
    if body.single_use is not None:
        updates.append("single_use = ?")
        params.append(int(body.single_use))
    if body.is_active is not None:
        updates.append("is_active = ?")
        params.append(int(body.is_active))
    if body.max_uses is not None:
        updates.append("max_uses = ?")
        params.append(body.max_uses)
    if body.expires_at is not None:
        updates.append("expires_at = ?")
        params.append(body.expires_at if body.expires_at else None)
    if body.starts_at is not None:
        updates.append("starts_at = ?")
        params.append(body.starts_at if body.starts_at else None)
    if body.applies_to is not None:
        updates.append("applies_to = ?")
        params.append(body.applies_to)
    if body.product_ids is not None:
        updates.append("product_ids = ?")
        params.append(body.product_ids)
    if body.exclude_from_other_coupons is not None:
        updates.append("exclude_from_other_coupons = ?")
        params.append(int(body.exclude_from_other_coupons))
    if body.excluded_brands is not None:
        updates.append("excluded_brands = ?")
        params.append(body.excluded_brands)
    if body.sync_to_clover is not None:
        updates.append("sync_to_clover = ?")
        params.append(int(body.sync_to_clover))
    if body.in_store_only is not None:
        updates.append("in_store_only = ?")
        params.append(int(body.in_store_only))
    if not updates:
        return {"status": "no changes"}
    if updates:
        params.append(promo_id)
        await db.execute(f"UPDATE promo_codes SET {', '.join(updates)} WHERE id = ?", params)
        await db.commit()

    # Sync to all Clover POS locations (non-blocking: errors logged, don't fail response)
    # A rename also has to be pushed, or the register keeps showing the old name.
    if body.sync_to_clover is True or renamed:
        try:
            cursor = await db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,))
            promo = await cursor.fetchone()
            if promo and (body.sync_to_clover is True or promo["sync_to_clover"]):
                pct_val = body.discount_pct if body.discount_pct is not None else promo["discount_pct"]
                amt_val = body.discount_amount if body.discount_amount is not None else promo["discount_amount"]
                pct = int(round(pct_val * 100)) if pct_val > 0 else 0
                amt = amt_val if amt_val > 0 else 0
                is_direct = bool(promo["is_direct_discount"]) if "is_direct_discount" in promo.keys() else False
                applies_to = promo["applies_to"] if "applies_to" in promo.keys() else "all"
                product_ids = promo["product_ids"] if "product_ids" in promo.keys() else ""
                clients = await _get_all_location_clients(db)
                ref_client = clients[0][2] if clients else None
                clover_name = await _build_clover_promo_name(
                    ref_client, promo["code"], is_direct, applies_to,
                    product_ids, pct_val, amt_val,
                )
                await _update_discount_on_all_locations(
                    db, promo_id, "promo",
                    name=clover_name, percentage=pct, amount=amt,
                )
        except Exception as e:
            print(f"[promo] Clover sync failed: {e}")
    if body.sync_to_clover is False:
        # Unsync from Clover: delete from all locations
        try:
            await _delete_discount_from_all_locations(db, promo_id, "promo")
            await db.execute("UPDATE promo_codes SET clover_discount_id = '' WHERE id = ?", (promo_id,))
            await db.commit()
        except Exception as e:
            print(f"[promo] Clover unsync failed: {e}")

    # When active status changes on a Clover-synced discount, push the change
    if body.is_active is not None and body.sync_to_clover is None:
        cursor = await db.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,))
        promo = await cursor.fetchone()
        if promo and promo["sync_to_clover"]:
            try:
                if body.is_active:
                    # Reactivating: recreate on all Clover locations
                    pct_val = promo["discount_pct"]
                    amt_val = promo["discount_amount"]
                    pct = int(round(pct_val * 100)) if pct_val > 0 else 0
                    amt = amt_val if amt_val > 0 else 0
                    is_direct = bool(promo.get("is_direct_discount", 0))
                    applies_to = promo.get("applies_to", "all")
                    p_ids = promo.get("product_ids", "")
                    clients = await _get_all_location_clients(db)
                    ref_client = clients[0][2] if clients else None
                    clover_name = await _build_clover_promo_name(
                        ref_client, promo["code"], is_direct, applies_to,
                        p_ids, pct_val, amt_val,
                    )
                    await _update_discount_on_all_locations(
                        db, promo_id, "promo",
                        name=clover_name, percentage=pct, amount=amt,
                    )
                else:
                    # Deactivating: remove from all Clover locations
                    await _delete_discount_from_all_locations(db, promo_id, "promo")
            except Exception as e:
                print(f"[promo] Clover sync on active toggle failed: {e}")

    return {"status": "updated"}


@router.delete("/promos/{promo_id}")
async def delete_promo(promo_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Delete a promo code (also removes from all Clover POS locations)."""
    # Delete from all Clover locations first (non-blocking: errors logged)
    try:
        await _delete_discount_from_all_locations(db, promo_id, "promo")
    except Exception as e:
        print(f"[promo] Clover delete failed: {e}")
    await db.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
    await db.commit()
    return {"status": "deleted"}


@router.post("/promos/resync-clover")
async def resync_promos_to_clover(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Re-sync all active promo/direct discounts to all Clover POS locations.

    Updates existing Clover discounts so direct discounts targeting specific
    products show the product names in Clover POS (instead of the internal code).
    Syncs to ALL configured store locations.
    """
    cursor = await db.execute(
        "SELECT * FROM promo_codes WHERE is_active = 1 AND (sync_to_clover = 1 OR clover_discount_id != '' OR is_direct_discount = 1)"
    )
    rows = await cursor.fetchall()
    clients = await _get_all_location_clients(db)
    if not clients:
        raise HTTPException(status_code=500, detail="No Clover locations configured")

    ref_client = clients[0][2]
    results = []
    for row in rows:
        row = dict(row)
        try:
            pct_val = row["discount_pct"]
            amt_val = row["discount_amount"]
            pct = int(round(pct_val * 100)) if pct_val > 0 else 0
            amt = amt_val if amt_val > 0 else 0
            is_direct = bool(row.get("is_direct_discount", 0))
            applies_to = row.get("applies_to", "all")
            product_ids = row.get("product_ids", "")
            clover_name = await _build_clover_promo_name(
                ref_client, row["code"], is_direct, applies_to,
                product_ids, pct_val, amt_val,
            )
            import asyncio as _asyncio
            await _asyncio.sleep(0.3)
            sync_results = await _update_discount_on_all_locations(
                db, row["id"], "promo",
                name=clover_name, percentage=pct, amount=amt,
            )
            results.append({
                "id": row["id"], "code": row["code"],
                "label": clover_name, "locations": sync_results,
            })
        except Exception as e:
            results.append({"id": row["id"], "code": row["code"], "error": str(e)})

    return {"status": "resynced", "results": results}


# ─── Volume Discounts ───────────────────────────────────────────────

class VolumeDiscountCreateRequest(BaseModel):
    product_sku: str
    product_name: str
    min_quantity: int = 2
    discount_type: str = "fixed_total"  # fixed_total | amount_off | percent_off
    discount_value: float = 0
    customer_label: str = ""
    is_active: bool = True
    sync_to_clover: bool = True


class VolumeDiscountUpdateRequest(BaseModel):
    product_sku: Optional[str] = None
    product_name: Optional[str] = None
    min_quantity: Optional[int] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    customer_label: Optional[str] = None
    is_active: Optional[bool] = None
    sync_to_clover: Optional[bool] = None


@router.get("/volume-discounts")
async def list_volume_discounts(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: List all volume discounts."""
    cursor = await db.execute("SELECT * FROM volume_discounts ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        # Count usage from discount_usage table
        uc = await db.execute(
            "SELECT COUNT(*) FROM discount_usage WHERE discount_code = ?",
            (f"VD-{d['id']}",),
        )
        count_row = await uc.fetchone()
        d["times_used"] = count_row[0] if count_row else 0
        results.append(d)
    return results


@router.get("/volume-discounts/active")
async def list_active_volume_discounts(db: aiosqlite.Connection = Depends(get_db)):
    """Public: List active volume discounts (for website auto-apply)."""
    cursor = await db.execute("SELECT * FROM volume_discounts WHERE is_active = 1")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def _compute_clover_volume_discount(client, product_sku: str, product_name: str,
                                           min_quantity: int, discount_type: str,
                                           discount_value: float, customer_label: str) -> tuple:
    """Compute the correct Clover discount name, percentage, and amount (cents) for a volume discount.

    For fixed_total discounts, looks up the item price in Clover to calculate the
    TOTAL discount amount (not per-item) so staff can apply it once to the order.
    Clover line-item discounts multiply amount × qty, so we use the total discount
    and instruct staff to apply at order level.
    Returns (label, percentage, amount_cents).
    """
    # Build a clear label that includes the product name so staff knows
    # this discount is ONLY for that specific product.
    # Clover discount names can be up to 64 chars. Use the full product name
    # but truncate if very long to leave room for the suffix.
    short_name = product_name if len(product_name) <= 25 else product_name[:25].rstrip()
    if discount_type == "percent_off":
        pct = int(round(discount_value))
        label = f"{short_name} ONLY: {customer_label or f'{pct}% off {min_quantity}+'}"  
        return label, pct, 0
    elif discount_type == "fixed_total":
        # fixed_total means the TOTAL price for min_quantity items is discount_value.
        # We compute the TOTAL discount (original_total - target_total) and send that
        # as a flat amount.  Staff must apply this to the ORDER (not a line item)
        # so Clover doesn't multiply it by the quantity.
        total_discount_cents = 0
        try:
            # product_sku may be a Clover item ID or a barcode SKU.
            # Try direct lookup first, then fall back to searching by SKU filter.
            original_price = 0
            try:
                item = await client.get_item(product_sku)
                original_price = item.get("price", 0)
            except Exception:
                # SKU is likely a barcode, search all items for it
                all_items = await client.get_items(expand="")
                for it in all_items.get("elements", []):
                    if it.get("sku") == product_sku:
                        original_price = it.get("price", 0)
                        break
            if original_price > 0:
                original_total = original_price * min_quantity
                target_total = int(round(discount_value * 100))
                total_discount_cents = max(original_total - target_total, 0)
        except Exception:
            total_discount_cents = 0
        total_dollars = total_discount_cents / 100
        label = f"{short_name} ONLY: ${total_dollars:.0f} off {min_quantity}+ (apply to order)"
        return label, 0, total_discount_cents
    else:
        # amount_off: discount_value is the dollar amount off per item
        amt = int(round(discount_value * 100))
        label = f"{short_name} ONLY: {customer_label or f'${discount_value:.2f} off {min_quantity}+'}"
        return label, 0, amt


@router.post("/volume-discounts")
async def create_volume_discount(body: VolumeDiscountCreateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Create a new volume discount."""
    if body.min_quantity < 2:
        raise HTTPException(status_code=400, detail="Minimum quantity must be at least 2")
    if body.discount_value <= 0:
        raise HTTPException(status_code=400, detail="Discount value must be greater than 0")

    clover_discount_id = ""
    cursor = await db.execute(
        """INSERT INTO volume_discounts (product_sku, product_name, min_quantity, discount_type,
           discount_value, customer_label, is_active, sync_to_clover, clover_discount_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.product_sku, body.product_name, body.min_quantity, body.discount_type,
         body.discount_value, body.customer_label, int(body.is_active),
         int(body.sync_to_clover), clover_discount_id),
    )
    await db.commit()
    vd_id = cursor.lastrowid

    # Sync to all Clover POS locations (non-blocking: errors logged)
    if body.sync_to_clover:
        try:
            clients = await _get_all_location_clients(db)
            ref_client = clients[0][2] if clients else None
            if ref_client:
                label, pct, amt = await _compute_clover_volume_discount(
                    ref_client, body.product_sku, body.product_name,
                    body.min_quantity, body.discount_type,
                    body.discount_value, body.customer_label,
                )
                sync_results = await _sync_discount_to_all_locations(
                    db, vd_id, "volume", name=label, percentage=pct, amount=amt,
                )
                for r in sync_results:
                    if r.get("clover_id"):
                        clover_discount_id = r["clover_id"]
                        await db.execute(
                            "UPDATE volume_discounts SET clover_discount_id = ? WHERE id = ?",
                            (clover_discount_id, vd_id),
                        )
                        await db.commit()
                        break
        except Exception as e:
            print(f"[volume-discount] Clover sync failed: {e}")

    return {"status": "created", "clover_discount_id": clover_discount_id}


@router.put("/volume-discounts/{discount_id}")
async def update_volume_discount(discount_id: int, body: VolumeDiscountUpdateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Update a volume discount."""
    updates = []
    params = []
    for field in ["product_sku", "product_name", "min_quantity", "discount_type",
                   "discount_value", "customer_label"]:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if body.is_active is not None:
        updates.append("is_active = ?")
        params.append(int(body.is_active))
    if body.sync_to_clover is not None:
        updates.append("sync_to_clover = ?")
        params.append(int(body.sync_to_clover))
    if not updates:
        return {"status": "no changes"}
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(discount_id)
    await db.execute(f"UPDATE volume_discounts SET {', '.join(updates)} WHERE id = ?", params)
    await db.commit()

    # Sync to all Clover POS locations (non-blocking: errors logged)
    if body.sync_to_clover:
        try:
            cursor = await db.execute("SELECT * FROM volume_discounts WHERE id = ?", (discount_id,))
            vd = await cursor.fetchone()
            if vd:
                clients = await _get_all_location_clients(db)
                ref_client = clients[0][2] if clients else None
                if ref_client:
                    label, pct, amt = await _compute_clover_volume_discount(
                        ref_client, vd["product_sku"], vd["product_name"],
                        vd["min_quantity"], vd["discount_type"],
                        vd["discount_value"], vd["customer_label"],
                    )
                    await _update_discount_on_all_locations(
                        db, discount_id, "volume", name=label, percentage=pct, amount=amt,
                    )
        except Exception as e:
            print(f"[volume-discount] Clover sync failed: {e}")

    # When active status changes on a Clover-synced volume discount, push the change
    if body.is_active is not None and body.sync_to_clover is None:
        cursor = await db.execute("SELECT * FROM volume_discounts WHERE id = ?", (discount_id,))
        vd = await cursor.fetchone()
        if vd and vd["sync_to_clover"]:
            try:
                if body.is_active:
                    # Reactivating: recreate on all Clover locations
                    clients = await _get_all_location_clients(db)
                    ref_client = clients[0][2] if clients else None
                    if ref_client:
                        label, pct, amt = await _compute_clover_volume_discount(
                            ref_client, vd["product_sku"], vd["product_name"],
                            vd["min_quantity"], vd["discount_type"],
                            vd["discount_value"], vd["customer_label"],
                        )
                        await _update_discount_on_all_locations(
                            db, discount_id, "volume", name=label, percentage=pct, amount=amt,
                        )
                else:
                    # Deactivating: remove from all Clover locations
                    await _delete_discount_from_all_locations(db, discount_id, "volume")
            except Exception as e:
                print(f"[volume-discount] Clover sync on active toggle failed: {e}")

    return {"status": "updated"}


@router.delete("/volume-discounts/{discount_id}")
async def delete_volume_discount(discount_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Delete a volume discount (also removes from all Clover POS locations)."""
    # Delete from all Clover locations first (non-blocking: errors logged)
    try:
        await _delete_discount_from_all_locations(db, discount_id, "volume")
    except Exception as e:
        print(f"[volume-discount] Clover delete failed: {e}")
    await db.execute("DELETE FROM volume_discounts WHERE id = ?", (discount_id,))
    await db.commit()
    return {"status": "deleted"}


@router.post("/volume-discounts/resync-clover")
async def resync_volume_discounts_to_clover(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Re-sync all active volume discounts to all Clover POS locations.

    Updates existing Clover discounts to include the product name in the label
    and fixes the discount amount calculation. Syncs to ALL configured locations.
    """
    cursor = await db.execute("SELECT * FROM volume_discounts WHERE is_active = 1 AND sync_to_clover = 1")
    rows = await cursor.fetchall()
    clients = await _get_all_location_clients(db)
    if not clients:
        raise HTTPException(status_code=500, detail="No Clover locations configured")

    ref_client = clients[0][2]
    results = []
    for vd in rows:
        vd = dict(vd)
        try:
            label, pct, amt = await _compute_clover_volume_discount(
                ref_client, vd["product_sku"], vd["product_name"],
                vd["min_quantity"], vd["discount_type"],
                vd["discount_value"], vd["customer_label"],
            )
            import asyncio as _asyncio
            await _asyncio.sleep(0.3)
            sync_results = await _update_discount_on_all_locations(
                db, vd["id"], "volume", name=label, percentage=pct, amount=amt,
            )
            results.append({"id": vd["id"], "product": vd["product_name"], "label": label, "locations": sync_results})
        except Exception as e:
            results.append({"id": vd["id"], "product": vd["product_name"], "error": str(e)})

    return {"status": "resynced", "results": results}


async def _check_realtime_stock(items: List[OrderItem], fulfillment_type: str) -> list[str]:
    """Check real-time Clover stock for order items. Returns list of out-of-stock item names.
    For pickup orders at East/West, resolves items by SKU/name since product_id is HQ's Clover ID."""
    # Determine which location to check based on fulfillment type
    if fulfillment_type == "pickup_west" and WEST_MERCHANT_ID and WEST_API_TOKEN:
        merchant_id = WEST_MERCHANT_ID
        api_token = WEST_API_TOKEN
        location_label = "West"
    elif fulfillment_type == "pickup_east" and EAST_MERCHANT_ID and EAST_API_TOKEN:
        merchant_id = EAST_MERCHANT_ID
        api_token = EAST_API_TOKEN
        location_label = "East"
    else:
        merchant_id = HQ_MERCHANT_ID
        api_token = HQ_API_TOKEN
        location_label = "HQ"

    out_of_stock: list[str] = []
    base = f"{CLOVER_BASE_URL}/merchants/{merchant_id}"
    headers = {"Authorization": f"Bearer {api_token}"}

    # For non-HQ locations, pre-fetch all items to resolve by SKU/name
    is_non_hq = fulfillment_type in ("pickup_west", "pickup_east")
    location_lookup = None
    if is_non_hq:
        try:
            location_lookup = await _resolve_location_items(merchant_id, api_token)
        except Exception as e:
            print(f"[stock-check] Failed to resolve location items: {e}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for item in items:
                # Skip LeafLife items (shipped from supplier, not local stock)
                if isinstance(item.sku, str) and item.sku.startswith("LF-"):
                    continue
                if not item.product_id and not item.sku and not item.name:
                    continue

                clover_item_id = item.product_id
                # Resolve the correct Clover item ID at the target location
                if is_non_hq and location_lookup and location_lookup["by_id"]:
                    local_item = _find_item_at_location(location_lookup, clover_item_id, item.sku, item.name)
                    if local_item:
                        clover_item_id = local_item["id"]
                        # Use the already-fetched itemStock data from resolve call
                        # to avoid discrepancies with separate /item_stocks API call
                        stock_info = local_item.get("itemStock", {})
                        current_qty = stock_info.get("quantity", 0) if stock_info else 0
                        if current_qty < item.quantity:
                            out_of_stock.append(
                                f"{item.name} (only {current_qty} in stock at {location_label}, you requested {item.quantity})"
                            )
                            print(f"[stock-check] {item.name} INSUFFICIENT at {location_label}: {current_qty} < {item.quantity}")
                        else:
                            print(f"[stock-check] {item.name} OK at {location_label}: {current_qty} >= {item.quantity}")
                        continue
                    else:
                        print(f"[stock-check] Could not find '{item.name}' (SKU: {item.sku}) at {location_label}")
                        continue

                if not clover_item_id:
                    continue
                try:
                    resp = await client.get(
                        f"{base}/item_stocks/{clover_item_id}",
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        stock_data = resp.json()
                        current_qty = stock_data.get("quantity", 0)
                        if current_qty < item.quantity:
                            out_of_stock.append(
                                f"{item.name} (only {current_qty} in stock at {location_label}, you requested {item.quantity})"
                            )
                            print(f"[stock-check] {item.name} INSUFFICIENT at {location_label}: {current_qty} < {item.quantity}")
                        else:
                            print(f"[stock-check] {item.name} OK at {location_label}: {current_qty} >= {item.quantity}")
                    else:
                        print(f"[stock-check] Could not verify stock for {item.name} ({clover_item_id}): {resp.status_code}")
                except Exception as e:
                    print(f"[stock-check] Error checking {item.name}: {e}")
    except Exception as e:
        print(f"[stock-check] Stock check failed entirely: {e}")
        # Don't block the order if the stock check itself fails
    return out_of_stock


# Directory for durably capturing orders that were charged but couldn't be saved to
# the DB, so they are never lost even if SQLite is unavailable and can be recovered.
LOST_ORDERS_DIR = os.path.join(os.path.dirname(DB_PATH) or ".", "lost_orders")


async def _insert_ecommerce_order(
    db: aiosqlite.Connection,
    order: "CreateOrderRequest",
    order_number: str,
    charge_id: str,
    payment_status: str,
    clover_order_id: str,
) -> int:
    """Insert an order + its line items using the given connection (no commit).

    Shared by the primary save, the fallback save, and manual recovery so all three
    paths write identical rows and can't drift apart.
    """
    cursor = await db.execute(
        """INSERT INTO ecommerce_orders
           (order_number, customer_first_name, customer_last_name, customer_email, customer_phone,
            shipping_address, shipping_apartment, shipping_city, shipping_state, shipping_zip,
            subtotal, discount, volume_discount, sale_discount, loyalty_discount, promo_code,
            shipping_cost, tax, total, notes, charge_id, payment_status,
            fulfillment_type, shipping_service, clover_order_id, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            order_number,
            order.customer.first_name,
            order.customer.last_name,
            order.customer.email,
            order.customer.phone,
            order.shipping_address.address,
            order.shipping_address.apartment,
            order.shipping_address.city,
            order.shipping_address.state,
            order.shipping_address.zip,
            order.subtotal,
            order.discount,
            order.volume_discount,
            order.sale_discount,
            order.loyalty_discount,
            order.promo_code or "",
            order.shipping_cost,
            order.tax,
            order.total,
            order.notes,
            charge_id,
            payment_status,
            order.fulfillment_type,
            order.shipping_service,
            clover_order_id,
            "website",
        ),
    )
    order_id = cursor.lastrowid
    for item in order.items:
        await db.execute(
            """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, price, quantity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, item.product_id, item.name, item.sku, item.price, item.quantity),
        )
    return order_id


def _dump_lost_order(
    order: "CreateOrderRequest",
    order_number: str,
    charge_id: str,
    payment_status: str,
    clover_order_id: str,
) -> None:
    """Persist a charged-but-unsaved order to disk so it is never lost and can be recovered."""
    try:
        os.makedirs(LOST_ORDERS_DIR, exist_ok=True)
        payload = {
            "order_number": order_number,
            "charge_id": charge_id,
            "payment_status": payment_status,
            "clover_order_id": clover_order_id,
            "captured_at": time.time(),
            "order": order.model_dump(),
        }
        path = os.path.join(LOST_ORDERS_DIR, f"{order_number}.json")
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"[ORDER LOST] Durable backup written to {path}")
    except Exception as dump_err:
        print(f"[ORDER LOST] FAILED to write durable backup for {order_number}: {dump_err}")


@router.post("/orders")
async def create_order(
    order: CreateOrderRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Create an e-commerce order with Clover payment processing."""
    charge_id = ""
    clover_order_id = ""
    payment_status = "pending"

    # Server-side enforcement: Block LeafLife items from pickup and local delivery orders.
    # LeafLife products ship from an out-of-state partner and are NEVER available for in-store pickup or local delivery.
    if order.fulfillment_type in ("pickup_west", "pickup_east", "local_delivery"):
        blocked_items = []
        for item in order.items:
            # LeafLife products (SKU starts with LF-) are shipping-only
            if isinstance(item.sku, str) and item.sku.startswith("LF-"):
                blocked_items.append(item.name)
                continue
        if blocked_items:
            names = ", ".join(blocked_items)
            method = "local delivery" if order.fulfillment_type == "local_delivery" else "in-store pickup"
            print(f"[order] BLOCKED {method} order containing shipping-only items: {names}")
            raise HTTPException(
                status_code=400,
                detail=f"The following items are only available for shipping and cannot be included in {method} orders: {names}. Please switch to 'Ship To Me' to order these products.",
            )

    # Server-side enforcement: Promo codes and loyalty rewards cannot be stacked together.
    if order.promo_code and order.loyalty_discount > 0:
        print(f"[order] BLOCKED promo+loyalty stacking: code={order.promo_code} loyalty=${order.loyalty_discount/100:.2f} from {order.customer.email}")
        raise HTTPException(
            status_code=400,
            detail="Promo codes and loyalty rewards cannot be used together. Please choose one or the other.",
        )

    # Server-side enforcement: Loyalty rewards capped to subtotal only (not tax),
    # and customer must pay at least $1.00 before tax/shipping.
    loyalty_customer_id: Optional[int] = None
    loyalty_reward_row: Optional[tuple] = None
    if order.loyalty_discount > 0:
        item_subtotal = sum(item.price * item.quantity for item in order.items)
        effective_subtotal = order.subtotal - order.discount - order.volume_discount
        # Cap: loyalty cannot exceed effective subtotal (no covering tax),
        # AND must leave at least $1.00 of product cost for the customer to pay.
        max_loyalty = max(min(item_subtotal - 100, effective_subtotal - 100), 0)
        if order.loyalty_discount > max_loyalty:
            # Reject rather than apply part of the reward — the customer would spend the
            # full points for less than the reward's value.
            print(f"[order] BLOCKED partial loyalty redemption: requested ${order.loyalty_discount/100:.2f}, max allowed ${max_loyalty/100:.2f} (item_subtotal ${item_subtotal/100:.2f}, effective_subtotal ${effective_subtotal/100:.2f})")
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This reward is worth ${order.loyalty_discount/100:.2f} and needs an order subtotal of at least "
                    f"${(order.loyalty_discount + 100)/100:.2f} to be used in full. Please add more to your cart or pick a smaller reward."
                ),
            )

        # Server-side verification: look up the loyalty customer and verify they have
        # enough points for the selected reward before accepting the order.
        if order.loyalty_reward_id and order.loyalty_number:
            loyalty_identifier = order.loyalty_number.strip()
            if "@" in loyalty_identifier:
                lcur = await db.execute(
                    "SELECT id, points_balance, first_name, last_name FROM loyalty_customers WHERE email = ? COLLATE NOCASE",
                    (loyalty_identifier,),
                )
            else:
                phone = "".join(ch for ch in loyalty_identifier if ch.isdigit())[-10:]
                lcur = await db.execute(
                    "SELECT id, points_balance, first_name, last_name FROM loyalty_customers WHERE "
                    "REPLACE(REPLACE(REPLACE(REPLACE(phone, '-', ''), ' ', ''), '(', ''), ')', '') LIKE ?",
                    (f"%{phone}",),
                )
            lrow = await lcur.fetchone()
            if not lrow:
                print(f"[order] Loyalty customer not found for '{loyalty_identifier}', zeroing loyalty discount")
                order.loyalty_discount = 0
                order.total = order.subtotal - order.discount - order.volume_discount + order.shipping_cost + order.tax
            else:
                loyalty_customer_id = lrow[0]
                # Verify the reward exists and customer has enough points
                rcur = await db.execute(
                    "SELECT id, name, points_required, reward_value FROM loyalty_rewards WHERE id = ? AND is_active = 1",
                    (order.loyalty_reward_id,),
                )
                loyalty_reward_row = await rcur.fetchone()
                if not loyalty_reward_row:
                    print(f"[order] Loyalty reward {order.loyalty_reward_id} not found or inactive, zeroing loyalty discount")
                    order.loyalty_discount = 0
                    order.total = order.subtotal - order.discount - order.volume_discount + order.shipping_cost + order.tax
                elif lrow[1] < loyalty_reward_row[2]:
                    print(f"[order] Loyalty customer {lrow[2]} {lrow[3]} (id={lrow[0]}) has {lrow[1]} pts but needs {loyalty_reward_row[2]} for reward '{loyalty_reward_row[1]}', zeroing loyalty discount")
                    order.loyalty_discount = 0
                    order.total = order.subtotal - order.discount - order.volume_discount + order.shipping_cost + order.tax
                else:
                    # Enforce: loyalty discount cannot exceed the reward's actual value
                    reward_value_cents = round(loyalty_reward_row[3] * 100)
                    if order.loyalty_discount > reward_value_cents:
                        print(f"[order] Loyalty discount capped to reward value: requested ${order.loyalty_discount/100:.2f}, reward value ${reward_value_cents/100:.2f}")
                        order.loyalty_discount = min(order.loyalty_discount, reward_value_cents)
                        effective_subtotal = order.subtotal - order.discount - order.volume_discount
                        order.total = effective_subtotal - order.loyalty_discount + order.shipping_cost + order.tax
                    print(f"[order] Loyalty verified: customer {lrow[2]} {lrow[3]} (id={lrow[0]}) has {lrow[1]} pts, redeeming {loyalty_reward_row[2]} pts for '{loyalty_reward_row[1]}'")
        else:
            # loyalty_discount > 0 but missing reward_id or loyalty_number — reject the discount
            print(f"[order] Loyalty discount ${order.loyalty_discount/100:.2f} requested without reward_id or loyalty_number, zeroing")
            order.loyalty_discount = 0
            order.total = order.subtotal - order.discount - order.volume_discount + order.shipping_cost + order.tax

    # Server-side enforcement: FIRST10 phone number check (prevent multi-email abuse)
    if order.promo_code and order.promo_code.upper() == "FIRST10" and order.customer.phone:
        phone = order.customer.phone.strip().replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if len(phone) >= 10:
            phone_normalized = phone[-10:]
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ecommerce_orders WHERE REPLACE(REPLACE(REPLACE(REPLACE(customer_phone, '-', ''), ' ', ''), '(', ''), ')', '') LIKE ? AND promo_code = 'FIRST10' AND payment_status != 'cancelled'",
                (f"%{phone_normalized}",),
            )
            count = (await cursor.fetchone())[0]
            if count > 0:
                print(f"[order] BLOCKED FIRST10 reuse by phone {phone_normalized} (email: {order.customer.email})")
                raise HTTPException(
                    status_code=400,
                    detail="The FIRST10 promo code has already been used with this phone number. This code is limited to one use per customer.",
                )

    # Server-side enforcement: Shipping orders MUST have a non-zero shipping cost.
    # Prevents customers from bypassing the shipping rate selection (e.g. via DevTools)
    # and getting free shipping on orders that should be charged.
    if _is_shipping_fulfillment(order.fulfillment_type) and order.shipping_cost <= 0:
        print(f"[order] BLOCKED shipping order with $0 shipping cost from {order.customer.email}")
        raise HTTPException(
            status_code=400,
            detail="Shipping cost is required for delivery orders. Please select a shipping rate and try again.",
        )

    # Server-side enforcement: Local delivery orders must have the correct delivery fee
    # and the address must be within the delivery radius.
    if order.fulfillment_type == "local_delivery":
        # Validate delivery radius server-side (prevent bypass of client-side check)
        if order.shipping_address and order.shipping_address.address:
            try:
                coords = await _geocode_address(
                    order.shipping_address.address,
                    order.shipping_address.city,
                    order.shipping_address.state,
                    order.shipping_address.zip,
                )
                if coords:
                    d_lat, d_lon = coords
                    distance = _haversine_miles(HQ_LAT, HQ_LON, d_lat, d_lon)
                    if distance > DELIVERY_RADIUS_MILES:
                        print(f"[order] BLOCKED delivery order — address is {round(distance, 1)} miles from HQ (limit {DELIVERY_RADIUS_MILES})")
                        raise HTTPException(
                            status_code=400,
                            detail=f"Sorry, your address is {round(distance, 1)} miles from our store. Local delivery is available within {DELIVERY_RADIUS_MILES} miles. Please select 'Ship To Me' instead.",
                        )
                else:
                    print(f"[order] WARNING: Could not geocode delivery address for {order.shipping_address.city}, {order.shipping_address.state} — allowing order to proceed")
            except HTTPException:
                raise
            except Exception as geo_err:
                print(f"[order] WARNING: Geocoding failed for delivery order — allowing order to proceed: {geo_err}")

        item_subtotal = sum(item.price * item.quantity for item in order.items)
        expected_fee = DELIVERY_FEE_DISCOUNTED if item_subtotal >= DELIVERY_DISCOUNT_THRESHOLD else DELIVERY_FEE_STANDARD
        if order.shipping_cost != expected_fee:
            print(f"[order] Correcting delivery fee: submitted={order.shipping_cost}, expected={expected_fee}")
            order.shipping_cost = expected_fee
            order.total = order.subtotal - order.discount - order.volume_discount - order.loyalty_discount + order.shipping_cost + order.tax

    # Real-time stock validation BEFORE charging the customer.
    # Prevents customers from ordering items that are out of stock (stale cache).
    out_of_stock = await _check_realtime_stock(order.items, order.fulfillment_type)
    if out_of_stock:
        detail_lines = "; ".join(out_of_stock)
        raise HTTPException(
            status_code=409,
            detail=f"Some items are out of stock: {detail_lines}. Please update your cart and try again.",
        )

    # Server-side enforcement of LeafLife minimum price floors.
    # Prevents stale cached prices on the frontend from undercharging.
    corrected_subtotal = 0
    for item in order.items:
        enforced_price = _enforce_leaflife_price_floor(item.sku, item.name, item.price)
        if enforced_price != item.price:
            print(f"[order] Price floor corrected: {item.name} ({item.sku}) from ${item.price/100:.2f} to ${enforced_price/100:.2f}")
            item.price = enforced_price
        corrected_subtotal += enforced_price * item.quantity
    if corrected_subtotal != order.subtotal:
        diff = corrected_subtotal - order.subtotal
        print(f"[order] Subtotal corrected from ${order.subtotal/100:.2f} to ${corrected_subtotal/100:.2f} (diff: ${diff/100:.2f})")
        order.subtotal = corrected_subtotal
        order.total = order.subtotal - order.discount - order.volume_discount + order.shipping_cost + order.tax - order.loyalty_discount

    # Server-side sale price enforcement: verify item prices match the active sale.
    # If the frontend sent items at full price but a sale is active, correct the prices
    # BEFORE charging so the customer is never overcharged.
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        _eastern = _ZI("America/New_York")
        _now = _dt.now(_eastern).strftime("%Y-%m-%dT%H:%M")
        _sc = await db.execute(
            """SELECT discount_pct, applies_to, product_ids, excluded_brands FROM promo_codes
               WHERE is_direct_discount = 1 AND is_active = 1
               AND (in_store_only = 0 OR in_store_only IS NULL)
               AND starts_at IS NOT NULL AND starts_at != ''
               AND expires_at IS NOT NULL AND expires_at != ''
               AND (CASE WHEN LENGTH(starts_at) <= 10 THEN starts_at || 'T00:00' ELSE starts_at END) <= ?
               AND (CASE WHEN LENGTH(expires_at) <= 10 THEN expires_at || 'T23:59' ELSE expires_at END) >= ?
               ORDER BY discount_pct DESC LIMIT 1""",
            (_now, _now),
        )
        _sale = await _sc.fetchone()
        if _sale:
            _pct = _sale["discount_pct"]
            _applies = _sale["applies_to"] if "applies_to" in _sale.keys() else "all"
            _pids = set(pid.strip() for pid in (_sale["product_ids"] or "").split(",") if pid.strip()) if _applies == "specific" else set()
            _excluded = set(b.strip().lower() for b in (_sale["excluded_brands"] or "").split(",") if b.strip())
            _cached = await _get_cached_products()
            _by_id = {p["id"]: p for p in _cached.get("products", [])}
            _total_savings = 0
            for _item in order.items:
                if _applies == "specific" and _pids and _item.product_id not in _pids:
                    continue
                _prod = _by_id.get(_item.product_id)
                if not _prod:
                    continue
                if _excluded:
                    _cats = [c.lower() for c in _prod.get("categories", [])]
                    if any(ex in _cats for ex in _excluded):
                        continue
                _catalog_price = _prod["price"]
                _correct_price = round(_catalog_price * (1 - _pct))
                if _item.price > _correct_price:
                    _savings_per = _item.price - _correct_price
                    _total_savings += _savings_per * _item.quantity
                    print(f"[order] Sale price enforced: {_item.name} ${_item.price/100:.2f} -> ${_correct_price/100:.2f} ({_pct*100:.0f}% off ${_catalog_price/100:.2f})")
                    _item.price = _correct_price
            if _total_savings > 0:
                order.sale_discount = (order.sale_discount or 0) + _total_savings
                _old_subtotal = order.subtotal
                order.subtotal = sum(_it.price * _it.quantity for _it in order.items)
                # Recalculate tax proportionally
                if _old_subtotal > 0 and order.tax > 0:
                    _tax_rate = order.tax / _old_subtotal
                    order.tax = round(order.subtotal * _tax_rate)
                order.total = order.subtotal - order.discount - order.volume_discount - order.loyalty_discount + order.shipping_cost + order.tax
                print(f"[order] Sale enforcement: discount=${order.sale_discount/100:.2f} new_subtotal=${order.subtotal/100:.2f} tax=${order.tax/100:.2f} new_total=${order.total/100:.2f}")
    except Exception as _e:
        print(f"[order] Sale price enforcement failed (non-fatal, proceeding with original prices): {_e}")

    # Process payment via Clover if a payment token is provided
    if order.payment_token:
        client_ip = request.client.host if request.client else "127.0.0.1"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        charge_headers = {
            "Authorization": f"Bearer {HQ_ECOMM_TOKEN}",
            "Content-Type": "application/json",
            "x-forwarded-for": client_ip,
        }
        # Build item descriptions for Clover receipt
        item_lines = [f"{item.name} x{item.quantity}" for item in order.items]
        description = "; ".join(item_lines)
        if len(description) > 255:
            description = description[:252] + "..."

        charge_data = {
            "amount": order.total,
            "currency": "usd",
            "source": order.payment_token,
            "description": description,
            "ecomind": "ecom",
        }

        # Always use HQ for payment processing since the card token is generated with HQ's PAKMS key.
        # Stock deduction to the correct location (West/East/HQ) is handled separately after order creation.
        order_merchant_id = HQ_MERCHANT_ID
        order_api_token = HQ_API_TOKEN

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Create a Clover order with line items so the receipt shows actual products
            try:
                clover_order_headers = {
                    "Authorization": f"Bearer {order_api_token}",
                    "Content-Type": "application/json",
                }
                clover_order_url = f"{CLOVER_BASE_URL}/merchants/{order_merchant_id}/orders"
                order_body = {
                    "state": "open",
                    "manualTransaction": False,
                    "note": f"Online Order - {order.customer.first_name} {order.customer.last_name} ({order.customer.email})",
                }
                order_resp = await client.post(clover_order_url, headers=clover_order_headers, json=order_body)
                if order_resp.status_code == 200:
                    clover_order = order_resp.json()
                    clover_order_id = clover_order.get("id", "")
                    # Add line items to the Clover order
                    for item in order.items:
                        line_item_url = f"{clover_order_url}/{clover_order_id}/line_items"
                        line_item_body = {
                            "name": item.name,
                            "price": item.price,
                            "unitQty": item.quantity * 1000,  # Clover uses millis for quantity
                        }
                        await client.post(line_item_url, headers=clover_order_headers, json=line_item_body)

                    # Add discounts to the Clover order so its total matches the charge amount.
                    # Without this, Clover may use the order total (sum of full-price line items)
                    # instead of our specified charge amount, causing customers to be overcharged.
                    total_discount = order.discount + order.volume_discount + order.loyalty_discount + order.sale_discount
                    if total_discount > 0:
                        discount_url = f"{clover_order_url}/{clover_order_id}/discounts"
                        discount_parts = []
                        if order.discount > 0:
                            discount_parts.append(f"Promo -${order.discount/100:.2f}")
                        if order.volume_discount > 0:
                            discount_parts.append(f"Volume -${order.volume_discount/100:.2f}")
                        if order.loyalty_discount > 0:
                            discount_parts.append(f"Loyalty -${order.loyalty_discount/100:.2f}")
                        if order.sale_discount > 0:
                            discount_parts.append(f"Sale -${order.sale_discount/100:.2f}")
                        discount_name = ", ".join(discount_parts) if discount_parts else "Online Discount"
                        if len(discount_name) > 127:
                            discount_name = discount_name[:124] + "..."
                        discount_body = {
                            "name": discount_name,
                            "amount": -total_discount,  # Clover expects negative cents
                        }
                        disc_resp = await client.post(discount_url, headers=clover_order_headers, json=discount_body)
                        if disc_resp.status_code == 200:
                            print(f"[order] Added Clover order discount: {discount_name} ({-total_discount} cents)")
                        else:
                            print(f"[order] Failed to add Clover order discount: {disc_resp.status_code} {disc_resp.text}")

                    # Add shipping/delivery fee as a line item if applicable
                    if order.shipping_cost > 0:
                        ship_line_url = f"{clover_order_url}/{clover_order_id}/line_items"
                        ft = order.fulfillment_type or "shipping"
                        ship_label = "Delivery Fee" if ft == "local_delivery" else f"Shipping ({order.shipping_service})" if order.shipping_service else "Shipping"
                        ship_body = {"name": ship_label, "price": order.shipping_cost, "unitQty": 1000}
                        await client.post(ship_line_url, headers=clover_order_headers, json=ship_body)

                    # Add tax as a line item so Clover order total = charge amount
                    if order.tax > 0:
                        tax_line_url = f"{clover_order_url}/{clover_order_id}/line_items"
                        tax_body = {"name": "Sales Tax", "price": order.tax, "unitQty": 1000}
                        await client.post(tax_line_url, headers=clover_order_headers, json=tax_body)

                    # NOTE: Do NOT pass orderId to the charge. When orderId is present,
                    # Clover overrides the charge amount with the Clover order total
                    # (sum of line items), ignoring discounts. This caused customers to
                    # be charged full price even when loyalty/promo discounts applied.
                    # The Clover order is kept for record-keeping but not linked to payment.
                    print(f"[order] Created Clover order {clover_order_id} with {len(order.items)} line items, discount={total_discount}, shipping={order.shipping_cost}, tax={order.tax}")
                else:
                    print(f"[order] Failed to create Clover order: {order_resp.status_code} {order_resp.text}")
            except Exception as e:
                print(f"[order] Clover order creation failed (charge will still proceed): {e}")

            try:
                print(f"[order] Charging ${order.total/100:.2f} (subtotal=${order.subtotal/100:.2f} discount=${order.discount/100:.2f} vol_disc=${order.volume_discount/100:.2f} loyalty=${order.loyalty_discount/100:.2f} ship=${order.shipping_cost/100:.2f} tax=${order.tax/100:.2f})")
                resp = await client.post(
                    CLOVER_CHARGES_URL,
                    headers=charge_headers,
                    json=charge_data,
                )
                charge_result = resp.json()

                if resp.status_code == 200 and charge_result.get("status") == "succeeded":
                    charge_id = charge_result.get("id", "")
                    payment_status = "paid"
                    print(f"[order] Charge succeeded: id={charge_id} amount=${charge_result.get('amount', 0)/100:.2f}")
                else:
                    raw_msg = charge_result.get("message") or charge_result.get("error", {}).get("message", "")
                    print(f"[order] Clover charge failed: status={resp.status_code} raw={raw_msg} result={charge_result}")
                    # Show user-friendly message for card declines (Clover 402)
                    if resp.status_code == 402 or "decline" in raw_msg.lower():
                        user_msg = "Your card was declined. Please check your card details or try a different payment method."
                    elif raw_msg:
                        user_msg = raw_msg
                    else:
                        user_msg = "Payment was declined. Please try again or use a different card."
                    raise HTTPException(
                        status_code=400,
                        detail=f"Payment failed: {user_msg}",
                    )
            except httpx.HTTPError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Payment service error: {str(e)}",
                )
    else:
        raise HTTPException(
            status_code=400,
            detail="Payment token is required.",
        )

    # Payment succeeded — create the order
    order_number = "HD-" + hex(int(time.time()))[2:].upper() + "-" + str(int(time.time() * 1000) % 10000)

    # CRITICAL: DB save with retry logic. SQLite can fail with "database is locked"
    # when background sync tasks hold a write lock. Wait longer for the lock (30s
    # busy_timeout) and retry up to 3 times with backoff.
    order_id = 0
    db_save_ok = False
    max_retries = 3
    try:
        await db.execute("PRAGMA busy_timeout = 30000")
    except Exception:
        pass
    for attempt in range(max_retries):
        try:
            order_id = await _insert_ecommerce_order(
                db, order, order_number, charge_id, payment_status, clover_order_id
            )
            await db.commit()
            db_save_ok = True
            if attempt > 0:
                print(f"[order] Order {order_number} saved to DB after {attempt + 1} attempts (id={order_id})")
            else:
                print(f"[order] Order {order_number} saved to DB (id={order_id}, fulfillment={order.fulfillment_type}, total=${order.total/100:.2f})")
            break
        except Exception as db_err:
            # Roll back any partial transaction so the next attempt starts clean
            # (otherwise a half-inserted order row lingers and causes UNIQUE failures).
            try:
                await db.rollback()
            except Exception:
                pass
            if attempt < max_retries - 1:
                wait_secs = 0.5 * (2 ** attempt)
                print(f"[order] DB save attempt {attempt + 1} failed for {order_number}: {db_err} — retrying in {wait_secs}s")
                await asyncio.sleep(wait_secs)
                continue
            # All retries exhausted — log full order details for manual recovery
            items_dump = "; ".join(f"{it.name} x{it.quantity} @${it.price/100:.2f} (SKU:{it.sku})" for it in order.items)
            print(f"[ORDER LOST] DB SAVE FAILED for {order_number} — PAYMENT WAS CHARGED!")
            print(f"[ORDER LOST] charge_id={charge_id} total=${order.total/100:.2f} fulfillment={order.fulfillment_type}")
            print(f"[ORDER LOST] customer={order.customer.first_name} {order.customer.last_name} email={order.customer.email} phone={order.customer.phone}")
            print(f"[ORDER LOST] items: {items_dump}")
            print(f"[ORDER LOST] DB error: {db_err}")
            # Last resort: retry on a FRESH dedicated connection. The request-scoped
            # connection may be wedged (locked/aborted transaction); a new one with a
            # long busy_timeout can still commit.
            fallback_db = None
            try:
                fallback_db = await aiosqlite.connect(DB_PATH)
                fallback_db.row_factory = aiosqlite.Row
                await fallback_db.execute("PRAGMA busy_timeout = 30000")
                order_id = await _insert_ecommerce_order(
                    fallback_db, order, order_number, charge_id, payment_status, clover_order_id
                )
                await fallback_db.commit()
                db_save_ok = True
                print(f"[ORDER RECOVERED] Saved via fresh connection: {order_number} (id={order_id})")
            except Exception as retry_err:
                print(f"[ORDER LOST] Fresh-connection retry also failed: {retry_err}")
            finally:
                if fallback_db is not None:
                    await fallback_db.close()

    # Durably capture the charged order to disk if it still couldn't be saved, so it is
    # never lost and can be recovered even if SQLite is completely unavailable.
    if not db_save_ok:
        _dump_lost_order(order, order_number, charge_id, payment_status, clover_order_id)

    # If DB save failed, send an alert email to support so the order can be recovered manually.
    # The customer was already charged and will receive a confirmation email, but the order
    # won't appear in HempVentory without manual intervention.
    if not db_save_ok:
        try:
            alert_smtp = await _get_smtp_settings(db)
            items_list = "; ".join(f"{it.name} x{it.quantity} @${it.price/100:.2f}" for it in order.items)
            alert_html = f"""
            <h2 style="color: #dc2626;">ALERT: Order Charged But NOT Saved to Database</h2>
            <p>A customer was charged but the order failed to save to HempVentory. This order needs manual recovery.</p>
            <table style="border-collapse: collapse; width: 100%;">
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Order Number</td><td style="padding: 6px; border: 1px solid #ddd;">{order_number}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Customer</td><td style="padding: 6px; border: 1px solid #ddd;">{order.customer.first_name} {order.customer.last_name}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Email</td><td style="padding: 6px; border: 1px solid #ddd;">{order.customer.email}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Phone</td><td style="padding: 6px; border: 1px solid #ddd;">{order.customer.phone}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Charge ID</td><td style="padding: 6px; border: 1px solid #ddd;">{charge_id}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Total Charged</td><td style="padding: 6px; border: 1px solid #ddd;">${order.total/100:.2f}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Fulfillment</td><td style="padding: 6px; border: 1px solid #ddd;">{order.fulfillment_type}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Items</td><td style="padding: 6px; border: 1px solid #ddd;">{items_list}</td></tr>
                <tr><td style="padding: 6px; border: 1px solid #ddd; font-weight: bold;">Shipping</td><td style="padding: 6px; border: 1px solid #ddd;">{f"{order.shipping_address.address}, {order.shipping_address.city}, {order.shipping_address.state} {order.shipping_address.zip}" if order.shipping_address else "N/A (pickup)"}</td></tr>
            </table>
            <p style="color: #dc2626; font-weight: bold;">ACTION REQUIRED: Manually add this order to HempVentory or contact the customer.</p>
            """
            await asyncio.to_thread(
                _send_smtp_email,
                alert_smtp,
                STORE_EMAIL,
                f"URGENT: Lost Order {order_number} — Customer Charged ${order.total/100:.2f} But Order Not Saved",
                alert_html,
            )
            print(f"[ORDER LOST] Alert email sent to {STORE_EMAIL} for {order_number}")
        except Exception as alert_err:
            print(f"[ORDER LOST] Failed to send alert email for {order_number}: {alert_err}")

    # Log discount usage if a promo code was used
    if db_save_ok and order.promo_code:
        try:
            # Determine location name from fulfillment type
            loc_name = ""
            if order.fulfillment_type == "pickup_west":
                loc_name = "West"
            elif order.fulfillment_type == "pickup_east":
                loc_name = "East"
            elif order.fulfillment_type == "local_delivery":
                loc_name = "Local Delivery"
            elif order.fulfillment_type == "shipping":
                loc_name = "Online / Shipping"

            # For POS/pickup orders, find which employee was clocked in at order time
            employee_id = None
            employee_name = None
            if order.fulfillment_type in ("pickup_west", "pickup_east"):
                try:
                    emp_cursor = await db.execute(
                        """SELECT e.id, e.name FROM time_entries te
                           JOIN employees e ON e.id = te.employee_id
                           WHERE te.clock_in <= CURRENT_TIMESTAMP
                             AND (te.clock_out IS NULL OR te.clock_out >= CURRENT_TIMESTAMP)
                           ORDER BY te.clock_in DESC LIMIT 1"""
                    )
                    emp_row = await emp_cursor.fetchone()
                    if emp_row:
                        employee_id = emp_row[0]
                        employee_name = emp_row[1]
                except Exception:
                    pass  # Non-critical — don't block order completion

            customer_name = f"{order.customer.first_name} {order.customer.last_name}".strip()
            await db.execute(
                """INSERT INTO discount_usage
                   (discount_code, customer_email, customer_name, order_id, order_number,
                    location_name, employee_id, employee_name, order_total,
                    discount_amount_applied, fulfillment_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.promo_code,
                    order.customer.email,
                    customer_name,
                    order_id,
                    order_number,
                    loc_name,
                    employee_id,
                    employee_name,
                    order.total,
                    order.discount,
                    order.fulfillment_type,
                ),
            )
            await db.commit()
            print(f"[order] Discount usage logged: code={order.promo_code} customer={order.customer.email} order={order_number}")
        except Exception as usage_err:
            print(f"[order] Failed to log discount usage (non-critical): {usage_err}")

    # Deduct loyalty points from the customer's account after order is saved
    if db_save_ok and order.loyalty_discount > 0 and loyalty_customer_id and loyalty_reward_row:
        try:
            points_to_deduct = loyalty_reward_row[2]  # points_required
            reward_name = loyalty_reward_row[1]
            # Determine location name for transaction log
            loyalty_loc = ""
            if order.fulfillment_type == "pickup_west":
                loyalty_loc = "West"
            elif order.fulfillment_type == "pickup_east":
                loyalty_loc = "East"
            elif order.fulfillment_type == "local_delivery":
                loyalty_loc = "Local Delivery"
            else:
                loyalty_loc = "Online"
            update_cursor = await db.execute(
                """UPDATE loyalty_customers
                   SET points_balance = points_balance - ?,
                       lifetime_redeemed = lifetime_redeemed + ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND points_balance >= ?""",
                (points_to_deduct, points_to_deduct, loyalty_customer_id, points_to_deduct),
            )
            if update_cursor.rowcount == 0:
                print(f"[order] WARNING: Loyalty points deduction had no effect for customer {loyalty_customer_id} — possible race condition or insufficient balance at commit time")
                print(f"[order] MANUAL FIX NEEDED: Verify loyalty_customer id={loyalty_customer_id} balance and deduct {points_to_deduct} pts if appropriate")
            else:
                await db.execute(
                    """INSERT INTO loyalty_transactions (customer_id, type, points, description, location_name)
                       VALUES (?, 'redeem', ?, ?, ?)""",
                    (loyalty_customer_id, -points_to_deduct, f"Redeemed: {reward_name} (Order {order_number})", loyalty_loc),
                )
                await db.execute(
                    """INSERT INTO loyalty_redemptions (customer_id, reward_id, points_spent, location_name)
                       VALUES (?, ?, ?, ?)""",
                    (loyalty_customer_id, order.loyalty_reward_id, points_to_deduct, loyalty_loc),
                )
                await db.commit()
                await _sync_balance_to_clover_quietly(db, loyalty_customer_id)
                print(f"[order] Loyalty points deducted: customer_id={loyalty_customer_id} points={points_to_deduct} reward='{reward_name}' order={order_number}")
        except Exception as loyalty_err:
            print(f"[order] WARNING: Failed to deduct loyalty points for {order_number}: {loyalty_err}")
            print(f"[order] MANUAL FIX NEEDED: Deduct {loyalty_reward_row[2]} pts from loyalty_customer id={loyalty_customer_id}")

    # Fetch SMTP settings while DB is still open
    smtp_settings = await _get_smtp_settings(db)

    # Send email notifications (non-blocking, with error logging)
    def _log_task_error(task: asyncio.Task, label: str = "") -> None:
        if task.cancelled():
            print(f"[order] Background task {label} was cancelled for {order_number}")
        elif task.exception():
            print(f"[order] Background task {label} FAILED for {order_number}: {task.exception()}")

    email_task = asyncio.create_task(
        _send_order_emails(smtp_settings, order, order_number, charge_id, payment_status)
    )
    _keep_task(email_task)
    email_task.add_done_callback(lambda t: _log_task_error(t, "email"))

    # Deduct stock from correct Clover location based on fulfillment type (non-blocking)
    stock_task = asyncio.create_task(
        _deduct_stock_and_flag(order_id, order.items, order.fulfillment_type)
    )
    _keep_task(stock_task)
    stock_task.add_done_callback(lambda t: _log_task_error(t, "stock_deduct"))

    # Award loyalty points for online orders (non-blocking).
    # POS purchases get points via Clover sync, but online orders need explicit awarding.
    if db_save_ok and payment_status == "paid":
        loyalty_award_task = asyncio.create_task(
            _award_loyalty_points_for_order(order, order_number, order_id)
        )
        _keep_task(loyalty_award_task)
        loyalty_award_task.add_done_callback(lambda t: _log_task_error(t, "loyalty_award"))

    # Auto-write LeafLife (LF-) shipping orders into the shared Google Sheet so
    # staff no longer copy them by hand (non-blocking; never fails the order).
    if db_save_ok and payment_status == "paid":
        leaflife_task = asyncio.create_task(_sync_leaflife_order(order, order_number))
        _keep_task(leaflife_task)
        leaflife_task.add_done_callback(lambda t: _log_task_error(t, "leaflife_sheet"))

    return {
        "success": True,
        "order_number": order_number,
        "order_id": order_id,
        "total": order.total,
        "payment_status": payment_status,
        "charge_id": charge_id,
    }


async def _resolve_location_items(merchant_id: str, api_token: str) -> dict:
    """Fetch all items from a Clover location and build lookup maps.
    Returns {"by_id": {clover_id: item}, "by_sku": {sku: item}, "by_name": {normalized_name: item}}"""
    by_id: dict[str, dict] = {}
    by_sku: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    base = f"{CLOVER_BASE_URL}/merchants/{merchant_id}"
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            offset = 0
            while True:
                resp = await client.get(
                    f"{base}/items",
                    headers=headers,
                    params={"expand": "itemStock", "limit": 1000, "offset": offset, "filter": "deleted=false"},
                )
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                for item in elements:
                    item_id = item.get("id", "")
                    sku = item.get("sku", "") or ""
                    name = " ".join((item.get("name", "") or "").split())
                    by_id[item_id] = item
                    if sku:
                        by_sku[sku] = item
                    if name:
                        by_name[name.upper()] = item
                if len(elements) < 1000:
                    break
                offset += 1000
    except Exception as e:
        print(f"[resolve] Failed to fetch location items: {e}")
    return {"by_id": by_id, "by_sku": by_sku, "by_name": by_name}


def _find_item_at_location(lookup: dict, product_id: str, sku: str, name: str) -> Optional[dict]:
    """Find an item at a location using multiple lookup strategies.
    Tries: direct ID match, SKU match, then normalized name match."""
    item = lookup["by_id"].get(product_id)
    if item:
        return item
    if sku:
        item = lookup["by_sku"].get(sku)
        if item:
            return item
    if name:
        normalized = " ".join(name.split()).upper()
        item = lookup["by_name"].get(normalized)
        if item:
            return item
    return None


def _format_price(cents: int) -> str:
    """Format cents as dollar string."""
    return f"${cents / 100:.2f}"


def _order_items_as_dicts(items: List[OrderItem]) -> List[dict]:
    return [
        {"name": it.name, "sku": it.sku, "price": it.price, "quantity": it.quantity}
        for it in items
    ]


# The website sends "ship" for Ship-to-Me; older clients/defaults use "shipping".
_SHIPPING_FULFILLMENT = ("ship", "shipping")


def _is_shipping_fulfillment(fulfillment_type: str) -> bool:
    return (fulfillment_type or "ship") in _SHIPPING_FULFILLMENT


async def _record_leaflife_sync(
    order_number: str, status: str, rows_written: int, error: str
) -> None:
    """Upsert the LeafLife sheet-sync status for an order (own connection)."""
    short = leaflife_orders.short_order_no(order_number)
    try:
        conn = await aiosqlite.connect(DB_PATH)
        try:
            await conn.execute("PRAGMA busy_timeout = 30000")
            await conn.execute(
                """
                INSERT INTO leaflife_order_sync
                    (order_number, status, rows_written, attempts, last_error,
                     synced_at, updated_at)
                VALUES (?, ?, ?, 1, ?,
                        CASE WHEN ?='synced' THEN CURRENT_TIMESTAMP ELSE NULL END,
                        CURRENT_TIMESTAMP)
                ON CONFLICT(order_number) DO UPDATE SET
                    status=excluded.status,
                    rows_written=excluded.rows_written,
                    attempts=leaflife_order_sync.attempts + 1,
                    last_error=excluded.last_error,
                    synced_at=CASE WHEN excluded.status='synced'
                        THEN CURRENT_TIMESTAMP ELSE leaflife_order_sync.synced_at END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (short, status, rows_written, error or None, status),
            )
            await conn.commit()
        finally:
            await conn.close()
    except Exception as e:  # noqa: BLE001 - tracking is best-effort
        print(f"[leaflife-orders] failed to record sync status for {short}: {e}")


# Fire-and-forget tasks are only weakly referenced by the event loop, so a task
# whose only reference is a local variable can be garbage-collected mid-await.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _keep_task(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _sync_leaflife_order(order: "CreateOrderRequest", order_number: str) -> dict:
    """Best-effort: append a shipping order's LeafLife (LF-) items to the sheet.

    Only "Ship to Me" orders with LF- items are written; non-LeafLife lines are
    ignored. Never raises — records status for retry/backfill.
    """
    if not _is_shipping_fulfillment(order.fulfillment_type):
        return {"ok": False, "reason": "not a shipping order", "written": 0}
    items = _order_items_as_dicts(order.items)
    if not leaflife_orders.leaflife_items(items):
        return {"ok": False, "reason": "no LeafLife items", "written": 0}
    if not leaflife_orders.is_configured():
        return {"ok": False, "reason": "Google Sheets credentials not configured", "written": 0}

    ship = order.shipping_address or OrderShipping()
    result = await leaflife_orders.sync_order(
        order_number=order_number,
        first_name=order.customer.first_name,
        last_name=order.customer.last_name,
        street=ship.address,
        city=ship.city,
        state=ship.state,
        zip_code=ship.zip,
        notes=order.notes,
        shipping_service=order.shipping_service,
        items=items,
    )
    if result.get("ok"):
        status = "synced" if result.get("written") else "already_present"
        await _record_leaflife_sync(order_number, status, int(result.get("written", 0)), "")
    else:
        await _record_leaflife_sync(order_number, "failed", 0, str(result.get("reason", "")))
    return result


async def _deduct_stock_for_order(items: List[OrderItem], fulfillment_type: str = "shipping") -> List[bool]:
    """Deduct stock from the correct Clover location based on fulfillment type.
    For pickup orders at East/West, resolves items by SKU/name since the
    product_id from the website is HQ's Clover item ID which differs per merchant.

    Returns one flag per line item — True where Clover accepted the new count —
    so a retry only touches the lines that never made it through."""
    written = [False] * len(items)
    try:
        if fulfillment_type == "pickup_west" and WEST_MERCHANT_ID and WEST_API_TOKEN:
            merchant_id = WEST_MERCHANT_ID
            api_token = WEST_API_TOKEN
        elif fulfillment_type == "pickup_east" and EAST_MERCHANT_ID and EAST_API_TOKEN:
            merchant_id = EAST_MERCHANT_ID
            api_token = EAST_API_TOKEN
        else:
            merchant_id = HQ_MERCHANT_ID
            api_token = HQ_API_TOKEN
        base = f"{CLOVER_BASE_URL}/merchants/{merchant_id}"
        headers = {"Authorization": f"Bearer {api_token}"}

        # For non-HQ locations, pre-fetch all items to resolve by SKU/name
        is_non_hq = fulfillment_type in ("pickup_west", "pickup_east")
        location_lookup = None
        if is_non_hq:
            location_lookup = await _resolve_location_items(merchant_id, api_token)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for index, item in enumerate(items):
                clover_item_id = item.product_id
                if not clover_item_id and not item.sku and not item.name:
                    print(f"[stock] Skipping stock deduction for '{item.name}' — no identifiers")
                    continue

                # Resolve the correct Clover item ID at the target location
                if is_non_hq and location_lookup and location_lookup["by_id"]:
                    local_item = _find_item_at_location(location_lookup, clover_item_id, item.sku, item.name)
                    if local_item:
                        clover_item_id = local_item["id"]
                    else:
                        print(f"[stock] Could not find '{item.name}' (SKU: {item.sku}) at target location")
                        continue

                if not clover_item_id:
                    print(f"[stock] Skipping stock deduction for '{item.name}' — no product_id")
                    continue

                try:
                    resp = await client.get(
                        f"{base}/item_stocks/{clover_item_id}",
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        print(f"[stock] Could not get stock for {clover_item_id} ({item.name}): {resp.status_code}")
                        continue

                    stock_data = resp.json()
                    current_stock = stock_data.get("quantity", 0)
                    new_stock = max(0, current_stock - item.quantity)

                    update_resp = await client.post(
                        f"{base}/item_stocks/{clover_item_id}",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"quantity": new_stock},
                    )
                    if update_resp.status_code in (200, 201):
                        written[index] = True
                        print(f"[stock] Deducted {item.quantity} from '{item.name}' ({clover_item_id}): {current_stock} -> {new_stock}")
                    else:
                        print(f"[stock] Failed to update stock for {clover_item_id}: {update_resp.status_code} {update_resp.text[:200]}")
                except Exception as e:
                    print(f"[stock] Error deducting stock for '{item.name}': {e}")

        invalidate_product_cache()
        print(f"[stock] Stock deduction complete for {sum(written)}/{len(items)} item(s), cache invalidated")
    except Exception as e:
        print(f"[stock] Stock deduction task failed: {e}")
    return written


async def _pending_stock_lines(
    db: aiosqlite.Connection, order_id: int
) -> tuple[List[int], List[OrderItem]]:
    """An order's line items whose stock hasn't left Clover yet, with their row ids."""
    cursor = await db.execute(
        """SELECT id, product_id, product_name, sku, price, quantity
           FROM ecommerce_order_items
           WHERE order_id = ? AND stock_deducted_at IS NULL
           ORDER BY id""",
        (order_id,),
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows], [
        OrderItem(
            product_id=row[1] or "",
            name=row[2] or "",
            sku=row[3] or "",
            price=row[4] or 0,
            quantity=row[5] or 0,
        )
        for row in rows
    ]


async def _flag_deducted_lines(
    db: aiosqlite.Connection, order_id: int, line_ids: List[int], written: List[bool]
) -> bool:
    """Stamp each line Clover accepted, and the order once no line is left.

    Per-line stamps are what make a retry safe: a line already taken off the
    shelf is never written a second time.
    """
    for line_id, ok in zip(line_ids, written):
        if ok:
            await db.execute(
                "UPDATE ecommerce_order_items SET stock_deducted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (line_id,),
            )
    cursor = await db.execute(
        "SELECT COUNT(*) FROM ecommerce_order_items WHERE order_id = ? AND stock_deducted_at IS NULL",
        (order_id,),
    )
    complete = (await cursor.fetchone())[0] == 0
    if complete:
        await db.execute(
            "UPDATE ecommerce_orders SET stock_deducted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_id,),
        )
    await db.commit()
    return complete


async def _deduct_order_stock_once(
    db: aiosqlite.Connection, order_id: int, fulfillment_type: str
) -> bool:
    """Deduct whatever of an order hasn't been deducted yet. Safe to call repeatedly."""
    line_ids, pending = await _pending_stock_lines(db, order_id)
    if not pending:
        return True
    written = await _deduct_stock_for_order(pending, fulfillment_type)
    complete = await _flag_deducted_lines(db, order_id, line_ids, written)
    if not complete:
        print(f"[stock] Order {order_id} not fully deducted — will retry when its status changes")
    return complete


async def _deduct_stock_and_flag(order_id: int, items: List[OrderItem], fulfillment_type: str) -> bool:
    """Background stock deduction for a fresh order, on its own DB connection.

    Falls back to the request's items when the order didn't make it into the DB —
    the shelf still needs to be right even if the row is missing.
    """
    if not order_id:
        written = await _deduct_stock_for_order(items, fulfillment_type)
        return all(written)
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")
            return await _deduct_order_stock_once(conn, order_id, fulfillment_type)
    except Exception as e:
        print(f"[stock] Could not deduct stock for order {order_id}: {e}")
        return False


async def _award_loyalty_points_for_order(
    order: CreateOrderRequest,
    order_number: str,
    order_id: int,
) -> None:
    """Award loyalty points for an online order if the customer has a loyalty account.
    POS orders get points via Clover sync, but online orders need explicit awarding.

    Opens its own DB connection: this runs as a background task after the request
    handler returns, by which point the request-scoped connection is already closed."""
    from app.database import DB_PATH
    db = None
    try:
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout = 5000")
        # Look up loyalty settings
        settings_cursor = await db.execute(
            "SELECT value FROM loyalty_settings WHERE key = 'points_per_dollar'"
        )
        settings_row = await settings_cursor.fetchone()
        points_per_dollar = int(settings_row[0]) if settings_row else 1

        # Try to find the customer's loyalty account by email or phone
        loyalty_customer = None
        if order.customer.email:
            cur = await db.execute(
                "SELECT id, first_name, last_name, email, phone FROM loyalty_customers WHERE email = ? COLLATE NOCASE",
                (order.customer.email,),
            )
            loyalty_customer = await cur.fetchone()
        if not loyalty_customer and order.customer.phone:
            phone = "".join(ch for ch in order.customer.phone if ch.isdigit())
            if len(phone) >= 10:
                phone = phone[-10:]
                cur = await db.execute(
                    "SELECT id, first_name, last_name, email, phone FROM loyalty_customers WHERE "
                    "REPLACE(REPLACE(REPLACE(REPLACE(phone, '-', ''), ' ', ''), '(', ''), ')', '') LIKE ?",
                    (f"%{phone}",),
                )
                loyalty_customer = await cur.fetchone()

        if not loyalty_customer:
            # Auto-enroll the shopper: signing up here (rather than skipping) also
            # creates them in Clover, so the register recognises them in store.
            digits = "".join(ch for ch in (order.customer.phone or "") if ch.isdigit())
            if len(digits) < 10:
                print(f"[loyalty-award] No loyalty account and no usable phone for {order.customer.email} — skipping points")
                return
            try:
                await _do_signup(
                    order.customer.phone,
                    order.customer.first_name or "Customer",
                    order.customer.last_name or "",
                    order.customer.email or "",
                    db,
                )
            except Exception as signup_err:
                print(f"[loyalty-award] Auto-enroll failed for {order.customer.email}: {signup_err}")
                return
            cur = await db.execute(
                "SELECT id, first_name, last_name, email, phone FROM loyalty_customers WHERE "
                "REPLACE(REPLACE(REPLACE(REPLACE(phone, '-', ''), ' ', ''), '(', ''), ')', '') LIKE ?",
                (f"%{digits[-10:]}",),
            )
            loyalty_customer = await cur.fetchone()
            if not loyalty_customer:
                print(f"[loyalty-award] Auto-enroll produced no account for {order.customer.email} — skipping points")
                return
            print(f"[loyalty-award] Auto-enrolled {order.customer.email or digits[-10:]} in Hemp Rewards")

        customer_id = loyalty_customer[0]

        # Calculate points from the item subtotal (exclude shipping, tax, discounts already applied)
        item_subtotal = sum(item.price * item.quantity for item in order.items)
        order_dollars = item_subtotal / 100.0
        points_to_award = math.floor(order_dollars * points_per_dollar)

        if points_to_award <= 0:
            print(f"[loyalty-award] Zero points for {order_number} (subtotal ${order_dollars:.2f}) — skipping")
            return

        # Determine location name
        loc_name = "Online"
        if order.fulfillment_type == "pickup_west":
            loc_name = "West"
        elif order.fulfillment_type == "pickup_east":
            loc_name = "East"
        elif order.fulfillment_type == "local_delivery":
            loc_name = "Local Delivery"

        await db.execute(
            """UPDATE loyalty_customers
               SET points_balance = points_balance + ?,
                   lifetime_points = lifetime_points + ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (points_to_award, points_to_award, customer_id),
        )
        await db.execute(
            """INSERT INTO loyalty_transactions (customer_id, type, points, description, order_id, location_name)
               VALUES (?, 'earn', ?, ?, ?, ?)""",
            (customer_id, points_to_award,
             f"Online purchase ${order_dollars:.2f} ({order_number})",
             str(order_id), loc_name),
        )
        await db.commit()
        await _sync_balance_to_clover_quietly(db, customer_id)
        print(f"[loyalty-award] Awarded {points_to_award} pts to customer {customer_id} for {order_number} (${order_dollars:.2f})")
    except Exception as e:
        print(f"[loyalty-award] Failed to award points for {order_number}: {e}")
    finally:
        if db is not None:
            await db.close()


async def _get_smtp_settings(db: aiosqlite.Connection) -> dict[str, str]:
    """Get SMTP settings from database, falling back to env vars."""
    smtp_settings: dict[str, str] = {}
    for key in ["smtp_host", "smtp_port", "smtp_user", "smtp_password"]:
        try:
            cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cursor.fetchone()
            if row:
                smtp_settings[key] = row[0]
        except Exception:
            pass
    # Fall back to env vars for any missing settings
    if not smtp_settings.get("smtp_host"):
        smtp_settings["smtp_host"] = SMTP_HOST
    if not smtp_settings.get("smtp_port"):
        smtp_settings["smtp_port"] = SMTP_PORT
    if not smtp_settings.get("smtp_user") and SMTP_USER:
        smtp_settings["smtp_user"] = SMTP_USER
    if not smtp_settings.get("smtp_password") and SMTP_PASSWORD:
        smtp_settings["smtp_password"] = SMTP_PASSWORD
    return smtp_settings


def _send_smtp_email(smtp_settings: dict[str, str], to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via SMTP (synchronous). to_email can be a single address or comma-separated list."""
    smtp_host = smtp_settings.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(smtp_settings.get("smtp_port", "587"))
    smtp_user = smtp_settings.get("smtp_user", "")
    smtp_password = smtp_settings.get("smtp_password", "")

    if not smtp_user or not smtp_password:
        print("SMTP credentials not configured, skipping email")
        return False

    # Support multiple recipients in a single SMTP session
    recipients = [r.strip() for r in to_email.split(",") if r.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f"Failed to send email to {recipients}: {e}")
        return False


async def _send_order_emails(
    smtp_settings: dict[str, str],
    order: CreateOrderRequest,
    order_number: str,
    charge_id: str,
    payment_status: str,
) -> None:
    """Send order notification to store and confirmation to customer."""
    try:
        if not smtp_settings.get("smtp_user") or not smtp_settings.get("smtp_password"):
            print("SMTP not configured — skipping order emails")
            return

        # Build items HTML table rows
        items_html = ""
        for item in order.items:
            line_total = item.price * item.quantity
            items_html += f"""
            <tr>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb;">{item.name}</td>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">{item.quantity}</td>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: right;">{_format_price(item.price)}</td>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: right;">{_format_price(line_total)}</td>
            </tr>
            """

        is_pickup = order.fulfillment_type and order.fulfillment_type.startswith("pickup")
        is_delivery = order.fulfillment_type == "local_delivery"
        if is_pickup:
            if order.fulfillment_type == "pickup_west":
                shipping_label = "Pickup Location"
                shipping_line = "The Hemp Dispensary — West<br>6175 Deltona Blvd, Suite 104<br>Spring Hill, FL 34606"
            else:
                shipping_label = "Pickup Location"
                shipping_line = "The Hemp Dispensary — East<br>14312 Spring Hill Dr<br>Spring Hill, FL 34609"
        elif is_delivery:
            shipping_label = "Delivering To"
            shipping_line = f"{order.shipping_address.address}"
            if order.shipping_address.apartment:
                shipping_line += f", {order.shipping_address.apartment}"
            shipping_line += f"<br>{order.shipping_address.city}, {order.shipping_address.state} {order.shipping_address.zip}"
            shipping_line += "<br><strong style='color: #059669;'>Estimated delivery within 42 hours</strong>"
        else:
            shipping_label = "Shipping To"
            shipping_line = f"{order.shipping_address.address}"
            if order.shipping_address.apartment:
                shipping_line += f", {order.shipping_address.apartment}"
            shipping_line += f"<br>{order.shipping_address.city}, {order.shipping_address.state} {order.shipping_address.zip}"

        cost_label = "Delivery Fee" if is_delivery else ("Pickup" if is_pickup else "Shipping")

        # --- Store notification email ---
        store_html = f"""
        <html>
        <body style="font-family: 'Helvetica Neue', Arial, sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
            <div style="background: #065f46; padding: 20px; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 22px;">New Online Order!</h1>
            </div>
            <div style="padding: 24px; background: #f9fafb;">
                <p style="font-size: 16px;">A new order has been placed and payment was <strong style="color: #059669;">successful</strong>.</p>

                <table style="width: 100%; margin: 16px 0; background: white; border-radius: 8px; overflow: hidden;">
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">Order Number</td>
                        <td style="padding: 10px 12px;">{order_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Customer</td>
                        <td style="padding: 10px 12px;">{order.customer.first_name} {order.customer.last_name}</td>
                    </tr>
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">Email</td>
                        <td style="padding: 10px 12px;">{order.customer.email}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Phone</td>
                        <td style="padding: 10px 12px;">{order.customer.phone}</td>
                    </tr>
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">{shipping_label}</td>
                        <td style="padding: 10px 12px;">{shipping_line}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Charge ID</td>
                        <td style="padding: 10px 12px; font-family: monospace; font-size: 13px;">{charge_id}</td>
                    </tr>
                </table>

                <h3 style="margin-top: 20px;">Items Ordered</h3>
                <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden;">
                    <thead>
                        <tr style="background: #065f46; color: white;">
                            <th style="padding: 10px 12px; text-align: left;">Product</th>
                            <th style="padding: 10px 12px; text-align: center;">Qty</th>
                            <th style="padding: 10px 12px; text-align: right;">Price</th>
                            <th style="padding: 10px 12px; text-align: right;">Total</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                    </tbody>
                </table>

                <table style="width: 100%; margin-top: 16px; background: white; border-radius: 8px; overflow: hidden;">
                    <tr>
                        <td style="padding: 8px 12px;">Subtotal</td>
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(order.subtotal + order.sale_discount) if order.sale_discount else _format_price(order.subtotal)}</td>
                    </tr>
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Sale Discount</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.sale_discount)}</td></tr>' if order.sale_discount else ''}
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Discount ({order.promo_code})</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.discount)}</td></tr>' if order.discount else ''}
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Volume Discount</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.volume_discount)}</td></tr>' if order.volume_discount else ''}
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Loyalty Reward</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.loyalty_discount)}</td></tr>' if order.loyalty_discount else ''}
                    <tr>
                        <td style="padding: 8px 12px;">{cost_label}</td>
                        <td style="padding: 8px 12px; text-align: right;">{'Free' if order.shipping_cost == 0 else _format_price(order.shipping_cost)}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px;">Tax</td>
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(order.tax)}</td>
                    </tr>
                    <tr style="font-weight: bold; font-size: 18px; background: #f3f4f6;">
                        <td style="padding: 12px;">Total</td>
                        <td style="padding: 12px; text-align: right; color: #059669;">{_format_price(order.total)}</td>
                    </tr>
                </table>

                {f'<p style="margin-top: 12px;"><strong>Notes:</strong> {order.notes}</p>' if order.notes else ''}
            </div>
            <div style="padding: 16px; text-align: center; color: #9ca3af; font-size: 12px;">
                The Hemp Dispensary — Online Orders
            </div>
        </body>
        </html>
        """

        # Send to store — route to location-specific email(s) for pickup orders
        store_subject = f"New Order {order_number} — {_format_price(order.total)} from {order.customer.first_name} {order.customer.last_name}"
        if order.fulfillment_type == "pickup_west":
            store_recipients = ["west@thehempdispensary.com", "THD1SHW@icloud.com"]
        elif order.fulfillment_type == "pickup_east":
            store_recipients = ["east@thehempdispensary.com", "THD7SHE@icloud.com"]
        else:
            store_recipients = [STORE_EMAIL]

        loop = asyncio.get_event_loop()
        # Send to ALL store recipients in a single SMTP session to avoid
        # Gmail rate-limiting or transient failures between connections.
        all_store_recipients = ", ".join(store_recipients)
        sent = await loop.run_in_executor(
            None,
            _send_smtp_email,
            smtp_settings,
            all_store_recipients,
            store_subject,
            store_html,
        )
        if sent:
            print(f"Store notification sent to {store_recipients} for order {order_number}")
        else:
            print(f"FAILED to send store notification to {store_recipients} for order {order_number}")

        # --- Customer confirmation email ---
        customer_html = f"""
        <html>
        <body style="font-family: 'Helvetica Neue', Arial, sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
            <div style="background: #065f46; padding: 20px; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 22px;">Order Confirmed!</h1>
            </div>
            <div style="padding: 24px; background: #f9fafb;">
                <p style="font-size: 16px;">Hi {order.customer.first_name},</p>
                <p>Thank you for your order! Your payment has been processed successfully.</p>

                <table style="width: 100%; margin: 16px 0; background: white; border-radius: 8px; overflow: hidden;">
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">Order Number</td>
                        <td style="padding: 10px 12px;">{order_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Payment Status</td>
                        <td style="padding: 10px 12px; color: #059669; font-weight: bold;">Paid</td>
                    </tr>
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">{shipping_label}</td>
                        <td style="padding: 10px 12px;">{shipping_line}</td>
                    </tr>
                </table>

                <h3>Your Items</h3>
                <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden;">
                    <thead>
                        <tr style="background: #065f46; color: white;">
                            <th style="padding: 10px 12px; text-align: left;">Product</th>
                            <th style="padding: 10px 12px; text-align: center;">Qty</th>
                            <th style="padding: 10px 12px; text-align: right;">Price</th>
                            <th style="padding: 10px 12px; text-align: right;">Total</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                    </tbody>
                </table>

                <table style="width: 100%; margin-top: 16px; background: white; border-radius: 8px; overflow: hidden;">
                    <tr>
                        <td style="padding: 8px 12px;">Subtotal</td>
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(order.subtotal + order.sale_discount) if order.sale_discount else _format_price(order.subtotal)}</td>
                    </tr>
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Sale Discount</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.sale_discount)}</td></tr>' if order.sale_discount else ''}
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Discount ({order.promo_code})</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.discount)}</td></tr>' if order.discount else ''}
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Volume Discount</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.volume_discount)}</td></tr>' if order.volume_discount else ''}
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Loyalty Reward</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.loyalty_discount)}</td></tr>' if order.loyalty_discount else ''}
                    <tr>
                        <td style="padding: 8px 12px;">{cost_label}</td>
                        <td style="padding: 8px 12px; text-align: right;">{'Free' if order.shipping_cost == 0 else _format_price(order.shipping_cost)}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px;">Tax</td>
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(order.tax)}</td>
                    </tr>
                    <tr style="font-weight: bold; font-size: 18px; background: #f3f4f6;">
                        <td style="padding: 12px;">Total Charged</td>
                        <td style="padding: 12px; text-align: right; color: #059669;">{_format_price(order.total)}</td>
                    </tr>
                </table>

                <p style="margin-top: 20px;">If you have any questions about your order, reply to this email or contact us at <a href="mailto:{STORE_EMAIL}">{STORE_EMAIL}</a>.</p>
                <p>Thank you for choosing The Hemp Dispensary!</p>
            </div>
            <div style="padding: 16px; text-align: center; color: #9ca3af; font-size: 12px;">
                The Hemp Dispensary — Premium Hemp Products<br>
                Spring Hill, FL
            </div>
        </body>
        </html>
        """

        # Send to customer
        await loop.run_in_executor(
            None,
            _send_smtp_email,
            smtp_settings,
            order.customer.email,
            f"Order Confirmed — {order_number} | The Hemp Dispensary",
            customer_html,
        )
        print(f"Customer confirmation sent to {order.customer.email} for order {order_number}")

    except Exception as e:
        print(f"Error sending order emails: {e}")


@router.get("/products/{product_id}")
async def get_product_detail(product_id: str):
    """Public endpoint: Get a single product detail by Clover item ID.
    Serves from the in-memory cache when available, falls back to direct Clover API."""
    cached = await _get_cached_products()
    for p in cached["products"]:
        if p["id"] == product_id:
            return p

    # Not in cache — fetch directly from Clover as fallback
    base = f"{CLOVER_BASE_URL}/merchants/{HQ_MERCHANT_ID}"
    headers = {"Authorization": f"Bearer {HQ_API_TOKEN}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{base}/items/{product_id}",
            headers=headers,
            params={"expand": "categories,itemStock"},
        )
        resp.raise_for_status()
        item = resp.json()

    name = item.get("name", "")
    sku = item.get("sku", "") or item.get("id", "")
    stock_info = item.get("itemStock", {})
    stock = stock_info.get("quantity", 0) if stock_info else 0

    image_base_url = os.environ.get("BASE_URL", "https://thd-inventory-api.fly.dev") + "/api/inventory/images"
    from app.database import DB_PATH
    db = await aiosqlite.connect(DB_PATH)
    try:
        cursor = await db.execute(
            "SELECT sku, updated_at FROM product_images WHERE sku = ? OR UPPER(product_name) = ?",
            (sku, name.upper()),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()
    image_url = f"{image_base_url}/{url_quote(row[0], safe='')}?v=2&bg=1&t={str(row[1] or '').replace(' ', '_')}" if row else None
    name_up = name.upper()
    if "GUMMIES" in name_up or "GUMMY" in name_up:
        gummy_image_sku = os.environ.get("GUMMY_IMAGE_SKU", "2025754319138")
        image_url = f"{image_base_url}/{url_quote(gummy_image_sku, safe='')}?v=2&bg=1"

    is_shipping_only = sku.startswith("LF-") if isinstance(sku, str) else False

    # Look up linked COA lab results (check both SKU and Clover item ID)
    coa_db = await aiosqlite.connect(DB_PATH)
    try:
        coa_cursor = await coa_db.execute(
            """SELECT cr.sample_accession, cr.description, cr.batch_no,
                      cr.sample_status, cr.coa_approved_date
               FROM coa_sku_links csl
               JOIN coa_results cr ON csl.sample_accession = cr.sample_accession
               WHERE csl.sku = ? OR csl.sku = ?
               ORDER BY cr.coa_approved_date DESC""",
            (sku, product_id),
        )
        coa_rows = await coa_cursor.fetchall()
    finally:
        await coa_db.close()
    lab_results = [
        {
            "sample_accession": r[0],
            "description": r[1],
            "batch_no": r[2],
            "sample_status": r[3],
            "coa_approved_date": r[4],
        }
        for r in coa_rows
    ]

    return {
        "id": item.get("id", ""),
        "name": name,
        "online_name": item.get("onlineName", "") or name,
        "sku": sku,
        "price": item.get("price", 0),
        "description": item.get("description", ""),
        "categories": [c.get("name", "") for c in item.get("categories", {}).get("elements", [])],
        "stock": stock,
        "available": item.get("available", True) and stock > 0,
        "image_url": image_url,
        "is_age_restricted": item.get("isAgeRestricted", False),
        "shipping_only": is_shipping_only,
        "modified_time": item.get("modifiedTime", 0),
        "lab_results": lab_results,
    }


@router.get("/orders")
async def get_orders(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    search: Optional[str] = None,
):
    """Get online orders (requires admin auth via Authorization header)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    # Verify JWT token
    import jwt
    token = auth.split(" ", 1)[1]
    jwt_secret = os.environ.get("JWT_SECRET", "hemp-inventory-secret-key")
    try:
        jwt.decode(token, jwt_secret, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Build WHERE clauses
    where_clauses: list[str] = []
    params: list = []

    if status:
        where_clauses.append("o.payment_status = ?")
        params.append(status)

    if search and search.strip():
        # Split search into words so "jimmy jimmy" matches first_name + last_name
        words = search.strip().split()
        for word in words:
            like = f"%{word}%"
            where_clauses.append(
                """(o.customer_first_name LIKE ? COLLATE NOCASE
                   OR o.customer_last_name LIKE ? COLLATE NOCASE
                   OR o.customer_email LIKE ? COLLATE NOCASE
                   OR o.order_number LIKE ? COLLATE NOCASE
                   OR o.tracking_number LIKE ? COLLATE NOCASE
                   OR o.shipping_address LIKE ? COLLATE NOCASE
                   OR o.shipping_city LIKE ? COLLATE NOCASE
                   OR o.shipping_state LIKE ? COLLATE NOCASE
                   OR o.shipping_zip LIKE ? COLLATE NOCASE
                   OR o.id IN (
                       SELECT oi.order_id FROM ecommerce_order_items oi
                       WHERE oi.product_name LIKE ? COLLATE NOCASE
                   ))"""
            )
            params.extend([like, like, like, like, like, like, like, like, like, like])

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Fetch orders
    query = f"SELECT o.* FROM ecommerce_orders o{where_sql} ORDER BY o.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor = await db.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    orders = [dict(zip(columns, row)) for row in rows]

    # Fetch items and shipments for each order
    for order in orders:
        item_cursor = await db.execute(
            "SELECT product_id, product_name, sku, price, quantity FROM ecommerce_order_items WHERE order_id = ?",
            (order["id"],),
        )
        item_cols = [desc[0] for desc in item_cursor.description]
        item_rows = await item_cursor.fetchall()
        order["items"] = [dict(zip(item_cols, row)) for row in item_rows]

        # Attach split-shipment data when present
        scur = await db.execute(
            """SELECT id, shipment_type, from_label, tracking_number, tracking_url,
                      label_url, tracking_status, item_ids
               FROM order_shipments WHERE order_id = ? ORDER BY id""",
            (order["id"],),
        )
        shipment_rows = await scur.fetchall()
        if shipment_rows:
            shipments = []
            for s in shipment_rows:
                shipments.append({
                    "shipment_id": s[0],
                    "shipment_type": s[1],
                    "from_label": s[2],
                    "tracking_number": s[3] or "",
                    "tracking_url": s[4] or "",
                    "label_url": s[5] or "",
                    "tracking_status": s[6] or "",
                })
            order["shipments"] = shipments

    # Get total count with same filters
    count_params: list = []
    count_where_clauses: list[str] = []

    if status:
        count_where_clauses.append("o.payment_status = ?")
        count_params.append(status)

    if search and search.strip():
        words = search.strip().split()
        for word in words:
            like = f"%{word}%"
            count_where_clauses.append(
                """(o.customer_first_name LIKE ? COLLATE NOCASE
                   OR o.customer_last_name LIKE ? COLLATE NOCASE
                   OR o.customer_email LIKE ? COLLATE NOCASE
                   OR o.order_number LIKE ? COLLATE NOCASE
                   OR o.tracking_number LIKE ? COLLATE NOCASE
                   OR o.shipping_address LIKE ? COLLATE NOCASE
                   OR o.shipping_city LIKE ? COLLATE NOCASE
                   OR o.shipping_state LIKE ? COLLATE NOCASE
                   OR o.shipping_zip LIKE ? COLLATE NOCASE
                   OR o.id IN (
                       SELECT oi.order_id FROM ecommerce_order_items oi
                       WHERE oi.product_name LIKE ? COLLATE NOCASE
                   ))"""
            )
            count_params.extend([like, like, like, like, like, like, like, like, like, like])

    count_where_sql = (" WHERE " + " AND ".join(count_where_clauses)) if count_where_clauses else ""
    count_query = f"SELECT COUNT(*) FROM ecommerce_orders o{count_where_sql}"
    count_cursor = await db.execute(count_query, count_params)
    total = (await count_cursor.fetchone())[0]

    return {"orders": orders, "total": total}


@router.patch("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update an order's fulfillment status (requires admin auth)."""
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

    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in ("pending", "paid", "processing", "shipped", "delivered", "cancelled"):
        raise HTTPException(status_code=400, detail="Invalid status")

    await db.execute(
        "UPDATE ecommerce_orders SET payment_status = ? WHERE id = ?",
        (new_status, order_id),
    )
    await db.commit()

    # Checkout deducts stock in a background task that can lose its Clover call
    # (network blip, item not yet resolvable at the pickup location). Staff moving
    # the order forward is the moment the goods really leave the shelf, so catch up
    # here — stock_deducted_at keeps it to exactly once per order.
    stock_deducted = False
    if new_status in ("paid", "processing", "shipped", "delivered"):
        cursor = await db.execute(
            "SELECT fulfillment_type, stock_deducted_at FROM ecommerce_orders WHERE id = ?",
            (order_id,),
        )
        row = await cursor.fetchone()
        if row and not row[1]:
            stock_deducted = await _deduct_order_stock_once(db, order_id, row[0] or "shipping")

    return {
        "success": True,
        "order_id": order_id,
        "status": new_status,
        "stock_deducted": stock_deducted,
    }


@router.post("/orders/recover")
async def recover_order(
    order: RecoverOrderRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Manually recover an order that was charged but failed to save (requires admin auth).

    Used to re-enter an order from the "Order Charged But NOT Saved" alert email. Idempotent
    on order_number so re-submitting the same order won't create a duplicate.
    """
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

    if not order.items:
        raise HTTPException(status_code=400, detail="At least one item is required")

    order_number = order.order_number.strip() or (
        "HD-" + hex(int(time.time()))[2:].upper() + "-" + str(int(time.time() * 1000) % 10000)
    )

    # Idempotency: don't recreate an order that already exists.
    existing = await db.execute(
        "SELECT id FROM ecommerce_orders WHERE order_number = ?",
        (order_number,),
    )
    row = await existing.fetchone()
    if row is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Order {order_number} already exists (id={row[0]})",
        )

    await db.execute("PRAGMA busy_timeout = 30000")
    order_id = await _insert_ecommerce_order(
        db,
        order,
        order_number,
        order.charge_id,
        order.payment_status or "paid",
        "",
    )
    await db.commit()

    # If this order was captured to disk as a lost order, clean up the backup file.
    try:
        lost_path = os.path.join(LOST_ORDERS_DIR, f"{order_number}.json")
        if os.path.exists(lost_path):
            os.remove(lost_path)
    except Exception as cleanup_err:
        print(f"[recover] Could not remove lost-order file for {order_number}: {cleanup_err}")

    print(f"[recover] Manually recovered order {order_number} (id={order_id}, total=${order.total/100:.2f})")
    return {"success": True, "order_id": order_id, "order_number": order_number}


def _require_admin(request: Request) -> None:
    """Raise 401 unless the request carries a valid admin bearer token."""
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


async def _sync_leaflife_from_db(db: aiosqlite.Connection, order_number: str) -> dict:
    """Reconstruct a stored order and (re)write its LeafLife items to the sheet."""
    cur = await db.execute(
        """SELECT customer_first_name, customer_last_name, shipping_address,
                  shipping_city, shipping_state, shipping_zip, notes,
                  shipping_service, fulfillment_type, id
             FROM ecommerce_orders WHERE order_number = ?""",
        (order_number,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Order {order_number} not found")
    if not _is_shipping_fulfillment(row[8]):
        return {"ok": False, "reason": "not a shipping order", "written": 0}

    items_cur = await db.execute(
        "SELECT product_name, sku, price, quantity FROM ecommerce_order_items WHERE order_id = ?",
        (row[9],),
    )
    item_rows = await items_cur.fetchall()
    items = [
        {"name": r[0] or "", "sku": r[1] or "", "price": r[2] or 0, "quantity": r[3] or 1}
        for r in item_rows
    ]
    if not leaflife_orders.leaflife_items(items):
        return {"ok": False, "reason": "no LeafLife items", "written": 0}

    result = await leaflife_orders.sync_order(
        order_number=order_number,
        first_name=row[0] or "",
        last_name=row[1] or "",
        street=row[2] or "",
        city=row[3] or "",
        state=row[4] or "",
        zip_code=row[5] or "",
        notes=row[6] or "",
        shipping_service=row[7] or "",
        items=items,
    )
    if result.get("ok"):
        status = "synced" if result.get("written") else "already_present"
        await _record_leaflife_sync(order_number, status, int(result.get("written", 0)), "")
    else:
        await _record_leaflife_sync(order_number, "failed", 0, str(result.get("reason", "")))
    return result


@router.post("/leaflife-sheet/sync/{order_number}")
async def leaflife_sheet_sync_order(
    order_number: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Manually (re)sync one existing order into the LeafLife Order Sheet.

    Works for backfilling old orders and for retrying failed writes. Idempotent:
    the sheet is checked for the order # before appending.
    """
    _require_admin(request)
    if not leaflife_orders.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Sheets service account is not configured (GOOGLE_SHEETS_SA_JSON).",
        )
    result = await _sync_leaflife_from_db(db, order_number.strip())
    return {"order_number": order_number.strip(), **result}


@router.get("/leaflife-sheet/status")
async def leaflife_sheet_status(
    request: Request,
    limit: int = 100,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Report LeafLife sheet-sync configuration + recent per-order sync status."""
    _require_admin(request)
    cur = await db.execute(
        """SELECT order_number, status, rows_written, attempts, last_error,
                  synced_at, updated_at
             FROM leaflife_order_sync
             ORDER BY updated_at DESC LIMIT ?""",
        (max(1, min(limit, 500)),),
    )
    rows = await cur.fetchall()
    records = [
        {
            "order_number": r[0],
            "status": r[1],
            "rows_written": r[2],
            "attempts": r[3],
            "last_error": r[4],
            "synced_at": r[5],
            "updated_at": r[6],
        }
        for r in rows
    ]
    failed = sum(1 for r in records if r["status"] == "failed")
    return {"configured": leaflife_orders.is_configured(), "failed": failed, "records": records}


_LEAFLIFE_UNPAID_STATUSES = ("cancelled", "refunded", "failed", "unpaid", "pending")


async def _do_leaflife_sweep(db: aiosqlite.Connection, days: int = 30) -> dict:
    """Write any shipping order with LeafLife items that never reached the sheet.

    The write at checkout is fire-and-forget, so a restart, a Sheets hiccup or a
    dropped task leaves the order out of the sheet — and out of the tracking
    table too, which makes the miss invisible. This reconciles the last `days`
    of orders against the sheet. Idempotent: `sync_order` checks the sheet for
    the order # before appending.
    """
    if not leaflife_orders.is_configured():
        return {"checked": 0, "synced": 0, "failed": 0}

    placeholders = ",".join("?" for _ in _LEAFLIFE_UNPAID_STATUSES)
    cur = await db.execute(
        f"""SELECT DISTINCT o.order_number
              FROM ecommerce_orders o
              JOIN ecommerce_order_items i ON i.order_id = o.id
             WHERE o.created_at >= datetime('now', ?)
               AND UPPER(COALESCE(i.sku, '')) LIKE 'LF-%'
               AND LOWER(COALESCE(o.payment_status, '')) NOT IN ({placeholders})
             ORDER BY o.created_at""",
        (f"-{max(1, days)} days", *_LEAFLIFE_UNPAID_STATUSES),
    )
    candidates = [r[0] for r in await cur.fetchall()]

    done_cur = await db.execute(
        "SELECT order_number FROM leaflife_order_sync WHERE status IN ('synced', 'already_present')"
    )
    done = {r[0] for r in await done_cur.fetchall()}

    synced = 0
    failed = 0
    results = []
    for order_number in candidates:
        if leaflife_orders.short_order_no(order_number) in done:
            continue
        try:
            res = await _sync_leaflife_from_db(db, order_number)
        except Exception as e:  # noqa: BLE001 - sweep must never die on one order
            failed += 1
            results.append({"order_number": order_number, "ok": False, "reason": str(e)})
            continue
        if res.get("ok") and res.get("written"):
            synced += 1
        elif not res.get("ok") and res.get("reason") not in (
            "not a shipping order",
            "no LeafLife items",
        ):
            failed += 1
        results.append({"order_number": order_number, **res})
    return {"checked": len(candidates), "synced": synced, "failed": failed, "results": results}


@router.post("/leaflife-sheet/sweep")
async def leaflife_sheet_sweep(
    request: Request,
    days: int = 30,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Sync every recent LeafLife order that is missing from the Order Sheet."""
    _require_admin(request)
    if not leaflife_orders.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Sheets service account is not configured (GOOGLE_SHEETS_SA_JSON).",
        )
    return await _do_leaflife_sweep(db, days=days)


@router.post("/leaflife-sheet/retry-failed")
async def leaflife_sheet_retry_failed(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Retry every order whose last LeafLife sheet write failed."""
    _require_admin(request)
    if not leaflife_orders.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google Sheets service account is not configured (GOOGLE_SHEETS_SA_JSON).",
        )
    cur = await db.execute(
        "SELECT order_number FROM leaflife_order_sync WHERE status = 'failed'"
    )
    failed_numbers = [r[0] for r in await cur.fetchall()]
    results = []
    for short in failed_numbers:
        # The tracking table stores the short order #; match it back to the full one.
        oc = await db.execute(
            "SELECT order_number FROM ecommerce_orders WHERE order_number LIKE ?",
            (f"HD-{short}-%",),
        )
        orow = await oc.fetchone()
        full = orow[0] if orow else short
        try:
            res = await _sync_leaflife_from_db(db, full)
            results.append({"order_number": short, **res})
        except HTTPException as e:
            results.append({"order_number": short, "ok": False, "reason": e.detail})
    return {"retried": len(results), "results": results}


@router.patch("/orders/{order_id}/notes")
async def update_order_notes(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update an order's staff notes (requires admin auth)."""
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

    body = await request.json()
    staff_notes = body.get("staff_notes", "")

    await db.execute(
        "UPDATE ecommerce_orders SET staff_notes = ? WHERE id = ?",
        (staff_notes, order_id),
    )
    await db.commit()
    return {"success": True, "order_id": order_id, "staff_notes": staff_notes}


@router.patch("/orders/{order_id}/customer")
async def update_order_customer(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Update an order's customer details (requires admin auth)."""
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

    body = await request.json()

    # Build dynamic update query from allowed fields
    allowed_fields = [
        "customer_first_name", "customer_last_name", "customer_email",
        "customer_phone", "shipping_address", "shipping_apartment",
        "shipping_city", "shipping_state", "shipping_zip",
    ]
    updates = []
    params = []
    for field in allowed_fields:
        if field in body:
            updates.append(f"{field} = ?")
            params.append(body[field])

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    params.append(order_id)
    query = f"UPDATE ecommerce_orders SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    await db.execute(query, params)
    await db.commit()

    # Return the updated order fields
    cursor = await db.execute(
        """SELECT customer_first_name, customer_last_name, customer_email, customer_phone,
                  shipping_address, shipping_apartment, shipping_city, shipping_state, shipping_zip
           FROM ecommerce_orders WHERE id = ?""",
        (order_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    return {
        "success": True,
        "order_id": order_id,
        "customer_first_name": row[0],
        "customer_last_name": row[1],
        "customer_email": row[2],
        "customer_phone": row[3],
        "shipping_address": row[4],
        "shipping_apartment": row[5],
        "shipping_city": row[6],
        "shipping_state": row[7],
        "shipping_zip": row[8],
    }


@router.patch("/orders/{order_id}/convert-to-shipping")
async def convert_to_shipping(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Convert a pickup order to a shipping order (requires admin auth).
    Accepts shipping address fields and updates fulfillment_type to 'shipping'."""
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

    # Verify order exists and is currently a pickup order
    cursor = await db.execute(
        "SELECT fulfillment_type FROM ecommerce_orders WHERE id = ?", (order_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    current_type = row[0] or ""
    if not current_type.startswith("pickup"):
        raise HTTPException(status_code=400, detail="Order is not a pickup order")

    body = await request.json()

    # Require shipping address fields
    shipping_address = (body.get("shipping_address") or "").strip()
    shipping_city = (body.get("shipping_city") or "").strip()
    shipping_state = (body.get("shipping_state") or "").strip()
    shipping_zip = (body.get("shipping_zip") or "").strip()

    if not shipping_address or not shipping_city or not shipping_state or not shipping_zip:
        raise HTTPException(
            status_code=400,
            detail="Shipping address, city, state, and zip are required",
        )

    await db.execute(
        """UPDATE ecommerce_orders
           SET fulfillment_type = 'shipping',
               shipping_address = ?,
               shipping_apartment = ?,
               shipping_city = ?,
               shipping_state = ?,
               shipping_zip = ?,
               updated_at = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (
            shipping_address,
            (body.get("shipping_apartment") or "").strip(),
            shipping_city,
            shipping_state,
            shipping_zip,
            order_id,
        ),
    )
    await db.commit()

    # Return the updated order data
    cursor = await db.execute(
        """SELECT fulfillment_type, shipping_address, shipping_apartment,
                  shipping_city, shipping_state, shipping_zip
           FROM ecommerce_orders WHERE id = ?""",
        (order_id,),
    )
    updated = await cursor.fetchone()
    return {
        "success": True,
        "order_id": order_id,
        "fulfillment_type": updated[0],
        "shipping_address": updated[1],
        "shipping_apartment": updated[2],
        "shipping_city": updated[3],
        "shipping_state": updated[4],
        "shipping_zip": updated[5],
    }


@router.patch("/orders/{order_id}/fulfillment-type")
async def update_order_fulfillment_type(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin: Fix the fulfillment type for an order (e.g. when fallback insert lost the type)."""
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

    body = await request.json()
    new_type = body.get("fulfillment_type", "").strip()
    if new_type not in ("shipping", "pickup_west", "pickup_east", "local_delivery"):
        raise HTTPException(status_code=400, detail="Invalid fulfillment type")

    cursor = await db.execute("SELECT id FROM ecommerce_orders WHERE id = ?", (order_id,))
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Order not found")

    await db.execute(
        "UPDATE ecommerce_orders SET fulfillment_type = ? WHERE id = ?",
        (new_type, order_id),
    )
    await db.commit()
    return {"success": True, "order_id": order_id, "fulfillment_type": new_type}


@router.post("/orders/{order_id}/resend-confirmation")
async def resend_order_confirmation(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Resend order confirmation email to customer (requires admin auth)."""
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

    # Get order details
    cursor = await db.execute("SELECT * FROM ecommerce_orders WHERE id = ?", (order_id,))
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(zip(columns, row))

    customer_email = order.get("customer_email", "")
    if not customer_email:
        raise HTTPException(status_code=400, detail="No customer email on this order")

    # Get order items
    item_cursor = await db.execute(
        "SELECT product_name, price, quantity FROM ecommerce_order_items WHERE order_id = ?",
        (order_id,),
    )
    item_cols = [desc[0] for desc in item_cursor.description]
    item_rows = await item_cursor.fetchall()
    items = [dict(zip(item_cols, r)) for r in item_rows]

    # Build items HTML
    items_html = ""
    for item in items:
        line_total = item["price"] * item["quantity"]
        items_html += f"""
        <tr>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb;">{item["product_name"]}</td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">{item["quantity"]}</td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: right;">{_format_price(item["price"])}</td>
            <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: right;">{_format_price(line_total)}</td>
        </tr>
        """

    shipping_line = order.get("shipping_address", "")
    if order.get("shipping_apartment"):
        shipping_line += f", {order['shipping_apartment']}"
    shipping_line += f"<br>{order.get('shipping_city', '')}, {order.get('shipping_state', '')} {order.get('shipping_zip', '')}"

    order_number = order.get("order_number", f"THD-{order_id}")
    first_name = order.get("customer_first_name", "Customer")
    subtotal = order.get("subtotal", 0)
    discount = order.get("discount", 0)
    volume_discount = order.get("volume_discount", 0) or 0
    loyalty_discount = order.get("loyalty_discount", 0) or 0
    promo_code = order.get("promo_code", "")
    shipping_cost = order.get("shipping_cost", 0)
    tax = order.get("tax", 0)
    total = order.get("total", 0)

    customer_html = f"""
    <html>
    <body style="font-family: 'Helvetica Neue', Arial, sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
        <div style="background: #065f46; padding: 20px; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 22px;">Order Confirmed!</h1>
        </div>
        <div style="padding: 24px; background: #f9fafb;">
            <p style="font-size: 16px;">Hi {first_name},</p>
            <p>Thank you for your order! Your payment has been processed successfully.</p>

            <table style="width: 100%; margin: 16px 0; background: white; border-radius: 8px; overflow: hidden;">
                <tr style="background: #f3f4f6;">
                    <td style="padding: 10px 12px; font-weight: bold;">Order Number</td>
                    <td style="padding: 10px 12px;">{order_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px 12px; font-weight: bold;">Payment Status</td>
                    <td style="padding: 10px 12px; color: #059669; font-weight: bold;">Paid</td>
                </tr>
                <tr style="background: #f3f4f6;">
                    <td style="padding: 10px 12px; font-weight: bold;">Shipping To</td>
                    <td style="padding: 10px 12px;">{shipping_line}</td>
                </tr>
            </table>

            <h3>Your Items</h3>
            <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden;">
                <thead>
                    <tr style="background: #065f46; color: white;">
                        <th style="padding: 10px 12px; text-align: left;">Product</th>
                        <th style="padding: 10px 12px; text-align: center;">Qty</th>
                        <th style="padding: 10px 12px; text-align: right;">Price</th>
                        <th style="padding: 10px 12px; text-align: right;">Total</th>
                    </tr>
                </thead>
                <tbody>
                    {items_html}
                </tbody>
            </table>

            <table style="width: 100%; margin-top: 16px; background: white; border-radius: 8px; overflow: hidden;">
                <tr>
                    <td style="padding: 8px 12px;">Subtotal</td>
                    <td style="padding: 8px 12px; text-align: right;">{_format_price(subtotal)}</td>
                </tr>
                {f'<tr><td style="padding: 8px 12px; color: #059669;">Discount ({promo_code})</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(discount)}</td></tr>' if discount else ''}
                {f'<tr><td style="padding: 8px 12px; color: #059669;">Volume Discount</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(volume_discount)}</td></tr>' if volume_discount else ''}
                {f'<tr><td style="padding: 8px 12px; color: #059669;">Loyalty Reward</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(loyalty_discount)}</td></tr>' if loyalty_discount else ''}
                <tr>
                    <td style="padding: 8px 12px;">Shipping</td>
                    <td style="padding: 8px 12px; text-align: right;">{'Free' if shipping_cost == 0 else _format_price(shipping_cost)}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px;">Tax</td>
                    <td style="padding: 8px 12px; text-align: right;">{_format_price(tax)}</td>
                </tr>
                <tr style="font-weight: bold; font-size: 18px; background: #f3f4f6;">
                    <td style="padding: 12px;">Total Charged</td>
                    <td style="padding: 12px; text-align: right; color: #059669;">{_format_price(total)}</td>
                </tr>
            </table>

            <p style="margin-top: 20px;">If you have any questions about your order, reply to this email or contact us at <a href="mailto:{STORE_EMAIL}">{STORE_EMAIL}</a>.</p>
            <p>Thank you for choosing The Hemp Dispensary!</p>
        </div>
        <div style="padding: 16px; text-align: center; color: #9ca3af; font-size: 12px;">
            The Hemp Dispensary — Premium Hemp Products<br>
            Spring Hill, FL
        </div>
    </body>
    </html>
    """

    smtp_settings = await _get_smtp_settings(db)
    subject = f"Order Confirmed — {order_number} | The Hemp Dispensary"

    try:
        loop = asyncio.get_event_loop()
        sent = await loop.run_in_executor(
            None, _send_smtp_email, smtp_settings, customer_email, subject, customer_html
        )
        if not sent:
            raise HTTPException(status_code=500, detail="Failed to send email — SMTP not configured or credentials invalid")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {str(e)}")

    return {"success": True, "order_id": order_id, "email": customer_email}


async def _restock_items(items: list, fulfillment_type: str = "shipping") -> None:
    """Re-add stock to the correct Clover location when items are refunded (inverse of _deduct_stock_for_order).
    For pickup orders at East/West, resolves items by SKU/name since the
    stored product_id is HQ's Clover item ID which differs per merchant."""
    try:
        if fulfillment_type == "pickup_west" and WEST_MERCHANT_ID and WEST_API_TOKEN:
            merchant_id = WEST_MERCHANT_ID
            api_token = WEST_API_TOKEN
        elif fulfillment_type == "pickup_east" and EAST_MERCHANT_ID and EAST_API_TOKEN:
            merchant_id = EAST_MERCHANT_ID
            api_token = EAST_API_TOKEN
        else:
            merchant_id = HQ_MERCHANT_ID
            api_token = HQ_API_TOKEN
        base = f"{CLOVER_BASE_URL}/merchants/{merchant_id}"
        headers = {"Authorization": f"Bearer {api_token}"}

        # For non-HQ locations, pre-fetch all items to resolve by SKU/name
        is_non_hq = fulfillment_type in ("pickup_west", "pickup_east")
        location_lookup = None
        if is_non_hq:
            location_lookup = await _resolve_location_items(merchant_id, api_token)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for item in items:
                clover_item_id = item.get("product_id", "")
                qty = item.get("quantity", 1)
                name = item.get("product_name", "unknown")
                sku = item.get("sku", "")

                # Resolve the correct Clover item ID at the target location
                if is_non_hq and location_lookup and location_lookup["by_id"]:
                    local_item = _find_item_at_location(location_lookup, clover_item_id, sku, name)
                    if local_item:
                        clover_item_id = local_item["id"]
                    else:
                        print(f"[restock] Could not find '{name}' (SKU: {sku}) at target location")
                        continue

                if not clover_item_id:
                    print(f"[restock] Skipping restock for '{name}' — no product_id")
                    continue
                try:
                    resp = await client.get(f"{base}/item_stocks/{clover_item_id}", headers=headers)
                    if resp.status_code != 200:
                        print(f"[restock] Could not get stock for {clover_item_id} ({name}): {resp.status_code}")
                        continue
                    stock_data = resp.json()
                    current_stock = stock_data.get("quantity", 0)
                    new_stock = current_stock + qty
                    update_resp = await client.post(
                        f"{base}/item_stocks/{clover_item_id}",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"quantity": new_stock},
                    )
                    if update_resp.status_code in (200, 201):
                        print(f"[restock] Restocked {qty} of '{name}' ({clover_item_id}): {current_stock} -> {new_stock}")
                    else:
                        print(f"[restock] Failed to restock {clover_item_id}: {update_resp.status_code} {update_resp.text[:200]}")
                except Exception as e:
                    print(f"[restock] Error restocking '{name}': {e}")

        invalidate_product_cache()
        print(f"[restock] Restock complete for {len(items)} item(s), cache invalidated")
    except Exception as e:
        print(f"[restock] Restock task failed: {e}")


@router.post("/orders/{order_id}/refund")
async def refund_order(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Refund an order via Clover (requires admin auth).
    Supports full refund, dollar-amount partial refund, and item-level partial refund with inventory restock.
    Body params:
      - amount (int, optional): partial refund in cents
      - refunded_items (list, optional): [{product_id, product_name, sku, price, quantity}] for item-level refund
    """
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

    body = await request.json()
    refund_amount = body.get("amount")  # Optional: partial refund in cents
    refunded_items = body.get("refunded_items")  # Optional: list of items to refund

    # Get order details
    async with db.execute(
        "SELECT charge_id, total, tax, subtotal, payment_status, fulfillment_type FROM ecommerce_orders WHERE id = ?",
        (order_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    charge_id, order_total, order_tax, order_subtotal, payment_status, fulfillment_type = row

    if payment_status == "refunded":
        raise HTTPException(status_code=400, detail="Order has already been refunded")

    if not charge_id:
        raise HTTPException(status_code=400, detail="No charge ID found for this order — cannot refund")

    # Calculate refund amount based on item selection or explicit amount
    if refunded_items and len(refunded_items) > 0:
        # Item-level partial refund: calculate subtotal of selected items
        items_subtotal = sum(item["price"] * item["quantity"] for item in refunded_items)
        # Calculate proportional tax: (items_subtotal / order_subtotal) * order_tax
        if order_subtotal and order_subtotal > 0:
            tax_proportion = items_subtotal / order_subtotal
            items_tax = round(order_tax * tax_proportion)
        else:
            items_tax = 0
        amount = items_subtotal + items_tax
        print(f"[refund] Item-level refund: items_subtotal={items_subtotal}, tax={items_tax}, total={amount}")

        # Check if all items are being refunded (= full refund)
        item_cursor = await db.execute(
            "SELECT product_id, quantity FROM ecommerce_order_items WHERE order_id = ?",
            (order_id,),
        )
        all_items = await item_cursor.fetchall()
        all_item_map = {}
        for r in all_items:
            all_item_map[r[0]] = all_item_map.get(r[0], 0) + r[1]
        refund_item_map = {}
        for ri in refunded_items:
            refund_item_map[ri["product_id"]] = refund_item_map.get(ri["product_id"], 0) + ri["quantity"]
        is_full_refund = (all_item_map == refund_item_map)
    elif refund_amount:
        amount = refund_amount
        is_full_refund = (amount >= order_total)
    else:
        amount = order_total
        is_full_refund = True

    # Call Clover refund API
    refund_url = "https://scl.clover.com/v1/refunds"
    refund_headers = {
        "Authorization": f"Bearer {HQ_ECOMM_TOKEN}",
        "Content-Type": "application/json",
    }
    refund_data: dict = {"charge": charge_id, "reason": "requested_by_customer"}
    if not is_full_refund:
        refund_data["amount"] = amount

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(refund_url, headers=refund_headers, json=refund_data)
            print(f"[refund] Clover response status={resp.status_code} body={resp.text[:500]}")

            try:
                result = resp.json()
            except Exception:
                # Clover sometimes returns non-JSON responses
                if resp.status_code in (200, 201):
                    new_status = "refunded" if is_full_refund else "partially_refunded"
                    await db.execute(
                        "UPDATE ecommerce_orders SET payment_status = ?, refund_amount = ? WHERE id = ?",
                        (new_status, amount, order_id),
                    )
                    await db.commit()
                    # Restock items in background if item-level refund
                    if refunded_items:
                        asyncio.create_task(_restock_items(refunded_items, fulfillment_type or "shipping"))
                    return {
                        "success": True,
                        "order_id": order_id,
                        "refund_id": "",
                        "refund_amount": amount,
                        "status": new_status,
                        "restocked_items": len(refunded_items) if refunded_items else 0,
                    }
                raise HTTPException(
                    status_code=400,
                    detail=f"Refund failed: Clover returned status {resp.status_code} — {resp.text[:200]}"
                )

            if resp.status_code in (200, 201):
                refund_id = result.get("id", "")
                new_status = "refunded" if is_full_refund else "partially_refunded"
                await db.execute(
                    "UPDATE ecommerce_orders SET payment_status = ?, refund_id = ?, refund_amount = ? WHERE id = ?",
                    (new_status, refund_id, amount, order_id),
                )
                await db.commit()
                # Restock items in background if item-level refund
                if refunded_items:
                    asyncio.create_task(_restock_items(refunded_items, fulfillment_type or "shipping"))
                return {
                    "success": True,
                    "order_id": order_id,
                    "refund_id": refund_id,
                    "refund_amount": amount,
                    "status": new_status,
                    "restocked_items": len(refunded_items) if refunded_items else 0,
                }
            else:
                error_msg = result.get("message") or result.get("error", {}).get("message", "Refund failed")
                raise HTTPException(status_code=400, detail=f"Refund failed: {error_msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refund service error: {str(e)}")


# ─── Address Autocomplete Proxy (Nominatim) ───────────────────────────
@router.get("/address/autocomplete")
async def address_autocomplete(q: str):
    """Proxy address lookup through Nominatim to avoid browser CORS/rate-limit issues."""
    if len(q) < 3:
        return []
    try:
        # Extract leading house number from user query (e.g. "10401 Yellowlegs Ave" → "10401")
        query_house_number = ""
        q_stripped = q.strip()
        if q_stripped and q_stripped[0].isdigit():
            parts = q_stripped.split(None, 1)
            if parts and parts[0].isdigit():
                query_house_number = parts[0]

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": q,
                    "format": "json",
                    "addressdetails": "1",
                    "countrycodes": "us",
                    "limit": "5",
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "THD-Website/1.0 (support@thehempdispensary.com)",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data:
                a = r.get("address", {})
                if not a:
                    continue
                house = a.get("house_number", "")
                road = a.get("road", "")
                # If Nominatim didn't return a house number, use the one from the query
                if not house and query_house_number:
                    house = query_house_number
                street = f"{house} {road}".strip() if house else road
                if not street:
                    continue
                # Don't use county as city — county names are not cities
                city = a.get("city") or a.get("town") or a.get("village") or a.get("hamlet") or ""
                state = a.get("state", "FL")
                # Normalize state name to abbreviation
                state_map = {
                    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
                    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
                    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
                    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
                    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
                    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
                    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
                    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
                    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
                    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
                    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
                    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
                    "Wisconsin": "WI", "Wyoming": "WY",
                }
                state_abbr = state_map.get(state, state[:2].upper() if len(state) > 2 else state)
                postcode = a.get("postcode", "")
                # Build a clean display string instead of using Nominatim's raw display_name
                # which often includes county names (e.g. "Hernando County") instead of city
                display_parts = [street]
                if city:
                    display_parts.append(city)
                if state_abbr:
                    display_parts.append(state_abbr)
                if postcode:
                    display_parts.append(postcode)
                clean_display = ", ".join(display_parts)
                results.append({
                    "display": clean_display,
                    "address": street,
                    "city": city,
                    "state": state_abbr,
                    "zip": postcode,
                })
            return results
    except Exception:
        return []


# ─── Delivery Eligibility Check ────────────────────────────────────────
@router.get("/delivery/check")
async def check_delivery_eligibility(address: str, city: str, state: str, zip: str):
    """Check if an address is within the local delivery radius (30 miles from HQ).
    Returns delivery fee and eligibility status."""
    try:
        coords = await _geocode_address(address, city, state, zip)
        if not coords:
            return {"eligible": False, "reason": "Could not verify address. Please check your address and try again."}
        lat, lon = coords
        distance = _haversine_miles(HQ_LAT, HQ_LON, lat, lon)
        if distance > DELIVERY_RADIUS_MILES:
            return {
                "eligible": False,
                "distance_miles": round(distance, 1),
                "reason": f"Sorry, your address is {round(distance, 1)} miles from our store. Local delivery is available within {DELIVERY_RADIUS_MILES} miles.",
            }
        return {
            "eligible": True,
            "distance_miles": round(distance, 1),
            "fee_standard": DELIVERY_FEE_STANDARD,
            "fee_discounted": DELIVERY_FEE_DISCOUNTED,
            "discount_threshold": DELIVERY_DISCOUNT_THRESHOLD,
        }
    except Exception as e:
        print(f"[delivery] Geocode error: {e}")
        return {"eligible": False, "reason": "Could not verify delivery address. Please try again or select a different fulfillment method."}


# ── Public Order Lookup (for customers) ─────────────────────────────────

@router.get("/orders/lookup")
async def lookup_customer_orders(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    order_number: Optional[str] = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: customers look up their orders by email, phone, or order number.
    No auth required. Returns order summary with tracking info but no sensitive payment details."""
    if not email and not phone and not order_number:
        raise HTTPException(status_code=400, detail="Provide email, phone, or order_number")

    where_clauses = []
    params: list = []

    if order_number:
        where_clauses.append("o.order_number = ?")
        params.append(order_number)
    if email:
        where_clauses.append("LOWER(o.customer_email) = LOWER(?)")
        params.append(email)
    if phone:
        # Normalize phone: strip non-digits for comparison
        digits = re.sub(r'\D', '', phone)
        if len(digits) >= 10:
            # Match last 10 digits
            where_clauses.append(
                "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(o.customer_phone, '-', ''), '(', ''), ')', ''), ' ', ''), '+', '') LIKE ?"
            )
            params.append(f"%{digits[-10:]}")

    if not where_clauses:
        return {"orders": [], "total": 0}

    where_sql = " OR ".join(where_clauses)
    query = f"""
        SELECT o.order_number, o.status, o.customer_first_name, o.customer_last_name,
               o.customer_email, o.subtotal, o.shipping_cost, o.tax, o.total,
               o.discount, o.promo_code, o.fulfillment_type, o.shipping_service,
               o.tracking_number, o.tracking_url, o.tracking_status,
               o.payment_status, o.created_at, o.id
        FROM ecommerce_orders o
        WHERE ({where_sql})
        ORDER BY o.created_at DESC
        LIMIT 50
    """

    cursor = await db.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    orders = []
    for row in rows:
        order = dict(zip(columns, row))
        order_id = order.pop("id")
        # Derive a customer-friendly status from payment_status + tracking_status
        # The `status` column is never updated, so we compute it here.
        ps = (order.get("payment_status") or "pending").lower()
        ts = (order.get("tracking_status") or "").lower()
        if ps == "refunded":
            display_status = "refunded"
        elif ps == "cancelled":
            display_status = "cancelled"
        elif ts == "delivered" or ps == "delivered":
            display_status = "delivered"
        elif ts == "out_for_delivery":
            display_status = "out_for_delivery"
        elif ts == "in_transit":
            display_status = "in_transit"
        elif ps == "shipped" or ts == "label_created":
            display_status = "shipped"
        elif ps == "paid":
            display_status = "confirmed"
        else:
            display_status = "pending"
        order["status"] = display_status
        # Fetch items
        item_cursor = await db.execute(
            "SELECT product_name, sku, price, quantity FROM ecommerce_order_items WHERE order_id = ?",
            (order_id,),
        )
        item_cols = [desc[0] for desc in item_cursor.description]
        item_rows = await item_cursor.fetchall()
        order["items"] = [dict(zip(item_cols, r)) for r in item_rows]
        orders.append(order)

    return {"orders": orders, "total": len(orders)}


@router.get("/discount-usage/{code}")
async def get_discount_usage(code: str, db=Depends(get_db)):
    """Get usage history for a specific discount code."""
    cursor = await db.execute(
        """SELECT id, discount_code, usage_timestamp, customer_email, customer_name,
                  order_id, order_number, location_name, employee_id, employee_name,
                  order_total, discount_amount_applied, fulfillment_type, created_at
           FROM discount_usage
           WHERE discount_code = ?
           ORDER BY usage_timestamp DESC""",
        (code,),
    )
    cols = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    usage_records = [dict(zip(cols, row)) for row in rows]
    return {"code": code, "total_uses": len(usage_records), "usage": usage_records}


@router.get("/discount-usage")
async def get_all_discount_usage(db=Depends(get_db)):
    """Get aggregated usage stats for all discount codes."""
    cursor = await db.execute(
        """SELECT discount_code, COUNT(*) as use_count,
                  MAX(usage_timestamp) as last_used,
                  SUM(discount_amount_applied) as total_discount_given
           FROM discount_usage
           GROUP BY discount_code
           ORDER BY use_count DESC"""
    )
    cols = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    stats = [dict(zip(cols, row)) for row in rows]
    return {"codes": stats}


# ── Clover native online-order sync ─────────────────────────────────────────
# Clover has its own online ordering system.  Orders placed through it land
# directly on the store's Clover account and bypass Hempventory entirely —
# meaning no DB record and no email notification.  This background job polls
# each Clover location for recent orders whose title starts with
# "Online_order" and imports any that aren't already tracked.


async def _get_locations_for_sync(db: aiosqlite.Connection) -> list:
    """Return all non-virtual/non-central locations."""
    cursor = await db.execute(
        "SELECT id, name, merchant_id, api_token FROM locations "
        "WHERE LOWER(name) NOT LIKE '%virtual%' AND LOWER(name) NOT LIKE '%central%'"
    )
    return await cursor.fetchall()


async def _sync_clover_online_orders(db: aiosqlite.Connection) -> dict:
    """Poll Clover locations for native online orders and import new ones."""
    from datetime import datetime, timezone, timedelta

    locations = await _get_locations_for_sync(db)
    if not locations:
        return {"synced": 0, "skipped": 0}

    synced = 0
    skipped = 0
    # Look back 7 days to catch anything recently missed
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)

    smtp_settings = await _get_smtp_settings(db)

    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        try:
            client = CloverClient(merchant_id, api_token)
            offset = 0
            limit = 100
            while True:
                orders_data = await client.get_orders(
                    limit=limit,
                    offset=offset,
                    filter_str=f"createdTime>={cutoff_ms}",
                    expand="lineItems,customers",
                )
                orders = orders_data.get("elements", [])
                if not orders:
                    break

                for order in orders:
                    clover_oid = order.get("id", "")
                    title = order.get("title") or ""
                    note = order.get("note") or ""

                    # Only process Clover-native online orders (title starts with "Online_order")
                    if not title.lower().startswith("online_order"):
                        continue

                    # Skip if already imported
                    dup_cursor = await db.execute(
                        "SELECT id FROM ecommerce_orders WHERE clover_order_id = ?",
                        (clover_oid,),
                    )
                    if await dup_cursor.fetchone():
                        skipped += 1
                        continue

                    # Also skip if this order was created by Hempventory (note contains "Online Order -")
                    if "Online Order -" in note:
                        skipped += 1
                        continue

                    order_total = order.get("total", 0)
                    if order_total <= 0:
                        skipped += 1
                        continue

                    # Extract customer info from the Clover order
                    cust_first = ""
                    cust_last = ""
                    cust_email = ""
                    cust_phone = ""
                    customers_data = order.get("customers", {})
                    cust_elements = customers_data.get("elements", []) if customers_data else []
                    if cust_elements:
                        c = cust_elements[0]
                        cust_first = (c.get("firstName") or "").strip()
                        cust_last = (c.get("lastName") or "").strip()
                        emails = c.get("emailAddresses", {})
                        if emails and emails.get("elements"):
                            cust_email = emails["elements"][0].get("emailAddress", "")
                        phones = c.get("phoneNumbers", {})
                        if phones and phones.get("elements"):
                            cust_phone = phones["elements"][0].get("phoneNumber", "")

                    # Extract line items
                    line_items_data = order.get("lineItems", {})
                    li_elements = line_items_data.get("elements", []) if line_items_data else []

                    items_subtotal = 0
                    item_records: list[dict] = []
                    for li in li_elements:
                        if li.get("refunded") or li.get("isRefund"):
                            continue
                        li_name = li.get("name", "Unknown")
                        li_price = li.get("price", 0)
                        li_item_ref = li.get("item", {})
                        li_product_id = li_item_ref.get("id", "") if li_item_ref else ""
                        li_qty = max(round(li.get("unitQty", 1000) / 1000), 1)
                        items_subtotal += li_price * li_qty
                        item_records.append({
                            "product_id": li_product_id,
                            "name": li_name,
                            "price": li_price,
                            "quantity": li_qty,
                        })

                    # Derive tax (total - items subtotal, capped at 0)
                    tax_amount = max(order_total - items_subtotal, 0)

                    # Generate an order number that identifies this as a Clover import
                    order_number = f"CLV-{clover_oid[:8]}"

                    # Determine fulfillment type from location
                    if "west" in loc_name.lower():
                        fulfillment_type = "pickup_west"
                    elif "east" in loc_name.lower():
                        fulfillment_type = "pickup_east"
                    else:
                        fulfillment_type = "shipping"

                    try:
                        cursor = await db.execute(
                            """INSERT INTO ecommerce_orders
                               (order_number, customer_first_name, customer_last_name,
                                customer_email, customer_phone,
                                subtotal, tax, total, notes,
                                payment_status, fulfillment_type,
                                clover_order_id, source)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                order_number,
                                cust_first,
                                cust_last,
                                cust_email,
                                cust_phone,
                                items_subtotal,
                                tax_amount,
                                order_total,
                                f"Clover online order synced from {loc_name}. Original title: {title}",
                                "paid",
                                fulfillment_type,
                                clover_oid,
                                "clover",
                            ),
                        )
                        new_order_id = cursor.lastrowid

                        for item_rec in item_records:
                            await db.execute(
                                """INSERT INTO ecommerce_order_items
                                   (order_id, product_id, product_name, price, quantity)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (
                                    new_order_id,
                                    item_rec["product_id"],
                                    item_rec["name"],
                                    item_rec["price"],
                                    item_rec["quantity"],
                                ),
                            )

                        await db.commit()
                        synced += 1
                        print(
                            f"[clover-sync] Imported order {order_number} "
                            f"(clover_id={clover_oid}) from {loc_name}: "
                            f"{_format_price(order_total)}, {len(item_records)} item(s)"
                        )

                        # Send store notification email for the imported order
                        asyncio.create_task(
                            _send_clover_order_notification(
                                smtp_settings, order_number, loc_name,
                                cust_first, cust_last, cust_email, cust_phone,
                                item_records, items_subtotal, tax_amount,
                                order_total, clover_oid, fulfillment_type,
                            )
                        )

                    except Exception as insert_err:
                        await db.rollback()
                        print(f"[clover-sync] Failed to import {clover_oid}: {insert_err}")
                        continue

                if len(orders) < limit:
                    break
                offset += limit
                await asyncio.sleep(0.5)

        except Exception as loc_err:
            print(f"[clover-sync] Error syncing {loc_name}: {loc_err}")

    return {"synced": synced, "skipped": skipped}


async def _send_clover_order_notification(
    smtp_settings: dict[str, str],
    order_number: str,
    location_name: str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str,
    items: list[dict],
    subtotal: int,
    tax: int,
    total: int,
    clover_order_id: str,
    fulfillment_type: str,
) -> None:
    """Send a store notification email for a Clover-imported online order."""
    try:
        if not smtp_settings.get("smtp_user") or not smtp_settings.get("smtp_password"):
            return

        items_html = ""
        for item in items:
            items_html += f"""
            <tr>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb;">{item['name']}</td>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: center;">{item['quantity']}</td>
                <td style="padding: 10px 12px; border-bottom: 1px solid #e5e7eb; text-align: right;">{_format_price(item['price'])}</td>
            </tr>
            """

        store_html = f"""
        <html>
        <body style="font-family: 'Helvetica Neue', Arial, sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
            <div style="background: #7c3aed; padding: 20px; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 22px;">Clover Online Order Detected</h1>
            </div>
            <div style="padding: 24px; background: #f9fafb;">
                <p style="font-size: 14px; color: #6b7280;">
                    This order was placed through <strong>Clover's online ordering</strong>
                    (not through the website).  It has been automatically imported into Hempventory.
                </p>

                <table style="width: 100%; margin: 16px 0; background: white; border-radius: 8px; overflow: hidden;">
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">Order Number</td>
                        <td style="padding: 10px 12px;">{order_number}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Clover Order ID</td>
                        <td style="padding: 10px 12px; font-family: monospace; font-size: 13px;">{clover_order_id}</td>
                    </tr>
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">Location</td>
                        <td style="padding: 10px 12px;">{location_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Customer</td>
                        <td style="padding: 10px 12px;">{first_name} {last_name}</td>
                    </tr>
                    <tr style="background: #f3f4f6;">
                        <td style="padding: 10px 12px; font-weight: bold;">Email</td>
                        <td style="padding: 10px 12px;">{email or 'N/A'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 12px; font-weight: bold;">Phone</td>
                        <td style="padding: 10px 12px;">{phone or 'N/A'}</td>
                    </tr>
                </table>

                <h3 style="margin-top: 20px;">Items Ordered</h3>
                <table style="width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden;">
                    <thead>
                        <tr style="background: #7c3aed; color: white;">
                            <th style="padding: 10px 12px; text-align: left;">Product</th>
                            <th style="padding: 10px 12px; text-align: center;">Qty</th>
                            <th style="padding: 10px 12px; text-align: right;">Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                    </tbody>
                </table>

                <table style="width: 100%; margin-top: 16px; background: white; border-radius: 8px; overflow: hidden;">
                    <tr>
                        <td style="padding: 8px 12px;">Subtotal</td>
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(subtotal)}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 12px;">Tax</td>
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(tax)}</td>
                    </tr>
                    <tr style="font-weight: bold; font-size: 18px; background: #f3f4f6;">
                        <td style="padding: 12px;">Total</td>
                        <td style="padding: 12px; text-align: right; color: #7c3aed;">{_format_price(total)}</td>
                    </tr>
                </table>
            </div>
            <div style="padding: 16px; text-align: center; color: #9ca3af; font-size: 12px;">
                The Hemp Dispensary — Clover Order Sync
            </div>
        </body>
        </html>
        """

        subject = f"Clover Online Order {order_number} — {_format_price(total)} from {first_name} {last_name} ({location_name})"

        if fulfillment_type == "pickup_west":
            store_recipients = "west@thehempdispensary.com, THD1SHW@icloud.com"
        elif fulfillment_type == "pickup_east":
            store_recipients = "east@thehempdispensary.com, THD7SHE@icloud.com"
        else:
            store_recipients = STORE_EMAIL

        loop = asyncio.get_event_loop()
        sent = await loop.run_in_executor(
            None, _send_smtp_email, smtp_settings, store_recipients, subject, store_html,
        )
        if sent:
            print(f"[clover-sync] Store notification sent for {order_number}")
        else:
            print(f"[clover-sync] FAILED to send notification for {order_number}")

    except Exception as e:
        print(f"[clover-sync] Email error for {order_number}: {e}")


# ─── Wholesale Inquiries ────────────────────────────────────────────

class WholesaleInquiryItem(BaseModel):
    product_name: str
    sku: str = ""
    quantity: int
    unit_price: int = 0  # cents
    wholesale_price: int = 0  # cents


class WholesaleInquiryRequest(BaseModel):
    customer_name: str
    business_name: str = ""
    email: str
    phone: str = ""
    items: List[WholesaleInquiryItem]
    message: str = ""


@router.post("/wholesale-inquiries")
async def create_wholesale_inquiry(body: WholesaleInquiryRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Public: Submit a wholesale inquiry (stores in DB and sends email notification)."""
    if not body.customer_name or not body.email:
        raise HTTPException(status_code=400, detail="Name and email are required")
    if not body.items or len(body.items) == 0:
        raise HTTPException(status_code=400, detail="At least one item is required")

    items_json = json.dumps([item.model_dump() for item in body.items])

    cursor = await db.execute(
        """INSERT INTO wholesale_inquiries (customer_name, business_name, email, phone, items, message, status)
           VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
        (body.customer_name, body.business_name, body.email, body.phone, items_json, body.message),
    )
    await db.commit()
    inquiry_id = cursor.lastrowid

    # Build and send email notification to store — escape all user inputs
    esc = html_mod.escape
    safe_name = esc(body.customer_name)
    safe_biz = esc(body.business_name)
    safe_email = esc(body.email)
    safe_phone = esc(body.phone)
    safe_msg = esc(body.message)

    items_rows = ""
    total_value = 0
    for item in body.items:
        line_total = item.wholesale_price * item.quantity if item.wholesale_price else item.unit_price * item.quantity
        total_value += line_total
        items_rows += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;">{esc(item.product_name)}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{item.quantity}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;">${item.unit_price / 100:.2f}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;">${item.wholesale_price / 100:.2f}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;">${line_total / 100:.2f}</td>
        </tr>"""

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:#231F20;padding:20px;text-align:center;">
            <h1 style="color:#B3D335;margin:0;font-size:24px;">New Wholesale Inquiry #{inquiry_id}</h1>
        </div>
        <div style="padding:20px;background:#fff;">
            <h2 style="color:#231F20;margin-top:0;">Customer Information</h2>
            <table style="width:100%;margin-bottom:20px;">
                <tr><td style="padding:4px 0;color:#666;">Name:</td><td style="padding:4px 0;font-weight:bold;">{safe_name}</td></tr>
                {"<tr><td style='padding:4px 0;color:#666;'>Business:</td><td style='padding:4px 0;font-weight:bold;'>" + safe_biz + "</td></tr>" if body.business_name else ""}
                <tr><td style="padding:4px 0;color:#666;">Email:</td><td style="padding:4px 0;"><a href="mailto:{safe_email}">{safe_email}</a></td></tr>
                {"<tr><td style='padding:4px 0;color:#666;'>Phone:</td><td style='padding:4px 0;'><a href='tel:" + safe_phone + "'>" + safe_phone + "</a></td></tr>" if body.phone else ""}
            </table>

            <h2 style="color:#231F20;">Requested Items</h2>
            <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
                <thead>
                    <tr style="background:#f5f5f5;">
                        <th style="padding:8px 12px;text-align:left;">Product</th>
                        <th style="padding:8px 12px;text-align:center;">Qty</th>
                        <th style="padding:8px 12px;text-align:right;">Retail</th>
                        <th style="padding:8px 12px;text-align:right;">Wholesale</th>
                        <th style="padding:8px 12px;text-align:right;">Total</th>
                    </tr>
                </thead>
                <tbody>{items_rows}</tbody>
                <tfoot>
                    <tr style="background:#f5f5f5;">
                        <td colspan="4" style="padding:8px 12px;text-align:right;font-weight:bold;">Estimated Total:</td>
                        <td style="padding:8px 12px;text-align:right;font-weight:bold;color:#126A44;">${total_value / 100:.2f}</td>
                    </tr>
                </tfoot>
            </table>

            {"<h2 style='color:#231F20;'>Message</h2><p style='background:#f9f9f9;padding:12px;border-radius:8px;'>" + safe_msg + "</p>" if body.message else ""}

            <div style="margin-top:20px;padding:16px;background:#B3D335;border-radius:8px;text-align:center;">
                <p style="margin:0;color:#231F20;font-weight:bold;">Reply to this customer at <a href="mailto:{safe_email}">{safe_email}</a> with an invoice.</p>
            </div>
        </div>
        <div style="background:#231F20;padding:12px;text-align:center;">
            <p style="color:#fff;margin:0;font-size:12px;">The Hemp Dispensary — Wholesale Inquiry System</p>
        </div>
    </div>"""

    try:
        smtp_settings = await _get_smtp_settings(db)
        subject = f"Wholesale Inquiry #{inquiry_id} — {safe_name}" + (f" ({safe_biz})" if body.business_name else "")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_smtp_email, smtp_settings, STORE_EMAIL, subject, html_body)
    except Exception as e:
        print(f"[wholesale] Failed to send notification email: {e}")

    return {"id": inquiry_id, "status": "pending", "message": "Wholesale inquiry submitted successfully"}


@router.get("/wholesale-inquiries")
async def list_wholesale_inquiries(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: List all wholesale inquiries."""
    cursor = await db.execute("SELECT * FROM wholesale_inquiries ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["items"] = json.loads(d["items"]) if d.get("items") else []
        results.append(d)
    return results


@router.patch("/wholesale-inquiries/{inquiry_id}")
async def update_wholesale_inquiry(inquiry_id: int, body: dict, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Update inquiry status or notes."""
    allowed = {"status", "admin_notes"}
    updates = []
    params: list = []
    for key, val in body.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(val)
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    params.append(inquiry_id)
    await db.execute(
        f"UPDATE wholesale_inquiries SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        params,
    )
    await db.commit()
    return {"status": "updated"}


# ─── Wholesale Bundles ───────────────────────────────────────────────

class WholesaleBundleCreateRequest(BaseModel):
    name: str
    description: str = ""
    min_quantity: int = 1
    price_cents: int = 0
    product_skus: List[str] = []
    category_filter: str = ""
    is_active: bool = True
    image_url: str = ""


class WholesaleBundleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    min_quantity: Optional[int] = None
    price_cents: Optional[int] = None
    product_skus: Optional[List[str]] = None
    category_filter: Optional[str] = None
    is_active: Optional[bool] = None
    image_url: Optional[str] = None


@router.get("/wholesale-bundles")
async def list_wholesale_bundles(db: aiosqlite.Connection = Depends(get_db)):
    """Admin: List all wholesale bundles."""
    cursor = await db.execute("SELECT * FROM wholesale_bundles ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["product_skus"] = json.loads(d["product_skus"]) if d.get("product_skus") else []
        results.append(d)
    return results


@router.get("/wholesale-bundles/active")
async def list_active_wholesale_bundles(db: aiosqlite.Connection = Depends(get_db)):
    """Public: List active wholesale bundles (for website)."""
    cursor = await db.execute("SELECT * FROM wholesale_bundles WHERE is_active = 1")
    rows = await cursor.fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["product_skus"] = json.loads(d["product_skus"]) if d.get("product_skus") else []
        results.append(d)
    return results


@router.post("/wholesale-bundles")
async def create_wholesale_bundle(body: WholesaleBundleCreateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Create a new wholesale bundle."""
    if not body.name:
        raise HTTPException(status_code=400, detail="Bundle name is required")
    skus_json = json.dumps(body.product_skus)
    cursor = await db.execute(
        """INSERT INTO wholesale_bundles (name, description, min_quantity, price_cents, product_skus, category_filter, is_active, image_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.name, body.description, body.min_quantity, body.price_cents, skus_json,
         body.category_filter, 1 if body.is_active else 0, body.image_url),
    )
    await db.commit()
    return {"id": cursor.lastrowid, "status": "created"}


@router.put("/wholesale-bundles/{bundle_id}")
async def update_wholesale_bundle(bundle_id: int, body: WholesaleBundleUpdateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Update a wholesale bundle."""
    updates = []
    params: list = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.description is not None:
        updates.append("description = ?")
        params.append(body.description)
    if body.min_quantity is not None:
        updates.append("min_quantity = ?")
        params.append(body.min_quantity)
    if body.price_cents is not None:
        updates.append("price_cents = ?")
        params.append(body.price_cents)
    if body.product_skus is not None:
        updates.append("product_skus = ?")
        params.append(json.dumps(body.product_skus))
    if body.category_filter is not None:
        updates.append("category_filter = ?")
        params.append(body.category_filter)
    if body.is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if body.is_active else 0)
    if body.image_url is not None:
        updates.append("image_url = ?")
        params.append(body.image_url)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    params.append(bundle_id)
    await db.execute(
        f"UPDATE wholesale_bundles SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        params,
    )
    await db.commit()
    return {"status": "updated"}


@router.delete("/wholesale-bundles/{bundle_id}")
async def delete_wholesale_bundle(bundle_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Delete a wholesale bundle."""
    await db.execute("DELETE FROM wholesale_bundles WHERE id = ?", (bundle_id,))
    await db.commit()
    return {"status": "deleted"}
