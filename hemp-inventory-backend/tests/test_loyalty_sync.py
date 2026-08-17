"""Loyalty stays in step with the register.

Three failure modes are locked in here: online signups that never reached Clover
(so the POS couldn't see the account), POS orders past the first page of 100
(silently never awarded), and purchases made before the account was linked
(recorded as `no_match` and never revisited).
"""
import os
import tempfile
import time

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
    order_filters: list = []

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
        FakeClover.order_filters.append(list(filters or []))
        return {"elements": FakeClover.orders[offset:offset + limit]}


@pytest.fixture(autouse=True)
def fake_clover(monkeypatch):
    FakeClover.created = []
    FakeClover.customers = []
    FakeClover.orders = []
    FakeClover.order_filters = []
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


async def _add_reward(db, name, value, points):
    cur = await db.execute(
        """INSERT INTO loyalty_rewards (name, reward_type, reward_value, points_required, is_active)
           VALUES (?, 'discount', ?, ?, 1)""",
        (name, value, points),
    )
    await db.commit()
    return cur.lastrowid


def _reward_discount(order, name, amount_cents):
    order["discounts"] = {"elements": [{"name": name, "amount": -abs(amount_cents)}]}
    return order


async def test_fully_discounted_ticket_still_takes_the_points_off(db):
    # A $5 reward on a $5 item leaves the ticket at $0, and skipping those orders
    # meant the member kept the points they had just spent.
    await _add_reward(db, "$5 off any purchase", 5.0, 100)
    customer_id = await _add_customer(db, "Kayla", "3865550101", clover_id="CLV_K")
    await db.execute(
        "UPDATE loyalty_customers SET points_balance = 223, lifetime_points = 223 WHERE id = ?",
        (customer_id,),
    )
    await db.commit()
    FakeClover.orders = [
        _reward_discount(_order("O_ZERO", 0, "CLV_K"), "$5 OFF *SMOKEN TOKEN*", 500)
    ]

    result = await lr._do_sync_orders(db)

    assert result["points_redeemed"] == 100
    cur = await db.execute(
        "SELECT points_balance, lifetime_redeemed FROM loyalty_customers WHERE id = ?",
        (customer_id,),
    )
    assert tuple(await cur.fetchone()) == (123, 100)


async def test_zero_total_order_recorded_before_the_fix_is_revisited(db):
    await _add_reward(db, "$5 off any purchase", 5.0, 100)
    customer_id = await _add_customer(db, "Kayla", "3865550101", clover_id="CLV_K")
    await db.execute(
        "UPDATE loyalty_customers SET points_balance = 223 WHERE id = ?", (customer_id,)
    )
    await db.execute(
        """INSERT INTO loyalty_synced_orders
           (clover_order_id, location_merchant_id, location_name, order_total, status)
           VALUES ('O_ZERO', 'MERCH_W', 'West', 0, 'zero_total')""",
    )
    await db.commit()
    FakeClover.orders = [
        _reward_discount(_order("O_ZERO", 0, "CLV_K"), "$5 OFF *SMOKEN TOKEN*", 500)
    ]

    assert (await lr._do_sync_orders(db))["points_redeemed"] == 100

    cur = await db.execute(
        "SELECT status, points_redeemed FROM loyalty_synced_orders WHERE clover_order_id = 'O_ZERO'"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1, "the retry must update the existing row"
    assert tuple(rows[0]) == ("zero_points", 100)


async def test_fully_discounted_ticket_redeems_only_once(db):
    await _add_reward(db, "$5 off any purchase", 5.0, 100)
    customer_id = await _add_customer(db, "Kayla", "3865550101", clover_id="CLV_K")
    await db.execute(
        "UPDATE loyalty_customers SET points_balance = 223 WHERE id = ?", (customer_id,)
    )
    await db.commit()
    FakeClover.orders = [
        _reward_discount(_order("O_ZERO", 0, "CLV_K"), "$5 OFF *SMOKEN TOKEN*", 500)
    ]

    await lr._do_sync_orders(db)
    await lr._do_sync_orders(db)
    await lr._do_sync_orders(db)

    cur = await db.execute(
        "SELECT points_balance, lifetime_redeemed FROM loyalty_customers WHERE id = ?",
        (customer_id,),
    )
    assert tuple(await cur.fetchone()) == (123, 100)


async def test_zero_total_order_without_discounts_is_still_skipped(db):
    await _add_customer(db, "Kayla", "3865550101", clover_id="CLV_K")
    FakeClover.orders = [_order("O_VOID", 0, "CLV_K")]

    result = await lr._do_sync_orders(db)

    assert (result["orders_processed"], result["points_redeemed"]) == (0, 0)
    cur = await db.execute(
        "SELECT status FROM loyalty_synced_orders WHERE clover_order_id = 'O_VOID'"
    )
    assert (await cur.fetchone())[0] == "zero_total"


async def test_unmatched_fully_discounted_ticket_is_recorded_for_retry(db):
    await _add_reward(db, "$5 off any purchase", 5.0, 100)
    FakeClover.orders = [
        _reward_discount(_order("O_ZERO", 0, "CLV_UNKNOWN"), "Rewards", 500)
    ]

    result = await lr._do_sync_orders(db)

    assert result["orders_no_match"] == 1
    cur = await db.execute(
        "SELECT status FROM loyalty_synced_orders WHERE clover_order_id = 'O_ZERO'"
    )
    assert (await cur.fetchone())[0] == "no_match"


async def test_awarded_order_is_never_awarded_twice(db):
    await _add_customer(db, "Ada", "3865550101", clover_id="CLV_A")
    FakeClover.orders = [_order("O1", 5000, "CLV_A")]

    await lr._do_sync_orders(db)
    await lr._do_sync_orders(db)

    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE clover_customer_id = 'CLV_A'")
    assert (await cur.fetchone())[0] == 50


async def test_name_match_ignores_staff_annotations(db):
    # Staff tag the loyalty record "Jaime Crews (Military)"; the register only has
    # "Jaime Crews", and that mismatch cost the member every in-store purchase.
    await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, points_balance, lifetime_points)
           VALUES ('Jaime', 'Crews (Military)', '', 0, 0)"""
    )
    await db.commit()
    FakeClover.customers = [{"id": "CLV_J", "firstName": "Jaime", "lastName": "Crews"}]
    FakeClover.orders = [_order("O_NAME", 2300, "CLV_J")]

    result = await lr._do_sync_orders(db)

    assert result["orders_processed"] == 1
    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE first_name = 'Jaime'")
    assert (await cur.fetchone())[0] == 23


async def test_order_matches_on_email_when_phone_and_name_differ(db):
    await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, email, points_balance, lifetime_points)
           VALUES ('Augusta', 'Byron', '3865550111', 'ada@example.com', 0, 0)"""
    )
    await db.commit()
    FakeClover.customers = [
        {"id": "CLV_E", "firstName": "Ada", "lastName": "Lovelace",
         "emailAddresses": {"elements": [{"emailAddress": "Ada@Example.com"}]}}
    ]
    FakeClover.orders = [_order("O_EMAIL", 1000, "CLV_E")]

    assert (await lr._do_sync_orders(db))["orders_processed"] == 1


