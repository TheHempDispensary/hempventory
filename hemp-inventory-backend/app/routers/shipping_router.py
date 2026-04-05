"""Shipping integration with Shippo for creating labels and tracking shipments."""

import asyncio
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import aiosqlite
from app.database import get_db

router = APIRouter(prefix="/api/shipping", tags=["shipping"])

SHIPPO_API_URL = "https://api.goshippo.com"
SHIPPO_API_TOKEN = os.environ.get("SHIPPO_API_TOKEN", "")

# Default sender address (HQ)
DEFAULT_FROM_ADDRESS = {
    "name": "The Hemp Dispensary",
    "company": "The Hemp Dispensary",
    "street1": "4119 Lamson Ave",
    "street2": "",
    "city": "Spring Hill",
    "state": "FL",
    "zip": "34608",
    "country": "US",
    "phone": "352-842-6185",
    "email": "support@thehempdispensary.com",
}

# LeafLife products ship from supplier in Madison, WI
LEAFLIFE_FROM_ADDRESS = {
    "name": "The Hemp Dispensary",
    "company": "The Hemp Dispensary",
    "street1": "2510 Pennsylvania Ave",
    "street2": "",
    "city": "Madison",
    "state": "WI",
    "zip": "53704",
    "country": "US",
    "phone": "352-340-2439",
    "email": "support@thehempdispensary.com",
}


def _has_leaflife_products_skus(skus: list[str]) -> bool:
    """Check if any SKUs indicate LeafLife products (SKU starts with LF-)."""
    return any(s.upper().startswith("LF-") for s in skus if s)


def _get_shippo_headers() -> dict:
    token = SHIPPO_API_TOKEN
    if not token:
        raise HTTPException(status_code=500, detail="Shippo API token not configured")
    return {
        "Authorization": f"ShippoToken {token}",
        "Content-Type": "application/json",
    }


def _verify_admin(request: Request) -> str:
    """Verify JWT admin auth from Authorization header. Returns username."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = auth.split(" ", 1)[1]
    jwt_secret = os.environ.get("JWT_SECRET", "hemp-inventory-secret-key")
    try:
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        return payload.get("sub", "admin")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


class CreateShipmentRequest(BaseModel):
    order_id: int
    parcel_length: float = 10.0
    parcel_width: float = 8.0
    parcel_height: float = 4.0
    parcel_distance_unit: str = "in"
    parcel_weight: float = 1.0
    parcel_mass_unit: str = "lb"
    is_hazmat: bool = False


class PurchaseLabelRequest(BaseModel):
    rate_id: str
    order_id: int
    label_file_type: str = "PDF"


@router.post("/create-shipment")
async def create_shipment(
    body: CreateShipmentRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create a Shippo shipment for an order and return available rates."""
    _verify_admin(request)

    # Get the order
    cursor = await db.execute("SELECT * FROM ecommerce_orders WHERE id = ?", (body.order_id,))
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(zip(columns, row))

    # Check if this order contains LeafLife products (ship from WI supplier)
    items_cursor = await db.execute(
        "SELECT sku FROM ecommerce_order_items WHERE order_id = ?", (body.order_id,)
    )
    item_rows = await items_cursor.fetchall()
    order_skus = [r[0] for r in item_rows if r[0]]
    from_address = LEAFLIFE_FROM_ADDRESS if _has_leaflife_products_skus(order_skus) else DEFAULT_FROM_ADDRESS

    # Build the destination address
    to_address = {
        "name": f"{order['customer_first_name']} {order['customer_last_name']}",
        "street1": order["shipping_address"],
        "street2": order.get("shipping_apartment", ""),
        "city": order["shipping_city"],
        "state": order["shipping_state"],
        "zip": order["shipping_zip"],
        "country": "US",
        "email": order.get("customer_email", ""),
        "phone": order.get("customer_phone", ""),
    }

    # Build parcel — Shippo allows max 4 decimal places for weight
    parcel = {
        "length": str(round(body.parcel_length, 4)),
        "width": str(round(body.parcel_width, 4)),
        "height": str(round(body.parcel_height, 4)),
        "distance_unit": body.parcel_distance_unit,
        "weight": str(round(body.parcel_weight, 4)),
        "mass_unit": body.parcel_mass_unit,
    }

    shipment_data = {
        "address_from": from_address,
        "address_to": to_address,
        "parcels": [parcel],
        "async": False,
    }

    # If hazmat, declare dangerous goods per Shippo API so the label prints hazmat markings
    if body.is_hazmat:
        shipment_data["extra"] = {
            "dangerous_goods": {
                "contains": True,
            },
        }

    headers = _get_shippo_headers()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SHIPPO_API_URL}/shipments/", headers=headers, json=shipment_data)
        if resp.status_code not in (200, 201):
            print(f"[shippo] Shipment creation failed: {resp.status_code} {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Shippo error: {resp.text}")

        shipment = resp.json()

    # Extract USPS Ground Advantage and Priority rates (exclude Priority Express)
    ALLOWED_SERVICES = {"ground advantage", "priority"}
    BLOCKED_SERVICES = {"priority mail express"}
    rates = shipment.get("rates", [])
    formatted_rates = []
    for rate in rates:
        provider = rate.get("provider", "")
        if "USPS" not in provider.upper():
            continue
        service_name = rate.get("servicelevel", {}).get("name", "").lower()
        if any(blocked in service_name for blocked in BLOCKED_SERVICES):
            continue
        if not any(allowed in service_name for allowed in ALLOWED_SERVICES):
            continue
        formatted_rates.append({
            "id": rate["object_id"],
            "provider": provider,
            "service_level": rate.get("servicelevel", {}).get("name", ""),
            "amount": rate.get("amount", "0"),
            "currency": rate.get("currency", "USD"),
            "estimated_days": rate.get("estimated_days"),
            "duration_terms": rate.get("duration_terms", ""),
            "arrives_by": rate.get("arrives_by"),
        })

    # Sort by price
    formatted_rates.sort(key=lambda r: float(r["amount"]))

    return {
        "shipment_id": shipment.get("object_id", ""),
        "rates": formatted_rates,
        "address_from": from_address,
        "address_to": to_address,
    }


