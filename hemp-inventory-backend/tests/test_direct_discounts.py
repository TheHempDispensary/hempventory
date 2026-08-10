"""Direct discounts apply automatically and never work as promo codes."""
import pytest
import aiosqlite

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import DB_PATH, init_db


@pytest.fixture
async def db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-DD-%'")
    await db.commit()
    yield db
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-DD-%'")
    await db.commit()
    await db.close()


async def _insert(db, code, **overrides):
    fields = {
        "discount_pct": 0.5,
        "discount_amount": 0,
        "single_use": 0,
        "is_active": 1,
        "max_uses": 0,
        "times_used": 0,
        "starts_at": None,
        "expires_at": None,
        "applies_to": "all",
        "product_ids": "",
        "exclude_from_other_coupons": 0,
        "clover_discount_id": "",
        "is_direct_discount": 1,
        "excluded_brands": "",
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


async def _get(path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _validate(code):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/ecommerce/validate-promo",
            json={"promo_code": code, "email": "shopper@example.com"},
        )


@pytest.mark.asyncio
async def test_direct_discount_without_dates_is_active(db):
    """A direct discount created with no start/end date runs until deactivated."""
    await _insert(db, "TEST-DD-NODATES", discount_pct=0.75)

    data = (await _get("/api/ecommerce/active-sale")).json()
    assert data["active"] is True
    assert data["discount_percent"] == 75.0
    assert [s["name"] for s in data["sales"]] == ["TEST-DD-NODATES"]


@pytest.mark.asyncio
async def test_all_overlapping_direct_discounts_returned(db):
    """Product-specific direct discounts coexist; each keeps its own scope."""
    await _insert(db, "TEST-DD-CBD", discount_pct=0.75, applies_to="specific", product_ids="AAA,BBB")
    await _insert(db, "TEST-DD-CBG", discount_pct=0.5, applies_to="specific", product_ids="CCC")

    data = (await _get("/api/ecommerce/active-sale")).json()
    sales = {s["name"]: s for s in data["sales"]}
    assert sales["TEST-DD-CBD"]["product_ids"] == ["AAA", "BBB"]
    assert sales["TEST-DD-CBG"]["discount_percent"] == 50.0
    assert data["promos_disabled"] is False


@pytest.mark.asyncio
async def test_direct_discount_name_is_not_a_promo_code(db):
    """The internal name of a direct discount must not validate as a code."""
    await _insert(db, "TEST-DD-NAMED", discount_pct=0.75, applies_to="specific", product_ids="AAA")

    data = (await _validate("TEST-DD-NAMED")).json()
    assert data["valid"] is False
    assert data["reason"] == "Invalid promo code"


@pytest.mark.asyncio
async def test_sitewide_sale_disables_promo_codes(db):
    """Only a sitewide direct discount blocks promo codes."""
    await _insert(db, "TEST-DD-SITEWIDE", discount_pct=0.3, applies_to="all")
    await _insert(db, "TEST-DD-CODE", discount_pct=0.1, is_direct_discount=0)

    sale = (await _get("/api/ecommerce/active-sale")).json()
    assert sale["promos_disabled"] is True

    data = (await _validate("TEST-DD-CODE")).json()
    assert data["valid"] is False
    assert "30% OFF sale" in data["reason"]


@pytest.mark.asyncio
async def test_select_items_sale_keeps_promo_codes_working(db):
    """A sale on select items leaves the rest of the catalog code-eligible."""
    await _insert(db, "TEST-DD-SELECT", discount_pct=0.75, applies_to="specific", product_ids="AAA")
    await _insert(db, "TEST-DD-CODE2", discount_pct=0.1, is_direct_discount=0)

    data = (await _validate("TEST-DD-CODE2")).json()
    assert data["valid"] is True
    assert data["discount_pct"] == 0.1
