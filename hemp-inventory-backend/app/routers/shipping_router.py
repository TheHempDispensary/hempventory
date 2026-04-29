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


LEAFLIFE_NAME_KEYWORDS = ["everyday", "premium", "essential", "smalls", "snowcaps"]


def _has_leaflife_products_skus(skus: list[str]) -> bool:
    """Check if any SKUs indicate LeafLife products (SKU starts with LF-)."""
    return any(s.upper().startswith("LF-") for s in skus if s)


def _has_leaflife_products_names(names: list[str]) -> bool:
    """Fallback: check if any product names contain LeafLife keywords."""
    for name in names:
        if name:
            lower = name.lower()
            if any(kw in lower for kw in LEAFLIFE_NAME_KEYWORDS):
                return True
    return False


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
    parcel_height: float = 2.0
    parcel_distance_unit: str = "in"
    parcel_weight: float = 0.375
    parcel_mass_unit: str = "lb"
    is_hazmat: bool = False


class PurchaseLabelRequest(BaseModel):
    rate_id: str
    order_id: int
    label_file_type: str = "PDF"
    shipment_id: int | None = None  # order_shipments.id for split-shipment orders


def _is_leaflife_item(sku: str, name: str) -> bool:
    """Check if a single item is a LeafLife product."""
    if sku and isinstance(sku, str) and sku.upper().startswith("LF-"):
        return True
    if name:
        lower = name.lower()
        if any(kw in lower for kw in LEAFLIFE_NAME_KEYWORDS):
            return True
    return False


def _filter_usps_rates(rates: list[dict]) -> list[dict]:
    """Extract and format USPS Ground Advantage and Priority rates."""
    ALLOWED_SERVICES = {"ground advantage", "priority"}
    BLOCKED_SERVICES = {"priority mail express"}
    formatted: list[dict] = []
    for rate in rates:
        provider = rate.get("provider", "")
        if "USPS" not in provider.upper():
            continue
        service_name = rate.get("servicelevel", {}).get("name", "").lower()
        if any(blocked in service_name for blocked in BLOCKED_SERVICES):
            continue
        if not any(allowed in service_name for allowed in ALLOWED_SERVICES):
            continue
        formatted.append({
            "id": rate["object_id"],
            "provider": provider,
            "service_level": rate.get("servicelevel", {}).get("name", ""),
            "amount": rate.get("amount", "0"),
            "currency": rate.get("currency", "USD"),
            "estimated_days": rate.get("estimated_days"),
            "duration_terms": rate.get("duration_terms", ""),
            "arrives_by": rate.get("arrives_by"),
        })
    formatted.sort(key=lambda r: float(r["amount"]))
    return formatted


async def _create_shippo_shipment(
    headers: dict, from_address: dict, to_address: dict, parcel: dict, is_hazmat: bool
) -> dict:
    """Create a single Shippo shipment and return the parsed JSON."""
    shipment_data: dict = {
        "address_from": from_address,
        "address_to": to_address,
        "parcels": [parcel],
        "async": False,
    }
    if is_hazmat:
        shipment_data["extra"] = {"dangerous_goods": {"contains": True}}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SHIPPO_API_URL}/shipments/", headers=headers, json=shipment_data)
        if resp.status_code not in (200, 201):
            print(f"[shippo] Shipment creation failed: {resp.status_code} {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Shippo error: {resp.text}")
        return resp.json()


