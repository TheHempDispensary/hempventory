"""Bud's active coupon-code context block."""
import pytest
import aiosqlite
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.database import DB_PATH, init_db
from app.routers.chat_router import _get_active_coupon_codes

EASTERN = ZoneInfo("America/New_York")


def _day(offset: int) -> str:
    return (datetime.now(EASTERN) + timedelta(days=offset)).strftime("%Y-%m-%d")


async def _insert(db, code, **overrides):
    fields = {
        "discount_pct": 0.5,
        "discount_amount": 0,
        "single_use": 0,
        "is_active": 1,
        "max_uses": 0,
        "times_used": 0,
        "starts_at": _day(-1),
        "expires_at": _day(30),
        "applies_to": "all",
        "product_ids": "",
        "is_direct_discount": 0,
        "in_store_only": 0,
    }
    fields.update(overrides)
    cols = ", ".join(["code"] + list(fields))
    placeholders = ", ".join(["?"] * (len(fields) + 1))
    await db.execute(
        f"INSERT INTO promo_codes ({cols}) VALUES ({placeholders})",
        [code] + list(fields.values()),
    )
    await db.commit()


@pytest.fixture
async def db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-CPN-%'")
    await db.commit()
    yield db
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-CPN-%'")
    await db.commit()
    await db.close()


@pytest.mark.asyncio
async def test_lists_active_code(db):
    await _insert(db, "TEST-CPN-ACTIVE", discount_pct=0.75)
    note = await _get_active_coupon_codes(db)
    assert "TEST-CPN-ACTIVE: 75% off sitewide" in note


@pytest.mark.asyncio
async def test_fixed_amount_code(db):
    await _insert(db, "TEST-CPN-AMT", discount_pct=0, discount_amount=1500)
    note = await _get_active_coupon_codes(db)
    assert "TEST-CPN-AMT: $15.00 off sitewide" in note


@pytest.mark.asyncio
async def test_hides_unadvertisable_codes(db):
    await _insert(db, "TEST-CPN-INSTORE", in_store_only=1)
    await _insert(db, "TEST-CPN-INACTIVE", is_active=0)
    await _insert(db, "TEST-CPN-SINGLE", single_use=1)
    await _insert(db, "TEST-CPN-DIRECT", is_direct_discount=1)
    await _insert(db, "TEST-CPN-EXPIRED", starts_at=_day(-30), expires_at=_day(-1))
    await _insert(db, "TEST-CPN-FUTURE", starts_at=_day(5), expires_at=_day(30))
    await _insert(db, "TEST-CPN-MAXED", max_uses=2, times_used=2)
    note = await _get_active_coupon_codes(db)
    assert note == ""


@pytest.mark.asyncio
async def test_specific_scope_without_known_products(db):
    await _insert(db, "TEST-CPN-SPEC", applies_to="specific", product_ids="NOPE1,NOPE2")
    note = await _get_active_coupon_codes(db)
    assert "TEST-CPN-SPEC: 50% off on select products" in note
