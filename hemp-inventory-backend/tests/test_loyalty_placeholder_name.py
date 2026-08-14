"""A member imported as "Customer" should pick up their real name automatically.

Clover records that carry only a phone number are stored as "Customer", so staff
see "Customer" at the register and retype the name by hand (Peter, 508-922-8559).
Once Clover knows the name, the loyalty record adopts it — but a real name staff
typed in is never overwritten.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_placeholder_name_test.db"))

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
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


async def _member(db, first, last=""):
    cursor = await db.execute(
        "INSERT INTO loyalty_customers (first_name, last_name, phone) VALUES (?, ?, '5089228559')",
        (first, last),
    )
    await db.commit()
    return cursor.lastrowid


async def _name(db, customer_id):
    cursor = await db.execute(
        "SELECT first_name, last_name FROM loyalty_customers WHERE id = ?", (customer_id,)
    )
    row = await cursor.fetchone()
    return row["first_name"], row["last_name"]


def test_placeholder_names_are_recognized():
    assert lr._is_placeholder_name("Customer", "") is True
    assert lr._is_placeholder_name("customer", None) is True
    assert lr._is_placeholder_name("", "") is True
    assert lr._is_placeholder_name("Peter", "") is False
    assert lr._is_placeholder_name("Customer", "Carbo") is False


@pytest.mark.asyncio
async def test_clover_name_replaces_placeholder(db):
    customer_id = await _member(db, "Customer")

    assert await lr._adopt_real_name(db, customer_id, "Customer", "", "Peter", "Boyle") is True
    await db.commit()

    assert await _name(db, customer_id) == ("Peter", "Boyle")


@pytest.mark.asyncio
async def test_real_name_is_never_overwritten(db):
    customer_id = await _member(db, "Peter")

    assert await lr._adopt_real_name(db, customer_id, "Peter", "", "Pete", "Smith") is False

    assert await _name(db, customer_id) == ("Peter", "")


@pytest.mark.asyncio
async def test_clover_without_a_name_leaves_the_placeholder(db):
    customer_id = await _member(db, "Customer")

    assert await lr._adopt_real_name(db, customer_id, "Customer", "", "", "  ") is False

    assert await _name(db, customer_id) == ("Customer", "")


@pytest.mark.asyncio
async def test_last_name_only_becomes_the_display_name(db):
    """Clover sometimes holds just a surname; better than "Customer"."""
    customer_id = await _member(db, "Customer")

    assert await lr._adopt_real_name(db, customer_id, "Customer", "", "", "Carbo") is True
    await db.commit()

    assert await _name(db, customer_id) == ("Carbo", "")
