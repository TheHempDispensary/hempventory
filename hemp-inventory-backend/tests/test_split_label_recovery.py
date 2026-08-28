"""A purchased split-shipment label must never be lost.

Order HD-6A8E1DAF-7455 bought a LeafLife label on Shippo but the local write
never landed — the shipment row kept its Shippo shipment id with no tracking,
the admin UI showed only the store package, and the LeafLife sheet never got
its label link. These tests lock in the three defenses:

* re-fetching rates must not delete shipment rows that already have a label;
* a purchase is persisted even when Shippo hasn't produced a tracking number
  yet (so the transaction id is recoverable);
* the recovery sweep finds the completed Shippo transaction and saves it.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_split_label_test.db"))

import aiosqlite
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import shipping_router as sr


@pytest_asyncio.fixture
async def db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    conn = await aiosqlite.connect(DB_PATH)
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


async def _add_split_order(db) -> int:
    cur = await db.execute(
        """INSERT INTO ecommerce_orders
               (order_number, customer_first_name, customer_last_name, customer_email,
                customer_phone, shipping_address, shipping_city, shipping_state,
                shipping_zip, subtotal, shipping_cost, tax, total, payment_status,
                fulfillment_type)
           VALUES ('HD-AAAA1111-0001', 'Ada', 'Byron', 'a@b.co', '5550000', '1 Main St',
                   'DeLand', 'FL', '32720', 15000, 789, 1080, 16869, 'paid', 'ship')"""
    )
    order_id = cur.lastrowid
    await db.execute(
        """INSERT INTO ecommerce_order_items (order_id, product_id, product_name, sku, quantity, price)
           VALUES (?, 'p1', 'Store Gummies', 'THD-GUM-1', 1, 5000),
                  (?, 'p2', 'LeafLife Moonbow', 'LF-MOONBOW-112-28', 1, 10000)""",
        (order_id, order_id),
    )
    await db.commit()
    return order_id


async def _add_shipment(db, order_id, stype, *, shippo_shipment_id="", tracking="", txn_id="", aged=False):
    cur = await db.execute(
        """INSERT INTO order_shipments
               (order_id, shipment_type, item_ids, from_label, shippo_shipment_id,
                tracking_number, tracking_url, label_url, shippo_transaction_id,
                tracking_status, created_at)
           VALUES (?, ?, '1', ?, ?, ?, ?, ?, ?, ?,
                   CASE WHEN ? THEN datetime('now', '-1 hour') ELSE CURRENT_TIMESTAMP END)""",
        (
            order_id,
            stype,
            "Madison, WI (LeafLife)" if stype == "leaflife" else "Spring Hill, FL (Store)",
            shippo_shipment_id,
            tracking or None,
            "https://tools.usps.com/track" if tracking else None,
            "https://shippo.example/label.pdf" if tracking else None,
            txn_id or None,
            "label_created" if tracking else None,
            1 if aged else 0,
        ),
    )
    await db.commit()
    return cur.lastrowid


async def test_rates_retry_keeps_purchased_shipment_rows(db, monkeypatch):
    """Re-opening the ship panel must not wipe a shipment whose label is bought."""
    order_id = await _add_split_order(db)
    store_id = await _add_shipment(
        db, order_id, "store",
        shippo_shipment_id="ship-store", tracking="9400111111", txn_id="txn-store",
    )
    leaf_id = await _add_shipment(db, order_id, "leaflife", shippo_shipment_id="ship-leaf-old")

    monkeypatch.setattr(sr, "SHIPPO_API_TOKEN", "test-token")
    monkeypatch.setattr(sr, "_verify_admin", lambda request: "admin")

    async def fake_create_shipment(headers, from_addr, to_address, parcel, is_hazmat):
        return {"object_id": "ship-leaf-new", "rates": [{"provider": "USPS", "servicelevel": {"name": "Priority Mail"}, "object_id": "rate-1", "amount": "9.99", "currency": "USD"}]}

    async def fake_validate(headers, address):
        return {"is_valid": True, "messages": []}

    monkeypatch.setattr(sr, "_create_shippo_shipment", fake_create_shipment)
    monkeypatch.setattr(sr, "_validate_shippo_address", fake_validate)

    body = sr.CreateShipmentRequest(order_id=order_id)
    result = await sr.create_shipment(body, request=None, db=db)

    cur = await db.execute(
        "SELECT id, tracking_number FROM order_shipments WHERE order_id = ? AND shipment_type = 'store'",
        (order_id,),
    )
    row = await cur.fetchone()
    assert row == (store_id, "9400111111")

    cur = await db.execute(
        "SELECT id FROM order_shipments WHERE order_id = ? AND shipment_type = 'leaflife'",
        (order_id,),
    )
    leaf_rows = await cur.fetchall()
    assert len(leaf_rows) == 1
    assert leaf_rows[0][0] != leaf_id  # unpurchased row is recreated

    purchased = [g for g in result["shipment_groups"] if g["purchased"]]
    assert len(purchased) == 1
    assert purchased[0]["shipment_type"] == "store"
    assert purchased[0]["tracking_number"] == "9400111111"
    unpurchased = [g for g in result["shipment_groups"] if not g["purchased"]]
    assert len(unpurchased) == 1
    assert unpurchased[0]["shipment_type"] == "leaflife"
    assert unpurchased[0]["rates"]


async def test_purchase_without_tracking_still_persists_transaction(db, monkeypatch):
    """If Shippo hasn't produced a tracking number yet, keep the transaction id."""
    order_id = await _add_split_order(db)
    leaf_id = await _add_shipment(db, order_id, "leaflife", shippo_shipment_id="ship-leaf")

    monkeypatch.setattr(sr, "SHIPPO_API_TOKEN", "test-token")
    monkeypatch.setattr(sr, "_verify_admin", lambda request: "admin")

    async def fake_purchase(headers, rate_id, label_file_type):
        return {"object_id": "txn-slow", "status": "QUEUED", "tracking_number": "", "label_url": ""}

    monkeypatch.setattr(sr, "_purchase_shippo_label", fake_purchase)

    body = sr.PurchaseLabelRequest(rate_id="rate-1", order_id=order_id, shipment_id=leaf_id)
    await sr.purchase_label(body, request=None, db=db)

    cur = await db.execute(
        "SELECT shippo_transaction_id, tracking_status FROM order_shipments WHERE id = ?",
        (leaf_id,),
    )
    assert await cur.fetchone() == ("txn-slow", "purchase_pending")


