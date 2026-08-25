"""Bud AI Chat Router — Claude-powered sales assistant with live inventory context."""

import os
import re
import time
import json
import asyncio
import html
from typing import Optional
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import aiosqlite
import anthropic

from app.database import get_db
from app.auth import get_current_user
from app.routers.alerts_router import send_service_alert_email

router = APIRouter(prefix="/api/chat", tags=["chat"])

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Model configuration ──────────────────────────────────────────────────
# Known-good Claude model identifiers supported by the pinned anthropic SDK.
# Used to warn on an unrecognized CLAUDE_MODEL and to pick a safe default.
_KNOWN_MODELS = {
    "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5",
    "claude-opus-4-5", "claude-sonnet-4-5", "claude-opus-4-1",
    "claude-opus-4-0", "claude-sonnet-4-0", "claude-3-haiku-20240307",
}
# Fast + capable + cost-effective default for customer-facing chat.
_DEFAULT_MODEL = "claude-sonnet-4-5"
# Cheaper/faster model tried automatically if the primary model call fails
# (e.g. a misconfigured/retired CLAUDE_MODEL) so Bud degrades instead of erroring.
_FALLBACK_MODEL = "claude-haiku-4-5"
CLAUDE_ALERT_INTERVAL = 6 * 60 * 60

_claude_consecutive_failures = 0
_claude_last_error: Optional[str] = None
_claude_last_failure_at: Optional[float] = None
_claude_last_success_at: Optional[float] = None
_claude_last_alert_at: Optional[float] = None


def _resolve_model() -> str:
    """Pick the chat model from CLAUDE_MODEL, defaulting safely.

    We trust an explicitly-configured value (ops may set a newer id than this
    SDK knows about) but log a note when it isn't recognized. If unset, use a
    fast, cost-effective default instead of a possibly-invalid hardcoded name.
    """
    configured = os.environ.get("CLAUDE_MODEL", "").strip()
    if not configured:
        return _DEFAULT_MODEL
    known = configured in _KNOWN_MODELS or any(
        configured.startswith(m + "-") for m in _KNOWN_MODELS
    )
    if not known:
        print(
            f"[chat] NOTE: CLAUDE_MODEL='{configured}' isn't in the known model list; "
            f"using it anyway (will fall back to '{_FALLBACK_MODEL}' if the call fails)."
        )
    return configured


MODEL = _resolve_model()

# ── Inventory context cache (reuses ecommerce product cache) ─────────────
_inventory_context: str = ""
_inventory_context_ts: float = 0.0
INVENTORY_CACHE_TTL = 300  # 5 minutes — keeps Bud's stock data fresh

# ── Recommendation context cache (bestsellers + active online sale) ──────
_recommendation_context: str = ""
_recommendation_context_ts: float = 0.0
RECOMMENDATION_CACHE_TTL = 1800  # 30 minutes

# Cap the conversation history sent to the model to bound cost/latency.
MAX_HISTORY_MESSAGES = 16

# ── Rate limiting (per client IP + session) ──────────────────────────────
_RATE_LIMIT_PER_MIN = 15
_RATE_LIMIT_PER_HOUR = 120
_rate_buckets: dict[str, list[float]] = {}


def _client_key(request: Optional[Request], session_id: str) -> str:
    """Best-effort client identity for rate limiting: real IP + session id.

    Behind Fly.io the real client IP is in Fly-Client-IP / X-Forwarded-For;
    fall back to the socket peer. Session id is client-provided so it can't be
    trusted alone — combining with IP keeps a single abuser from rotating ids.
    """
    ip = ""
    if request is not None:
        ip = (
            request.headers.get("fly-client-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "")
        )
    return f"{ip}|{session_id}"


def _check_rate_limit(request: Optional[Request], session_id: str) -> None:
    """Sliding-window limiter. Raises HTTP 429 when a client exceeds the cap.

    In-memory only: limits are per-process, so with multiple workers the
    effective ceiling scales with worker count. Fly runs this app as a single
    process, so it holds; document/tighten if that changes.
    """
    key = _client_key(request, session_id)
    now = time.time()
    times = [t for t in _rate_buckets.get(key, []) if now - t < 3600]
    last_min = sum(1 for t in times if now - t < 60)
    if last_min >= _RATE_LIMIT_PER_MIN or len(times) >= _RATE_LIMIT_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="You're sending messages a little too fast — give me a sec and try again!",
        )
    times.append(now)
    _rate_buckets[key] = times
    # Opportunistically prune empty/stale buckets so the dict can't grow forever.
    if len(_rate_buckets) > 5000:
        for k in [k for k, v in _rate_buckets.items() if not v or now - v[-1] > 3600]:
            _rate_buckets.pop(k, None)


SITE_BASE = "https://www.thehempdispensary.com"


def _build_inventory_summary(products: list[dict]) -> str:
    """Summarize products by category for Claude's system prompt, including direct links."""
    by_category: dict[str, list[dict]] = {}
    for p in products:
        if not p.get("available"):
            continue
        cats = p.get("categories", [])
        cat = cats[0] if cats else "Other"
        by_category.setdefault(cat, []).append(p)

    lines = []
    for cat in sorted(by_category.keys()):
        items = by_category[cat]
        lines.append(f"\n## {cat} ({len(items)} products)")
        for item in sorted(items, key=lambda x: x["name"]):
            price = f"${item['price'] / 100:.2f}" if item.get("price") else "Price TBD"
            west = item.get("stock_west", 0)
            east = item.get("stock_east", 0)
            hq = item.get("stock_hq", 0)
            slug = item.get("slug", "")
            url = f"{SITE_BASE}/products/product/{slug}" if slug else ""
            if item.get("shipping_only"):
                stock_note = "Ships from partner (1-3 days)"
            else:
                stock_note = f"West: {west}, East: {east}, HQ/Warehouse: {hq}"
            link_part = f" | Link: {url}" if url else ""
            lines.append(f"- {item['name']} | {price} | {stock_note}{link_part}")
    return "\n".join(lines)


async def _get_inventory_context() -> str:
    """Get cached inventory summary, refreshing from ecommerce cache if stale."""
    global _inventory_context, _inventory_context_ts
    now = time.time()
    if _inventory_context and (now - _inventory_context_ts) < INVENTORY_CACHE_TTL:
        return _inventory_context

    try:
        from app.routers.ecommerce_router import _get_cached_products
        data = await _get_cached_products()
        products = data.get("products", [])
        _inventory_context = _build_inventory_summary(products)
        _inventory_context_ts = now
        return _inventory_context
    except Exception as e:
        print(f"[chat] Failed to build inventory context: {e}")
        return _inventory_context or "(Inventory temporarily unavailable)"


