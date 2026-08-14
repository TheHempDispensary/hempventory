"""A pickup order marked delivered must come off the shelf — exactly once.

Checkout deducts stock in a background task; when its Clover call is lost the
shelf count stays too high and staff only notice when the shop sells an item it
doesn't have (order HD-6A7F27B8-6182). Staff advancing the order is the second
chance to get the count right, so the status endpoint retries the deduction, and
per-line stamps keep a retry from taking the same item off twice.
"""
import jwt
import pytest
import aiosqlite

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import DB_PATH, init_db
from app.routers import ecommerce_router as er


@pytest.fixture
async def db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    yield db
    await db.execute("DELETE FROM ecommerce_order_items WHERE order_id IN (SELECT id FROM ecommerce_orders WHERE order_number LIKE 'TEST-STK%')")
    await db.execute("DELETE FROM ecommerce_orders WHERE order_number LIKE 'TEST-STK%'")
    await db.commit()
    await db.close()


async def _make_order(db, order_number, items, fulfillment_type="pickup_east"):
    cursor = await db.execute(
        """INSERT INTO ecommerce_orders (order_number, customer_email, total, payment_status, fulfillment_type)
           VALUES (?, 'kim@example.com', 5000, 'paid', ?)""",
        (order_number, fulfillment_type),
    )
    order_id = cursor.lastrowid
    for name, sku, qty in items:
        await db.execute(
            """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, price, quantity)
               VALUES (?, 'T95RTZF4W7CC2', ?, ?, 2500, ?)""",
            (order_id, name, sku, qty),
        )
    await db.commit()
    return order_id


def _token():
    return jwt.encode({"sub": "kim"}, "hemp-inventory-secret-key", algorithm="HS256")


async def _set_status(order_id, status):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.patch(
            f"/api/ecommerce/orders/{order_id}/status",
            json={"status": status},
            headers={"Authorization": f"Bearer {_token()}"},
        )


class FakeClover:
    """Records what stock deduction was asked to write, and can fail on demand."""

    def __init__(self, failing_skus=()):
        self.calls: list[tuple[str, str, int]] = []
        self.failing_skus = set(failing_skus)

    async def __call__(self, items, fulfillment_type="shipping"):
        written = []
        for item in items:
            ok = item.sku not in self.failing_skus
            self.calls.append((fulfillment_type, item.sku, item.quantity))
            written.append(ok)
        return written


@pytest.mark.asyncio
async def test_delivered_pickup_order_deducts_stock(db, monkeypatch):
    order_id = await _make_order(db, "TEST-STK-1", [("OG Kush 28g", "2025754319186", 1)])
    fake = FakeClover()
    monkeypatch.setattr(er, "_deduct_stock_for_order", fake)

    resp = await _set_status(order_id, "delivered")

    assert resp.status_code == 200
    assert resp.json()["stock_deducted"] is True
    assert fake.calls == [("pickup_east", "2025754319186", 1)]


@pytest.mark.asyncio
async def test_repeated_status_updates_deduct_once(db, monkeypatch):
    order_id = await _make_order(db, "TEST-STK-2", [("OG Kush 28g", "2025754319186", 1)])
    fake = FakeClover()
    monkeypatch.setattr(er, "_deduct_stock_for_order", fake)

    await _set_status(order_id, "processing")
    await _set_status(order_id, "delivered")
    await _set_status(order_id, "delivered")

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_failed_line_is_retried_and_succeeded_line_is_not(db, monkeypatch):
    """A half-written order must finish later without double-deducting the rest."""
    order_id = await _make_order(
        db,
        "TEST-STK-3",
        [("OG Kush 28g", "SKU-OK", 1), ("Gummies", "SKU-BAD", 2)],
    )
    failing = FakeClover(failing_skus={"SKU-BAD"})
    monkeypatch.setattr(er, "_deduct_stock_for_order", failing)

    first = await _set_status(order_id, "processing")
    assert first.json()["stock_deducted"] is False

    retry = FakeClover()
    monkeypatch.setattr(er, "_deduct_stock_for_order", retry)
    second = await _set_status(order_id, "delivered")

    assert second.json()["stock_deducted"] is True
    assert retry.calls == [("pickup_east", "SKU-BAD", 2)]


@pytest.mark.asyncio
async def test_cancelling_an_order_never_deducts(db, monkeypatch):
    order_id = await _make_order(db, "TEST-STK-4", [("OG Kush 28g", "SKU-OK", 1)])
    fake = FakeClover()
    monkeypatch.setattr(er, "_deduct_stock_for_order", fake)

    await _set_status(order_id, "cancelled")

    assert fake.calls == []


@pytest.mark.asyncio
async def test_order_already_deducted_at_checkout_is_left_alone(db, monkeypatch):
    order_id = await _make_order(db, "TEST-STK-5", [("OG Kush 28g", "SKU-OK", 1)])
    await db.execute(
        "UPDATE ecommerce_orders SET stock_deducted_at = CURRENT_TIMESTAMP WHERE id = ?", (order_id,)
    )
    await db.execute(
        "UPDATE ecommerce_order_items SET stock_deducted_at = CURRENT_TIMESTAMP WHERE order_id = ?", (order_id,)
    )
    await db.commit()
    fake = FakeClover()
    monkeypatch.setattr(er, "_deduct_stock_for_order", fake)

    await _set_status(order_id, "delivered")

    assert fake.calls == []


@pytest.mark.asyncio
async def test_west_pickup_deducts_from_west(db, monkeypatch):
    order_id = await _make_order(
        db, "TEST-STK-6", [("OG Kush 28g", "SKU-OK", 3)], fulfillment_type="pickup_west"
    )
    fake = FakeClover()
    monkeypatch.setattr(er, "_deduct_stock_for_order", fake)

    await _set_status(order_id, "delivered")

    assert fake.calls == [("pickup_west", "SKU-OK", 3)]
