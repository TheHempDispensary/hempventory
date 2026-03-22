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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.database import get_db

STORE_EMAIL = "Support@TheHempDispensary.com"


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
    shipping_cost: int = 0
    tax: int = 0
    total: int = 0
    notes: str = ""
    payment_token: str = ""
    loyalty_number: str = ""

router = APIRouter(prefix="/api/ecommerce", tags=["ecommerce"])

# HQ location Clover credentials (public endpoint - no auth required)
HQ_MERCHANT_ID = "0AJ4FF0G1YFM1"
HQ_API_TOKEN = "9a06267a-6998-3f5a-521c-ca235f704856"
HQ_ECOMM_TOKEN = "81e997e6-89d0-0ff7-522d-d195e6cd9138"
CLOVER_BASE_URL = "https://api.clover.com/v3"
CLOVER_CHARGES_URL = "https://scl.clover.com/v1/charges"

# ── In-memory product cache ──────────────────────────────────────────────────
_product_cache: dict = {}  # {"products": [...], "total": int, "categories": [...]}
_product_cache_json: bytes = b""  # Pre-serialized JSON for the full /products response
_cache_timestamp: float = 0.0
_refresh_in_progress: bool = False
CACHE_TTL = 600  # 10 minutes
DISK_CACHE_PATH = os.environ.get("DB_PATH", "").replace("app.db", "product_cache.json") or "/tmp/product_cache.json"


async def _load_disk_cache() -> bool:
    """Load product cache from disk (survives restarts/deploys). Returns True if loaded."""
    global _product_cache, _product_cache_json, _cache_timestamp
    try:
        if os.path.exists(DISK_CACHE_PATH):
            with open(DISK_CACHE_PATH, "r") as f:
                disk_data = json.load(f)
            saved_at = disk_data.get("timestamp", 0)
            age = time.time() - saved_at
            if age < 3600:  # disk cache valid for 1 hour
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

        # Sequential pagination — avoids Clover 429 rate limits
        async with httpx.AsyncClient(timeout=120.0) as client:
            all_items: list = []
            current_offset = 0
            while True:
                resp = await client.get(
                    f"{base}/items",
                    headers=headers,
                    params={
                        "expand": "categories,itemStock",
                        "limit": 1000,
                        "offset": current_offset,
                        "filter": "deleted=false",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                elements = data.get("elements", [])
                all_items.extend(elements)
                if len(elements) < 1000:
                    break
                current_offset += 1000

        fetch_time = time.time() - start_time
        print(f"[cache] Clover API fetched {len(all_items)} items in {fetch_time:.1f}s")

        # Get image map from our database
        from app.database import DB_PATH
        image_base_url = os.environ.get("BASE_URL", "https://thd-inventory-api.fly.dev") + "/api/inventory/images"
        db = await aiosqlite.connect(DB_PATH)
        try:
            cursor = await db.execute("SELECT sku, product_name, updated_at FROM product_images")
            image_rows = await cursor.fetchall()
        finally:
            await db.close()
        image_by_sku = {row[0]: f"{image_base_url}/{row[0]}?nobg=1&v=2&t={row[2] or ''}" for row in image_rows}
        image_by_name = {}
        for row in image_rows:
            if row[1]:
                image_by_name[row[1].upper()] = f"{image_base_url}/{row[0]}?nobg=1&v=2&t={row[2] or ''}"

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
            stock = stock_info.get("quantity", 0) if stock_info else 0
            description = item.get("description", "")
            online_name = item.get("onlineName", "") or name

            for cat in item_categories:
                categories_set.add(cat)

            image_url = image_by_sku.get(sku)
            if not image_url:
                image_url = image_by_name.get(name.upper())

            slug = name.lower().replace(" ", "-").replace(",", "").replace(".", "")
            slug = "-".join(slug.split())

            products.append({
                "id": item.get("id", ""),
                "name": name,
                "online_name": online_name,
                "slug": slug,
                "sku": sku,
                "price": price,
                "description": description,
                "categories": item_categories,
                "stock": stock,
                "available": item.get("available", True) and stock > 0,
                "image_url": image_url,
                "is_age_restricted": item.get("isAgeRestricted", False),
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
        charge_data = {
            "amount": order.total,
            "currency": "usd",
            "source": order.payment_token,
            "description": f"Hemp Dispensary Online Order - {order.customer.first_name} {order.customer.last_name}",
            "ecomind": "ecom",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
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
            subtotal, shipping_cost, tax, total, notes, charge_id, payment_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            order.shipping_cost,
            order.tax,
            order.total,
            order.notes,
            charge_id,
            payment_status,
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


async def _get_smtp_settings(db: aiosqlite.Connection) -> dict[str, str]:
    """Get SMTP settings from database."""
    smtp_settings: dict[str, str] = {}
    for key in ["smtp_host", "smtp_port", "smtp_user", "smtp_password"]:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row:
            smtp_settings[key] = row[0]
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
    image_url = f"{image_base_url}/{row[0]}?nobg=1&v=2&t={row[1] or ''}" if row else None

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
    }
