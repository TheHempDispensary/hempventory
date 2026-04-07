"""Bud AI Chat Router — Claude-powered sales assistant with live inventory context."""

import os
import re
import time
import json
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
INVENTORY_CACHE_TTL = 1800  # 30 minutes


def _build_inventory_summary(products: list[dict]) -> str:
    """Summarize products by category for Claude's system prompt."""
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
            stock_note = ""
            if item.get("stock_west", 0) > 0 and item.get("stock_east", 0) > 0:
                stock_note = "In stock at both stores"
            elif item.get("stock_west", 0) > 0:
                stock_note = "In stock at West store"
            elif item.get("stock_east", 0) > 0:
                stock_note = "In stock at East store"
            if item.get("shipping_only"):
                stock_note = "Ships from partner (1-3 days)"
            elif item.get("stock_hq", 0) > 0 and not stock_note:
                stock_note = "Available for shipping"
            lines.append(f"- {item['name']} | {price} | {stock_note}")
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


SYSTEM_PROMPT = """You are Bud, the friendly and knowledgeable AI sales assistant for The Hemp Dispensary (THD) in Spring Hill, Florida.

PERSONALITY:
- Warm, approachable, and genuinely helpful
- Knowledgeable about hemp/CBD products but never give medical advice
- Use casual but professional tone
- Keep responses concise (2-4 sentences typically)
- Use emojis sparingly and naturally

STORE INFO:
- Two locations in Spring Hill, FL:
  * West Store: 1503 Deltona Blvd, Spring Hill, FL 34606
  * East Store: 7348 Spring Hill Dr, Spring Hill, FL 34606
- Phone: 352-842-6185
- Hours: Mon-Sat 10am-8pm, Sun 11am-6pm
- Website: thehempdispensary.com

PROMOTIONS:
- First-time customers: use code FIRST10 for 10% off online orders
- Always mention this for new customers

PRODUCT RULES:
- THCA flower products ordered online are shipped from our licensed out-of-state partner (1-3 business days)
- In-store pickup is available for most products at either location
- Never make medical claims or say products treat/cure anything
- If asked about drug testing: "Hemp products may contain trace THC. We recommend consulting your employer's policy."

CURRENT INVENTORY:
{INVENTORY_CONTEXT}

BEHAVIOR:
- If a customer seems interested in buying, naturally ask for their name so you can personalize the experience
- If they want to be contacted about deals, ask for their email
- Do NOT gate the conversation behind name/email — ask naturally when relevant
- If asked to speak to a human: "I'd love to connect you with our team! You can reach us at 352-842-6185 or stop by either Spring Hill location."
- If you don't know something, say so honestly rather than guessing
- Guide customers toward products based on their needs
- Always be helpful even if they're just browsing

RESPONSE FORMAT:
Always respond with valid JSON in this exact format:
{"message": "your response text here", "intent": "browsing", "customer_name": null, "customer_email": null}

- "intent" should be "purchase" if the customer is actively looking to buy, otherwise "browsing"
- "customer_name" should be the customer's name if they've shared it, otherwise null
- "customer_email" should be the customer's email if they've shared it, otherwise null
- ONLY include name/email when the customer explicitly provides them in their message"""


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

    # Call Claude
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        raw_text = response.content[0].text.strip()
    except Exception as e:
        print(f"[chat] Claude API error: {e}")
        # Fallback response
        raw_text = json.dumps({
            "message": "Hey there! I'm having a little trouble right now. You can reach our team at 352-842-6185 or stop by either Spring Hill location!",
            "intent": "browsing",
            "customer_name": None,
            "customer_email": None,
        })

    # Parse Claude's JSON response
    assistant_message = raw_text
    intent = "browsing"
    customer_name = None
    customer_email = None

    # Try to extract JSON from Claude's response (may be wrapped in markdown code fences)
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        # Strip markdown code fences
        lines = json_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        json_text = "\n".join(lines).strip()

    try:
        parsed = json.loads(json_text)
        assistant_message = parsed.get("message", raw_text)
        intent = parsed.get("intent", "browsing")
        customer_name = parsed.get("customer_name")
        customer_email = parsed.get("customer_email")
    except json.JSONDecodeError:
        # Claude didn't return valid JSON — try to find JSON object in the text
        json_match = re.search(r'\{[^{}]*"message"\s*:\s*"[^"]*"[^{}]*\}', raw_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                assistant_message = parsed.get("message", raw_text)
                intent = parsed.get("intent", "browsing")
                customer_name = parsed.get("customer_name")
                customer_email = parsed.get("customer_email")
            except json.JSONDecodeError:
                assistant_message = raw_text
        else:
            # Strip any trailing JSON-like fragments from plain text responses
            assistant_message = re.sub(r'[,"\s]*"intent"\s*:.*$', '', raw_text, flags=re.DOTALL).strip()

    # Clean up literal \n sequences that Claude sometimes includes
    assistant_message = assistant_message.replace("\\n", "\n").replace("\\t", " ")
    # Remove any stray JSON field remnants at end of message
    assistant_message = re.sub(r'[,"\s]*"(intent|customer_name|customer_email)"\s*:.*$', '', assistant_message, flags=re.DOTALL).strip()

    # Store assistant message
    await db.execute(
        "INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'assistant', ?)",
        (req.session_id, assistant_message),
    )

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
    if req.page_url:
        updates.append("page_url = ?")
        params.append(req.page_url)

    params.append(req.session_id)
    await db.execute(
        f"UPDATE chat_sessions SET {', '.join(updates)} WHERE session_id = ?",
        params,
    )
    await db.commit()

    return ChatMessageResponse(message=assistant_message, intent=intent)


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
            "(cs.customer_name LIKE ? OR cs.customer_email LIKE ? OR cs.session_id LIKE ? OR EXISTS (SELECT 1 FROM chat_messages cm2 WHERE cm2.session_id = cs.session_id AND cm2.content LIKE ?))"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])

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
        SELECT cs.session_id, cs.customer_name, cs.customer_email, cs.page_url,
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
            "page_url": row[3],
            "device_type": row[4],
            "intent": row[5],
            "created_at": row[6],
            "updated_at": row[7],
            "message_count": row[8],
            "first_message": row[9],
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
        "SELECT session_id, customer_name, customer_email, page_url, device_type, intent, created_at, updated_at FROM chat_sessions WHERE session_id = ?",
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
            "page_url": session_row[3],
            "device_type": session_row[4],
            "intent": session_row[5],
            "created_at": session_row[6],
            "updated_at": session_row[7],
        },
        "messages": [
            {"role": row[0], "content": row[1], "created_at": row[2]}
            for row in message_rows
        ],
    }
