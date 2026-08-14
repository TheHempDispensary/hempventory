"""Points must come off when a budtender applies a reward discount at the register.

Staff had no way to redeem on the Clover ticket: they applied the "Rewards"
discount, the customer walked out with the money off, and the balance stayed put.
"""
import ast
import inspect
import os
import tempfile

import pytest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "test_reg_redeem.db"))

import aiosqlite

from app import clover_client
from app.clover_client import CloverClient
from app.routers.loyalty_router import (
    push_reward_discounts,
    _order_discounts,
    _redeem_pos_discounts,
    _redemption_discount_names,
    _reward_for_discount,
)

REWARDS = [
    {"id": 1, "name": "$5 off any purchase", "points_required": 100, "reward_value": 5.0},
    {"id": 3, "name": "$15 off any purchase", "points_required": 250, "reward_value": 15.0},
]
NAMES = _redemption_discount_names({})


def _order(discounts=None, line_discounts=None):
    order = {}
    if discounts is not None:
        order["discounts"] = {"elements": discounts}
    if line_discounts is not None:
        order["lineItems"] = {"elements": [{"discounts": {"elements": line_discounts}}]}
    return order


@pytest.fixture
async def db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        conn = await aiosqlite.connect(tmp.name)
        await conn.execute(
            """CREATE TABLE loyalty_customers (
                   id INTEGER PRIMARY KEY, points_balance INTEGER DEFAULT 0,
                   lifetime_redeemed INTEGER DEFAULT 0, updated_at TIMESTAMP)"""
        )
        await conn.execute(
            """CREATE TABLE loyalty_transactions (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, type TEXT,
                   points INTEGER, description TEXT, order_id TEXT, location_name TEXT)"""
        )
        await conn.execute(
            """CREATE TABLE loyalty_redemptions (
                   id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER,
                   reward_id INTEGER, points_spent INTEGER, location_name TEXT)"""
        )
        await conn.execute(
            "INSERT INTO loyalty_customers (id, points_balance) VALUES (863, 2311)"
        )
        await conn.commit()
        yield conn
        await conn.close()


async def _balance(db, customer_id=863):
    cursor = await db.execute(
        "SELECT points_balance, lifetime_redeemed FROM loyalty_customers WHERE id = ?",
        (customer_id,),
    )
    return await cursor.fetchone()


def test_clovers_rewards_button_maps_to_the_five_dollar_reward():
    reward = _reward_for_discount({"name": "Rewards", "amount": -500}, REWARDS, NAMES)
    assert reward["id"] == 1


def test_fifteen_dollar_register_button_maps_to_its_reward():
    reward = _reward_for_discount(
        {"name": "Rewards $15 off (250 pts)", "amount": -1500}, REWARDS, NAMES
    )
    assert reward["id"] == 3


def test_store_specific_reward_name_still_counts():
    reward = _reward_for_discount(
        {"name": "$5 OFF *Smoken Token*", "amount": -500}, REWARDS, NAMES
    )
    assert reward["id"] == 1


def test_unrelated_discounts_are_never_treated_as_redemptions():
    for discount in (
        {"name": "Military Discount", "amount": -500},
        {"name": "Employee Discount", "percentage": 20},
        {"name": "PREROLL DISCOUNT", "amount": -1000},
        {"name": "Rewards", "percentage": 10},
        {"name": "", "amount": -500},
    ):
        assert _reward_for_discount(discount, REWARDS, NAMES) is None


def test_reward_discount_of_an_unconfigured_amount_is_ignored():
    assert _reward_for_discount({"name": "Rewards", "amount": -700}, REWARDS, NAMES) is None


def test_line_item_discounts_are_seen_as_well_as_order_discounts():
    order = _order(
        discounts=[{"name": "Rewards", "amount": -500}],
        line_discounts=[{"name": "Military", "amount": -200}],
    )
    assert [d["name"] for d in _order_discounts(order)] == ["Rewards", "Military"]


@pytest.mark.asyncio
async def test_points_come_off_and_the_redemption_is_recorded(db):
    spent = await _redeem_pos_discounts(
        db, {"id": 863}, _order([{"name": "Rewards", "amount": -500}]),
        "ORD1", "East", REWARDS, NAMES,
    )
    await db.commit()

    assert spent == 100
    assert await _balance(db) == (2211, 100)

    cursor = await db.execute(
        "SELECT reward_id, points_spent, location_name FROM loyalty_redemptions"
    )
    assert await cursor.fetchall() == [(1, 100, "East")]

    cursor = await db.execute(
        "SELECT type, points, order_id FROM loyalty_transactions"
    )
    assert await cursor.fetchall() == [("redeem", -100, "ORD1")]


