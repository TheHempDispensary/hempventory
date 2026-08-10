"""Loyalty stays in step with the register.

Three failure modes are locked in here: online signups that never reached Clover
(so the POS couldn't see the account), POS orders past the first page of 100
(silently never awarded), and purchases made before the account was linked
(recorded as `no_match` and never revisited).
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_loyalty_sync_test.db"))

import aiosqlite
import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
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
    await conn.execute(
        "INSERT OR REPLACE INTO loyalty_settings (key, value) VALUES ('points_per_dollar', '1')"
    )
    await conn.execute(
        "INSERT OR REPLACE INTO loyalty_settings (key, value) VALUES ('signup_bonus', '0')"
    )
    await conn.commit()
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


class FakeClover:
    """Stands in for CloverClient: records created customers, pages orders."""

    created: list = []
    customers: list = []
    orders: list = []

    def __init__(self, merchant_id, api_token):
        self.merchant_id = merchant_id

    async def create_customer(self, first_name, last_name="", phone="", email=""):
        FakeClover.created.append(
            {"first_name": first_name, "last_name": last_name, "phone": phone, "email": email}
        )
        return {"id": f"CLV{len(FakeClover.created)}"}

    async def get_customers(self, limit=100, offset=0):
        return {"elements": FakeClover.customers}

    async def get_orders(self, limit=100, offset=0, filters=None, filter_str=None, expand=""):
        assert any(f.startswith("createdTime>=") for f in (filters or [])), (
            "sync must bound the fetch by time so it can page safely"
        )
        return {"elements": FakeClover.orders[offset:offset + limit]}


@pytest.fixture(autouse=True)
def fake_clover(monkeypatch):
    FakeClover.created = []
    FakeClover.customers = []
    FakeClover.orders = []
    monkeypatch.setattr(lr, "CloverClient", FakeClover)
    return FakeClover


def _order(order_id, total, clover_customer_id=None):
    order: dict = {"id": order_id, "total": total}
    if clover_customer_id:
        order["customers"] = {"elements": [{"id": clover_customer_id}]}
    return order


async def _add_customer(db, first_name, phone, clover_id=""):
    cur = await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, clover_customer_id,
                                          points_balance, lifetime_points)
           VALUES (?, 'Tester', ?, ?, 0, 0)""",
        (first_name, phone, clover_id),
    )
    await db.commit()
    return cur.lastrowid


async def test_online_signup_is_created_in_clover(db):
    result = await lr._do_signup("(386) 555-0101", "Ada", "Byron", "ada@example.com", db)
    assert result["status"] == "created"

    assert FakeClover.created == [
        {"first_name": "Ada", "last_name": "Byron", "phone": "3865550101", "email": "ada@example.com"}
    ]
    cur = await db.execute("SELECT clover_customer_id, merchant_id FROM loyalty_clover_id_map")
    assert [tuple(r) for r in await cur.fetchall()] == [("CLV1", "MERCH_W")]


async def test_signup_survives_clover_failure(db):
    class Broken(FakeClover):
        async def create_customer(self, *a, **kw):
            raise RuntimeError("Clover down")

    lr.CloverClient = Broken
    try:
        result = await lr._do_signup("3865550102", "Grace", "Hopper", "g@example.com", db)
    finally:
        lr.CloverClient = FakeClover
    assert result["status"] == "created"


async def test_orders_beyond_the_first_page_are_awarded(db):
    await _add_customer(db, "Ada", "3865550101", clover_id="CLV_A")
    FakeClover.orders = [_order(f"O{i}", 1000) for i in range(120)]
    FakeClover.orders[115] = _order("O115", 2500, "CLV_A")

    result = await lr._do_sync_orders(db)

    assert result["orders_processed"] == 1
    assert result["points_awarded"] == 25
    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE clover_customer_id = 'CLV_A'")
    assert (await cur.fetchone())[0] == 25


async def test_no_match_order_is_awarded_once_the_account_exists(db):
    FakeClover.customers = [
        {"id": "CLV_B", "firstName": "Grace", "lastName": "Hopper",
         "phoneNumbers": {"elements": [{"phoneNumber": "(386) 555-0199"}]}}
    ]
    FakeClover.orders = [_order("O_LATE", 4000, "CLV_B")]

    first = await lr._do_sync_orders(db)
    assert first["orders_no_match"] == 1
    assert first["orders_processed"] == 0

    # The customer signs up after the purchase.
    customer_id = await _add_customer(db, "Grace", "3865550199")

    second = await lr._do_sync_orders(db)
    assert second["orders_processed"] == 1
    assert second["orders_late_matched"] == 1

    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    assert (await cur.fetchone())[0] == 40

    cur = await db.execute(
        "SELECT status, points_awarded FROM loyalty_synced_orders WHERE clover_order_id = 'O_LATE'"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1, "the retry must update the existing row, not add another"
    assert (rows[0][0], rows[0][1]) == ("awarded", 40)


async def test_awarded_order_is_never_awarded_twice(db):
    await _add_customer(db, "Ada", "3865550101", clover_id="CLV_A")
    FakeClover.orders = [_order("O1", 5000, "CLV_A")]

    await lr._do_sync_orders(db)
    await lr._do_sync_orders(db)

    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE clover_customer_id = 'CLV_A'")
    assert (await cur.fetchone())[0] == 50


async def test_lookup_matches_email_regardless_of_case(db):
    await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, email, points_balance, lifetime_points)
           VALUES ('Ada', 'Byron', '3865550101', 'ada@example.com', 10, 10)"""
    )
    await db.commit()

    result = await lr.lookup_customer(phone=None, email="Ada@Example.COM", db=db)
    assert result["found"] is True
    assert result["customer"]["first_name"] == "Ada"
