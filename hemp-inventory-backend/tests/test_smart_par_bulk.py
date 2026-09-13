import os
import tempfile
import time

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_smart_par_bulk_test.db"))

import aiosqlite
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import inventory_router as inv
from app.smart_par_bulk import (
    apply_bulk_netting,
    bulk_matches_product,
    collect_bulk_pool,
    grams_per_package,
)


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


def test_bulk_matches_same_form_and_strain():
    bulk = "Bulk - Blue Dream Sativa THC Flower Grams"
    assert bulk_matches_product(bulk, "THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS")
    assert bulk_matches_product(bulk, "THC FLOWER BLUE DREAM SATIVA 1 GRAM")
    # Smalls / ground are their own bulk; regular flower bulk doesn't make them.
    assert not bulk_matches_product(bulk, "THC FLOWER SMALLS BLUE DREAM 2 GRAMS")
    assert bulk_matches_product("Bulk - Blue Dream THC Smalls Flower Grams", "THC FLOWER SMALLS BLUE DREAM 2 GRAMS")
    assert not bulk_matches_product("Bulk - Blue Dream THC Smalls Flower Grams", "THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS")
    # Pre-rolls are a different form even though the strain matches.
    assert not bulk_matches_product(bulk, "THC PRE ROLLED JOINT SATIVA BLUE DREAM")
    # Other strains don't match.
    assert not bulk_matches_product(bulk, "THC FLOWER GREEN CRACK SATIVA 3.5 GRAMS")
    # A generic bulk can't claim everything.
    assert not bulk_matches_product("Bulk - THC Flower Grams", "THC FLOWER BLUE DREAM 3.5 GRAMS")


def test_grams_per_package():
    assert grams_per_package("THC FLOWER BLUE DREAM 3.5 GRAMS") == 3.5
    assert grams_per_package("THC FLOWER SMALLS BLUE DREAM 2 GRAMS") == 2.0
    assert grams_per_package("THC DISPOSABLE VAPE ONE GRAM HYBRID") == 1.0
    assert grams_per_package("CBN GUMMIES 15MG") == 0.0


def test_collect_bulk_pool_sums_duplicates():
    items = [
        {"name": "Bulk - Blue Dream Sativa THC Flower Grams", "locations": {"HQ": {"stock": 300}}},
        {"name": "Bulk -  Blue Dream Sativa THC Flower Grams", "locations": {"HQ": {"stock": 200}}},
        {"name": "THC FLOWER BLUE DREAM 3.5 GRAMS", "locations": {"HQ": {"stock": 5}}},
    ]
    assert collect_bulk_pool(items) == {"Bulk - Blue Dream Sativa THC Flower Grams": 500}


def _row(name, order_qty):
    return {"name": name, "order_qty": order_qty}


def test_netting_shares_bulk_without_double_counting():
    rows = [
        _row("THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS", 100),  # needs 350g
        _row("THC FLOWER BLUE DREAM SATIVA 1 GRAM", 50),       # needs 50g
        _row("THC FLOWER GREEN CRACK SATIVA 3.5 GRAMS", 20),
    ]
    pool = {"Bulk - Blue Dream Sativa THC Flower Grams": 375}
    apply_bulk_netting(rows, pool, recipes={})

    eighth, gram, gc = rows
    # 400g needed, 375g on hand: each size gets ~93.75% of its need, the
    # 3.5g of rounding leftover makes one more eighth. Total used == 375g.
    assert eighth["bulk_covers"] == 94 and eighth["order_qty"] == 6
    assert gram["bulk_covers"] == 46 and gram["order_qty"] == 4
    assert eighth["bulk_covers"] * 3.5 + gram["bulk_covers"] * 1 == 375
    assert eighth["bulk_shared_by"] == 2 and gram["bulk_shared_by"] == 2
    assert eighth["bulk_stock"] == 375 and eighth["bulk_unit"] == "g"
    assert gram["gross_order_qty"] == 50
    assert gc["bulk_name"] is None and gc["order_qty"] == 20