# ── Page awareness ───────────────────────────────────────────────────────

_CANNABINOID_PAGE_LABELS = {
    "delta-8": "Delta-8 THC",
    "delta-9": "Delta-9 THC",
    "cbd": "CBD",
    "cbg": "CBG",
    "cbn": "CBN",
}


def _format_product_page_context(p: dict) -> str:
    """Concise CURRENT PAGE block for a specific product the customer is viewing."""
    name = p.get("online_name") or p.get("name") or "this product"
    price = f"${p['price'] / 100:.2f}" if p.get("price") else "Price TBD"
    cats = p.get("categories") or []
    cat = cats[0] if cats else "Other"
    slug = p.get("slug", "")
    url = f"{SITE_BASE}/products/product/{slug}" if slug else ""
    if p.get("shipping_only"):
        stock_note = "Ships from partner (1-3 days); NOT available for pickup or local delivery"
    else:
        stock_note = (
            f"West: {p.get('stock_west', 0)}, East: {p.get('stock_east', 0)}, "
            f"HQ/Warehouse: {p.get('stock_hq', 0)}"
        )
    lines = [
        "CURRENT PAGE — the customer is viewing THIS product right now. "
        "Prioritize answering about it and respect its fulfillment-specific stock:",
        f"- Name: {name}",
        f"- Price: {price}",
        f"- Category: {cat}",
        f"- Stock — {stock_note}",
    ]
    if url:
        lines.append(f"- Link: {url}")
    return "\n".join(lines)


def _get_page_context(page_url: str, products: list[dict]) -> str:
    """Turn the customer's current page URL into a concise context block.

    Product pages match a live product by slug; cannabinoid/category pages get
    a light label. Never fabricates product facts for unmatched URLs.
    """
    if not page_url:
        return ""
    try:
        path = (urlparse(page_url).path or "").rstrip("/")
    except Exception:
        return ""
    if not path:
        return ""

    m = re.search(r"/products/product/([^/]+)$", path)
    if m:
        slug = unquote(m.group(1)).lower()
        for p in products:
            if (p.get("slug") or "").lower() == slug:
                return _format_product_page_context(p)
        return (
            "CURRENT PAGE: The customer is on a product page, but it doesn't match "
            "current inventory. Ask what they're looking for rather than guessing."
        )

    m = re.search(r"/cannabinoids?/([a-z0-9-]+)$", path)
    if m and m.group(1) in _CANNABINOID_PAGE_LABELS:
        return (
            f"CURRENT PAGE: The customer is browsing the {_CANNABINOID_PAGE_LABELS[m.group(1)]} "
            "category. Tailor suggestions to that cannabinoid when relevant."
        )

    if path.endswith("/thca"):
        return "CURRENT PAGE: The customer is browsing THCA flower products."
    if path.endswith("/cart"):
        return "CURRENT PAGE: The customer is viewing their cart."
    if "/checkout" in path:
        return "CURRENT PAGE: The customer is on checkout — they're close to buying; be helpful and concise."
    if path.endswith("/products") or path.startswith("/products"):
        return "CURRENT PAGE: The customer is browsing the shop."
    return ""


# ── Smarter recommendations (bestsellers + active online sale) ───────────

async def _get_bestsellers(db: aiosqlite.Connection, limit: int = 12) -> list[str]:
    """Top-selling product names. Reuses the Smart PAR sales cache when fresh,
    otherwise falls back to a single cheap query over online orders."""
    try:
        from app.routers.inventory_router import _smart_par_cache, _SMART_PAR_TTL
        data = _smart_par_cache.get("data")
        if data and (time.time() - _smart_par_cache.get("updated_at", 0)) < _SMART_PAR_TTL:
            sales = data.get("sales_by_product", {})
            ranked = sorted(sales.items(), key=lambda kv: kv[1], reverse=True)
            names = [name.title() for name, qty in ranked if qty > 0][:limit]
            if names:
                return names
    except Exception as e:
        print(f"[chat] bestseller (smart par cache) lookup failed: {e}")

    try:
        cursor = await db.execute(
            """SELECT oi.product_name, SUM(oi.quantity) AS q
               FROM ecommerce_order_items oi
               JOIN ecommerce_orders eo ON oi.order_id = eo.id
               WHERE eo.status NOT IN ('cancelled', 'refunded')
                 AND oi.product_name IS NOT NULL AND oi.product_name != ''
               GROUP BY oi.product_name
               ORDER BY q DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows if row[0]]
    except Exception as e:
        print(f"[chat] bestseller (orders) lookup failed: {e}")
        return []


async def _get_active_online_sale(db: aiosqlite.Connection) -> str:
    """Return a note describing the active online Direct Discount, or ''.

    Excludes in-store-only discounts so Bud never advertises them online."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_eastern = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M")
    try:
        cursor = await db.execute(
            """SELECT discount_pct, excluded_brands, expires_at, applies_to
               FROM promo_codes
               WHERE is_direct_discount = 1
                 AND is_active = 1
                 AND (in_store_only = 0 OR in_store_only IS NULL)
                 AND (starts_at IS NULL OR starts_at = ''
                      OR (CASE WHEN LENGTH(starts_at) <= 10 THEN starts_at || 'T00:00' ELSE starts_at END) <= ?)
                 AND (expires_at IS NULL OR expires_at = ''
                      OR (CASE WHEN LENGTH(expires_at) <= 10 THEN expires_at || 'T23:59' ELSE expires_at END) >= ?)
               ORDER BY discount_pct DESC
               LIMIT 1""",
            (now_eastern, now_eastern),
        )
        row = await cursor.fetchone()
    except Exception as e:
        print(f"[chat] active sale lookup failed: {e}")
        return ""
    if not row:
        return ""
    pct = round(row[0] * 100)
    applies_to = row[3] if row[3] else "all"
    excluded = [b.strip() for b in (row[1] or "").split(",") if b.strip()]
    if applies_to == "specific":
        scope = "select products"
    elif excluded:
        scope = f"sitewide (excluding {', '.join(excluded)})"
    else:
        scope = "sitewide"
    end = row[2]
    window = f" (through {end})" if end else ""
    return (
        f"ACTIVE ONLINE SALE: {pct}% off {scope} right now{window}. "
        "This automatic online discount applies at checkout with no code needed — "
        "mention it when relevant. Never mention in-store-only discounts to online customers."
    )


