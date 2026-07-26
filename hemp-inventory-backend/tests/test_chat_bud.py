"""Tests for Bud chat router helpers: page awareness, recommendations,
model resolution, intent/contact extraction, and rate limiting."""
import importlib
import os

import pytest
import aiosqlite
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.database import DB_PATH, init_db
from app.routers import chat_router as cr


@pytest.fixture
async def db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    yield db
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-BUD-%'")
    await db.commit()
    await db.close()


PRODUCTS = [
    {
        "name": "Crunch Berries Everyday 28 Grams",
        "online_name": "Crunch Berries (28g)",
        "slug": "crunch-berries-everyday-28-grams",
        "price": 12000,
        "categories": ["Flower"],
        "stock_west": 5, "stock_east": 0, "stock_hq": 2,
        "shipping_only": False,
    },
    {
        "name": "LeafLife Trippy White 3.5g",
        "slug": "leaflife-trippy-white-3-5g",
        "price": 4000,
        "categories": ["Flower"],
        "stock_west": 0, "stock_east": 0, "stock_hq": 0,
        "shipping_only": True,
    },
]


# ── Page awareness ────────────────────────────────────────────────────────

def test_page_context_matches_product():
    ctx = cr._get_page_context(
        "https://www.thehempdispensary.com/products/product/crunch-berries-everyday-28-grams",
        PRODUCTS,
    )
    assert "CURRENT PAGE" in ctx
    assert "Crunch Berries (28g)" in ctx
    assert "$120.00" in ctx
    assert "West: 5" in ctx


def test_page_context_shipping_only_product():
    ctx = cr._get_page_context(
        "https://www.thehempdispensary.com/products/product/leaflife-trippy-white-3-5g",
        PRODUCTS,
    )
    assert "Ships from partner" in ctx
    assert "NOT available for pickup or local delivery" in ctx


def test_page_context_unknown_product_does_not_fabricate():
    ctx = cr._get_page_context(
        "https://www.thehempdispensary.com/products/product/does-not-exist",
        PRODUCTS,
    )
    assert "doesn't match" in ctx
    assert "$" not in ctx  # no fabricated price/details


def test_page_context_category_and_empty():
    assert "THCA" in cr._get_page_context("https://x/thca", PRODUCTS)
    assert "Delta-8" in cr._get_page_context("https://x/cannabinoids/delta-8", PRODUCTS)
    assert cr._get_page_context("", PRODUCTS) == ""
    assert cr._get_page_context("not a url", PRODUCTS) == ""


# ── Intent + contact extraction ──────────────────────────────────────────

def test_infer_intent():
    assert cr._infer_intent("how much is the crunch berries?") == "purchase"
    assert cr._infer_intent("I want to buy some gummies") == "purchase"
    assert cr._infer_intent("what is CBG?") == "browsing"


def test_extract_contact():
    name, email, phone = cr._extract_contact(
        "Hi my name is Jane, email jane@example.com or call 352-555-1212"
    )
    assert name == "Jane"
    assert email == "jane@example.com"
    assert phone == "352-555-1212"


# ── Model resolution ──────────────────────────────────────────────────────

def test_resolve_model_default_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    assert cr._resolve_model() == cr._DEFAULT_MODEL


def test_resolve_model_respects_configured(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5")
    assert cr._resolve_model() == "claude-haiku-4-5"
    # Unknown ids are still respected (ops may know a newer model than the SDK).
    monkeypatch.setenv("CLAUDE_MODEL", "claude-future-99")
    assert cr._resolve_model() == "claude-future-99"


# ── Rate limiting ─────────────────────────────────────────────────────────

def test_rate_limit_triggers(monkeypatch):
    cr._rate_buckets.clear()
    monkeypatch.setattr(cr, "_RATE_LIMIT_PER_MIN", 3)
    sid = "test-bud-rl"
    for _ in range(3):
        cr._check_rate_limit(None, sid)  # request=None -> key is "|<sid>"
    with pytest.raises(HTTPException) as exc:
        cr._check_rate_limit(None, sid)
    assert exc.value.status_code == 429
    cr._rate_buckets.clear()


# ── Active online sale excludes in-store-only ─────────────────────────────

@pytest.mark.asyncio
async def test_active_online_sale_excludes_in_store_only(db):
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    await db.execute(
        """INSERT INTO promo_codes
           (code, discount_pct, discount_amount, single_use, max_uses,
            expires_at, starts_at, applies_to, product_ids,
            exclude_from_other_coupons, clover_discount_id, is_direct_discount,
            excluded_brands, in_store_only, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TEST-BUD-INSTORE", 0.30, 0, 0, 0, today, today, "all", "", 0, "", 1, "", 1, 1),
    )
    await db.commit()
    assert await cr._get_active_online_sale(db) == ""

    await db.execute(
        """INSERT INTO promo_codes
           (code, discount_pct, discount_amount, single_use, max_uses,
            expires_at, starts_at, applies_to, product_ids,
            exclude_from_other_coupons, clover_discount_id, is_direct_discount,
            excluded_brands, in_store_only, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TEST-BUD-ONLINE", 0.20, 0, 0, 0, today, today, "all", "", 0, "", 1, "", 0, 1),
    )
    await db.commit()
    note = await cr._get_active_online_sale(db)
    assert "20% off sitewide" in note
    assert "in-store-only" in note.lower()