def test_netting_does_not_starve_large_sizes():
    rows = [
        _row("THC FLOWER JEALOUSY SMALLS HYBRID 2 GRAMS", 148),
        _row("THC FLOWER JEALOUSY SMALLS HYBRID 3.5 GRAMS", 104),
        _row("THC FLOWER JEALOUSY SMALLS HYBRID 28 GRAMS", 52),
    ]
    apply_bulk_netting(rows, {"Bulk - Jealousy Hybrid THC Smalls Flower Grams": 525}, {})
    two, eighth, oz = rows
    assert two["bulk_covers"] == 50
    assert eighth["bulk_covers"] == 25
    assert oz["bulk_covers"] == 12 and oz["order_qty"] == 40
    assert sum(r["bulk_covers"] * r["bulk_per_unit"] for r in rows) <= 525
    assert all(r["bulk_shared_by"] == 3 for r in rows)


def test_netting_prefers_saved_recipe():
    rows = [_row("THC PRE ROLLED JOINT SATIVA BLUE DREAM", 10)]
    pool = {"Bulk - Blue Dream Shake Grams": 9}
    recipes = {"thc pre rolled joint sativa blue dream": ("Bulk - Blue Dream Shake Grams", 1.5)}
    apply_bulk_netting(rows, pool, recipes)
    assert rows[0]["bulk_name"] == "Bulk - Blue Dream Shake Grams"
    assert rows[0]["bulk_per_unit"] == 1.5
    assert rows[0]["bulk_covers"] == 6
    assert rows[0]["order_qty"] == 4


def test_count_bulk_is_one_per_unit():
    rows = [_row("THC DISPOSABLE VAPE TWO GRAMS HYBRID GRAPE GOJI OG", 100)]
    pool = {"Bulk - THC Disposable Vape Two Grams Hybrid Grape Goji OG": 75}
    apply_bulk_netting(rows, pool, recipes={})
    assert rows[0]["bulk_unit"] == "units"
    assert rows[0]["bulk_covers"] == 75
    assert rows[0]["order_qty"] == 25


async def test_smart_par_nets_bulk_and_on_order(db, monkeypatch):
    latest = time.time()
    orders = [
        {"createdTime": (latest - 30 * 86400) * 1000, "total": 1000,
         "lineItems": {"elements": [{"name": "THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS", "unitQty": 60000}]}},
        {"createdTime": latest * 1000, "total": 1000,
         "lineItems": {"elements": [{"name": "THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS", "unitQty": 60000}]}},
    ]

    async def fake_orders(client):
        return orders

    async def fake_locations(_db):
        return [(1, "HQ", "M1", "tok")]

    async def fake_sync(_db):
        return {"items": [
            {"name": "THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS", "sku": "BD35", "categories": ["Flower"],
             "price": 3500, "locations": {"HQ": {"stock": 2}}},
            {"name": "Bulk - Blue Dream Sativa THC Flower Grams", "sku": "", "categories": [],
             "price": 0, "locations": {"HQ": {"stock": 70}}},
        ]}

    monkeypatch.setattr(inv, "_fetch_all_clover_orders", fake_orders)
    monkeypatch.setattr(inv, "_get_locations", fake_locations)
    monkeypatch.setattr(inv, "_do_sync", fake_sync)
    monkeypatch.setitem(inv._smart_par_cache, "data", None)
    monkeypatch.setitem(inv._smart_par_cache, "updated_at", 0)

    await inv.upsert_smart_par_note(
        inv.SmartParNoteUpdate(name="THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS", note="ordered 9/3",
                               on_order_qty=10, on_order_date="2026-09-03"),
        user={"username": "kim"}, db=db,
    )

    res = await inv.smart_par(months=1, user={}, db=db)
    products = res["products"]
    assert [p["sku"] for p in products] == ["BD35"]  # bulk row is not a retail line
    p = products[0]
    assert p["par_level"] > 22
    gross = p["par_level"] - 2
    assert p["gross_order_qty"] == gross
    assert p["bulk_stock"] == 70 and p["bulk_covers"] == 20  # 70g / 3.5g
    assert p["note"] == "ordered 9/3" and p["on_order_qty"] == 10
    assert p["order_qty"] == max(gross - 20 - 10, 0)

    g = res["groups"][0]
    assert g["packages_from_bulk"] == 20
    assert g["packages_on_order"] == 10
    assert g["bulk_sources"][0]["stock"] == 70
    assert g["notes"] == ["THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS: ordered 9/3"]

    # Clearing both note and on-order deletes the row.
    await inv.upsert_smart_par_note(
        inv.SmartParNoteUpdate(name="THC FLOWER BLUE DREAM SATIVA 3.5 GRAMS"), user={}, db=db
    )
    cur = await db.execute("SELECT COUNT(*) FROM smart_par_notes")
    assert (await cur.fetchone())[0] == 0