async def _resolve_product_names(product_ids: list[str], limit: int = 4) -> list[str]:
    """Map Clover item ids to product names using the cached product list."""
    if not product_ids:
        return []
    try:
        from app.routers.ecommerce_router import _get_cached_products
        data = await _get_cached_products()
        by_id = {p.get("id"): (p.get("online_name") or p.get("name")) for p in data.get("products", [])}
    except Exception as e:
        print(f"[chat] product name lookup failed: {e}")
        return []
    names = [by_id[pid] for pid in product_ids if by_id.get(pid)]
    return names[:limit]


async def _get_active_coupon_codes(db: aiosqlite.Connection) -> str:
    """Return a note listing active online coupon codes customers can enter, or ''.

    Only codes safe to advertise: active, online, within their date window, not
    single-use or already exhausted. In-store-only codes stay hidden."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_eastern = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M")
    try:
        cursor = await db.execute(
            """SELECT code, discount_pct, discount_amount, expires_at, applies_to, product_ids
               FROM promo_codes
               WHERE (is_direct_discount = 0 OR is_direct_discount IS NULL)
                 AND is_active = 1
                 AND (in_store_only = 0 OR in_store_only IS NULL)
                 AND (single_use = 0 OR single_use IS NULL)
                 AND code IS NOT NULL AND code != '' AND UPPER(code) != 'FIRST10'
                 AND (max_uses = 0 OR max_uses IS NULL OR times_used < max_uses)
                 AND (starts_at IS NULL OR starts_at = ''
                      OR (CASE WHEN LENGTH(starts_at) <= 10 THEN starts_at || 'T00:00' ELSE starts_at END) <= ?)
                 AND (expires_at IS NULL OR expires_at = ''
                      OR (CASE WHEN LENGTH(expires_at) <= 10 THEN expires_at || 'T23:59' ELSE expires_at END) >= ?)
               ORDER BY discount_pct DESC, discount_amount DESC
               LIMIT 8""",
            (now_eastern, now_eastern),
        )
        rows = await cursor.fetchall()
    except Exception as e:
        print(f"[chat] active coupon lookup failed: {e}")
        return ""
    if not rows:
        return ""

    lines = []
    for code, pct, amount, expires_at, applies_to, product_ids in rows:
        if pct:
            value = f"{round(pct * 100)}% off"
        elif amount:
            value = f"${amount / 100:.2f} off"
        else:
            continue
        if applies_to == "specific":
            ids = [pid.strip() for pid in (product_ids or "").split(",") if pid.strip()]
            names = await _resolve_product_names(ids)
            scope = f"on {', '.join(names)}" if names else "on select products"
            if names and len(ids) > len(names):
                scope += " and more"
        else:
            scope = "sitewide"
        expiry = f", good through {expires_at}" if expires_at else ""
        lines.append(f"- {code}: {value} {scope}{expiry}")

    if not lines:
        return ""
    return (
        "ACTIVE COUPON CODES (customers enter these at checkout — share them when "
        "asked about coupons, codes, deals, promos, or savings):\n"
        + "\n".join(lines)
        + "\nOnly mention codes listed here; never invent a code or quote an expired one."
    )


async def _get_recommendation_context(db: aiosqlite.Connection) -> str:
    """Cached, concise bestseller + active-sale block for the system prompt."""
    global _recommendation_context, _recommendation_context_ts
    now = time.time()
    if _recommendation_context and (now - _recommendation_context_ts) < RECOMMENDATION_CACHE_TTL:
        return _recommendation_context

    parts: list[str] = []
    bestsellers = await _get_bestsellers(db)
    if bestsellers:
        parts.append(
            "TOP SELLERS (our most popular items — lean on these when a customer asks "
            "for popular/recommended picks, but only recommend ones in stock for their "
            "chosen fulfillment):\n"
            + "\n".join(f"- {n}" for n in bestsellers)
        )
    sale = await _get_active_online_sale(db)
    if sale:
        parts.append(sale)
    coupons = await _get_active_coupon_codes(db)
    if coupons:
        parts.append(coupons)

    _recommendation_context = "\n\n".join(parts)
    _recommendation_context_ts = now
    return _recommendation_context


ORDER_NUMBER_PATTERN = re.compile(r"\bHD-[0-9A-Fa-f]+-\d+\b")
LOYALTY_KEYWORDS = re.compile(r"\b(points?|rewards?|loyalty|redeem|balance)\b", re.IGNORECASE)


async def _lookup_order(order_number: str, db: aiosqlite.Connection) -> str:
    """Look up an order by order number and return a formatted summary for Bud."""
    cursor = await db.execute(
        """SELECT o.order_number, o.customer_first_name, o.customer_last_name,
                  o.subtotal, o.shipping_cost, o.tax, o.total,
                  o.fulfillment_type, o.shipping_service,
                  o.tracking_number, o.tracking_url, o.tracking_status,
                  o.payment_status, o.created_at, o.id
           FROM ecommerce_orders o
           WHERE o.order_number = ?
           LIMIT 1""",
        (order_number,),
    )
    row = await cursor.fetchone()
    if not row:
        return f"ORDER LOOKUP: No order found for {order_number}."

    cols = [desc[0] for desc in cursor.description]
    order = dict(zip(cols, row))
    order_id = order.pop("id")

    # Compute display status
    ps = (order.get("payment_status") or "pending").lower()
    ts = (order.get("tracking_status") or "").lower()
    if ps == "refunded":
        status = "Refunded"
    elif ps == "cancelled":
        status = "Cancelled"
    elif ts == "delivered" or ps == "delivered":
        status = "Delivered"
    elif ts == "out_for_delivery":
        status = "Out for Delivery"
    elif ts == "in_transit":
        status = "In Transit"
    elif ps == "shipped" or ts == "label_created":
        status = "Shipped"
    elif ps == "paid":
        status = "Confirmed (Processing)"
    else:
        status = "Pending"

    # Fulfillment label
    ft = order.get("fulfillment_type") or ""
    if ft == "pickup_west":
        fulfillment = "Pickup at West Store"
    elif ft == "pickup_east":
        fulfillment = "Pickup at East Store"
    elif ft == "local_delivery":
        fulfillment = "Local Delivery"
    else:
        fulfillment = "Shipping"

    # Items
    item_cursor = await db.execute(
        "SELECT product_name, quantity, price FROM ecommerce_order_items WHERE order_id = ?",
        (order_id,),
    )
    items = await item_cursor.fetchall()
    item_lines = [f"  - {r[0]} x{r[1]} (${r[2] / 100:.2f} each)" for r in items]

    tracking_number = order.get("tracking_number") or ""
    tracking_url = order.get("tracking_url") or ""
    total_cents = order.get("total") or 0

    lines = [
        f"ORDER LOOKUP RESULT for {order_number}:",
        f"  Status: {status}",
        f"  Fulfillment: {fulfillment}",
        f"  Order Date: {order.get('created_at', 'N/A')}",
        f"  Total: ${total_cents / 100:.2f}",
        f"  Items:",
    ]
    lines.extend(item_lines)
    if tracking_number:
        lines.append(f"  Tracking Number: {tracking_number}")
    if tracking_url:
        lines.append(f"  Tracking Link: {tracking_url}")
    if not tracking_number and status in ("Confirmed (Processing)", "Pending"):
        lines.append("  Tracking: Not yet available — order is still being processed.")

    return "\n".join(lines)


PHONE_PATTERN = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


async def _lookup_loyalty(identifier: str, db: aiosqlite.Connection) -> str:
    """Look up a customer's loyalty points by phone or email and return a formatted summary for Bud."""
    identifier = identifier.strip()
    is_email = "@" in identifier
    if is_email:
        param = identifier
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
               FROM loyalty_customers WHERE email = ? COLLATE NOCASE""",
            (param,),
        )
    else:
        # Strip non-digits for phone lookup
        param = re.sub(r"\D", "", identifier)
        cursor = await db.execute(
            """SELECT id, first_name, last_name, phone, email, points_balance, lifetime_points
               FROM loyalty_customers WHERE phone = ?""",
            (param,),
        )
    row = await cursor.fetchone()
    if not row:
        return f"LOYALTY LOOKUP RESULT: No loyalty account found for {'email' if is_email else 'phone'} {identifier}. The customer may need to create an account at checkout."

    cust_id, first_name, last_name, phone, email, points_balance, lifetime_points = row
    name = f"{first_name} {last_name}".strip() or "Customer"

    # Get available rewards
    rw_cursor = await db.execute(
        "SELECT name, points_required, reward_type, reward_value, description FROM loyalty_rewards WHERE is_active = 1 ORDER BY points_required ASC"
    )
    rewards = await rw_cursor.fetchall()

    lines = [
        f"LOYALTY LOOKUP RESULT for {name}:",
        f"  Points Balance: {points_balance} points",
        f"  Lifetime Points Earned: {lifetime_points}",
    ]

    if rewards:
        lines.append("  Available Rewards:")
        for r in rewards:
            rname, pts_required, rtype, rvalue, desc = r
            can_redeem = points_balance >= pts_required
            status = "ELIGIBLE" if can_redeem else f"need {pts_required - points_balance} more"
            if rtype == "percent_off":
                discount_desc = f"{int(rvalue)}% off"
            elif rtype == "fixed_amount":
                discount_desc = f"${rvalue:.2f} off"
            else:
                discount_desc = desc or rname
            lines.append(f"    - {rname}: {discount_desc} ({pts_required} pts) [{status}]")

    lines.append("  How to Apply: At checkout, enter phone/email in the Loyalty section, then select a reward to apply it to the order.")

    return "\n".join(lines)


SYSTEM_PROMPT = """You are Bud, the friendly and knowledgeable Virtual Budtender for The Hemp Dispensary (THD) in Spring Hill, Florida.

