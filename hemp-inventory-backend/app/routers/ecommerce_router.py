from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Optional, List
from pydantic import BaseModel
import httpx
import aiosqlite
import time
import smtplib
import asyncio
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

# West location eComm credentials (public endpoint - no auth required)
WEST_MERCHANT_ID = "XD21MGSEBV081"
WEST_ECOMM_TOKEN = "2d8433db-1e3e-4e94-9510-1a62a120eb6b"
CLOVER_BASE_URL = "https://api.clover.com/v3"
CLOVER_CHARGES_URL = "https://scl.clover.com/v1/charges"


@router.get("/products")
async def get_products(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Get products from Clover eCommerce catalog (West location).
    Returns products with categories, stock, and image URLs from inventory backend."""
    base = f"{CLOVER_BASE_URL}/merchants/{WEST_MERCHANT_ID}"
    headers = {"Authorization": f"Bearer {WEST_ECOMM_TOKEN}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Fetch all items with categories and stock
        all_items = []
        current_offset = offset
        while True:
            resp = await client.get(
                f"{base}/items",
                headers=headers,
                params={
                    "expand": "categories,itemStock",
                    "limit": min(limit, 1000),
                    "offset": current_offset,
                    "filter": "deleted=false",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])
            all_items.extend(elements)
            if len(elements) < 1000 or len(all_items) >= limit:
                break
            current_offset += 1000

    # Get image map from our database
    base_url = str(request.base_url).replace('http://', 'https://')
    image_base_url = f"{base_url}api/inventory/images".rstrip('/')
    cursor = await db.execute("SELECT sku, product_name FROM product_images")
    image_rows = await cursor.fetchall()
    image_by_sku = {row[0]: f"{image_base_url}/{row[0]}" for row in image_rows}
    image_by_name = {}
    for row in image_rows:
        if row[1]:
            image_by_name[row[1].upper()] = f"{image_base_url}/{row[0]}"

    # Build product list
    products = []
    categories_set = set()

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

        # Filter by category if specified
        if category and category.lower() != "all":
            if not any(c.lower() == category.lower() for c in item_categories):
                continue

        # Filter by search term
        if search:
            search_lower = search.lower()
            if search_lower not in name.lower() and search_lower not in description.lower():
                continue

        for cat in item_categories:
            categories_set.add(cat)

        # Find image URL: check by SKU first, then by product name
        image_url = image_by_sku.get(sku)
        if not image_url:
            image_url = image_by_name.get(name.upper())

        # Generate a URL-friendly slug from the product name
        slug = name.lower().replace(" ", "-").replace(",", "").replace(".", "")
        slug = "-".join(slug.split())  # normalize multiple spaces

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

    # Sort by name
    products.sort(key=lambda p: p["name"])

    return {
        "products": products,
        "total": len(products),
        "categories": sorted(categories_set),
    }


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
            "Authorization": f"Bearer {WEST_ECOMM_TOKEN}",
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
async def get_product_detail(
    product_id: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: Get a single product detail by Clover item ID."""
    base = f"{CLOVER_BASE_URL}/merchants/{WEST_MERCHANT_ID}"
    headers = {"Authorization": f"Bearer {WEST_ECOMM_TOKEN}"}

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

    # Find image URL
    base_url = str(request.base_url).replace('http://', 'https://')
    image_base_url = f"{base_url}api/inventory/images".rstrip('/')
    cursor = await db.execute(
        "SELECT sku FROM product_images WHERE sku = ? OR UPPER(product_name) = ?",
        (sku, name.upper()),
    )
    row = await cursor.fetchone()
    image_url = f"{image_base_url}/{row[0]}" if row else None

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
