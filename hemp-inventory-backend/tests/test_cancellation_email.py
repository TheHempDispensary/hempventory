"""Cancelling an order has to tell the customer — staff cancelled HD-6A945D6E-2113
and the customer was never emailed because the status endpoint sent nothing.
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
    await db.execute("DELETE FROM ecommerce_order_items WHERE order_id IN (SELECT id FROM ecommerce_orders WHERE order_number LIKE 'TEST-CXL%')")
    await db.execute("DELETE FROM ecommerce_orders WHERE order_number LIKE 'TEST-CXL%'")
    await db.commit()
    await db.close()


class FakeMailer:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list[tuple[str, str, str]] = []

    def __call__(self, smtp_settings, to_email, subject, html_body):
        self.sent.append((to_email, subject, html_body))
        return self.ok


async def _make_order(db, order_number, charge_id="CHG123", status="paid"):
    cursor = await db.execute(
        """INSERT INTO ecommerce_orders
           (order_number, customer_first_name, customer_email, total, payment_status, charge_id, fulfillment_type)
           VALUES (?, 'David', 'david@example.com', 3182, ?, ?, 'shipping')""",
        (order_number, status, charge_id),
    )
    order_id = cursor.lastrowid
    await db.execute(
        """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, price, quantity)
           VALUES (?, 'P1', 'ZKITTLEZ EVERYDAY 3.5 GRAMS', 'SKU-Z', 2500, 1)""",
        (order_id,),
    )
    await db.commit()
    return order_id


def _token():
    return jwt.encode({"sub": "kim"}, "hemp-inventory-secret-key", algorithm="HS256")


async def _request(method, path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        if method == "patch":
            return await client.patch(
                path, json={"status": "cancelled"}, headers={"Authorization": f"Bearer {_token()}"}
            )
        return await client.post(path, headers={"Authorization": f"Bearer {_token()}"})


@pytest.mark.asyncio
async def test_cancelling_emails_the_customer(db, monkeypatch):
    order_id = await _make_order(db, "TEST-CXL-1")
    mailer = FakeMailer()
    monkeypatch.setattr(er, "_send_smtp_email", mailer)

    resp = await _request("patch", f"/api/ecommerce/orders/{order_id}/status")

    assert resp.json()["cancellation_email_sent"] is True
    to_email, subject, html = mailer.sent[0]
    assert to_email == "david@example.com"
    assert "TEST-CXL-1" in subject
    assert "ZKITTLEZ EVERYDAY 3.5 GRAMS" in html
    assert "refunded" in html


@pytest.mark.asyncio
async def test_unpaid_order_is_not_told_about_a_refund(db, monkeypatch):
    order_id = await _make_order(db, "TEST-CXL-2", charge_id="", status="pending")
    mailer = FakeMailer()
    monkeypatch.setattr(er, "_send_smtp_email", mailer)

    await _request("patch", f"/api/ecommerce/orders/{order_id}/status")

    _, _, html = mailer.sent[0]
    assert "have not been charged" in html


@pytest.mark.asyncio
async def test_already_cancelled_order_is_not_emailed_again(db, monkeypatch):
    order_id = await _make_order(db, "TEST-CXL-3", status="cancelled")
    mailer = FakeMailer()
    monkeypatch.setattr(er, "_send_smtp_email", mailer)

    resp = await _request("patch", f"/api/ecommerce/orders/{order_id}/status")

    assert resp.json()["cancellation_email_sent"] is False
    assert mailer.sent == []


@pytest.mark.asyncio
async def test_staff_can_send_the_email_for_an_already_cancelled_order(db, monkeypatch):
    order_id = await _make_order(db, "TEST-CXL-4", status="cancelled")
    mailer = FakeMailer()
    monkeypatch.setattr(er, "_send_smtp_email", mailer)

    resp = await _request("post", f"/api/ecommerce/orders/{order_id}/send-cancellation")

    assert resp.status_code == 200
    assert len(mailer.sent) == 1


@pytest.mark.asyncio
async def test_status_still_updates_when_the_email_fails(db, monkeypatch):
    order_id = await _make_order(db, "TEST-CXL-5")
    monkeypatch.setattr(er, "_send_smtp_email", FakeMailer(ok=False))

    resp = await _request("patch", f"/api/ecommerce/orders/{order_id}/status")

    assert resp.json() == {
        "success": True,
        "order_id": order_id,
        "status": "cancelled",
        "stock_deducted": False,
        "cancellation_email_sent": False,
    }
    cursor = await db.execute("SELECT payment_status FROM ecommerce_orders WHERE id = ?", (order_id,))
    assert (await cursor.fetchone())[0] == "cancelled"
