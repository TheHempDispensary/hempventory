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
from urllib.parse import quote as url_quote
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import get_db

STORE_EMAIL = "Support@TheHempDispensary.com"

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
    address: str
    apartment: str = ""
    city: str
    state: str
    zip: str


class CreateOrderRequest(BaseModel):
    customer: OrderCustomer
    shipping_address: OrderShipping
    items: List[OrderItem]
    subtotal: int = 0
    discount: int = 0
    shipping_cost: int = 0
    tax: int = 0
    total: int = 0
    notes: str = ""
    payment_token: str = ""
    loyalty_number: str = ""
    promo_code: Optional[str] = None
    shipping_service: str = ""
    fulfillment_type: str = "shipping"  # "shipping", "pickup_west", "pickup_east"

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

# ── In-memory product cache ──────────────────────────────────────────────────
_product_cache: dict = {}  # {"products": [...], "total": int, "categories": [...]}
_product_cache_json: bytes = b""  # Pre-serialized JSON for the full /products response
_cache_timestamp: float = 0.0
_refresh_in_progress: bool = False
CACHE_TTL = 600  # 10 minutes


DISK_CACHE_PATH = os.environ.get("DB_PATH", "").replace("app.db", "product_cache.json") or "/tmp/product_cache.json"


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
        image_by_sku = {row[0]: f"{image_base_url}/{url_quote(row[0], safe='')}?v=2&t={str(row[2] or '').replace(' ', '_')}" for row in image_rows}
        image_by_name = {}
        for row in image_rows:
            if row[1]:
                image_by_name[row[1].upper()] = f"{image_base_url}/{url_quote(row[0], safe='')}?v=2&t={str(row[2] or '').replace(' ', '_')}"

        products = []
        categories_set: set = set()

        for item in all_items:
            if item.get("hidden", False):
                continue

            name = item.get("name", "")
            sku = item.get("sku", "") or item.get("id", "")
            price = item.get("price", 0)
            item_categories = [c.get("name", "") for c in item.get("categories", {}).get("elements", [])]
            stock_info = item.get("itemStock", {})
            hq_stock = stock_info.get("quantity", 0) if stock_info else 0
            description = item.get("description", "")
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

            slug = name.lower().replace(" ", "-").replace(",", "").replace(".", "")
            slug = "-".join(slug.split())

            # LeafLife products (SKU starts with LF-) are shipped from supplier, not available for pickup
            is_shipping_only = sku.startswith("LF-") if isinstance(sku, str) else False

            total_stock = max(hq_stock, w_stock, e_stock)

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
            })

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


