"""Bud AI Chat Router — Claude-powered sales assistant with live inventory context."""

import os
import re
import time
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import aiosqlite
import anthropic

from app.database import get_db
from app.auth import get_current_user

router = APIRouter(prefix="/api/chat", tags=["chat"])

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

# ── Inventory context cache (reuses ecommerce product cache) ─────────────
_inventory_context: str = ""
_inventory_context_ts: float = 0.0
INVENTORY_CACHE_TTL = 300  # 5 minutes — keeps Bud's stock data fresh


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


ORDER_NUMBER_PATTERN = re.compile(r"\bHD-[0-9A-Fa-f]+-\d+\b")


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


SYSTEM_PROMPT = """You are Bud, the friendly and knowledgeable Virtual Budtender for The Hemp Dispensary (THD) in Spring Hill, Florida.

PERSONALITY:
- Warm, approachable, and genuinely helpful
- Knowledgeable about hemp/CBD products but never give medical advice
- Use casual but professional tone
- Keep responses concise (2-4 sentences typically)
- Use emojis sparingly and naturally

STORE INFO:
- Two locations in Spring Hill, FL:
  * West Store: 6175 Deltona Blvd, Suite 104, Spring Hill, FL 34606 — Phone: 352-340-5860 — Hours: Daily 9am–10pm
  * East Store: 14312 Spring Hill Dr, Spring Hill, FL 34609 — Phone: 352-515-5370 — Hours: Daily 7am–10pm
- Website: thehempdispensary.com
- Shipping Orders / Customer Service: 352-842-6185 / Support@TheHempDispensary.com
- Always give each location its own address and phone number separately — never combine them into one generic number

PROMOTIONS:
- First-time customers: use code FIRST10 for 10% off online orders
- Always mention this for new customers

PRODUCT RULES:
- THCA flower products ordered online are shipped from our licensed out-of-state partner (1-3 business days)
- In-store pickup is available for most products at either location
- Never make medical claims or say products treat/cure anything
- NEVER use the words "medicate", "medication", "dose", or "dosing" — use "enjoy", "experience", or "use" instead
- If asked about drug testing: "Hemp products may contain trace THC. We recommend consulting your employer's policy."

PRODUCT LINKS:
- Each product in the inventory below includes a "Link:" field with its direct URL on thehempdispensary.com
- ALWAYS include the direct product link when recommending or mentioning a product — this lets customers click directly to it
- Format links naturally in your response, e.g. "Check out Crunch Berries (28g) for $120: https://www.thehempdispensary.com/products/product/crunch-berries-everyday-28-grams"
- If a customer asks for links, always provide them — you have them in the inventory data below

FULFILLMENT & INVENTORY LOGIC:
The website has three fulfillment options customers can select:
- Pick Up at Spring Hill West (shows West Store stock)
- Pick Up at Spring Hill East (shows East Store stock)
- Ship To Me (shows HQ/warehouse stock only)

The inventory data below shows stock levels for each location: West, East, and HQ/Warehouse.

IMPORTANT: Before recommending any product, verify its stock is > 0 for the relevant fulfillment method:
- If recommending for shipping: check HQ/Warehouse stock > 0
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
You MUST respond with ONLY a valid JSON object — no text before or after it.
Use this exact structure:
{"message": "your response text here", "intent": "browsing", "customer_name": null, "customer_email": null, "customer_phone": null}

Rules:
- The "message" field must contain ONLY your conversational response as plain text. Do NOT put JSON, code, or metadata inside the message field.
- Use actual newlines within the message string (not literal backslash-n). Keep the message as a single readable paragraph when possible.
- "intent" should be "purchase" if the customer is actively looking to buy, otherwise "browsing"
- "customer_name" should be the customer's name if they've shared it, otherwise null
- "customer_email" should be the customer's email if they've shared it, otherwise null
- "customer_phone" should be the customer's phone number if they've shared it, otherwise null
- ONLY include name/email/phone when the customer explicitly provides them in their message
- Do NOT wrap the JSON in markdown code fences or add any explanation outside the JSON object"""


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


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    req: ChatMessageRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Public endpoint: send a message to Bud and get an AI response."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="Chat service not configured")

    # Upsert session
    cursor = await db.execute(
        "SELECT id FROM chat_sessions WHERE session_id = ?", (req.session_id,)
    )
    session_row = await cursor.fetchone()
    if not session_row:
        await db.execute(
            """INSERT INTO chat_sessions (session_id, page_url, device_type)
               VALUES (?, ?, ?)""",
            (req.session_id, req.page_url, req.device_type),
        )
        await db.commit()

    # Store user message
    await db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'user', ?)",
        (req.session_id, req.message),
    )
    await db.commit()

    # Fetch conversation history for context
    cursor = await db.execute(
        "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
        (req.session_id,),
    )
    history_rows = await cursor.fetchall()
    messages = [{"role": row[0], "content": row[1]} for row in history_rows]

    # Build system prompt with live inventory
    inventory_context = await _get_inventory_context()
    system = SYSTEM_PROMPT.replace("{INVENTORY_CONTEXT}", inventory_context)

    # Check conversation for order numbers — look them up and inject context
    # Scan current message + recent history so follow-up questions still have order data
    all_user_text = req.message
    for msg in messages[-6:]:
        if msg["role"] == "user":
            all_user_text += " " + msg["content"]
    order_matches = list(dict.fromkeys(ORDER_NUMBER_PATTERN.findall(all_user_text)))
    if order_matches:
        order_contexts = []
        for on in order_matches[:3]:
            order_info = await _lookup_order(on.upper(), db)
            order_contexts.append(order_info)
        if order_contexts:
            system += "\n\n" + "\n\n".join(order_contexts)

    # Call Claude
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=messages,
        )
        raw_text = response.content[0].text.strip()
    except Exception as e:
        print(f"[chat] Claude API error: {e}")
        # Fallback response
        raw_text = json.dumps({
            "message": "Hey there! I'm having a little trouble right now. You can reach our West Store at 352-340-5860 or our East Store at 352-515-5370, or stop by either Spring Hill location!",
            "intent": "browsing",
            "customer_name": None,
            "customer_email": None,
        })

    # Parse Claude's JSON response
    assistant_message = raw_text
    intent = "browsing"
    customer_name = None
    customer_email = None
    customer_phone = None

    # Try to extract JSON from Claude's response (may be wrapped in markdown code fences)
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        lines = json_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        json_text = "\n".join(lines).strip()

    # Attempt 1: direct JSON parse
    parsed = None
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        pass

    # Attempt 2: find the outermost JSON object using brace matching
    if parsed is None:
        start = raw_text.find("{")
        if start != -1:
            depth = 0
            end = start
            for i in range(start, len(raw_text)):
                if raw_text[i] == "{":
                    depth += 1
                elif raw_text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            try:
                parsed = json.loads(raw_text[start:end])
            except json.JSONDecodeError:
                pass

    if parsed and isinstance(parsed, dict) and "message" in parsed:
        assistant_message = parsed["message"]
        intent = parsed.get("intent", "browsing")
        customer_name = parsed.get("customer_name")
        customer_email = parsed.get("customer_email")
        customer_phone = parsed.get("customer_phone")
    else:
        # Claude returned plain text — strip any trailing JSON fragments
        # Common failure: Claude writes the message as plain text, then appends a JSON object
        # e.g. "Hello!\n{\"message\": \"Hello!\"..." — strip the JSON suffix
        json_suffix = re.search(r'\s*\{\s*"message"\s*:', raw_text)
        if json_suffix:
            assistant_message = raw_text[:json_suffix.start()].strip()
        else:
            assistant_message = re.sub(r'[,"\s]*"(intent|customer_name|customer_email|customer_phone)"\s*:.*$', '', raw_text, flags=re.DOTALL).strip()

    # Clean up literal backslash-n sequences that Claude sometimes embeds
    assistant_message = assistant_message.replace("\\n", "\n").replace("\\t", " ")

    # Store assistant message
    await db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'assistant', ?)",
        (req.session_id, assistant_message),
    )

    # Check existing contact info BEFORE updating (to detect new leads)
    prev_email = None
    prev_phone = None
    if customer_name and (customer_email or customer_phone):
        notif_cursor = await db.execute(
            "SELECT customer_email, customer_phone FROM chat_sessions WHERE session_id = ?",
            (req.session_id,),
        )
        notif_row = await notif_cursor.fetchone()
        if notif_row:
            prev_email = notif_row[0]
            prev_phone = notif_row[1]

    # Update session metadata
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params: list = []
    if intent:
        updates.append("intent = ?")
        params.append(intent)
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
        f"UPDATE chat_sessions SET {', '.join(updates)} WHERE session_id = ?",
        params,
    )
    await db.commit()

    # Send lead notification if new contact info was captured this turn
    if customer_name and (customer_email or customer_phone):
        new_contact = (
            (customer_email and customer_email != prev_email) or
            (customer_phone and customer_phone != prev_phone)
        )
        if new_contact:
            smtp_settings = await _get_chat_smtp_settings(db)
            first_msg = req.message
            # Get the customer's first message for context
            first_msg_cursor = await db.execute(
                "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'user' ORDER BY created_at ASC LIMIT 1",
                (req.session_id,),
            )
            first_msg_row = await first_msg_cursor.fetchone()
            if first_msg_row:
                first_msg = first_msg_row[0]
            asyncio.create_task(
                _send_lead_notification(
                    smtp_settings, customer_name, customer_email, customer_phone,
                    first_msg, req.session_id, intent
                )
            )

    return ChatMessageResponse(message=assistant_message, intent=intent)


# ── Lead Notification Helpers ────────────────────────────────────────────

ADMIN_PANEL_URL = "https://inventory.thehempdispensary.com"
LEAD_NOTIFY_EMAIL = "Support@TheHempDispensary.com"


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

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, [LEAD_NOTIFY_EMAIL], msg.as_string())
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
