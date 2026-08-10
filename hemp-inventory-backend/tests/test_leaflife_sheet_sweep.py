"""The sweep recovers LeafLife orders the checkout-time sheet write missed.

The write at checkout is fire-and-forget, so a restart or a Sheets hiccup can
drop an order silently — it never reaches the sheet and never lands in the
tracking table either. These tests lock in that a later sweep finds it.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_leaflife_sweep_test.db"))

import aiosqlite
import pytest_asyncio

from app import leaflife_orders
from app.database import DB_PATH, init_db
from app.routers import ecommerce_router as er


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


async def _add_order(
    db,
    order_number,
    *,
    sku="LF-MOONBOW-112-28",
    fulfillment_type="ship",
    payment_status="paid",
):
    cur = await db.execute(
        """INSERT INTO ecommerce_orders
               (order_number, customer_first_name, customer_last_name, customer_email,
                customer_phone, shipping_address, shipping_city, shipping_state,
                shipping_zip, subtotal, shipping_cost, tax, total, payment_status,
                fulfillment_type, shipping_service)
           VALUES (?, 'Ada', 'Byron', 'a@b.co', '5550000', '1 Main St', 'DeLand',
                   'FL', '32720', 15000, 789, 1080, 16869, ?, ?, 'Ground Advantage')""",
        (order_number, payment_status, fulfillment_type),
    )
    await db.execute(
        """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, price, quantity)
           VALUES (?, 'p1', 'MOONBOW #112 EVERYDAY 28 GRAMS', ?, 15000, 1)""",
        (cur.lastrowid, sku),
    )
    await db.commit()


@pytest_asyncio.fixture
def sheet(monkeypatch):
    """Stub the Google Sheets side; records which orders were appended."""
    written: list[str] = []

    async def fake_sync_order(*, order_number, **kwargs):
        written.append(order_number)
        return {"ok": True, "written": 2, "order_number": leaflife_orders.short_order_no(order_number)}

    monkeypatch.setattr(leaflife_orders, "is_configured", lambda: True)
    monkeypatch.setattr(leaflife_orders, "sync_order", fake_sync_order)
    return written


async def test_sweep_writes_order_that_checkout_missed(db, sheet):
    await _add_order(db, "HD-6A762116-4005")

    result = await er._do_leaflife_sweep(db)

    assert sheet == ["HD-6A762116-4005"]
    assert result["synced"] == 1
    cur = await db.execute(
        "SELECT status FROM leaflife_order_sync WHERE order_number = '6A762116'"
    )
    assert (await cur.fetchone())[0] == "synced"


async def test_sweep_skips_orders_already_on_the_sheet(db, sheet):
    await _add_order(db, "HD-6A762116-4005")
    await db.execute(
        "INSERT INTO leaflife_order_sync (order_number, status) VALUES ('6A762116', 'synced')"
    )
    await db.commit()

    result = await er._do_leaflife_sweep(db)

    assert sheet == []
    assert result["synced"] == 0


async def test_sweep_retries_a_previously_failed_write(db, sheet):
    await _add_order(db, "HD-6A762116-4005")
    await db.execute(
        "INSERT INTO leaflife_order_sync (order_number, status, last_error) "
        "VALUES ('6A762116', 'failed', 'timeout')"
    )
    await db.commit()

    result = await er._do_leaflife_sweep(db)

    assert sheet == ["HD-6A762116-4005"]
    assert result["synced"] == 1


async def test_sweep_ignores_pickup_cancelled_and_non_leaflife_orders(db, sheet):
    await _add_order(db, "HD-AAAA1111-1", fulfillment_type="pickup_east")
    await _add_order(db, "HD-BBBB2222-2", payment_status="cancelled")
    await _add_order(db, "HD-CCCC3333-3", sku="8EXJH38RCDPNW")

    result = await er._do_leaflife_sweep(db)

    assert sheet == []
    assert result["synced"] == 0