PERSONALITY:
- Warm, approachable, and genuinely helpful
- Knowledgeable about hemp/CBD products but never give medical advice
- Use casual but professional tone
- Keep responses concise (2-4 sentences typically)
- Use emojis sparingly and naturally

STORE INFO:
- Two locations in Spring Hill, FL:
  * West Store: 6175 Deltona Blvd, Suite 104, Spring Hill, FL 34606 — Call or text: 352-340-5860 — Hours: Daily 9am–10pm
  * East Store: 14312 Spring Hill Dr, Spring Hill, FL 34609 — Call or text: 352-515-5370 — Hours: Daily 7am–10pm
- Website: thehempdispensary.com
- Customer Service — Call or text: 352-842-6185 — Hours: Mon, Tue, Thu, Fri 7am–5pm (closed Wed, Sat, Sun)
- Customer Service email: Support@TheHempDispensary.com
- IMPORTANT: When a customer asks about anything that requires human help (job applications, complaints, order issues, returns, shipping questions, general inquiries, or anything you can't fully resolve), ALWAYS provide the customer service number (352-842-6185 — call or text) and email (Support@TheHempDispensary.com) along with the CS hours. Don't just give store location numbers — the CS line is the main contact for all non-in-store inquiries.
- If a customer contacts outside CS hours, let them know when the team will be back (e.g. "Our customer service team is available Mon, Tue, Thu, and Fri from 7am to 5pm — they'll get back to you on the next business day! You can also text 352-842-6185 or email Support@TheHempDispensary.com")
- Always give each location its own address and phone number separately — never combine them into one generic number

PROMOTIONS:
- First-time customers: use code FIRST10 for 10% off online orders
- Always mention this for new customers
- When a customer asks about coupons, promo codes, deals, sales, or ways to save, list every code in the ACTIVE COUPON CODES block below (with what it applies to) alongside FIRST10 — don't just point them to the newsletter
- Never make up a code, and never mention codes that aren't in that block

PRODUCT RULES:
- THCA flower products ordered online are shipped from our licensed out-of-state partner (1-3 business days)
- In-store pickup is available for most products at either location
- We DO offer local delivery within 30 miles of our Spring Hill store (ZIP 34608). Never tell a customer we don't offer delivery.
- Never make medical claims or say products treat/cure anything
- NEVER use the words "medicate", "medication", "dose", or "dosing" — use "enjoy", "experience", or "use" instead
- If asked about drug testing: "Hemp products may contain trace THC. We recommend consulting your employer's policy."

PRODUCT LINKS:
- Each product in the inventory below includes a "Link:" field with its direct URL on thehempdispensary.com
- ALWAYS include the direct product link when recommending or mentioning a product — this lets customers click directly to it
- Format links naturally in your response, e.g. "Check out Crunch Berries (28g) for $120: https://www.thehempdispensary.com/products/product/crunch-berries-everyday-28-grams"
- If a customer asks for links, always provide them — you have them in the inventory data below

FULFILLMENT & INVENTORY LOGIC:
The website has four fulfillment options customers can select:
- Pick Up at Spring Hill West (shows West Store stock)
- Pick Up at Spring Hill East (shows East Store stock)
- Ship To Me (shows HQ/warehouse stock only)
- Local Delivery (shows HQ/warehouse stock; delivered from our warehouse)

LOCAL DELIVERY DETAILS:
- Available within a 30-mile radius of our Spring Hill store (ZIP 34608)
- Delivery fee is $15, or just $5 for orders over $150
- Next-day delivery (about 42 hours)
- Customers select "Local Delivery" using the fulfillment selector at the top of the page and enter their address to confirm they're in the delivery area
- Local delivery uses HQ/Warehouse stock. LeafLife / partner-shipped THCA flower is NOT available for local delivery (those items ship only)

The inventory data below shows stock levels for each location: West, East, and HQ/Warehouse.

IMPORTANT: Before recommending any product, verify its stock is > 0 for the relevant fulfillment method:
- If recommending for shipping: check HQ/Warehouse stock > 0
- If recommending for local delivery: check HQ/Warehouse stock > 0
- If recommending for pickup West: check West stock > 0
- If recommending for pickup East: check East stock > 0
- Do NOT recommend products with 0 stock for the customer's fulfillment method

When a customer says a product shows out of stock:
1. Check all three inventory numbers — East Stock, West Stock, and HQ Stock.
2. If HQ is zero but East or West has stock, say:
   "It looks like that product isn't available for shipping from our warehouse right now, but we do have it in stock at our [East/West] store! You have two options: you can switch your fulfillment to in-store pickup at the top of the page and grab it there, OR you can contact our customer service team at 352-842-6185 and they'll arrange to have it picked up from our store and shipped directly to you."
3. If all three locations show zero, say:
   "It looks like that product is out of stock across all our locations right now. I'd recommend calling or texting our customer service at 352-842-6185 — they can let you know when it's expected back in and can hold one for you."
4. Always give the customer service number (352-842-6185) for any fulfillment issue — this is the number for shipping assistance, not the individual store lines.
5. When referencing how to switch fulfillment, tell customers: "You can change your pickup or shipping preference using the selector at the top of the page."

CURRENT INVENTORY:
{INVENTORY_CONTEXT}
{RECOMMENDATIONS}
{PAGE_CONTEXT}

IDENTITY RULES:
- You are "Bud" — a Virtual Budtender. NEVER refer to yourself as an "AI", "assistant", "bot", or "chatbot"
- If asked what you are, say you're a Virtual Budtender or just "Bud"
- Introduce yourself as: "Hey there! Welcome to The Hemp Dispensary! 👋 I'm Bud, your Virtual Budtender."

ORDER TRACKING:
- If a customer asks about their order status, tracking, or shipping, ask them for their order number (it starts with "HD-" and was included in their confirmation email).
- When the system provides an "ORDER LOOKUP RESULT" in your context, use that data to give the customer a helpful, friendly summary:
  * Tell them the current status of their order (e.g. "Your order is confirmed and being processed!", "Great news — your order has shipped!")
  * If there's a tracking link, share it with them so they can follow their package
  * If there's a tracking number but no link, share the tracking number
  * If the order is still being processed with no tracking yet, reassure them and give a timeline (most orders ship within 1-2 business days; THCA flower ships from a partner within 1-3 business days)
  * Share what items are in their order so they can verify
- If no order is found for the number they gave, let them know politely and suggest double-checking the number (it's in their confirmation email) or contacting customer service at 352-842-6185 or Support@TheHempDispensary.com.
- For complex shipping issues (lost packages, wrong items, damage), direct them to customer service at 352-842-6185 or Support@TheHempDispensary.com.
- Customers can also check their orders anytime from their account page (click the user icon in the header, sign in with email or phone).

LOYALTY POINTS:
- THD has a loyalty program where customers earn points on purchases and redeem them for discounts.
- If a customer asks about their points, rewards, loyalty balance, or how to apply/redeem points:
  * Ask them for their phone number or email (the one they used when signing up for loyalty).
  * When the system provides a "LOYALTY LOOKUP RESULT" in your context, use that data to tell them their balance, what rewards they've earned, and how to apply them.
  * To apply points at checkout: In the checkout page, there's a "Loyalty" section where they enter their phone or email. Once verified, they'll see their point balance and available rewards. They just select a reward and it applies the discount to their order.
  * Points cannot be combined with promo codes on the same order.
  * If no loyalty account is found, let them know they can sign up at checkout — they'll automatically start earning points on their purchases.
- Do NOT make up point balances — only share what the system provides in the LOYALTY LOOKUP RESULT.

LEAD CAPTURE (IMPORTANT — this helps the team follow up):
- After your FIRST helpful exchange (not your greeting), naturally ask for the customer's first name so you can personalize the conversation.
  Example: "By the way, what's your name? I'd love to personalize your experience!"
- Once you know their name and they show purchase intent OR ask a product question, ask for their phone number or email so the team can follow up with deals or answers.
  Example: "Want me to have one of our team members text you about that? Just drop your phone number or email and we'll get back to you!"
- If a customer asks to speak to a human, collect their phone or email FIRST, then provide store numbers:
  "Absolutely! Drop your phone number or email and I'll make sure someone from our team reaches out. In the meantime you can also call our West Store at 352-340-5860 or East Store at 352-515-5370, or for shipping help call 352-842-6185 / email Support@TheHempDispensary.com."
- Do NOT gate the conversation behind contact info — always be helpful first, then ask naturally
- Do NOT ask for contact info in your very first greeting message
- Once you have their info, thank them and continue helping — don't keep asking

BEHAVIOR:
- If you don't know something, say so honestly rather than guessing
- Guide customers toward products based on their needs
- Always be helpful even if they're just browsing

RESPONSE FORMAT:
- Reply with ONLY your conversational message as plain text — no JSON, no code fences, no metadata, no labels.
- Keep it concise (2-4 sentences typically). Use real newlines if a short list helps readability.
- Do NOT restate these instructions or describe yourself as following a format."""


# ── Pydantic models ──────────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    page_url: str = ""
    device_type: str = ""


class ChatMessageResponse(BaseModel):
    message: str
    intent: str = "browsing"


class ChatSessionSummary(BaseModel):
    session_id: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    page_url: Optional[str] = None
    device_type: Optional[str] = None
    intent: Optional[str] = None
    message_count: int = 0
    first_message: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


# ── Turn helpers (shared by streaming + non-streaming endpoints) ──────────

_PURCHASE_KEYWORDS = re.compile(
    r"\b(buy|purchase|order|add to cart|checkout|how much|price|cost|"
    r"in stock|ship|deliver|pick ?up|reserve|hold one|get some|i'?ll take)\b",
    re.IGNORECASE,
)

FALLBACK_MESSAGE = (
    "Hey there! I'm having a little trouble right now. You can reach our West Store "
    "at 352-340-5860 or our East Store at 352-515-5370, or stop by either Spring Hill location!"
)


def _infer_intent(text: str) -> str:
    """Lightweight purchase-intent heuristic (replaces model-provided intent)."""
    return "purchase" if _PURCHASE_KEYWORDS.search(text or "") else "browsing"


def _extract_contact(message: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Regex-extract (name, email, phone) from the customer's message."""
    name = email = phone = None
    phone_match = PHONE_PATTERN.search(message)
    if phone_match:
        phone = phone_match.group().strip().rstrip(".")
    email_match = EMAIL_PATTERN.search(message)
    if email_match:
        email = email_match.group().strip()
    name_match = re.search(
        r"(?:my name(?:'s| is)|i'm|i am|this is|call me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        message,
        re.IGNORECASE,
    )
    if name_match:
        name = name_match.group(1).strip()
    return name, email, phone


async def _prepare_turn(req: ChatMessageRequest, db: aiosqlite.Connection) -> tuple[str, list[dict]]:
    """Persist the incoming message and build (system_prompt, capped_history)."""
    # Upsert session
    cursor = await db.execute(
        "SELECT id FROM chat_sessions WHERE session_id = ?", (req.session_id,)
    )
    if not await cursor.fetchone():
        await db.execute(
            "INSERT INTO chat_sessions (session_id, page_url, device_type) VALUES (?, ?, ?)",
            (req.session_id, req.page_url, req.device_type),
        )
        await db.commit()

    # Store user message
    await db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'user', ?)",
        (req.session_id, req.message),
    )
    await db.commit()

    # Fetch conversation history, capped to the most recent turns to bound cost.
    cursor = await db.execute(
        "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
        (req.session_id,),
    )
    history_rows = await cursor.fetchall()
    messages = [{"role": row[0], "content": row[1]} for row in history_rows]
    messages = messages[-MAX_HISTORY_MESSAGES:]
    # Anthropic requires the first message to be from the user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    # Base system prompt: inventory + recommendations + page awareness.
    inventory_context = await _get_inventory_context()
    system = SYSTEM_PROMPT.replace("{INVENTORY_CONTEXT}", inventory_context)

    recommendations = await _get_recommendation_context(db)
    system = system.replace(
        "{RECOMMENDATIONS}", f"\n{recommendations}" if recommendations else ""
    )

    page_context = ""
    if req.page_url:
        try:
            from app.routers.ecommerce_router import _get_cached_products
            products = (await _get_cached_products()).get("products", [])
            page_context = _get_page_context(req.page_url, products)
        except Exception as e:
            print(f"[chat] page context failed: {e}")
    system = system.replace(
        "{PAGE_CONTEXT}", f"\n{page_context}" if page_context else ""
    )

    # Order-status lookups from current message + recent history.
    all_user_text = req.message
    for msg in messages[-6:]:
        if msg["role"] == "user":
            all_user_text += " " + msg["content"]
    order_matches = list(dict.fromkeys(ORDER_NUMBER_PATTERN.findall(all_user_text)))
    if order_matches:
        order_contexts = [await _lookup_order(on.upper(), db) for on in order_matches[:3]]
        if order_contexts:
            system += "\n\n" + "\n\n".join(order_contexts)

    # Loyalty lookups when the customer asks and we have a phone/email.
    if LOYALTY_KEYWORDS.search(all_user_text):
        phones = PHONE_PATTERN.findall(all_user_text)
        emails = EMAIL_PATTERN.findall(all_user_text)
        if not phones and not emails:
            sess_cursor = await db.execute(
                "SELECT customer_phone, customer_email FROM chat_sessions WHERE session_id = ?",
                (req.session_id,),
            )
            sess_row = await sess_cursor.fetchone()
            if sess_row:
                if sess_row[0]:
                    phones = [sess_row[0]]
                if sess_row[1]:
                    emails = [sess_row[1]]
        identifier = phones[0] if phones else (emails[0] if emails else None)
        if identifier:
            system += "\n\n" + await _lookup_loyalty(identifier, db)

    return system, messages