@pytest.mark.asyncio
async def test_a_resync_of_the_same_order_does_not_deduct_twice(db):
    order = _order([{"name": "Rewards", "amount": -500}])
    assert await _redeem_pos_discounts(
        db, {"id": 863}, order, "ORD1", "East", REWARDS, NAMES
    ) == 100
    await db.commit()

    assert await _redeem_pos_discounts(
        db, {"id": 863}, order, "ORD1", "East", REWARDS, NAMES
    ) == 0
    await db.commit()

    assert await _balance(db) == (2211, 100)
    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_redemptions")
    assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_a_later_order_at_the_other_store_still_redeems(db):
    await _redeem_pos_discounts(
        db, {"id": 863}, _order([{"name": "Rewards", "amount": -500}]),
        "ORD1", "East", REWARDS, NAMES,
    )
    spent = await _redeem_pos_discounts(
        db, {"id": 863}, _order([{"name": "Rewards", "amount": -1500}]),
        "ORD2", "West", REWARDS, NAMES,
    )
    await db.commit()

    assert spent == 250
    assert await _balance(db) == (2311 - 350, 350)
    cursor = await db.execute("SELECT location_name FROM loyalty_redemptions ORDER BY id")
    assert [row[0] for row in await cursor.fetchall()] == ["East", "West"]


@pytest.mark.asyncio
async def test_a_balance_too_small_for_the_reward_is_left_alone(db):
    await db.execute("UPDATE loyalty_customers SET points_balance = 40 WHERE id = 863")
    await db.commit()

    spent = await _redeem_pos_discounts(
        db, {"id": 863}, _order([{"name": "Rewards", "amount": -500}]),
        "ORD1", "East", REWARDS, NAMES,
    )
    await db.commit()

    assert spent == 0
    assert await _balance(db) == (40, 0)
    cursor = await db.execute("SELECT COUNT(*) FROM loyalty_redemptions")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_two_rewards_on_one_ticket_both_come_off(db):
    spent = await _redeem_pos_discounts(
        db, {"id": 863},
        _order([{"name": "Rewards", "amount": -500}, {"name": "Rewards", "amount": -1500}]),
        "ORD1", "East", REWARDS, NAMES,
    )
    await db.commit()

    assert spent == 350
    assert await _balance(db) == (2311 - 350, 350)


@pytest.mark.asyncio
async def test_an_order_without_a_reward_discount_leaves_points_alone(db):
    spent = await _redeem_pos_discounts(
        db, {"id": 863}, _order([{"name": "Military Discount", "amount": -500}]),
        "ORD1", "East", REWARDS, NAMES,
    )
    await db.commit()

    assert spent == 0
    assert await _balance(db) == (2311, 0)


def test_clover_client_has_no_shadowed_methods():
    """A second definition silently replaces the first.

    A duplicate `create_discount` took the reward's cents as `percentage`, so
    Clover rejected every reward button with "percentage should be between zero
    and hundred".
    """
    tree = ast.parse(inspect.getsource(clover_client))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        names = [
            child.name for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        duplicates = {name for name in names if names.count(name) > 1}
        assert not duplicates, f"{node.name} defines {duplicates} more than once"


def test_reward_discounts_are_created_as_a_dollar_amount():
    """`create_discount` must be given the reward value as `amount`, not `percentage`."""
    params = inspect.signature(CloverClient.create_discount).parameters
    assert "amount" in params and "percentage" in params
    # Both are keyword-defaulted, so a positional call lands on `percentage`.
    assert "amount=cents" in inspect.getsource(push_reward_discounts)


@pytest.mark.asyncio
async def test_configured_names_replace_the_defaults(db):
    names = _redemption_discount_names({"redemption_discount_names": "Punch Card"})
    assert _reward_for_discount({"name": "Rewards", "amount": -500}, REWARDS, names) is None
    assert _reward_for_discount(
        {"name": "Punch Card $5", "amount": -500}, REWARDS, names
    )["id"] == 1