@router.post("/validate-promo")
async def validate_promo(
    body: ValidatePromoRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Validate a promo code and check if customer can use it."""
    code = body.promo_code.strip().upper()
    email = body.email.strip().lower()

    cursor = await db.execute("SELECT * FROM promo_codes WHERE code = ? AND is_active = 1", (code,))
    promo = await cursor.fetchone()
    if not promo:
        return {"valid": False, "reason": "Invalid promo code"}

    # Check expiration
    if promo["expires_at"]:
        from datetime import datetime
        try:
            exp = datetime.fromisoformat(promo["expires_at"])
            if datetime.utcnow() > exp:
                return {"valid": False, "reason": "This promo code has expired"}
        except Exception:
            pass

    # Check max uses
    if promo["max_uses"] > 0 and promo["times_used"] >= promo["max_uses"]:
        return {"valid": False, "reason": "This promo code has reached its usage limit"}

    # Check single-use per customer
    if promo["single_use"] and email:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM ecommerce_orders WHERE LOWER(customer_email) = ? AND promo_code = ? AND payment_status != 'cancelled'",
            (email, code),
        )
        count = (await cursor.fetchone())[0]
        if count > 0:
            return {"valid": False, "reason": "This promo code has already been used with this email address"}

    return {"valid": True, "discount_pct": promo["discount_pct"], "discount_amount": promo["discount_amount"], "code": code}


# ── Promo Code Management (Admin) ────────────────────────────────────────────

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
        promos.append({
            "id": row["id"],
            "code": row["code"],
            "discount_pct": row["discount_pct"],
            "discount_amount": row["discount_amount"],
            "single_use": bool(row["single_use"]),
            "is_active": bool(row["is_active"]),
            "max_uses": row["max_uses"],
            "times_used": order_count,
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        })
    return promos


class PromoCreateRequest(BaseModel):
    code: str
    discount_pct: float = 0
    discount_amount: int = 0
    single_use: bool = False
    max_uses: int = 0
    expires_at: Optional[str] = None


@router.post("/promos")
async def create_promo(body: PromoCreateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Create a new promo code."""
    code = body.code.strip().upper()
    try:
        await db.execute(
            "INSERT INTO promo_codes (code, discount_pct, discount_amount, single_use, max_uses, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (code, body.discount_pct, body.discount_amount, int(body.single_use), body.max_uses, body.expires_at),
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=400, detail=f"Promo code '{code}' already exists")
    return {"status": "created", "code": code}


class PromoUpdateRequest(BaseModel):
    discount_pct: Optional[float] = None
    discount_amount: Optional[int] = None
    single_use: Optional[bool] = None
    is_active: Optional[bool] = None
    max_uses: Optional[int] = None
    expires_at: Optional[str] = None


@router.put("/promos/{promo_id}")
async def update_promo(promo_id: int, body: PromoUpdateRequest, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Update an existing promo code."""
    updates = []
    params = []
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
    if not updates:
        return {"status": "no changes"}
    params.append(promo_id)
    await db.execute(f"UPDATE promo_codes SET {', '.join(updates)} WHERE id = ?", params)
    await db.commit()
    return {"status": "updated"}


@router.delete("/promos/{promo_id}")
async def delete_promo(promo_id: int, db: aiosqlite.Connection = Depends(get_db)):
    """Admin: Delete a promo code."""
    await db.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
    await db.commit()
    return {"status": "deleted"}


@router.post("/orders")
async def create_order(
    order: CreateOrderRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Create an e-commerce order with Clover payment processing."""
    charge_id = ""
    payment_status = "pending"

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

        # Determine the correct Clover location for this order
        if order.fulfillment_type == "pickup_west" and WEST_MERCHANT_ID and WEST_API_TOKEN:
            order_merchant_id = WEST_MERCHANT_ID
            order_api_token = WEST_API_TOKEN
        elif order.fulfillment_type == "pickup_east" and EAST_MERCHANT_ID and EAST_API_TOKEN:
            order_merchant_id = EAST_MERCHANT_ID
            order_api_token = EAST_API_TOKEN
        else:
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
                    # Associate charge with the Clover order
                    charge_data["orderId"] = clover_order_id
                    print(f"[order] Created Clover order {clover_order_id} with {len(order.items)} line items")
                else:
                    print(f"[order] Failed to create Clover order: {order_resp.status_code} {order_resp.text}")
            except Exception as e:
                print(f"[order] Clover order creation failed (charge will still proceed): {e}")

            try:
                resp = await client.post(
                    CLOVER_CHARGES_URL,
                    headers=charge_headers,
                    json=charge_data,
                )
                charge_result = resp.json()

                if resp.status_code == 200 and charge_result.get("status") == "succeeded":
                    charge_id = charge_result.get("id", "")
                    payment_status = "paid"
                else:
                    error_msg = charge_result.get("message") or charge_result.get("error", {}).get("message", "Payment was declined.")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Payment failed: {error_msg}",
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

    cursor = await db.execute(
        """INSERT INTO ecommerce_orders
           (order_number, customer_first_name, customer_last_name, customer_email, customer_phone,
            shipping_address, shipping_apartment, shipping_city, shipping_state, shipping_zip,
            subtotal, discount, promo_code, shipping_cost, tax, total, notes, charge_id, payment_status, fulfillment_type, shipping_service)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            order.promo_code or "",
            order.shipping_cost,
            order.tax,
            order.total,
            order.notes,
            charge_id,
            payment_status,
            order.fulfillment_type,
            order.shipping_service,
        ),
    )
    order_id = cursor.lastrowid

    for item in order.items:
        await db.execute(
            """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, price, quantity)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, item.product_id, item.name, item.sku, item.price, item.quantity),
        )

    await db.commit()

    # Fetch SMTP settings while DB is still open
    smtp_settings = await _get_smtp_settings(db)

    # Send email notifications (non-blocking)
    asyncio.create_task(
        _send_order_emails(smtp_settings, order, order_number, charge_id, payment_status)
    )

    # Deduct stock from correct Clover location based on fulfillment type (non-blocking)
    asyncio.create_task(
        _deduct_stock_for_order(order.items, order.fulfillment_type)
    )

    return {
        "success": True,
        "order_number": order_number,
        "order_id": order_id,
        "total": order.total,
        "payment_status": payment_status,
        "charge_id": charge_id,
    }


