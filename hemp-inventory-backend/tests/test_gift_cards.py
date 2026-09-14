"""Online gift cards: fixed denominations, emailed codes, balance redemption."""
import pytest
import aiosqlite
import jwt

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import DB_PATH, init_db
from app import gift_cards
from app.routers import ecommerce_router as er


@pytest.fixture
async def db():
    await init_db()
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    yield db
    await db.execute("DELETE FROM gift_card_transactions")
    await db.execute("DELETE FROM gift_cards")
    await db.execute("DELETE FROM ecommerce_order_items WHERE order_id IN (SELECT id FROM ecommerce_orders WHERE customer_email = 'gc@example.com')")
    await db.execute("DELETE FROM ecommerce_orders WHERE customer_email = 'gc@example.com'")
    await db.commit()
    await db.close()


@pytest.fixture
def quiet_order(monkeypatch):
    """Keep create_order off the network: no Clover stock, no SMTP, no sheets."""
    async def no_stock(items, fulfillment_type):
        return []

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(er, "_check_realtime_stock", no_stock)
    monkeypatch.setattr(er, "_deduct_stock_and_flag", noop)
    monkeypatch.setattr(er, "_send_order_emails", noop)
    monkeypatch.setattr(er, "_award_loyalty_points_for_order", noop)
    monkeypatch.setattr(er, "_sync_leaflife_order", noop)
    sent = []

    async def capture_gift_email(smtp_settings, order, order_number, cards):
        sent.append((order.customer.email, order_number, cards))

    monkeypatch.setattr(er, "_send_gift_card_email", capture_gift_email)
    return sent


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _admin_headers():
    token = jwt.encode({"sub": "admin"}, "hemp-inventory-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _order(items, **overrides):
    subtotal = sum(i["price"] * i["quantity"] for i in items)
    body = {
        "customer": {"first_name": "Gift", "last_name": "Buyer", "email": "gc@example.com", "phone": ""},
        "shipping_address": {},
        "items": items,
        "subtotal": subtotal,
        "discount": 0,
        "shipping_cost": 0,
        "tax": 0,
        "total": subtotal,
        "payment_token": "",
        "fulfillment_type": "pickup_west",
    }
    body.update(overrides)
    return body


def test_denominations_are_virtual_products():
    products = gift_cards.gift_card_products()
    assert [p["price"] for p in products] == [2500, 5000, 10000]
    assert all(p["is_gift_card"] and p["categories"] == ["Gift Cards"] for p in products)
    assert gift_cards.gift_card_amount("GIFTCARD-50") == 5000
    assert gift_cards.gift_card_amount("GIFTCARD-75") is None
    assert gift_cards.gift_card_amount("ABC-25") is None


def test_add_gift_card_products_is_idempotent():
    products, cats = gift_cards.add_gift_card_products([{"name": "Zed", "sku": "Z"}], ["FLOWER"])
    products, cats = gift_cards.add_gift_card_products(products, cats)
    assert sum(1 for p in products if p.get("is_gift_card")) == 3
    assert cats == ["FLOWER", "Gift Cards"]


def test_code_format_roundtrip():
    code = gift_cards.generate_code()
    assert len(code) == 16
    pretty = gift_cards.format_code(code)
    assert pretty.count("-") == 3
    assert gift_cards.normalize_code(pretty.lower()) == code


@pytest.mark.asyncio
async def test_redeem_is_atomic_and_never_overdraws(db):
    card = await gift_cards.issue(db, 2500, "A", "a@example.com")
    assert await gift_cards.redeem(db, card["code"], 1000, "HD-1") == 1500
    assert await gift_cards.redeem(db, card["code"], 2000, "HD-2") is None
    assert await gift_cards.redeem(db, card["code"], 1500, "HD-3") == 0
    assert await gift_cards.redeem(db, card["code"], 1, "HD-4") is None
    fresh = await gift_cards.lookup(db, gift_cards.format_code(card["code"]))
    assert fresh["balance"] == 0


@pytest.mark.asyncio
async def test_cancelled_order_refunds_spend_and_voids_issued_cards(db):
    spent = await gift_cards.issue(db, 5000, "A", "a@example.com")
    assert await gift_cards.redeem(db, spent["code"], 3000, "HD-9") == 2000
    issued = await gift_cards.issue(db, 2500, "A", "a@example.com", order_number="HD-9")

    result = await gift_cards.reverse_order(db, "HD-9")
    assert result == {"refunded": 3000, "deactivated": 1}
    assert (await gift_cards.lookup(db, spent["code"]))["balance"] == 5000
    assert (await gift_cards.lookup(db, issued["code"]))["is_active"] is False
    # running it twice must not double-credit
    assert await gift_cards.reverse_order(db, "HD-9") == {"refunded": 0, "deactivated": 0}
    assert (await gift_cards.lookup(db, spent["code"]))["balance"] == 5000


@pytest.mark.asyncio
async def test_validate_promo_recognises_gift_card(db):
    card = await gift_cards.issue(db, 5000, "A", "a@example.com")
    async with _client() as client:
        resp = await client.post(
            "/api/ecommerce/validate-promo",
            json={"promo_code": gift_cards.format_code(card["code"]).lower(), "email": "x@example.com"},
        )
    data = resp.json()
    assert data["valid"] is True
    assert data["gift_card"] is True
    assert data["balance"] == 5000
    assert data["discount_amount"] == 5000
    assert data["discount_pct"] is None


@pytest.mark.asyncio
async def test_validate_promo_rejects_empty_or_inactive_card(db):
    card = await gift_cards.issue(db, 2500, "A", "a@example.com")
    await gift_cards.redeem(db, card["code"], 2500)
    async with _client() as client:
        resp = await client.post(
            "/api/ecommerce/validate-promo",
            json={"promo_code": card["code"], "email": "x@example.com"},
        )
    assert resp.json()["valid"] is False
    await db.execute("UPDATE gift_cards SET is_active = 0, balance = 100 WHERE id = ?", (card["id"],))
    await db.commit()
    async with _client() as client:
        resp = await client.post(
            "/api/ecommerce/validate-promo",
            json={"promo_code": card["code"], "email": "x@example.com"},
        )
    assert resp.json()["valid"] is False


@pytest.mark.asyncio
async def test_gift_card_covers_whole_order_and_is_debited(db, quiet_order):
    card = await gift_cards.issue(db, 5000, "A", "a@example.com")
    items = [{"product_id": "ITEM1", "name": "Gummies", "sku": "GUM-1", "price": 3000, "quantity": 1}]
    body = _order(items, discount=3000, total=0, promo_code=gift_cards.format_code(card["code"]))
    async with _client() as client:
        resp = await client.post("/api/ecommerce/orders", json=body)
    assert resp.status_code == 200, resp.text
    remaining = await gift_cards.lookup(db, card["code"])
    assert remaining["balance"] == 2000
    cursor = await db.execute(
        "SELECT payment_status, total FROM ecommerce_orders WHERE order_number = ?",
        (resp.json()["order_number"],),
    )
    row = await cursor.fetchone()
    assert row["payment_status"] == "paid"
    assert row["total"] == 0


@pytest.mark.asyncio
async def test_order_rejected_when_discount_exceeds_balance(db, quiet_order):
    card = await gift_cards.issue(db, 1000, "A", "a@example.com")
    items = [{"product_id": "ITEM1", "name": "Gummies", "sku": "GUM-1", "price": 3000, "quantity": 1}]
    body = _order(items, discount=3000, total=0, promo_code=card["code"])
    async with _client() as client:
        resp = await client.post("/api/ecommerce/orders", json=body)
    assert resp.status_code == 400
    assert (await gift_cards.lookup(db, card["code"]))["balance"] == 1000


@pytest.mark.asyncio
async def test_zero_total_without_gift_card_still_needs_payment(db, quiet_order):
    items = [{"product_id": "ITEM1", "name": "Gummies", "sku": "GUM-1", "price": 3000, "quantity": 1}]
    body = _order(items, discount=3000, total=0)
    async with _client() as client:
        resp = await client.post("/api/ecommerce/orders", json=body)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_buying_gift_cards_with_a_gift_card_is_blocked(db, quiet_order):
    card = await gift_cards.issue(db, 5000, "A", "a@example.com")
    items = [{"product_id": "GIFTCARD-25", "name": "$25 Gift Card", "sku": "GIFTCARD-25", "price": 2500, "quantity": 1}]
    body = _order(items, discount=2500, total=0, promo_code=card["code"])
    async with _client() as client:
        resp = await client.post("/api/ecommerce/orders", json=body)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_purchased_gift_cards_are_issued_and_emailed(db, quiet_order, monkeypatch):
    """Payment is faked as succeeded; each purchased unit becomes its own code."""
    class FakeResp:
        status_code = 200

        def json(self):
            return {"status": "succeeded", "id": "CHG-1"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(er.httpx, "AsyncClient", FakeClient)
    items = [
        # Client-submitted price is wrong on purpose; the server must fix it.
        {"product_id": "GIFTCARD-25", "name": "$25 Gift Card", "sku": "GIFTCARD-25", "price": 100, "quantity": 2},
        {"product_id": "GIFTCARD-100", "name": "$100 Gift Card", "sku": "GIFTCARD-100", "price": 10000, "quantity": 1},
    ]
    body = _order(items, payment_token="tok_test", fulfillment_type="shipping", shipping_cost=0)
    async with _client() as client:
        resp = await client.post("/api/ecommerce/orders", json=body)
    assert resp.status_code == 200, resp.text
    order_number = resp.json()["order_number"]

    cursor = await db.execute(
        "SELECT initial_amount, balance, purchaser_email FROM gift_cards WHERE order_number = ? ORDER BY initial_amount",
        (order_number,),
    )
    cards = [tuple(r) for r in await cursor.fetchall()]
    assert cards == [(2500, 2500, "gc@example.com"), (2500, 2500, "gc@example.com"), (10000, 10000, "gc@example.com")]

    assert len(quiet_order) == 1
    to_email, emailed_order, emailed_cards = quiet_order[0]
    assert to_email == "gc@example.com" and emailed_order == order_number
    assert len(emailed_cards) == 3

    cursor = await db.execute("SELECT total FROM ecommerce_orders WHERE order_number = ?", (order_number,))
    assert (await cursor.fetchone())["total"] == 15000


@pytest.mark.asyncio
async def test_admin_lookup_and_in_store_redeem(db):
    card = await gift_cards.issue(db, 2500, "A", "a@example.com")
    async with _client() as client:
        denied = await client.get("/api/ecommerce/gift-cards")
        assert denied.status_code == 401
        found = await client.get(
            "/api/ecommerce/gift-cards/lookup", params={"code": card["code"]}, headers=_admin_headers()
        )
        assert found.status_code == 200 and found.json()["balance"] == 2500
        redeemed = await client.post(
            f"/api/ecommerce/gift-cards/{card['id']}/redeem", json={"amount": 1500}, headers=_admin_headers()
        )
        assert redeemed.json()["balance"] == 1000
        over = await client.post(
            f"/api/ecommerce/gift-cards/{card['id']}/redeem", json={"amount": 5000}, headers=_admin_headers()
        )
        assert over.status_code == 400
        listing = await client.get("/api/ecommerce/gift-cards", headers=_admin_headers())
        assert any(c["id"] == card["id"] and c["balance"] == 1000 for c in listing.json()["gift_cards"])