async def _note_claude_failure(db: aiosqlite.Connection, error: str) -> None:
    """Record a Claude outage and notify the team when the alert is due."""
    global _claude_consecutive_failures, _claude_last_error
    global _claude_last_failure_at, _claude_last_alert_at
    now = time.time()
    was_healthy = _claude_consecutive_failures == 0
    _claude_consecutive_failures += 1
    _claude_last_error = error
    _claude_last_failure_at = now
    alert_due = (
        was_healthy
        or _claude_last_alert_at is None
        or now - _claude_last_alert_at >= CLAUDE_ALERT_INTERVAL
    )
    if not alert_due:
        return

    subject = "Bud is offline"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #dc2626;">Bud is offline</h2>
        <p>Customers are currently seeing Bud's fallback reply because every Anthropic model call is failing.</p>
        <p><strong>Anthropic error:</strong> {html.escape(error)}</p>
    </div>
    """
    try:
        sent = await send_service_alert_email(db, subject, html_body)
        if sent:
            _claude_last_alert_at = now
    except Exception as e:
        print(f"[chat] Failed to send Bud outage alert: {e}")


async def _note_claude_success(db: aiosqlite.Connection) -> None:
    """Record a successful Claude call and notify after an outage recovers."""
    global _claude_consecutive_failures, _claude_last_success_at, _claude_last_alert_at
    had_failures = _claude_consecutive_failures > 0
    now = time.time()
    _claude_consecutive_failures = 0
    _claude_last_success_at = now
    if not had_failures:
        return

    try:
        sent = await send_service_alert_email(
            db,
            "Bud is back online",
            """
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #2d5016;">Bud is back online</h2>
                <p>Anthropic calls are succeeding again and Bud is no longer serving the fallback reply.</p>
            </div>
            """,
        )
        if sent:
            _claude_last_alert_at = now
    except Exception as e:
        print(f"[chat] Failed to send Bud recovery alert: {e}")


async def _call_claude(system: str, messages: list[dict], db: aiosqlite.Connection) -> str:
    """Non-blocking Claude call with automatic fallback to a cheaper model."""
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    last_error = ""
    for model in (MODEL, _FALLBACK_MODEL):
        try:
            response = await client.messages.create(
                model=model, max_tokens=1024, system=system, messages=messages,
            )
            response_text = response.content[0].text.strip()
            await _note_claude_success(db)
            return response_text
        except Exception as e:
            last_error = str(e)
            print(f"[chat] Claude API error (model={model}): {e}")
    await _note_claude_failure(db, last_error)
    return FALLBACK_MESSAGE


async def _finalize_turn(
    req: ChatMessageRequest, db: aiosqlite.Connection, assistant_message: str
) -> str:
    """Persist the reply, capture lead metadata, update session, notify on new leads."""
    intent = _infer_intent(req.message)
    customer_name, customer_email, customer_phone = _extract_contact(req.message)

    assistant_message = (assistant_message or "").replace("\\n", "\n").replace("\\t", " ").strip()
    if not assistant_message:
        assistant_message = FALLBACK_MESSAGE

    await db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'assistant', ?)",
        (req.session_id, assistant_message),
    )

    # Existing contact info (to detect new leads).
    prev_name = prev_email = prev_phone = None
    notif_cursor = await db.execute(
        "SELECT customer_name, customer_email, customer_phone FROM chat_sessions WHERE session_id = ?",
        (req.session_id,),
    )
    notif_row = await notif_cursor.fetchone()
    if notif_row:
        prev_name, prev_email, prev_phone = notif_row[0], notif_row[1], notif_row[2]

    updates = ["updated_at = CURRENT_TIMESTAMP", "intent = ?"]
    params: list = [intent]
    if customer_name:
        updates.append("customer_name = ?")
        params.append(customer_name)
    if customer_email:
        updates.append("customer_email = ?")
        params.append(customer_email)
    if customer_phone:
        updates.append("customer_phone = ?")
        params.append(customer_phone)
    if req.page_url:
        updates.append("page_url = ?")
        params.append(req.page_url)
    params.append(req.session_id)
    await db.execute(
        f"UPDATE chat_sessions SET {', '.join(updates)} WHERE session_id = ?", params
    )
    await db.commit()

    async def _first_msg() -> str:
        cur = await db.execute(
            "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user' ORDER BY created_at ASC LIMIT 1",
            (req.session_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else req.message

    if customer_email or customer_phone:
        new_contact = (
            (customer_email and customer_email != prev_email)
            or (customer_phone and customer_phone != prev_phone)
        )
        if new_contact:
            lead_name = customer_name or prev_name or "Anonymous"
            smtp_settings = await _get_chat_smtp_settings(db)
            asyncio.create_task(
                _send_lead_notification(
                    smtp_settings, lead_name, customer_email, customer_phone,
                    await _first_msg(), req.session_id, intent,
                )
            )
    elif customer_name and intent == "purchase" and not prev_name:
        smtp_settings = await _get_chat_smtp_settings(db)
        asyncio.create_task(
            _send_lead_notification(
                smtp_settings, customer_name, None, None,
                await _first_msg(), req.session_id, intent,
            )
        )

    return assistant_message


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    req: ChatMessageRequest,
    request: Request = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: send a message to Bud and get an AI response (JSON)."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Chat service not configured")
    _check_rate_limit(request, req.session_id)

    system, messages = await _prepare_turn(req, db)
    raw_text = await _call_claude(system, messages, db)
    assistant_message = await _finalize_turn(req, db, raw_text)
    return ChatMessageResponse(message=assistant_message, intent=_infer_intent(req.message))


@router.post("/message/stream")
async def send_message_stream(
    req: ChatMessageRequest,
    request: Request = None,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: stream Bud's reply token-by-token as Server-Sent Events.

    Emits `data: {"delta": "..."}` chunks, then a terminal `data: {"done": true}`.
    On any model error it streams the fallback message so the widget still renders.
    """
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Chat service not configured")
    _check_rate_limit(request, req.session_id)

    system, messages = await _prepare_turn(req, db)

    async def event_stream():
        collected: list[str] = []
        client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        streamed = False
        last_error = ""
        for model in (MODEL, _FALLBACK_MODEL):
            try:
                async with client.messages.stream(
                    model=model, max_tokens=1024, system=system, messages=messages,
                ) as stream:
                    async for text in stream.text_stream:
                        if text:
                            collected.append(text)
                            yield f"data: {json.dumps({'delta': text})}\n\n"
                streamed = True
                await _note_claude_success(db)
                break
            except Exception as e:
                last_error = str(e)
                print(f"[chat] Claude stream error (model={model}): {e}")
                collected = []  # discard partial output before trying fallback
                continue

        if not streamed:
            await _note_claude_failure(db, last_error)
            collected = [FALLBACK_MESSAGE]
            yield f"data: {json.dumps({'delta': FALLBACK_MESSAGE})}\n\n"

        try:
            await _finalize_turn(req, db, "".join(collected))
        except Exception as e:
            print(f"[chat] finalize (stream) failed: {e}")
        yield f"data: {json.dumps({'done': True, 'intent': _infer_intent(req.message)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Lead Notification Helpers ────────────────────────────────────────────

