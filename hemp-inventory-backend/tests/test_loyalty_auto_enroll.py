"""An online shopper with no loyalty account is enrolled instead of ignored.

Previously the award step bailed out ("no loyalty account found"), so a first-time
online customer earned nothing and never appeared at the register.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_loyalty_enroll_test.db"))

import aiosqlite
import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import ecommerce_router as er
from app.routers import loyalty_router as lr


@pytest_asyncio.fixture
async def db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES ('West', 'MERCH_W', 'tok')"
    )
    for key, value in (("points_per_dollar", "1"), ("signup_bonus", "25")):
        await conn.execute(
            "INSERT OR REPLACE INTO loyalty_settings (key, value) VALUES (?, ?)", (key, value)
        )
    await conn.commit()
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


class FakeClover:
    created: list = []

    def __init__(self, merchant_id, api_token):
        pass

    async def create_customer(self, first_name, last_name="", phone="", email=""):
        FakeClover.created.append({"phone": phone, "email": email})
        return {"id": f"CLV{len(FakeClover.created)}"}


@pytest.fixture(autouse=True)
def fake_clover(monkeypatch):
    FakeClover.created = []
    monkeypatch.setattr(lr, "CloverClient", FakeClover)
    return FakeClover


def _order(email="new@example.com", phone="(386) 555-0123", price=4000):
    return er.CreateOrderRequest(
        customer=er.OrderCustomer(first_name="Ada", last_name="Byron", email=email, phone=phone),
        shipping_address=er.OrderShipping(),
        items=[er.OrderItem(product_id="p1", name="Flower", price=price, quantity=1)],
    )


async def test_first_time_online_shopper_is_enrolled_and_earns_points(db):
    await er._award_loyalty_points_for_order(_order(), "THD-1001", 1)

    cur = await db.execute(
        "SELECT id, points_balance, clover_customer_id FROM loyalty_customers WHERE phone = '3865550123'"
    )
    row = await cur.fetchone()
    assert row is not None, "the shopper should have been enrolled"
    # 25 sign-up bonus + 40 for the $40 order
    assert row[1] == 65
    assert row[2] == "CLV1", "the new member must also exist in Clover"

    cur = await db.execute(
        "SELECT type, points FROM loyalty_transactions WHERE customer_id = ? ORDER BY id", (row[0],)
    )
    assert [(r[0], r[1]) for r in await cur.fetchall()] == [("earn", 25), ("earn", 40)]


async def test_existing_member_is_not_duplicated(db):
    await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, email, points_balance, lifetime_points)
           VALUES ('Ada', 'Byron', '3865550123', 'ADA@example.com', 5, 5)"""
    )
    await db.commit()

    await er._award_loyalty_points_for_order(_order(email="ada@example.com"), "THD-1002", 2)

    cur = await db.execute("SELECT COUNT(*), SUM(points_balance) FROM loyalty_customers")
    count, balance = await cur.fetchone()
    assert count == 1
    assert balance == 45  # 5 existing + 40 earned, no sign-up bonus
    assert FakeClover.created == []


async def test_shopper_without_a_usable_phone_is_skipped(db):
    await er._award_loyalty_points_for_order(_order(phone=""), "THD-1003", 3)

    cur = await db.execute("SELECT COUNT(*) FROM loyalty_customers")
    assert (await cur.fetchone())[0] == 0
