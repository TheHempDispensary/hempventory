"""Test the /active-sale endpoint."""
import pytest
import aiosqlite
from datetime import datetime
from zoneinfo import ZoneInfo

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import DB_PATH, init_db


@pytest.fixture
async def db():
    """Set up and tear down a test database."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    yield db
    # Clean up test data
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-SALE-%'")
    await db.commit()
    await db.close()


@pytest.mark.asyncio
async def test_active_sale_no_discounts(db):
    """When no active direct discounts exist, return active=False."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/ecommerce/active-sale")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False


@pytest.mark.asyncio
async def test_active_sale_with_discount(db):
    """When an active direct discount covers today, return it."""
    eastern = ZoneInfo("America/New_York")
    today = datetime.now(eastern).strftime("%Y-%m-%d")

    await db.execute(
        """INSERT INTO promo_codes
           (code, discount_pct, discount_amount, single_use, max_uses,
            expires_at, starts_at, applies_to, product_ids,
            exclude_from_other_coupons, clover_discount_id, is_direct_discount, excluded_brands)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TEST-SALE-1", 0.25, 0, 0, 0, today, today, "all", "", 0, "", 1, "LeafLife"),
    )
    await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/ecommerce/active-sale")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["discount_percent"] == 25.0
        assert data["excluded_brands"] == ["LeafLife"]
        assert data["start_date"] == today
        assert data["end_date"] == today

    # Clean up
    await db.execute("DELETE FROM promo_codes WHERE code = 'TEST-SALE-1'")
    await db.commit()


@pytest.mark.asyncio
async def test_active_sale_returns_highest(db):
    """When multiple active discounts overlap, return the highest discount_pct."""
    eastern = ZoneInfo("America/New_York")
    today = datetime.now(eastern).strftime("%Y-%m-%d")

    await db.execute(
        """INSERT INTO promo_codes
           (code, discount_pct, discount_amount, single_use, max_uses,
            expires_at, starts_at, applies_to, product_ids,
            exclude_from_other_coupons, clover_discount_id, is_direct_discount, excluded_brands)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TEST-SALE-2", 0.25, 0, 0, 0, today, today, "all", "", 0, "", 1, "LeafLife"),
    )
    await db.execute(
        """INSERT INTO promo_codes
           (code, discount_pct, discount_amount, single_use, max_uses,
            expires_at, starts_at, applies_to, product_ids,
            exclude_from_other_coupons, clover_discount_id, is_direct_discount, excluded_brands)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("TEST-SALE-3", 0.42, 0, 0, 0, today, today, "all", "", 0, "", 1, "LeafLife,Brand2"),
    )
    await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/ecommerce/active-sale")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["discount_percent"] == 42.0
        assert data["excluded_brands"] == ["LeafLife", "Brand2"]

    # Clean up
    await db.execute("DELETE FROM promo_codes WHERE code IN ('TEST-SALE-2', 'TEST-SALE-3')")
    await db.commit()
