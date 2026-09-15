"""A fully refunded or cancelled online order goes back on the shelf — exactly once.

Only item-level partial refunds used to restock; a "full refund" followed by
"cancelled" (order HD-6A9E15BE-215) left two 28g ounces missing at HQ.
"""
import aiosqlite
import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.database import DB_PATH, init_db
from app.main import app
from app.routers import ecommerce_router as er


@pytest.fixture
async def db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    yield db
    await db.execute("DELETE FROM ecommerce_order_items WHERE order_id IN (SELECT id FROM ecommerce_orders WHERE order_number LIKE 'TEST-RSTK%')")
    await db.execute("DELETE FROM ecommerce_orders WHERE order_number LIKE 'TEST-RSTK%'")
    await db.commit()
    await db.close()


async def _make_order(db, order_number, items, deducted=True, fulfillment_type="ship"):
    cursor = await db.execute(
        """INSERT INTO ecommerce_orders
           (order_number, customer_email, charge_id, total, subtotal, tax, payment_status, fulfillment_type)
           VALUES (?, 'ted@example.com', 'CHG1', 23821, 22000, 1821, 'paid', ?)""",
        (order_number, fulfillment_type),
    )
    order_id = cursor.lastrowid
    for pid, name, sku, qty in items:
        await db.execute(
            """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, price, quantity)
               VALUES (?, ?, ?, ?, 10000, ?)""",
            (order_id, pid, name, sku, qty),
        )
    if deducted:
        await db.execute(
            "UPDATE ecommerce_order_items SET stock_deducted_at = CURRENT_TIMESTAMP WHERE order_id = ?", (order_id,)
        )
        await db.execute(
            "UPDATE ecommerce_orders SET stock_deducted_at = CURRENT_TIMESTAMP WHERE id = ?", (order_id,)
        )
    await db.commit()
    return order_id


def _headers():
    return {"Authorization": f"Bearer {jwt.encode({'sub': 'kim'}, 'hemp-inventory-secret-key', algorithm='HS256')}"}


async def _post(path, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body, headers=_headers())


async def _patch(path, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.patch(path, json=body, headers=_headers())


class FakeRestock:
    def __init__(self):
        self.calls: list[tuple[str, list[tuple[str, int]]]] = []

    async def __call__(self, items, fulfillment_type="shipping"):
        self.calls.append((fulfillment_type, [(i["product_id"], i["quantity"]) for i in items]))


class FakeCloverResponse:
    status_code = 200
    text = '{"id": "REF1"}'

    def json(self):
        return {"id": "REF1"}


class FakeHttpClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return FakeCloverResponse()


@pytest.fixture
def fakes(monkeypatch):
    restock = FakeRestock()
    monkeypatch.setattr(er, "_restock_items", restock)
    monkeypatch.setattr(er.httpx, "AsyncClient", FakeHttpClient)
    return restock


@pytest.mark.asyncio
async def test_full_refund_restocks_every_deducted_line(db, fakes):
    order_id = await _make_order(db, "TEST-RSTK-1", [("NERDS28", "Nerds 28g", "NERDS28", 1), ("DIVINE28", "Divine 28g", "DIVINE28", 1)])

    resp = await _post(f"/api/ecommerce/orders/{order_id}/refund", {})

    assert resp.status_code == 200
    assert resp.json()["restocked_items"] == 2
    assert fakes.calls == [("ship", [("NERDS28", 1), ("DIVINE28", 1)])]


@pytest.mark.asyncio
async def test_cancel_after_full_refund_does_not_restock_twice(db, fakes):
    order_id = await _make_order(db, "TEST-RSTK-2", [("NERDS28", "Nerds 28g", "NERDS28", 1)])

    await _post(f"/api/ecommerce/orders/{order_id}/refund", {})
    resp = await _patch(f"/api/ecommerce/orders/{order_id}/status", {"status": "cancelled"})

    assert resp.status_code == 200
    assert len(fakes.calls) == 1


@pytest.mark.asyncio
async def test_cancelling_a_deducted_order_restocks_it(db, fakes):
    order_id = await _make_order(db, "TEST-RSTK-3", [("NERDS28", "Nerds 28g", "NERDS28", 2)], fulfillment_type="pickup_west")

    await _patch(f"/api/ecommerce/orders/{order_id}/status", {"status": "cancelled"})

    assert fakes.calls == [("pickup_west", [("NERDS28", 2)])]


@pytest.mark.asyncio
async def test_lines_never_deducted_are_not_restocked(db, fakes):
    order_id = await _make_order(db, "TEST-RSTK-4", [("NERDS28", "Nerds 28g", "NERDS28", 1)], deducted=False)

    resp = await _post(f"/api/ecommerce/orders/{order_id}/refund", {})

    assert resp.json()["restocked_items"] == 0
    assert fakes.calls == []


@pytest.mark.asyncio
async def test_dollar_amount_partial_refund_restocks_nothing(db, fakes):
    order_id = await _make_order(db, "TEST-RSTK-5", [("NERDS28", "Nerds 28g", "NERDS28", 1)])

    resp = await _post(f"/api/ecommerce/orders/{order_id}/refund", {"amount": 500})

    assert resp.json()["restocked_items"] == 0
    assert fakes.calls == []
