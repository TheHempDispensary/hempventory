import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_par_alerts_test.db"))

import aiosqlite
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import inventory_router as inv
from app.routers import par_router


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


def _item(item_id, sku, name, qty, price=1000):
    return {"id": item_id, "sku": sku, "name": name, "price": price, "itemStock": {"quantity": qty}}


ITEMS = {
    "m-east": [
        _item("e-dab", "DAB", "$5 DAB", 0, 500),
        _item("e-lem", "LEM", "D9 LEMONADE 8 OZ", 1, 2000),
        _item("e-gum", "GUM", "GUMMIES", 2, 2500),
    ],
    "m-hq": [
        _item("h-dab", "DAB", "$5 DAB", 0, 500),
        _item("h-lem", "LEM", "D9 LEMONADE 8 OZ", 3, 2000),
        _item("h-gum", "GUM", "GUMMIES", 40, 2500),
    ],
}


async def _setup(db, monkeypatch):
    for name, mid in (("East", "m-east"), ("HQ", "m-hq")):
        await db.execute(
            "INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, 'tok')", (name, mid)
        )
    rows = await (await db.execute("SELECT id, name FROM locations")).fetchall()
    ids = {r["name"]: r["id"] for r in rows}
    for sku in ("DAB", "LEM", "GUM"):
        await db.execute("INSERT INTO par_levels (sku, location_id, par_level) VALUES (?, ?, 10)", (sku, ids["East"]))
    await db.execute("INSERT INTO par_levels (sku, location_id, par_level) VALUES ('DAB', ?, 10)", (ids["HQ"],))
    await db.commit()

    class FakeClover:
        def __init__(self, merchant_id, token):
            self.merchant_id = merchant_id

        async def get_items(self):
            return {"elements": ITEMS[self.merchant_id]}

    monkeypatch.setattr(par_router, "CloverClient", FakeClover)
    now = 1_790_000_000
    tally = inv._SalesTally()
    tally.by_item_id = {"e-dab": 150, "e-lem": 130, "e-gum": 10}
    tally.first_by_item_id = {k: now - 30 * 86400 for k in tally.by_item_id}
    tally.earliest_ts, tally.latest_ts = now - 30 * 86400, now
    monkeypatch.setitem(inv._smart_par_cache, "data", tally.to_cache())


async def test_alerts_follow_hq_stock_and_rank_by_sales(db, monkeypatch):
    await _setup(db, monkeypatch)
    alerts = await par_router.build_par_alerts(db)
    by_key = {(a["location"], a["sku"]): a for a in alerts}

    assert by_key[("East", "DAB")]["recommendation"] == "HQ is out: produce or reorder 15 units for East"
    assert by_key[("East", "LEM")]["recommendation"] == (
        "Send 3 units from HQ to East (all HQ has), then produce or reorder 11 more"
    )
    assert by_key[("East", "GUM")]["recommendation"] == "Send 13 units from HQ to East"
    assert by_key[("HQ", "DAB")]["recommendation"] == "Produce or reorder 15 units for HQ"
    assert not any("from HQ to HQ" in a["recommendation"] for a in alerts)

    # Lemonade ($20 x ~130/mo) outranks DAB ($5 x ~150/mo) and gummies (~10/mo).
    assert [(a["location"], a["sku"]) for a in alerts][:3] == [
        ("East", "LEM"), ("East", "DAB"), ("East", "GUM"),
    ]


async def test_run_alert_check_records_history(db, monkeypatch):
    from app.routers import alerts_router

    await _setup(db, monkeypatch)
    sent = []

    async def fake_send(_db, subject, html):
        sent.append(html)
        return True

    monkeypatch.setattr(alerts_router, "send_service_alert_email", fake_send)
    result = await alerts_router.run_alert_check(db)
    assert result["alerts_found"] == 4
    assert result["email_sent"] is True
    assert result["notification_email"] == alerts_router.DEFAULT_NOTIFICATION_EMAIL
    assert "HQ is out: produce or reorder" in sent[0]
    row = await (await db.execute("SELECT COUNT(*), SUM(email_sent) FROM alert_history")).fetchone()
    assert tuple(row) == (4, 4)
