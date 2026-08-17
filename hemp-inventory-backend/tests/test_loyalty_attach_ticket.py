"""Register tickets that land on nobody's account.

Clover builds a nameless customer profile from the swiped card when a budtender
doesn't pick the member on the ticket, so the sale matched nothing and the member
earned nothing. Staff can attach such a ticket to the member afterwards, and the
card is remembered so the next ticket lands on its own.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_attach_ticket_test.db"))

import aiosqlite
import pytest
import pytest_asyncio
from fastapi import HTTPException

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
    await conn.commit()
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


class FakeClover:
    customers: list = []
    orders: list = []

    def __init__(self, merchant_id, api_token):
        self.merchant_id = merchant_id

    async def get_customers(self, limit=100, offset=0):
        return {"elements": FakeClover.customers}

    async def get_orders(self, limit=100, offset=0, filters=None, filter_str=None, expand=""):
        return {"elements": FakeClover.orders[offset:offset + limit]}

    async def get_order(self, order_id, expand=""):
        return next(o for o in FakeClover.orders if o["id"] == order_id)


@pytest.fixture(autouse=True)
def fake_clover(monkeypatch):
    FakeClover.customers = []
    FakeClover.orders = []
    monkeypatch.setattr(lr, "CloverClient", FakeClover)
    return FakeClover


def _card_order(order_id, total, clover_customer_id, first6="427538", last4="5129", discount=None):
    """A ticket paid by card, sitting on Clover's nameless card profile."""
    order: dict = {
        "id": order_id,
        "total": total,
        "customers": {"elements": [{"id": clover_customer_id, "firstName": "", "lastName": ""}]},
        "payments": {
            "elements": [{"cardTransaction": {"first6": first6, "last4": last4}}]
        },
    }
    if discount:
        order["discounts"] = {"elements": [{"name": discount[0], "amount": -abs(discount[1])}]}
    return order


async def _add_member(db, first_name="Erianna", phone="3529423605", balance=0):
    cur = await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, points_balance, lifetime_points)
           VALUES (?, 'Fay', ?, ?, ?)""",
        (first_name, phone, balance, balance),
    )
    await db.commit()
    return cur.lastrowid


async def _balance(db, member_id):
    cur = await db.execute(
        "SELECT points_balance FROM loyalty_customers WHERE id = ?", (member_id,)
    )
    return (await cur.fetchone())[0]


async def test_card_only_ticket_is_listed_as_uncredited(db):
    await _add_member(db)
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]

    assert (await lr._do_sync_orders(db))["orders_no_match"] == 1

    listed = (await lr.list_unmatched_orders(days=14, limit=50, user={}, db=db))["orders"]
    assert [(o["clover_order_id"], o["order_total"], o["card_last4"]) for o in listed] == [
        ("O_CARD", 31.95, "5129")
    ]


async def test_uncredited_ticket_shows_the_name_the_register_used(db):
    # A register profile can carry a name but no phone, and the name on it need
    # not be the member's name in HempVentory ("Elena Gilbert" vs "Elena Regalado").
    await _add_member(db, first_name="Elena", phone="7278312785")
    FakeClover.customers = [
        {"id": "CLV_ELENA", "firstName": "Elena", "lastName": "Gilbert"}
    ]
    FakeClover.orders = [_card_order("O_NAMED", 1598, "CLV_ELENA")]

    await lr._do_sync_orders(db)

    listed = (await lr.list_unmatched_orders(days=14, limit=50, user={}, db=db))["orders"]
    assert [(o["clover_order_id"], o["register_name"]) for o in listed] == [
        ("O_NAMED", "Elena Gilbert")
    ]


async def test_attaching_a_ticket_awards_the_points(db):
    member_id = await _add_member(db)
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]
    await lr._do_sync_orders(db)

    result = await lr.attach_unmatched_order(
        "O_CARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
    )

    assert (result["points_awarded"], result["points_redeemed"]) == (31, 0)
    assert await _balance(db, member_id) == 31
    cur = await db.execute(
        "SELECT status, customer_id, points_awarded FROM loyalty_synced_orders WHERE clover_order_id = 'O_CARD'"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1, "attaching must update the ticket's row, not add one"
    assert tuple(rows[0]) == ("awarded", member_id, 31)


async def test_attaching_a_ticket_twice_does_not_double_the_points(db):
    member_id = await _add_member(db)
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]
    await lr._do_sync_orders(db)
    await lr.attach_unmatched_order(
        "O_CARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
    )

    with pytest.raises(HTTPException) as excinfo:
        await lr.attach_unmatched_order(
            "O_CARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
        )

    assert excinfo.value.status_code == 409
    assert await _balance(db, member_id) == 31


async def test_attached_ticket_is_not_awarded_again_by_the_next_sync(db):
    member_id = await _add_member(db)
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]
    await lr._do_sync_orders(db)
    await lr.attach_unmatched_order(
        "O_CARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
    )

    await lr._do_sync_orders(db)

    assert await _balance(db, member_id) == 31


async def test_the_next_ticket_on_a_known_card_is_credited_automatically(db):
    member_id = await _add_member(db)
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]
    await lr._do_sync_orders(db)
    await lr.attach_unmatched_order(
        "O_CARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
    )

    # Same card, a brand new nameless profile — nothing else ties it to the member.
    FakeClover.orders.append(_card_order("O_NEXT", 1000, "CLV_NAMELESS_2"))
    result = await lr._do_sync_orders(db)

    assert result["orders_processed"] == 1
    assert await _balance(db, member_id) == 41


async def test_a_different_card_is_still_uncredited(db):
    member_id = await _add_member(db)
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]
    await lr._do_sync_orders(db)
    await lr.attach_unmatched_order(
        "O_CARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
    )

    FakeClover.orders.append(
        _card_order("O_OTHER", 5000, "CLV_NAMELESS_3", first6="512345", last4="9876")
    )
    result = await lr._do_sync_orders(db)

    assert result["orders_no_match"] == 1
    assert await _balance(db, member_id) == 31


async def test_attaching_a_ticket_spends_the_reward_that_was_applied(db):
    member_id = await _add_member(db, balance=223)
    await db.execute(
        """INSERT INTO loyalty_rewards (name, reward_type, reward_value, points_required, is_active)
           VALUES ('$5 off any purchase', 'discount', 5.0, 100, 1)"""
    )
    await db.commit()
    FakeClover.orders = [
        _card_order("O_REWARD", 500, "CLV_NAMELESS", discount=("$5 OFF *SMOKEN TOKEN*", 500))
    ]
    await lr._do_sync_orders(db)

    result = await lr.attach_unmatched_order(
        "O_REWARD", lr.AttachOrderToMember(customer_id=member_id), user={}, db=db
    )

    assert (result["points_awarded"], result["points_redeemed"]) == (5, 100)
    assert await _balance(db, member_id) == 128


async def test_attaching_an_unknown_member_is_rejected(db):
    FakeClover.orders = [_card_order("O_CARD", 3195, "CLV_NAMELESS")]
    await lr._do_sync_orders(db)

    with pytest.raises(HTTPException) as excinfo:
        await lr.attach_unmatched_order(
            "O_CARD", lr.AttachOrderToMember(customer_id=999), user={}, db=db
        )
    assert excinfo.value.status_code == 404