def _format_price(cents: int) -> str:
    """Format cents as dollar string."""
    return f"${cents / 100:.2f}"


async def _deduct_stock_for_order(items: List[OrderItem], fulfillment_type: str = "shipping") -> None:
    """Deduct stock from the correct Clover location based on fulfillment type."""
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            for item in items:
                clover_item_id = item.product_id
                if not clover_item_id:
                    print(f"[stock] Skipping stock deduction for '{item.name}' — no product_id")
                    continue

                try:
                    # Get current stock
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

                    # Update stock
                    update_resp = await client.post(
                        f"{base}/item_stocks/{clover_item_id}",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"quantity": new_stock},
                    )
                    if update_resp.status_code in (200, 201):
                        print(f"[stock] Deducted {item.quantity} from '{item.name}' ({clover_item_id}): {current_stock} -> {new_stock}")
                    else:
                        print(f"[stock] Failed to update stock for {clover_item_id}: {update_resp.status_code} {update_resp.text[:200]}")
                except Exception as e:
                    print(f"[stock] Error deducting stock for '{item.name}': {e}")

        # Invalidate product cache so website shows updated stock
        invalidate_product_cache()
        print(f"[stock] Stock deduction complete for {len(items)} item(s), cache invalidated")
    except Exception as e:
        print(f"[stock] Stock deduction task failed: {e}")


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
    """Send an email via SMTP (synchronous)."""
    smtp_host = smtp_settings.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(smtp_settings.get("smtp_port", "587"))
    smtp_user = smtp_settings.get("smtp_user", "")
    smtp_password = smtp_settings.get("smtp_password", "")

    if not smtp_user or not smtp_password:
        print("SMTP credentials not configured, skipping email")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
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

        shipping_line = f"{order.shipping_address.address}"
        if order.shipping_address.apartment:
            shipping_line += f", {order.shipping_address.apartment}"
        shipping_line += f"<br>{order.shipping_address.city}, {order.shipping_address.state} {order.shipping_address.zip}"

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
                        <td style="padding: 10px 12px; font-weight: bold;">Shipping To</td>
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
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(order.subtotal)}</td>
                    </tr>
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Discount ({order.promo_code})</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.discount)}</td></tr>' if order.discount else ''}
                    <tr>
                        <td style="padding: 8px 12px;">Shipping</td>
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

        # Send to store
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _send_smtp_email,
            smtp_settings,
            STORE_EMAIL,
            f"New Order {order_number} — {_format_price(order.total)} from {order.customer.first_name} {order.customer.last_name}",
            store_html,
        )
        print(f"Store notification sent to {STORE_EMAIL} for order {order_number}")

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
                        <td style="padding: 8px 12px; text-align: right;">{_format_price(order.subtotal)}</td>
                    </tr>
                    {f'<tr><td style="padding: 8px 12px; color: #059669;">Discount ({order.promo_code})</td><td style="padding: 8px 12px; text-align: right; color: #059669;">-{_format_price(order.discount)}</td></tr>' if order.discount else ''}
                    <tr>
                        <td style="padding: 8px 12px;">Shipping</td>
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
    image_url = f"{image_base_url}/{url_quote(row[0], safe='')}?v=2&t={str(row[1] or '').replace(' ', '_')}" if row else None

    is_shipping_only = sku.startswith("LF-") if isinstance(sku, str) else False

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
    }