ADMIN_PANEL_URL = "https://inventory.thehempdispensary.com"
LEAD_NOTIFY_EMAIL = "Support@TheHempDispensary.com"


@router.get("/health")
async def get_chat_health(user: dict = Depends(get_current_user)):
    """Return Bud's current Claude service health state."""
    return {
        "consecutive_failures": _claude_consecutive_failures,
        "last_error": _claude_last_error,
        "last_failure_at": _claude_last_failure_at,
        "last_success_at": _claude_last_success_at,
        "healthy": _claude_consecutive_failures == 0,
    }


async def _get_chat_smtp_settings(db: aiosqlite.Connection) -> dict[str, str]:
    """Get SMTP settings from database, falling back to env vars."""
    smtp_settings: dict[str, str] = {}
    for key in ["smtp_host", "smtp_port", "smtp_user", "smtp_password"]:
        try:
            cursor = await db.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            if row and row[0]:
                smtp_settings[key] = row[0]
        except Exception:
            pass

    if not smtp_settings.get("smtp_host"):
        smtp_settings["smtp_host"] = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    if not smtp_settings.get("smtp_port"):
        smtp_settings["smtp_port"] = os.environ.get("SMTP_PORT", "587")
    if not smtp_settings.get("smtp_user"):
        smtp_settings["smtp_user"] = os.environ.get("SMTP_USER", "")
    if not smtp_settings.get("smtp_password"):
        smtp_settings["smtp_password"] = os.environ.get("SMTP_PASSWORD", "")
    return smtp_settings


