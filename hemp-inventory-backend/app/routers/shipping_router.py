"""Shipping integration with Shippo for creating labels and tracking shipments."""

import os
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
    "street1": "6175 Deltona Blvd",
    "street2": "Ste 104",
    "city": "Spring Hill",
    "state": "FL",
    "zip": "34606",
    "country": "US",
    "phone": "352-340-2439",
    "email": "support@thehempdispensary.com",
}


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

    # Build parcel
    parcel = {
        "length": str(body.parcel_length),
        "width": str(body.parcel_width),
        "height": str(body.parcel_height),
        "distance_unit": body.parcel_distance_unit,
        "weight": str(body.parcel_weight),
        "mass_unit": body.parcel_mass_unit,
    }

    shipment_data = {
        "address_from": DEFAULT_FROM_ADDRESS,
        "address_to": to_address,
        "parcels": [parcel],
        "async": False,
    }

    headers = _get_shippo_headers()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SHIPPO_API_URL}/shipments/", headers=headers, json=shipment_data)
        if resp.status_code not in (200, 201):
            print(f"[shippo] Shipment creation failed: {resp.status_code} {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=f"Shippo error: {resp.text}")

        shipment = resp.json()

    # Extract rates
    rates = shipment.get("rates", [])
    formatted_rates = []
    for rate in rates:
        formatted_rates.append({
            "id": rate["object_id"],
            "provider": rate.get("provider", ""),
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
        "address_from": DEFAULT_FROM_ADDRESS,
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
                   payment_status = CASE WHEN payment_status IN ('paid', 'processing') THEN 'shipped' ELSE payment_status END
               WHERE id = ?""",
            (tracking_number, tracking_url, label_url, transaction.get("object_id", ""), body.order_id),
        )
        await db.commit()

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

    shipment_data = {
        "address_from": DEFAULT_FROM_ADDRESS,
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

    # Extract USPS rates only and add $2 markup
    rates = shipment.get("rates", [])
    formatted_rates = []
    for rate in rates:
        provider = rate.get("provider", "")
        if "USPS" not in provider.upper():
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
