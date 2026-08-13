"""Staff can rename a sale/discount from the Discounts screen."""
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
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-RN%'")
    await db.commit()
    yield db
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'TEST-RN%'")
    await db.execute("DELETE FROM promo_codes WHERE code LIKE 'Test Rn%'")
    await db.commit()
    await db.close()


async def _insert(db, code, is_direct_discount=1):
    cursor = await db.execute(
        """INSERT INTO promo_codes (code, discount_pct, discount_amount, single_use, is_active,
           max_uses, applies_to, product_ids, is_direct_discount, sync_to_clover)
           VALUES (?, 0.1, 0, 0, 1, 0, 'all', '', ?, 0)""",
        (code, is_direct_discount),
    )
    await db.commit()
    return cursor.lastrowid


async def _update(promo_id, body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.put(f"/api/ecommerce/promos/{promo_id}", json=body)


async def _code(db, promo_id):
    cursor = await db.execute("SELECT code FROM promo_codes WHERE id = ?", (promo_id,))
    return (await cursor.fetchone())["code"]


@pytest.mark.asyncio
async def test_direct_discount_can_be_renamed(db):
    """A sale's display name is editable and keeps the capitalization typed."""
    promo_id = await _insert(db, "TEST-RN-OLD")

    resp = await _update(promo_id, {"code": "Test Rn Military Discount", "discount_pct": 0.2})

    assert resp.status_code == 200
    assert await _code(db, promo_id) == "Test Rn Military Discount"


@pytest.mark.asyncio
async def test_promo_code_rename_is_uppercased(db):
    """Promo codes are what a shopper types, so they stay uppercase."""
    promo_id = await _insert(db, "TEST-RNCODE", is_direct_discount=0)

    resp = await _update(promo_id, {"code": "test-rncode2", "discount_pct": 0.2})

    assert resp.status_code == 200
    assert await _code(db, promo_id) == "TEST-RNCODE2"


@pytest.mark.asyncio
async def test_rename_to_existing_name_is_rejected(db):
    """Two discounts can't share a name; the old name is kept."""
    await _insert(db, "TEST-RNTAKEN")
    promo_id = await _insert(db, "TEST-RNMINE")

    resp = await _update(promo_id, {"code": "TEST-RNTAKEN"})

    assert resp.status_code == 400
    assert "already in use" in resp.json()["detail"]
    assert await _code(db, promo_id) == "TEST-RNMINE"


@pytest.mark.asyncio
async def test_blank_name_is_rejected(db):
    """Clearing the name doesn't wipe the discount's identity."""
    promo_id = await _insert(db, "TEST-RNBLANK")

    resp = await _update(promo_id, {"code": "   "})

    assert resp.status_code == 400
    assert await _code(db, promo_id) == "TEST-RNBLANK"


@pytest.mark.asyncio
async def test_editing_other_fields_leaves_the_name_alone(db):
    """Saving an edit without touching the name is not a rename."""
    promo_id = await _insert(db, "TEST-RNKEEP")

    resp = await _update(promo_id, {"discount_pct": 0.35})

    assert resp.status_code == 200
    assert await _code(db, promo_id) == "TEST-RNKEEP"
    cursor = await db.execute("SELECT discount_pct FROM promo_codes WHERE id = ?", (promo_id,))
    assert (await cursor.fetchone())["discount_pct"] == 0.35


@pytest.mark.asyncio
async def test_rename_pushes_new_name_to_clover(db, monkeypatch):
    """A synced discount's new name reaches the registers."""
    from app.routers import ecommerce_router

    pushed = {}

    async def fake_update(db_, promo_id, kind, name, percentage, amount):
        pushed["name"] = name
        return []

    monkeypatch.setattr(ecommerce_router, "_update_discount_on_all_locations", fake_update)
    monkeypatch.setattr(
        ecommerce_router, "_get_all_location_clients", lambda db_: _empty_clients()
    )

    promo_id = await _insert(db, "TEST-RNSYNC")
    await db.execute("UPDATE promo_codes SET sync_to_clover = 1 WHERE id = ?", (promo_id,))
    await db.commit()

    resp = await _update(promo_id, {"code": "TEST-RNSYNC-NEW"})

    assert resp.status_code == 200
    assert "TEST-RNSYNC-NEW" in pushed["name"]


async def _empty_clients():
    return []
