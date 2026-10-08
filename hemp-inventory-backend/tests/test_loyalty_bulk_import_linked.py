"""A Clover profile already linked to a member must not become a second member.

After Tiffany Mendez's two accounts were merged, her old-number Clover profile
stayed linked to the kept account, yet the scheduled import saw an unknown phone
and re-created the old account with a fresh sign-up bonus.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_bulk_import_linked_test.db"))

import aiosqlite
import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import loyalty_router as lr

MERCHANT = "MERCHANT1"


def _profile(cc_id, phone):
    return {
        "id": cc_id,
        "firstName": "Tiffany",
        "lastName": "Mendez",
        "phoneNumbers": {"elements": [{"phoneNumber": phone}]},
        "emailAddresses": {"elements": []},
    }


@pytest_asyncio.fixture
async def db(monkeypatch):
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "INSERT INTO locations (name, merchant_id, api_token) VALUES ('West', ?, 'tok')", (MERCHANT,)
    )
    await conn.execute(
        "INSERT INTO loyalty_customers (id, first_name, last_name, phone) VALUES (1, 'Tiffany', 'Mendez', '8134429687')"
    )
    await conn.execute(
        "INSERT INTO loyalty_clover_id_map (loyalty_customer_id, clover_customer_id, merchant_id) VALUES (1, 'OLDPROFILE', ?)",
        (MERCHANT,),
    )
    await conn.commit()

    profiles = [_profile("OLDPROFILE", "813-541-9173"), _profile("NEWPROFILE", "727-555-0101")]

    class FakeClover:
        def __init__(self, merchant_id, api_token):
            pass

        async def get_customers(self, limit=100, offset=0):
            return {"elements": profiles if offset == 0 else []}

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(lr, "CloverClient", FakeClover)
    monkeypatch.setattr(lr.asyncio, "sleep", no_sleep)
    yield conn
    await conn.close()
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


async def _phones(db):
    cursor = await db.execute("SELECT phone FROM loyalty_customers ORDER BY id")
    return [row["phone"] for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_scheduled_import_skips_linked_profile(db):
    await lr._do_bulk_import_customers(db)

    assert await _phones(db) == ["8134429687", "7275550101"]


@pytest.mark.asyncio
async def test_manual_import_skips_linked_profile(db):
    await lr.bulk_import_clover_customers(user={}, db=db)

    assert await _phones(db) == ["8134429687", "7275550101"]