async def test_recovery_sweep_saves_lost_purchase_from_shippo(db, monkeypatch):
    """The sweep finds the completed Shippo transaction and fills in the row."""
    order_id = await _add_split_order(db)
    await _add_shipment(
        db, order_id, "store",
        shippo_shipment_id="ship-store", tracking="9400111111", txn_id="txn-store", aged=True,
    )
    leaf_id = await _add_shipment(db, order_id, "leaflife", shippo_shipment_id="ship-leaf", aged=True)

    monkeypatch.setattr(sr, "SHIPPO_API_TOKEN", "test-token")

    leaf_txn = {
        "object_id": "txn-leaf",
        "status": "SUCCESS",
        "rate": "rate-leaf",
        "tracking_number": "9400122222",
        "tracking_url_provider": "https://tools.usps.com/track2",
        "label_url": "https://shippo.example/leaf.pdf",
    }

    async def fake_list_txns(client, headers):
        return [leaf_txn]

    async def fake_find(client, headers, shippo_shipment_id, transactions):
        return leaf_txn if shippo_shipment_id == "ship-leaf" else None

    synced: list[tuple] = []

    async def fake_sync(db_, order_id_, shipment_id_, tracking_):
        synced.append((order_id_, shipment_id_, tracking_))

    monkeypatch.setattr(sr, "_list_recent_transactions", fake_list_txns)
    monkeypatch.setattr(sr, "_find_shipment_transaction", fake_find)
    monkeypatch.setattr(sr, "_sync_leaflife_label", fake_sync)

    result = await sr.recover_missing_split_labels(db)
    assert result == {"checked": 1, "recovered": 1}

    cur = await db.execute(
        "SELECT tracking_number, label_url, shippo_transaction_id, tracking_status FROM order_shipments WHERE id = ?",
        (leaf_id,),
    )
    assert await cur.fetchone() == (
        "9400122222", "https://shippo.example/leaf.pdf", "txn-leaf", "label_created",
    )

    # Both packages now have labels, so the order is shipped.
    cur = await db.execute("SELECT payment_status FROM ecommerce_orders WHERE id = ?", (order_id,))
    assert (await cur.fetchone())[0] == "shipped"

    assert synced == [(order_id, leaf_id, "9400122222")]

    # Idempotent: nothing left to recover.
    assert await sr.recover_missing_split_labels(db) == {"checked": 0, "recovered": 0}