@router.post("/create-shipment")
async def create_shipment(
    body: CreateShipmentRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Create Shippo shipment(s) for an order and return available rates.

    For mixed orders (containing both store and LeafLife items), two shipments
    are created — one from the store (FL) and one from the LeafLife supplier (WI).
    Each shipment group is stored in the ``order_shipments`` table and returned
    with its own set of rates.
    """
    _verify_admin(request)

    cursor = await db.execute("SELECT * FROM ecommerce_orders WHERE id = ?", (body.order_id,))
    columns = [desc[0] for desc in cursor.description]
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(zip(columns, row))

    items_cursor = await db.execute(
        "SELECT id, sku, product_name FROM ecommerce_order_items WHERE order_id = ?", (body.order_id,)
    )
    item_rows = await items_cursor.fetchall()

    # Partition items into store vs LeafLife
    store_items: list[tuple] = []
    leaflife_items: list[tuple] = []
    for item_id, sku, name in item_rows:
        if _is_leaflife_item(sku or "", name or ""):
            leaflife_items.append((item_id, sku, name))
        else:
            store_items.append((item_id, sku, name))

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

    parcel = {
        "length": str(round(body.parcel_length, 4)),
        "width": str(round(body.parcel_width, 4)),
        "height": str(round(body.parcel_height, 4)),
        "distance_unit": body.parcel_distance_unit,
        "weight": str(round(body.parcel_weight, 4)),
        "mass_unit": body.parcel_mass_unit,
    }

    headers = _get_shippo_headers()
    is_mixed = bool(store_items) and bool(leaflife_items)

    # Clear any previous shipment records for this order (in case admin retries)
    await db.execute("DELETE FROM order_shipments WHERE order_id = ?", (body.order_id,))

    # Build shipment groups
    groups_to_create: list[tuple[str, dict, list[tuple]]] = []
    if is_mixed:
        groups_to_create.append(("store", DEFAULT_FROM_ADDRESS, store_items))
        groups_to_create.append(("leaflife", LEAFLIFE_FROM_ADDRESS, leaflife_items))
    elif leaflife_items:
        groups_to_create.append(("leaflife", LEAFLIFE_FROM_ADDRESS, leaflife_items))
    else:
        groups_to_create.append(("store", DEFAULT_FROM_ADDRESS, store_items or list(item_rows)))

    shipment_groups: list[dict] = []
    for stype, from_addr, items in groups_to_create:
        shipment = await _create_shippo_shipment(headers, from_addr, to_address, parcel, body.is_hazmat)
        rates = _filter_usps_rates(shipment.get("rates", []))

        item_id_list = ",".join(str(i[0]) for i in items)
        item_names = [i[2] or i[1] or "?" for i in items]
        from_label = "Madison, WI (LeafLife)" if stype == "leaflife" else "Spring Hill, FL (Store)"

        cur = await db.execute(
            """INSERT INTO order_shipments (order_id, shipment_type, item_ids, from_label, shippo_shipment_id)
               VALUES (?, ?, ?, ?, ?)""",
            (body.order_id, stype, item_id_list, from_label, shipment.get("object_id", "")),
        )
        shipment_db_id = cur.lastrowid

        shippo_shipment_id = shipment.get("object_id", "")
        shipment_groups.append({
            "shipment_id": shipment_db_id,
            "shipment_type": stype,
            "from_label": from_label,
            "item_names": item_names,
            "rates": rates,
            "address_from": from_addr,
            "shippo_shipment_id": shippo_shipment_id,
        })

    await db.commit()

    return {
        "is_split": is_mixed,
        "shipment_groups": shipment_groups,
        "address_to": to_address,
        # Legacy fields for single-shipment orders (backwards compat)
        "shipment_id": shipment_groups[0].get("shippo_shipment_id", "") if shipment_groups else "",
        "rates": shipment_groups[0]["rates"] if len(shipment_groups) == 1 else [],
        "address_from": shipment_groups[0]["address_from"] if shipment_groups else DEFAULT_FROM_ADDRESS,
    }


async def _purchase_shippo_label(headers: dict, rate_id: str, label_file_type: str) -> dict:
    """Purchase a Shippo label for a given rate and return the transaction JSON."""
    transaction_data = {
        "rate": rate_id,
        "label_file_type": label_file_type,
        "async": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SHIPPO_API_URL}/transactions/", headers=headers, json=transaction_data)
        if resp.status_code not in (200, 201):
            print(f"[shippo] Label purchase failed: {resp.status_code} {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Shippo error: {resp.text}")
        return resp.json()


@router.post("/purchase-label")
async def purchase_label(
    body: PurchaseLabelRequest,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Purchase a shipping label for the selected rate.

    If ``shipment_id`` is provided, the label is stored in the ``order_shipments``
    row instead of directly on the order.  The order-level fields are updated to
    the *first* purchased shipment's tracking info for backwards compatibility.
    When all shipment groups for an order have labels, the order is marked shipped
    and a single tracking email with all tracking numbers is sent.
    """
    _verify_admin(request)

    headers = _get_shippo_headers()
    transaction = await _purchase_shippo_label(headers, body.rate_id, body.label_file_type)

    status = transaction.get("status", "")
    if status == "ERROR":
        messages = transaction.get("messages", [])
        error_text = "; ".join([m.get("text", "") for m in messages]) if messages else "Label creation failed"
        raise HTTPException(status_code=400, detail=error_text)

    label_url = transaction.get("label_url", "")
    tracking_number = transaction.get("tracking_number", "")
    tracking_url = transaction.get("tracking_url_provider", "")
    txn_id = transaction.get("object_id", "")

    if body.shipment_id and tracking_number:
        # Split-shipment: store tracking in order_shipments row
        await db.execute(
            """UPDATE order_shipments
               SET tracking_number = ?, tracking_url = ?, label_url = ?,
                   shippo_transaction_id = ?, tracking_status = 'label_created'
               WHERE id = ?""",
            (tracking_number, tracking_url, label_url, txn_id, body.shipment_id),
        )

        # Check if ALL shipment groups for this order now have labels
        cursor = await db.execute(
            "SELECT id, tracking_number, tracking_url, from_label, label_url, shippo_transaction_id FROM order_shipments WHERE order_id = ?",
            (body.order_id,),
        )
        all_shipments = await cursor.fetchall()
        all_have_labels = all(s[1] for s in all_shipments)

        # Keep order-level tracking in sync with the first labelled shipment
        # All fields (tracking, label, txn_id) come from the same shipment row
        first_labelled = next((s for s in all_shipments if s[1]), None)
        if first_labelled:
            await db.execute(
                """UPDATE ecommerce_orders
                   SET tracking_number = ?, tracking_url = ?, label_url = ?,
                       shippo_transaction_id = ?, tracking_status = 'label_created'
                   WHERE id = ?""",
                (first_labelled[1], first_labelled[2], first_labelled[4], first_labelled[5], body.order_id),
            )

        if all_have_labels:
            await db.execute(
                """UPDATE ecommerce_orders
                   SET payment_status = CASE WHEN payment_status IN ('paid', 'processing') THEN 'shipped' ELSE payment_status END
                   WHERE id = ?""",
                (body.order_id,),
            )
            # Send one tracking email with all tracking numbers
            cursor2 = await db.execute(
                "SELECT order_number, customer_first_name, customer_email FROM ecommerce_orders WHERE id = ?",
                (body.order_id,),
            )
            order_row = await cursor2.fetchone()
            if order_row:
                order_number, first_name, customer_email = order_row
                if customer_email:
                    shipment_info = [
                        {"tracking_number": s[1], "tracking_url": s[2] or "", "from_label": s[3]}
                        for s in all_shipments if s[1]
                    ]
                    smtp_settings = await _get_smtp_settings(db)
                    if len(shipment_info) > 1:
                        asyncio.create_task(
                            _send_split_tracking_email(
                                smtp_settings, customer_email, first_name or "Customer",
                                order_number or "", shipment_info,
                            )
                        )
                    else:
                        asyncio.create_task(
                            _send_tracking_email(
                                smtp_settings, customer_email, first_name or "Customer",
                                order_number or "", tracking_number, tracking_url, "shipped",
                            )
                        )

        await db.commit()

    elif tracking_number:
        # Legacy single-shipment flow
        await db.execute(
            """UPDATE ecommerce_orders
               SET tracking_number = ?, tracking_url = ?, label_url = ?, shippo_transaction_id = ?,
                   tracking_status = 'label_created',
                   payment_status = CASE WHEN payment_status IN ('paid', 'processing') THEN 'shipped' ELSE payment_status END
               WHERE id = ?""",
            (tracking_number, tracking_url, label_url, txn_id, body.order_id),
        )
        await db.commit()

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
        "transaction_id": txn_id,
        "status": status,
        "shipment_id": body.shipment_id,
    }


