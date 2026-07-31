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