@router.post("/purchase-label")
async def purchase_label(
    body: PurchaseLabelRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Purchase a shipping label for the selected rate."""
    _verify_admin(request)

    transaction_data = {
        "rate": body.rate_id,
        "label_file_type": body.label_file_type,
        "async": False,
    }

    headers = _get_shippo_headers()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SHIPPO_API_URL}/transactions/", headers=headers, json=transaction_data)
        if resp.status_code not in (200, 201):
            print(f"[shippo] Label purchase failed: {resp.status_code} {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Shippo error: {resp.text}")

        transaction = resp.json()

    status = transaction.get("status", "")
    if status == "ERROR":
        messages = transaction.get("messages", [])
        error_text = "; ".join([m.get("text", "") for m in messages]) if messages else "Label creation failed"
        raise HTTPException(status_code=400, detail=error_text)

    label_url = transaction.get("label_url", "")
    tracking_number = transaction.get("tracking_number", "")
    tracking_url = transaction.get("tracking_url_provider", "")

    # Save tracking info to the order
    if tracking_number:
        await db.execute(
            """UPDATE ecommerce_orders 
               SET tracking_number = ?, tracking_url = ?, label_url = ?, shippo_transaction_id = ?,
                   tracking_status = 'label_created',
                   payment_status = CASE WHEN payment_status IN ('paid', 'processing') THEN 'shipped' ELSE payment_status END
               WHERE id = ?""",
            (tracking_number, tracking_url, label_url, transaction.get("object_id", ""), body.order_id),
        )
        await db.commit()

        # Send tracking email to customer (non-blocking)
        cursor = await db.execute(
            "SELECT order_number, customer_first_name, customer_email FROM ecommerce_orders WHERE id = ?",
            (body.order_id,),
        )
        order_row = await cursor.fetchone()
        if order_row:
            order_number, first_name, customer_email = order_row
            if customer_email:
                smtp_settings = await _get_smtp_settings(db)
                asyncio.create_task(
                    _send_tracking_email(
                        smtp_settings, customer_email, first_name or "Customer",
                        order_number or "", tracking_number, tracking_url, "shipped",
                    )
                )

    return {
        "success": True,
        "label_url": label_url,
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
        "transaction_id": transaction.get("object_id", ""),
        "status": status,
    }


@router.get("/label/{order_id}")
async def get_label(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get the shipping label and tracking info for an order."""
    _verify_admin(request)

    cursor = await db.execute(
        "SELECT tracking_number, tracking_url, label_url, shippo_transaction_id FROM ecommerce_orders WHERE id = ?",
        (order_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    tracking_number, tracking_url, label_url, transaction_id = row
    if not label_url:
        return {"has_label": False}

    return {
        "has_label": True,
        "label_url": label_url,
        "tracking_number": tracking_number or "",
        "tracking_url": tracking_url or "",
        "transaction_id": transaction_id or "",
    }


SHIPPING_MARKUP_CENTS = 200  # $2.00 markup on all rates


class PublicRatesRequest(BaseModel):
    street1: str
    street2: str = ""
    city: str
    state: str
    zip_code: str
    product_names: list[str] = []
    product_skus: list[str] = []


@router.post("/rates")
async def get_public_shipping_rates(body: PublicRatesRequest):
    """Public endpoint: Get USPS shipping rates for a given address (no auth required)."""
    to_address = {
        "name": "Customer",
        "street1": body.street1,
        "street2": body.street2,
        "city": body.city,
        "state": body.state,
        "zip": body.zip_code,
        "country": "US",
    }

    # Default parcel dimensions for e-commerce orders
    parcel = {
        "length": "10",
        "width": "8",
        "height": "4",
        "distance_unit": "in",
        "weight": "1",
        "mass_unit": "lb",
    }

    # Use LeafLife origin address if any products are LeafLife (SKU starts with LF-)
    from_address = LEAFLIFE_FROM_ADDRESS if _has_leaflife_products_skus(body.product_skus) else DEFAULT_FROM_ADDRESS

    shipment_data = {
        "address_from": from_address,
        "address_to": to_address,
        "parcels": [parcel],
        "async": False,
    }

    headers = _get_shippo_headers()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{SHIPPO_API_URL}/shipments/", headers=headers, json=shipment_data)
            if resp.status_code not in (200, 201):
                print(f"[shippo] Public rates failed: {resp.status_code} {resp.text}")
                raise HTTPException(status_code=400, detail="Unable to get shipping rates for this address")

            shipment = resp.json()
    except httpx.HTTPError as e:
        print(f"[shippo] Connection error: {e}")
        raise HTTPException(status_code=500, detail="Shipping service unavailable")

    # Extract USPS Ground Advantage and Priority rates (exclude Priority Express), add $2 markup
    ALLOWED_SERVICES = {"ground advantage", "priority"}
    BLOCKED_SERVICES = {"priority mail express"}
    rates = shipment.get("rates", [])
    formatted_rates = []
    for rate in rates:
        provider = rate.get("provider", "")
        if "USPS" not in provider.upper():
            continue
        service_name = rate.get("servicelevel", {}).get("name", "").lower()
        if any(blocked in service_name for blocked in BLOCKED_SERVICES):
            continue
        if not any(allowed in service_name for allowed in ALLOWED_SERVICES):
            continue
        base_amount = float(rate.get("amount", "0"))
        markup_amount = base_amount + (SHIPPING_MARKUP_CENTS / 100)
        amount_cents = int(round(markup_amount * 100))
        formatted_rates.append({
            "id": rate["object_id"],
            "provider": provider,
            "service_level": rate.get("servicelevel", {}).get("name", ""),
            "amount": f"{markup_amount:.2f}",
            "amount_cents": amount_cents,
            "currency": rate.get("currency", "USD"),
            "estimated_days": rate.get("estimated_days"),
            "duration_terms": rate.get("duration_terms", ""),
        })

    # Sort by price
    formatted_rates.sort(key=lambda r: r["amount_cents"])

    if not formatted_rates:
        raise HTTPException(status_code=400, detail="No USPS shipping rates available for this address")

    return {"rates": formatted_rates}


@router.get("/validate-address")
async def validate_address(
    request: Request,
    street1: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
):
    """Validate a shipping address using Shippo."""
    _verify_admin(request)

    address_data = {
        "street1": street1,
        "city": city,
        "state": state,
        "zip": zip_code,
        "country": "US",
        "validate": True,
    }

    headers = _get_shippo_headers()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{SHIPPO_API_URL}/addresses/", headers=headers, json=address_data)
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=resp.status_code, detail="Address validation failed")
        result = resp.json()

    validation = result.get("validation_results", {})
    return {
        "is_valid": validation.get("is_valid", False),
        "messages": validation.get("messages", []),
    }


# ── SMTP helpers (reuse ecommerce_router patterns) ──────────────────────────

STORE_EMAIL = "Support@TheHempDispensary.com"
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = os.environ.get("SMTP_PORT", "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")


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
        print("[tracking] SMTP credentials not configured, skipping email")
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
        print(f"[tracking] Failed to send email to {to_email}: {e}")
        return False


# ── Tracking status display names ────────────────────────────────────────────

_STATUS_DISPLAY = {
    "shipped": ("Your Order Has Shipped!", "Your order is on its way! Here's your tracking information:"),
    "in_transit": ("Your Package Is In Transit", "Great news — your package is moving through the carrier network:"),
    "out_for_delivery": ("Out For Delivery!", "Your package is out for delivery and should arrive today:"),
    "delivered": ("Your Package Has Been Delivered!", "Your package has been delivered. We hope you enjoy your purchase!"),
    "returned": ("Package Return Notice", "Your package has been returned to us. Please contact us if you have questions:"),
    "failure": ("Delivery Issue", "There was an issue delivering your package. Please contact us for assistance:"),
}


def _build_tracking_html(
    first_name: str, order_number: str, tracking_number: str, tracking_url: str, status_key: str
) -> str:
    """Build a branded HTML email for tracking notifications."""
    title, message = _STATUS_DISPLAY.get(status_key, ("Shipping Update", "Here's an update on your order:"))

    tracking_link = ""
    if tracking_url:
        tracking_link = f"""
                <a href="{tracking_url}"
                   style="display: inline-block; background: #065f46; color: white; padding: 12px 28px;
                          border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 12px;">
                    Track Your Package
                </a>"""

    return f"""
    <html>
    <body style="font-family: 'Helvetica Neue', Arial, sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
        <div style="background: #065f46; padding: 20px; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 22px;">{title}</h1>
        </div>
        <div style="padding: 24px; background: #f9fafb;">
            <p style="font-size: 16px;">Hi {first_name},</p>
            <p>{message}</p>

            <table style="width: 100%; margin: 16px 0; background: white; border-radius: 8px; overflow: hidden;">
                <tr style="background: #f3f4f6;">
                    <td style="padding: 10px 12px; font-weight: bold;">Order Number</td>
                    <td style="padding: 10px 12px;">{order_number}</td>
                </tr>
                <tr>
                    <td style="padding: 10px 12px; font-weight: bold;">Tracking Number</td>
                    <td style="padding: 10px 12px; font-family: monospace;">{tracking_number}</td>
                </tr>
            </table>

            <div style="text-align: center;">
                {tracking_link}
            </div>

            <p style="margin-top: 20px;">If you have any questions, reply to this email or contact us at
               <a href="mailto:{STORE_EMAIL}">{STORE_EMAIL}</a>.</p>
            <p>Thank you for choosing The Hemp Dispensary!</p>
        </div>
        <div style="padding: 16px; text-align: center; color: #9ca3af; font-size: 12px;">
            The Hemp Dispensary — Premium Hemp Products<br>
            Spring Hill, FL
        </div>
    </body>
    </html>
    """


async def _send_tracking_email(
    smtp_settings: dict[str, str],
    customer_email: str,
    first_name: str,
    order_number: str,
    tracking_number: str,
    tracking_url: str,
    status_key: str,
) -> None:
    """Send a tracking notification email to the customer (runs as background task)."""
    try:
        title, _ = _STATUS_DISPLAY.get(status_key, ("Shipping Update", ""))
        subject = f"{title} — Order {order_number} | The Hemp Dispensary"
        html = _build_tracking_html(first_name, order_number, tracking_number, tracking_url, status_key)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_smtp_email, smtp_settings, customer_email, subject, html)
        print(f"[tracking] Sent '{status_key}' email to {customer_email} for order {order_number}")
    except Exception as e:
        print(f"[tracking] Error sending tracking email: {e}")


# ── Shippo Webhook for tracking status updates ──────────────────────────────

@router.post("/webhook/tracking")
async def shippo_tracking_webhook(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Receive tracking status updates from Shippo webhooks.
    Shippo sends POST requests when a shipment's tracking status changes.
    This endpoint is public (no auth) because Shippo calls it directly."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Shippo webhook payload structure:
    # { "data": { "tracking_number": "...", "tracking_status": { "status": "...", ... } }, "event": "track_updated" }
    event = body.get("event", "")
    if event != "track_updated":
        return {"status": "ignored", "event": event}

    data = body.get("data", {})
    tracking_number = data.get("tracking_number", "")
    tracking_status_obj = data.get("tracking_status") or {}
    new_status = tracking_status_obj.get("status", "").upper()
    status_detail = tracking_status_obj.get("status_details", "")

    if not tracking_number:
        return {"status": "ignored", "reason": "no_tracking_number"}

    # Map Shippo status to our status keys
    status_map = {
        "PRE_TRANSIT": "label_created",
        "TRANSIT": "in_transit",
        "DELIVERED": "delivered",
        "RETURNED": "returned",
        "FAILURE": "failure",
    }
    status_key = status_map.get(new_status, "in_transit")

    # Special case: check for "out for delivery" in status_details
    if new_status == "TRANSIT" and status_detail and "out for delivery" in status_detail.lower():
        status_key = "out_for_delivery"

    # Find the order by tracking number
    cursor = await db.execute(
        "SELECT id, order_number, customer_first_name, customer_email, tracking_url, tracking_status FROM ecommerce_orders WHERE tracking_number = ?",
        (tracking_number,),
    )
    row = await cursor.fetchone()
    if not row:
        print(f"[tracking] Webhook received for unknown tracking number: {tracking_number}")
        return {"status": "ignored", "reason": "order_not_found"}

    order_id, order_number, first_name, customer_email, tracking_url, current_status = row

    # Only send email if status actually changed
    if current_status == status_key:
        return {"status": "ok", "detail": "no_change"}

    # Update tracking status in database
    await db.execute(
        "UPDATE ecommerce_orders SET tracking_status = ? WHERE id = ?",
        (status_key, order_id),
    )
    # If delivered, update payment_status too
    if status_key == "delivered":
        await db.execute(
            "UPDATE ecommerce_orders SET payment_status = 'delivered' WHERE id = ? AND payment_status = 'shipped'",
            (order_id,),
        )
    await db.commit()

    # Send email notification to customer
    if customer_email and status_key in _STATUS_DISPLAY:
        smtp_settings = await _get_smtp_settings(db)
        asyncio.create_task(
            _send_tracking_email(
                smtp_settings, customer_email, first_name or "Customer",
                order_number or "", tracking_number, tracking_url or "", status_key,
            )
        )

    print(f"[tracking] Order {order_number}: {current_status} -> {status_key} (tracking: {tracking_number})")
    return {"status": "ok", "order": order_number, "new_status": status_key}


async def register_shippo_tracking_webhook() -> None:
    """Register a webhook with Shippo to receive tracking updates.
    Called once on app startup."""
    token = SHIPPO_API_TOKEN
    if not token:
        print("[tracking] No Shippo token — skipping webhook registration")
        return

    base_url = os.environ.get("BASE_URL", "https://thd-inventory-api.fly.dev")
    webhook_url = f"{base_url}/api/shipping/webhook/tracking"

    headers = {
        "Authorization": f"ShippoToken {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Check existing webhooks first
            resp = await client.get(f"{SHIPPO_API_URL}/webhooks/", headers=headers)
            if resp.status_code == 200:
                existing = resp.json().get("results", [])
                for wh in existing:
                    if wh.get("url") == webhook_url and wh.get("event") == "track_updated":
                        print(f"[tracking] Shippo webhook already registered: {webhook_url}")
                        return

            # Register new webhook
            resp = await client.post(
                f"{SHIPPO_API_URL}/webhooks/",
                headers=headers,
                json={"url": webhook_url, "event": "track_updated", "is_test": False},
            )
            if resp.status_code in (200, 201):
                print(f"[tracking] Shippo tracking webhook registered: {webhook_url}")
            else:
                print(f"[tracking] Failed to register webhook: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[tracking] Error registering Shippo webhook: {e}")
