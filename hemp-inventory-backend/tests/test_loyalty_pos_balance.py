"""The register must show the balance HempVentory keeps.

Clover Rewards counts points per merchant — East and West disagree with each
other and with HempVentory, and the Platform API can't write that counter — so
the member's real cross-store balance is written to their Clover customer note,
which is what a budtender reads at the POS. These tests pin that the note is
written at every location the member is linked to, refreshed whenever the
balance moves, and that a register Clover can't reach never costs the member
their points.
"""
import json
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_pos_balance_test.db"))

import aiosqlite
import httpx
import pytest
import pytest_asyncio

from app import clover_client
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
        "INSERT INTO locations (name, merchant_id, api_token) VALUES ('East', 'MERCH_E', 'tok')"
    )
    await conn.execute(
        "INSERT OR REPLACE INTO loyalty_settings (key, value) VALUES ('points_per_dollar', '1')"
    )
    await conn.execute(
        "INSERT OR REPLACE INTO loyalty_settings (key, value) VALUES ('program_name', 'Hemp Rewards')"
    )
    await conn.commit()
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


class FakeClover:
    notes: list = []
    orders: list = []
    customers: list = []

    def __init__(self, merchant_id, api_token):
        self.merchant_id = merchant_id

    async def update_customer_note(self, customer_id, note):
        FakeClover.notes.append(
            {"merchant_id": self.merchant_id, "customer_id": customer_id, "note": note}
        )
        return {}

    async def get_customers(self, limit=100, offset=0):
        return {"elements": FakeClover.customers}

    async def get_orders(self, limit=100, offset=0, filters=None, filter_str=None, expand=""):
        # Each register only reports its own sales.
        if self.merchant_id != "MERCH_W":
            return {"elements": []}
        return {"elements": FakeClover.orders[offset:offset + limit]}


@pytest.fixture(autouse=True)
def fake_clover(monkeypatch):
    FakeClover.notes = []
    FakeClover.orders = []
    FakeClover.customers = []
    monkeypatch.setattr(lr, "CloverClient", FakeClover)
    return FakeClover


async def _member(db, balance=0, first_name="Jaime", phone="3865550101"):
    cur = await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, points_balance, lifetime_points)
           VALUES (?, 'Crews', ?, ?, ?)""",
        (first_name, phone, balance, balance),
    )
    customer_id = cur.lastrowid
    for merchant_id, loc_name, clover_id in (
        ("MERCH_W", "West", f"CLV_W{customer_id}"),
        ("MERCH_E", "East", f"CLV_E{customer_id}"),
    ):
        await db.execute(
            """INSERT INTO loyalty_clover_id_map
               (loyalty_customer_id, clover_customer_id, merchant_id, location_name)
               VALUES (?, ?, ?, ?)""",
            (customer_id, clover_id, merchant_id, loc_name),
        )
    await db.commit()
    return customer_id


async def test_balance_is_written_to_every_register(db):
    customer_id = await _member(db, balance=4012)

    updated = await lr._sync_balance_to_clover(db, customer_id)

    assert sorted(updated) == ["East", "West"]
    assert sorted(n["merchant_id"] for n in FakeClover.notes) == ["MERCH_E", "MERCH_W"]
    # Both stores must be told the same number — that's the point of the sync.
    assert len({n["note"] for n in FakeClover.notes}) == 1
    note = FakeClover.notes[0]["note"]
    assert "4,012 points" in note
    assert "Hemp Rewards" in note


async def test_award_refreshes_the_pos_balance(db):
    customer_id = await _member(db, balance=100)

    await lr.award_points(
        customer_id, lr.AwardPoints(points=50, description="Test"), user={}, db=db
    )

    assert [n["merchant_id"] for n in FakeClover.notes] == ["MERCH_W", "MERCH_E"]
    assert "150 points" in FakeClover.notes[0]["note"]


async def test_redeeming_lowers_the_number_the_register_shows(db):
    customer_id = await _member(db, balance=500)
    cur = await db.execute(
        """INSERT INTO loyalty_rewards (name, points_required, reward_type, reward_value, is_active)
           VALUES ('$5 off', 100, 'discount', 5.0, 1)"""
    )
    reward_id = cur.lastrowid
    await db.commit()

    await lr.redeem_reward(customer_id, lr.RedeemReward(reward_id=reward_id), user={}, db=db)

    assert "400 points" in FakeClover.notes[-1]["note"]


async def test_pos_purchase_pushes_the_new_balance_once(db):
    customer_id = await _member(db, balance=0)
    clover_id = f"CLV_W{customer_id}"
    FakeClover.customers = [{"id": clover_id, "firstName": "Jaime", "lastName": "Crews"}]
    FakeClover.orders = [
        {"id": "O1", "total": 2500, "customers": {"elements": [{"id": clover_id}]}},
        {"id": "O2", "total": 1000, "customers": {"elements": [{"id": clover_id}]}},
    ]

    result = await lr._do_sync_orders(db)

    assert result["orders_processed"] == 2
    assert result["balances_pushed"] == 1, "one push per member, not per order"
    # The note goes to both registers so the number matches wherever they shop.
    assert sorted(n["merchant_id"] for n in FakeClover.notes) == ["MERCH_E", "MERCH_W"]
    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    assert (await cur.fetchone())[0] == 35
    assert "35 points" in FakeClover.notes[-1]["note"]


async def test_unreachable_register_does_not_lose_the_points(db):
    customer_id = await _member(db, balance=10)

    class Broken(FakeClover):
        async def update_customer_note(self, customer_id, note):
            raise RuntimeError("Clover down")

    lr.CloverClient = Broken
    try:
        result = await lr.award_points(
            customer_id, lr.AwardPoints(points=40, description="Test"), user={}, db=db
        )
    finally:
        lr.CloverClient = FakeClover

    assert result["points_balance"] == 50
    cur = await db.execute("SELECT points_balance FROM loyalty_customers WHERE id = ?", (customer_id,))
    assert (await cur.fetchone())[0] == 50


async def test_push_balances_endpoint_covers_linked_members(db):
    await _member(db, balance=10, first_name="Jaime", phone="3865550101")
    await _member(db, balance=20, first_name="Ada", phone="3865550102")
    # A member the register has never heard of can't be updated and isn't counted.
    await db.execute(
        """INSERT INTO loyalty_customers (first_name, last_name, phone, points_balance, lifetime_points)
           VALUES ('Grace', 'Hopper', '3865550103', 5, 5)"""
    )
    await db.commit()

    result = await lr.push_balances_to_clover(limit=500, user={}, db=db)

    assert (result["members_considered"], result["members_updated"], result["members_failed"]) == (2, 2, 0)
    assert len(FakeClover.notes) == 4  # two members × two registers


@pytest.mark.asyncio
async def test_update_customer_note_uses_the_metadata_endpoint(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json={})

    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(clover_client.httpx, "AsyncClient", fake_client)

    await clover_client.CloverClient("M1", "token").update_customer_note("CUST1", "500 points")

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/merchants/M1/customers/CUST1/metadata")
    assert seen["body"] == {"note": "500 points"}
