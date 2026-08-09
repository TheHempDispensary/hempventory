import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_sales_report_test.db"))

import aiosqlite
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import sales_router as sr


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


JULY_1_NOON_ET_MS = 1782921600000  # 2026-07-01 12:00 America/New_York


def _order(total, tax=None, paid=True, order_tax=None):
    payments = []
    if paid:
        payments = [{"amount": total, "taxAmount": tax, "result": "SUCCESS"}]
    return {
        "id": "O1",
        "total": total,
        "taxAmount": order_tax,
        "createdTime": JULY_1_NOON_ET_MS,
        "payType": "FULL",
        "payments": {"elements": payments},
        "lineItems": {"elements": [{"name": "Flower 3.5g", "price": total}]},
    }


async def _report(db, orders, monkeypatch, refunds=None):
    await db.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES ('East', 'M1', 'tok')"
    )
    await db.commit()

    async def fake_orders(merchant_id, api_token, start_ms, end_ms):
        return orders

    async def fake_refunds(merchant_id, api_token, start_ms, end_ms):
        return refunds or []

    monkeypatch.setattr(sr, "_fetch_orders_for_location", fake_orders)
    monkeypatch.setattr(sr, "_fetch_refunds_for_location", fake_refunds)
    return await sr.get_sales_report(
        start_date="2026-07-01", end_date="2026-07-01", user={"id": 1}, db=db
    )


async def test_tax_from_payments_is_excluded_from_net_sales(db, monkeypatch):
    """Clover leaves order.taxAmount empty; tax lives on the payment."""
    report = await _report(db, [_order(1000, tax=52)], monkeypatch)

    assert report["summary"]["total_tax"] == 52
    assert report["summary"]["amount_collected"] == 1000
    assert report["summary"]["net_sales"] == 948
    assert report["summary"]["total_revenue"] == 948
    assert report["by_location"]["East"]["revenue"] == 948
    assert report["by_location"]["East"]["tax"] == 52
    assert report["daily"][0]["revenue"] == 948
    assert report["summary"]["avg_order_value"] == 948


async def test_order_level_tax_still_used_when_present(db, monkeypatch):
    report = await _report(db, [_order(1000, tax=None, order_tax=70)], monkeypatch)

    assert report["summary"]["total_tax"] == 70
    assert report["summary"]["net_sales"] == 930


async def test_unpaid_orders_are_excluded(db, monkeypatch):
    """Website orders pushed into Clover carry payType but were never tendered."""
    report = await _report(
        db, [_order(1000, tax=52), _order(5000, paid=False)], monkeypatch
    )

    assert report["summary"]["total_orders"] == 1
    assert report["summary"]["unpaid_orders"] == 1
    assert report["summary"]["unpaid_total"] == 5000
    assert report["summary"]["net_sales"] == 948
    assert report["summary"]["total_items_sold"] == 1


async def test_refunds_reduce_net_sales(db, monkeypatch):
    refunds = [{"amount": 200, "createdTime": JULY_1_NOON_ET_MS}]
    report = await _report(db, [_order(1000, tax=52)], monkeypatch, refunds=refunds)

    assert report["summary"]["total_refunds"] == 200
    assert report["summary"]["net_sales"] == 748
    assert report["by_location"]["East"]["revenue"] == 748
    assert report["daily"][0]["revenue"] == 748