@router.get("/label/{order_id}")
async def get_label(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get shipping label(s) and tracking info for an order.

    Returns split-shipment data in ``shipments`` when the order has multiple
    shipment groups, plus legacy top-level fields for backwards compatibility.
    """
    _verify_admin(request)

    cursor = await db.execute(
        "SELECT tracking_number, tracking_url, label_url, shippo_transaction_id FROM ecommerce_orders WHERE id = ?",
        (order_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")

    tracking_number, tracking_url, label_url, transaction_id = row

    # Check for split shipments
    scur = await db.execute(
        """SELECT id, shipment_type, from_label, tracking_number, tracking_url,
                  label_url, shippo_transaction_id, tracking_status, item_ids
           FROM order_shipments WHERE order_id = ?""",
        (order_id,),
    )
    shipment_rows = await scur.fetchall()

    shipments = []
    for s in shipment_rows:
        if s[5]:  # has label_url
            shipments.append({
                "shipment_id": s[0],
                "shipment_type": s[1],
                "from_label": s[2],
                "tracking_number": s[3] or "",
                "tracking_url": s[4] or "",
                "label_url": s[5] or "",
                "transaction_id": s[6] or "",
                "tracking_status": s[7] or "",
            })

    if not label_url and not shipments:
        return {"has_label": False, "shipments": []}

    return {
        "has_label": True,
        "label_url": label_url or (shipments[0]["label_url"] if shipments else ""),
        "tracking_number": tracking_number or "",
        "tracking_url": tracking_url or "",
        "transaction_id": transaction_id or "",
        "shipments": shipments,
    }


@router.get("/shipments/{order_id}")
async def get_order_shipments(
    order_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Get all shipment groups for an order (split-shipment aware)."""
    _verify_admin(request)

    cursor = await db.execute(
        """SELECT id, shipment_type, from_label, item_ids, tracking_number,
                  tracking_url, label_url, tracking_status
           FROM order_shipments WHERE order_id = ? ORDER BY id""",
        (order_id,),
    )
    rows = await cursor.fetchall()

    shipments = []
    for r in rows:
        item_ids = [int(x) for x in r[3].split(",") if x.strip()] if r[3] else []
        item_names: list[str] = []
        if item_ids:
            placeholders = ",".join("?" * len(item_ids))
            icur = await db.execute(
                f"SELECT product_name FROM ecommerce_order_items WHERE id IN ({placeholders})",
                item_ids,
            )
            item_names = [row[0] for row in await icur.fetchall() if row[0]]

        shipments.append({
            "shipment_id": r[0],
            "shipment_type": r[1],
            "from_label": r[2],
            "item_names": item_names,
            "tracking_number": r[4] or "",
            "tracking_url": r[5] or "",
            "label_url": r[6] or "",
            "tracking_status": r[7] or "",
        })

    return {"shipments": shipments}


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

    # Default parcel dimensions for e-commerce orders (envelope: 10x8x2, ~6oz)
    parcel = {
        "length": "10",
        "width": "8",
        "height": "2",
        "distance_unit": "in",
        "weight": "0.375",
        "mass_unit": "lb",
    }

    # Use LeafLife origin address if any products are LeafLife (check SKU first, fall back to name keywords)
    is_leaflife = _has_leaflife_products_skus(body.product_skus) or _has_leaflife_products_names(body.product_names)
    from_address = LEAFLIFE_FROM_ADDRESS if is_leaflife else DEFAULT_FROM_ADDRESS

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


def _build_split_tracking_html(
    first_name: str, order_number: str, shipments: list[dict],
) -> str:
    """Build an HTML email showing tracking for multiple shipments."""
    rows = ""
    for i, s in enumerate(shipments, 1):
        tracking_link = ""
        if s.get("tracking_url"):
            tracking_link = (
                f' &mdash; <a href="{s["tracking_url"]}" style="color: #065f46;">Track</a>'
            )
        rows += f"""
                <tr{"" if i % 2 else ' style="background: #f3f4f6;"'}>
                    <td style="padding: 10px 12px; font-weight: bold;">Shipment {i}</td>
                    <td style="padding: 10px 12px;">{s["from_label"]}</td>
                </tr>
                <tr{"" if i % 2 else ' style="background: #f3f4f6;"'}>
                    <td style="padding: 10px 12px; font-weight: bold;">Tracking</td>
                    <td style="padding: 10px 12px; font-family: monospace;">{s["tracking_number"]}{tracking_link}</td>
                </tr>"""

    return f"""
    <html>
    <body style="font-family: 'Helvetica Neue', Arial, sans-serif; color: #1f2937; max-width: 600px; margin: 0 auto;">
        <div style="background: #065f46; padding: 20px; text-align: center;">
            <h1 style="color: white; margin: 0; font-size: 22px;">Your Order Has Shipped!</h1>
        </div>
        <div style="padding: 24px; background: #f9fafb;">
            <p style="font-size: 16px;">Hi {first_name},</p>
            <p>Your order <strong>{order_number}</strong> is shipping in <strong>{len(shipments)} packages</strong> from different locations. Here are your tracking details:</p>

            <table style="width: 100%; margin: 16px 0; background: white; border-radius: 8px; overflow: hidden;">
                <tr style="background: #065f46; color: white;">
                    <td style="padding: 10px 12px; font-weight: bold;">Order</td>
                    <td style="padding: 10px 12px;">{order_number}</td>
                </tr>{rows}
            </table>

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


async def _send_split_tracking_email(
    smtp_settings: dict[str, str],
    customer_email: str,
    first_name: str,
    order_number: str,
    shipments: list[dict],
) -> None:
    """Send a tracking email with multiple shipment tracking numbers."""
    try:
        subject = f"Your Order Has Shipped! — Order {order_number} | The Hemp Dispensary"
        html = _build_split_tracking_html(first_name, order_number, shipments)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _send_smtp_email, smtp_settings, customer_email, subject, html)
        print(f"[tracking] Sent split-shipment email to {customer_email} for order {order_number} ({len(shipments)} shipments)")
    except Exception as e:
        print(f"[tracking] Error sending split tracking email: {e}")


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

    # Find the order by tracking number (check order_shipments first, then orders)
    shipment_row_id = None
    shipment_current_status: str | None = None
    shipment_tracking_url: str | None = None
    lookup_order_id: int | None = None
    scur = await db.execute(
        "SELECT id, order_id, tracking_status, tracking_url FROM order_shipments WHERE tracking_number = ?",
        (tracking_number,),
    )
    srow = await scur.fetchone()
    if srow:
        shipment_row_id = srow[0]
        lookup_order_id = srow[1]
        shipment_current_status = srow[2]
        shipment_tracking_url = srow[3]
        # Update shipment-level tracking status
        if shipment_current_status != status_key:
            await db.execute(
                "UPDATE order_shipments SET tracking_status = ? WHERE id = ?",
                (status_key, shipment_row_id),
            )

    cursor = await db.execute(
        "SELECT id, order_number, customer_first_name, customer_email, tracking_url, tracking_status FROM ecommerce_orders WHERE tracking_number = ?",
        (tracking_number,),
    )
    row = await cursor.fetchone()

    # If not found by order-level tracking, try via order_shipments.order_id
    if not row and srow:
        cursor = await db.execute(
            "SELECT id, order_number, customer_first_name, customer_email, tracking_url, tracking_status FROM ecommerce_orders WHERE id = ?",
            (lookup_order_id,),
        )
        row = await cursor.fetchone()

    if not row:
        print(f"[tracking] Webhook received for unknown tracking number: {tracking_number}")
        return {"status": "ignored", "reason": "order_not_found"}

    order_id, order_number, first_name, customer_email, tracking_url, current_status = row

    # Only send email if status actually changed (for both order-level and shipment-level)
    if current_status == status_key and (not shipment_row_id or shipment_current_status == status_key):
        return {"status": "ok", "detail": "no_change"}

    # Preserve the actual webhook status for this specific shipment's email
    email_status_key = status_key

    # Status ordering: only advance order-level status, never regress
    _STATUS_ORDER = {
        "label_created": 0, "in_transit": 1, "out_for_delivery": 2,
        "delivered": 3, "returned": 4, "failure": 5,
    }
    new_order = _STATUS_ORDER.get(status_key, 1)
    cur_order = _STATUS_ORDER.get(current_status or "", -1)

    # For split shipments, set order-level status to the minimum across all shipments
    order_level_status = status_key
    if shipment_row_id:
        scur2 = await db.execute(
            "SELECT tracking_status FROM order_shipments WHERE order_id = ?",
            (order_id,),
        )
        shipment_statuses = [r[0] for r in await scur2.fetchall() if r[0]]
        if shipment_statuses:
            min_status = min(shipment_statuses, key=lambda s: _STATUS_ORDER.get(s, 1))
            new_order = _STATUS_ORDER.get(min_status, 1)
            order_level_status = min_status

    if new_order > cur_order:
        await db.execute(
            "UPDATE ecommerce_orders SET tracking_status = ? WHERE id = ?",
            (order_level_status, order_id),
        )
    # If delivered, check if ALL shipments are delivered before marking order delivered
    if order_level_status == "delivered":
        all_delivered = True
        if shipment_row_id:
            dcur = await db.execute(
                "SELECT tracking_status FROM order_shipments WHERE order_id = ?",
                (order_id,),
            )
            for drow in await dcur.fetchall():
                if drow[0] != "delivered":
                    all_delivered = False
                    break
        if all_delivered:
            await db.execute(
                "UPDATE ecommerce_orders SET payment_status = 'delivered' WHERE id = ? AND payment_status = 'shipped'",
                (order_id,),
            )
    await db.commit()

    # Send email with the actual shipment status (not the aggregated order-level status)
    if customer_email and email_status_key in _STATUS_DISPLAY:
        smtp_settings = await _get_smtp_settings(db)
        asyncio.create_task(
            _send_tracking_email(
                smtp_settings, customer_email, first_name or "Customer",
                order_number or "", tracking_number,
                (shipment_tracking_url if shipment_row_id and shipment_tracking_url else tracking_url) or "",
                email_status_key,
            )
        )

    print(f"[tracking] Order {order_number}: {current_status} -> {order_level_status} (tracking: {tracking_number}, shipment status: {email_status_key})")
    return {"status": "ok", "order": order_number, "new_status": order_level_status}


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
