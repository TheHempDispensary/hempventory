import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_inv_merge_test.db"))

import aiosqlite
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import inventory_router as inv


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


def test_strip_batch_suffix():
    s = inv._strip_batch_suffix
    assert s("DELTA 8 WAX SATIVA PINEAPPLE EXPRESS BATCH 01182515") == "DELTA 8 WAX SATIVA PINEAPPLE EXPRESS"
    assert s("DELTA 8 WAX SATIVA PINEAPPLE EXPRESS") == "DELTA 8 WAX SATIVA PINEAPPLE EXPRESS"
    # a "batch" that isn't a trailing lot code is left alone
    assert s("THC FLOWER BATCH BROWNIE 3.5 GRAMS") == "THC FLOWER BATCH BROWNIE 3.5 GRAMS"
    # multiple trailing suffixes
    assert s("Gummies BATCH 12 BATCH 34") == "Gummies"


async def test_do_sync_merges_batch_named_item_across_locations(db, monkeypatch):
    """Same SKU, one store carrying a 'BATCH ####' name, merges into one row."""
    await db.execute("INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)", ("East", "EAST", "t"))
    await db.execute("INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)", ("HQ", "HQ", "t"))
    await db.commit()

    catalogs = {
        "EAST": [{"id": "east1", "sku": "702575439857",
                  "name": "DELTA 8 THC WAX THREE GRAMS SATIVA PINEAPPLE EXPRESS BATCH 01182515",
                  "itemStock": {"quantity": 6}}],
        "HQ": [{"id": "hq1", "sku": "702575439857",
                "name": "DELTA 8 THC WAX THREE GRAMS SATIVA PINEAPPLE EXPRESS",
                "itemStock": {"quantity": 13}}],
    }

    class FakeClient:
        def __init__(self, merchant_id, api_token, *a, **k):
            self.merchant_id = merchant_id

        async def get_item_groups(self):
            return {"elements": []}

        async def get_items(self, expand=""):
            return {"elements": catalogs[self.merchant_id]}

    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    result = await inv._do_sync(db)
    rows = [i for i in result["items"] if "PINEAPPLE EXPRESS" in i["name"]]
    assert len(rows) == 1, [r["name"] for r in rows]
    row = rows[0]
    assert row["name"] == "DELTA 8 THC WAX THREE GRAMS SATIVA PINEAPPLE EXPRESS"
    assert row["locations"]["East"]["stock"] == 6
    assert row["locations"]["HQ"]["stock"] == 13


async def test_do_sync_merges_case_only_name_difference(db, monkeypatch):
    """Same SKU where stores differ only by capitalization merges into one row."""
    await db.execute("INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)", ("East", "EAST", "t"))
    await db.execute("INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)", ("HQ", "HQ", "t"))
    await db.commit()

    catalogs = {
        "EAST": [{"id": "east1", "sku": "058808442752",
                  "name": "THC FLOWER SMALLS BLUE DREAM SATIVA 28 GRAMS",
                  "itemStock": {"quantity": 16}}],
        "HQ": [{"id": "hq1", "sku": "058808442752",
                "name": "THC FLOWER SMALLS BLUE DREAM Sativa 28 GRAMS",
                "itemStock": {"quantity": 9}}],
    }

    class FakeClient:
        def __init__(self, merchant_id, api_token, *a, **k):
            self.merchant_id = merchant_id

        async def get_item_groups(self):
            return {"elements": []}

        async def get_items(self, expand=""):
            return {"elements": catalogs[self.merchant_id]}

    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    result = await inv._do_sync(db)
    rows = [i for i in result["items"] if "BLUE DREAM" in i["name"].upper()]
    assert len(rows) == 1, [r["name"] for r in rows]
    row = rows[0]
    assert row["locations"]["East"]["stock"] == 16
    assert row["locations"]["HQ"]["stock"] == 9


async def test_do_sync_shared_sku_rows_do_not_log_phantom_changes(db, monkeypatch):
    """Two differently-named items sharing a SKU must not flip the (sku, location)
    snapshot back and forth and log a change on every sync."""
    await db.execute("INSERT INTO locations (name, merchant_id, api_token) VALUES (?, ?, ?)", ("East", "EAST", "t"))
    await db.commit()

    catalogs = {
        "EAST": [
            {"id": "a", "sku": "2025754319197", "name": "THC WAX THREE GRAMS INDICA KING LOUIS",
             "itemStock": {"quantity": 0}},
            {"id": "b", "sku": "2025754319197", "name": "THC WAX 3G KING LOUIS INDICA",
             "itemStock": {"quantity": 3}},
        ],
    }

    class FakeClient:
        def __init__(self, merchant_id, api_token, *a, **k):
            self.merchant_id = merchant_id

        async def get_item_groups(self):
            return {"elements": []}

        async def get_items(self, expand=""):
            return {"elements": catalogs[self.merchant_id]}

    monkeypatch.setattr(inv, "CloverClient", FakeClient)

    await inv._do_sync(db)
    await inv._do_sync(db)
    await inv._do_sync(db)

    cur = await db.execute("SELECT COUNT(*) FROM inventory_changes WHERE sku = ?", ("2025754319197",))
    assert (await cur.fetchone())[0] == 0
    cur = await db.execute(
        "SELECT stock FROM inventory_snapshots WHERE sku = ? AND location_name = ?", ("2025754319197", "East")
    )
    assert (await cur.fetchone())[0] == 3


def test_narrow_to_named_targets_only_the_edited_row():
    items = [
        {"id": "a", "name": "THC WAX THREE GRAMS INDICA KING LOUIS"},
        {"id": "b", "name": "THC WAX 3G KING LOUIS INDICA"},
        {"id": "c", "name": "THC WAX THREE GRAMS INDICA KING LOUIS BATCH 0912"},
    ]
    picked = inv._narrow_to_named(items, "THC Wax Three Grams Indica King Louis")
    assert [i["id"] for i in picked] == ["a", "c"]
    # unknown name falls back to everything rather than matching nothing
    assert inv._narrow_to_named(items, "SOMETHING ELSE") == items
    assert inv._narrow_to_named(items, None) == items