async def _send_lead_notification(
    smtp_settings: dict[str, str],
    name: str,
    email: Optional[str],
    phone: Optional[str],
    first_message: str,
    session_id: str,
    intent: str,
) -> None:
    """Send an email notification to the support team when Bud captures a new lead."""
    import html as html_mod
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_user = smtp_settings.get("smtp_user", "")
    smtp_password = smtp_settings.get("smtp_password", "")
    if not smtp_user or not smtp_password:
        print("[chat] SMTP not configured, skipping lead notification")
        return

    # Escape user-controlled values to prevent HTML injection
    safe_name = html_mod.escape(name)
    safe_phone = html_mod.escape(phone) if phone else ""
    safe_email = html_mod.escape(email) if email else ""
    safe_message = html_mod.escape(first_message[:300])

    contact_line = ""
    if safe_phone:
        contact_line += f"<p><strong>Phone:</strong> {safe_phone}</p>"
    if safe_email:
        contact_line += f"<p><strong>Email:</strong> {safe_email}</p>"

    intent_label = "Looking to buy" if intent == "purchase" else "Browsing"

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #2d5016; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
            <h2 style="margin: 0;">New Lead from Bud 🌿</h2>
            <p style="margin: 5px 0 0; opacity: 0.9;">A customer shared their contact info</p>
        </div>
        <div style="border: 1px solid #e0e0e0; border-top: none; padding: 20px; border-radius: 0 0 8px 8px;">
            <h3 style="color: #2d5016; margin-top: 0;">Customer Info</h3>
            <p><strong>Name:</strong> {safe_name}</p>
            {contact_line}
            <p><strong>Intent:</strong> {intent_label}</p>

            <h3 style="color: #2d5016;">What they asked about</h3>
            <p style="background: #f5f5f5; padding: 12px; border-radius: 4px; font-style: italic;">
                &ldquo;{safe_message}&rdquo;
            </p>

            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #e0e0e0;">
                <p style="color: #666; font-size: 13px;">
                    View the full conversation in the
                    <a href="{ADMIN_PANEL_URL}" style="color: #2d5016;">Admin Panel &rarr; Conversations</a>
                </p>
            </div>
        </div>
    </div>
    """

    subject = f"New Lead from Bud: {name}"
    if phone:
        subject += f" ({phone})"

    smtp_host = smtp_settings.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(smtp_settings.get("smtp_port", "587"))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = LEAD_NOTIFY_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    def _do_send():
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [LEAD_NOTIFY_EMAIL], msg.as_string())

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _do_send)
        print(f"[chat] Lead notification sent for {name} ({phone or email})")
    except Exception as e:
        print(f"[chat] Failed to send lead notification: {e}")


@router.get("/sessions")
async def list_sessions(
    search: Optional[str] = None,
    intent: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin endpoint: list chat sessions with search/filter."""
    where_clauses = []
    params: list = []

    if search:
        where_clauses.append(
            "(cs.customer_name LIKE ? OR cs.customer_email LIKE ? OR cs.customer_phone LIKE ? OR cs.session_id LIKE ? OR EXISTS (SELECT 1 FROM chat_messages cm2 WHERE cm2.session_id = cs.session_id AND cm2.content LIKE ?))"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like, like])

    if intent:
        where_clauses.append("cs.intent = ?")
        params.append(intent)

    if date_from:
        where_clauses.append("cs.created_at >= ?")
        params.append(date_from)

    if date_to:
        where_clauses.append("cs.created_at <= ?")
        params.append(date_to + " 23:59:59")

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Count total
    count_cursor = await db.execute(
        f"SELECT COUNT(*) FROM chat_sessions cs {where_sql}", params
    )
    total = (await count_cursor.fetchone())[0]

    # Fetch sessions with first message and message count
    query = f"""
        SELECT cs.session_id, cs.customer_name, cs.customer_email, cs.customer_phone, cs.page_url,
               cs.device_type, cs.intent, cs.created_at, cs.updated_at,
               (SELECT COUNT(*) FROM chat_messages cm WHERE cm.session_id = cs.session_id) as msg_count,
               (SELECT cm.content FROM chat_messages cm WHERE cm.session_id = cs.session_id AND cm.role = 'user' ORDER BY cm.created_at ASC LIMIT 1) as first_msg
        FROM chat_sessions cs
        {where_sql}
        ORDER BY cs.updated_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()

    sessions = []
    for row in rows:
        sessions.append({
            "session_id": row[0],
            "customer_name": row[1],
            "customer_email": row[2],
            "customer_phone": row[3],
            "page_url": row[4],
            "device_type": row[5],
            "intent": row[6],
            "created_at": row[7],
            "updated_at": row[8],
            "message_count": row[9],
            "first_message": row[10],
        })

    return {"sessions": sessions, "total": total}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Admin endpoint: get full conversation transcript."""
    # Session info
    cursor = await db.execute(
        "SELECT session_id, customer_name, customer_email, customer_phone, page_url, device_type, intent, created_at, updated_at FROM chat_sessions WHERE session_id = ?",
        (session_id,),
    )
    session_row = await cursor.fetchone()
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found")

    # Messages
    cursor = await db.execute(
        "SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    )
    message_rows = await cursor.fetchall()

    return {
        "session": {
            "session_id": session_row[0],
            "customer_name": session_row[1],
            "customer_email": session_row[2],
            "customer_phone": session_row[3],
            "page_url": session_row[4],
            "device_type": session_row[5],
            "intent": session_row[6],
            "created_at": session_row[7],
            "updated_at": session_row[8],
        },
        "messages": [
            {"role": row[0], "content": row[1], "created_at": row[2]}
            for row in message_rows
        ],
    }