@router.get("/orders")
async def get_orders(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
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

    # Fetch orders
    query = "SELECT * FROM ecommerce_orders"
    params: list = []
    if status:
        query += " WHERE payment_status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor = await db.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = await cursor.fetchall()
    orders = [dict(zip(columns, row)) for row in rows]

    # Fetch items for each order
    for order in orders:
        item_cursor = await db.execute(
            "SELECT product_id, product_name, sku, price, quantity FROM ecommerce_order_items WHERE order_id = ?",
            (order["id"],),
        )
        item_cols = [desc[0] for desc in item_cursor.description]
        item_rows = await item_cursor.fetchall()
        order["items"] = [dict(zip(item_cols, row)) for row in item_rows]

    # Get total count
    count_query = "SELECT COUNT(*) FROM ecommerce_orders"
    count_params: list = []
    if status:
        count_query += " WHERE payment_status = ?"
        count_params.append(status)
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
    return {"success": True, "order_id": order_id, "status": new_status}


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


@router.post("/orders/{order_id}/refund")
async def refund_order(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Refund an order via Clover (requires admin auth)."""
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

    # Get order details
    async with db.execute(
        "SELECT charge_id, total, payment_status FROM ecommerce_orders WHERE id = ?",
        (order_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    charge_id, order_total, payment_status = row

    if payment_status == "refunded":
        raise HTTPException(status_code=400, detail="Order has already been refunded")

    if not charge_id:
        raise HTTPException(status_code=400, detail="No charge ID found for this order — cannot refund")

    amount = refund_amount if refund_amount else order_total

    # Call Clover refund API
    import httpx
    refund_url = "https://scl.clover.com/v1/refunds"
    refund_headers = {
        "Authorization": f"Bearer {HQ_ECOMM_TOKEN}",
        "Content-Type": "application/json",
    }
    refund_data: dict = {"charge": charge_id, "reason": "requested_by_customer"}
    if refund_amount:
        refund_data["amount"] = refund_amount

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(refund_url, headers=refund_headers, json=refund_data)
            print(f"[refund] Clover response status={resp.status_code} body={resp.text[:500]}")

            try:
                result = resp.json()
            except Exception:
                # Clover sometimes returns non-JSON responses
                if resp.status_code in (200, 201):
                    # Refund succeeded but response wasn't JSON
                    new_status = "refunded"
                    await db.execute(
                        "UPDATE ecommerce_orders SET payment_status = ?, refund_amount = ? WHERE id = ?",
                        (new_status, amount, order_id),
                    )
                    await db.commit()
                    return {
                        "success": True,
                        "order_id": order_id,
                        "refund_id": "",
                        "refund_amount": amount,
                        "status": new_status,
                    }
                raise HTTPException(
                    status_code=400,
                    detail=f"Refund failed: Clover returned status {resp.status_code} — {resp.text[:200]}"
                )

            if resp.status_code in (200, 201):
                refund_id = result.get("id", "")
                new_status = "refunded"
                await db.execute(
                    "UPDATE ecommerce_orders SET payment_status = ?, refund_id = ?, refund_amount = ? WHERE id = ?",
                    (new_status, refund_id, amount, order_id),
                )
                await db.commit()
                return {
                    "success": True,
                    "order_id": order_id,
                    "refund_id": refund_id,
                    "refund_amount": amount,
                    "status": new_status,
                }
            else:
                error_msg = result.get("message") or result.get("error", {}).get("message", "Refund failed")
                raise HTTPException(status_code=400, detail=f"Refund failed: {error_msg}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refund service error: {str(e)}")
