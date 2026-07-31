import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_disc_uses_test.db"))

import aiosqlite
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import ecommerce_router as ec


@pytest_asyncio.fixture
async def db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def test_extract_applied_discount_codes_by_name_and_id():
    name_to_code = {"35% OFF MAGAZINE COUPON": "35% OFF MAGAZINE COUPON"}
    cdid_to_code = {"ZW0MW2MDJX8JA": "35% OFF MAGAZINE COUPON"}

    # order-level, matched by name
    o1 = {"discounts": {"elements": [{"name": "35% off magazine coupon", "percentage": 35}]}}
    assert ec._extract_applied_discount_codes(o1, {}, name_to_code) == {"35% OFF MAGAZINE COUPON"}

    # line-item, matched by discount definition id
    o2 = {"lineItems": {"elements": [
        {"discounts": {"elements": [{"name": "whatever", "discount": {"id": "ZW0MW2MDJX8JA"}}]}}
    ]}}
    assert ec._extract_applied_discount_codes(o2, cdid_to_code, {}) == {"35% OFF MAGAZINE COUPON"}

    # no matching discount
    o3 = {"discounts": {"elements": [{"name": "Some other discount"}]}}
    assert ec._extract_applied_discount_codes(o3, cdid_to_code, name_to_code) == set()


async def test_sync_clover_discount_uses_counts_instore(db, monkeypatch):
    await db.execute(
        "INSERT INTO promo_codes (code, discount_pct) VALUES (?, ?)",
        ("35% OFF MAGAZINE COUPON", 0.35),
    )
    await db.commit()

    east_orders = [
        {"id": "E1", "createdTime": 100, "discounts": {"elements": [{"name": "35% OFF MAGAZINE COUPON"}]}},
        {"id": "E2", "createdTime": 200, "discounts": {"elements": [{"name": "35% OFF MAGAZINE COUPON"}]}},
        {"id": "E3", "createdTime": 300, "discounts": {"elements": []}},
    ]

    class FakeClient:
        def __init__(self, orders):
            self._orders = orders

        async def get_orders(self, limit=100, offset=0, expand="", filters=None):
            return {"elements": self._orders if offset == 0 else []}

    async def fake_clients(_db):
        return [("EAST", "East", FakeClient(east_orders)), ("WEST", "West", FakeClient([]))]

    monkeypatch.setattr(ec, "_get_all_location_clients", fake_clients)

    result = await ec._sync_clover_discount_uses(db)
    assert result["recorded"] == 2

    cur = await db.execute(
        "SELECT COUNT(*) FROM clover_discount_uses WHERE discount_code = ?",
        ("35% OFF MAGAZINE COUPON",),
    )
    assert (await cur.fetchone())[0] == 2

    # idempotent: re-running records nothing new
    result2 = await ec._sync_clover_discount_uses(db)
    assert result2["recorded"] == 0