async def test_lookback_window_can_be_widened(db):
    await _add_customer(db, "Ada", "3865550101", clover_id="CLV_A")
    FakeClover.orders = [_order("O1", 1000, "CLV_A")]

    result = await lr._do_sync_orders(db, lookback_days=180)

    assert result["lookback_days"] == 180
    start_ms = int(
        next(f for f in FakeClover.order_filters[0] if f.startswith("createdTime>=")).split(">=")[1]
    )
    days_back = (time.time() * 1000 - start_ms) / 86_400_000
    assert 179 < days_back < 181, "purchases older than the default window must be reachable"


async def test_push_covers_a_location_the_member_is_missing_from(db):
    # Linked at West only, so the East register still can't find them.
    await db.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES ('East', 'MERCH_E', 'tok')"
    )
    customer_id = await _add_customer(db, "Ada", "3865550101", clover_id="CLV_W")
    await db.execute(
        """INSERT INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id, location_name)
           VALUES (?, 'CLV_W', 'MERCH_W', 'West')""",
        (customer_id,),
    )
    await db.commit()

    result = await lr.push_customers_to_clover(limit=200, user={}, db=db)

    assert (result["pushed"], result["remaining"]) == (1, 0)
    cur = await db.execute(
        "SELECT merchant_id FROM loyalty_clover_id_map WHERE loyalty_customer_id = ? ORDER BY merchant_id",
        (customer_id,),
    )
    assert [r[0] for r in await cur.fetchall()] == ["MERCH_E", "MERCH_W"]


async def test_push_links_an_existing_clover_record_instead_of_duplicating(db):
    customer_id = await _add_customer(db, "Ada", "3865550101")
    FakeClover.customers = [
        {"id": "CLV_EXISTING", "firstName": "Ada", "lastName": "Tester",
         "phoneNumbers": {"elements": [{"phoneNumber": "(386) 555-0101"}]}}
    ]

    result = await lr.push_customers_to_clover(limit=200, user={}, db=db)

    assert result["pushed"] == 1
    assert FakeClover.created == [], "the register already knows this number"
    cur = await db.execute(
        "SELECT clover_customer_id FROM loyalty_clover_id_map WHERE loyalty_customer_id = ?",
        (customer_id,),
    )
    assert [r[0] for r in await cur.fetchall()] == ["CLV_EXISTING"]


async def test_lookup_matches_email_regardless_of_case(db):
    await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, email, points_balance, lifetime_points)
           VALUES ('Ada', 'Byron', '3865550101', 'ada@example.com', 10, 10)"""
    )
    await db.commit()

    result = await lr.lookup_customer(phone=None, email="Ada@Example.COM", db=db)
    assert result["found"] is True
    assert result["customer"]["first_name"] == "Ada"
