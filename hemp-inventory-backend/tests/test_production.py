"""Tests for the production planner: in-house flag matching, plan derivation
from Smart PAR, and batch tracking CRUD."""
import aiosqlite
import pytest

from app.database import DB_PATH, init_db
from app.routers import production_router as pr


@pytest.fixture
async def db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await init_db()
    yield db
    await db.execute("DELETE FROM production_flags")
    await db.execute("DELETE FROM production_batches")
    await db.commit()
    await db.close()


def test_fuzzy_match_hits_and_misses():
    seed_tokens = pr._tokens("Cold Brew 2 oz")
    seed_compact = pr._compact("Cold Brew 2 oz")
    assert pr._is_match(seed_tokens, seed_compact, "Cold Brew 2 oz")
    assert pr._is_match(seed_tokens, seed_compact, "THD Cold Brew (2oz)")
    assert not pr._is_match(seed_tokens, seed_compact, "Lemonade 8 oz")
    # A single shared, non-descriptive token should not match.
    st = pr._tokens("Delta 8 90mg gummy")
    sc = pr._compact("Delta 8 90mg gummy")
    assert not pr._is_match(st, sc, "Delta 9 Beverage")


async def test_flag_toggle_and_list(db):
    await pr.set_flag("SKU1", pr.FlagSet(made_in_house=True, product_name="Cold Brew"), user={}, db=db)
    res = await pr.list_flags(user={}, db=db)
    assert any(f["sku"] == "SKU1" for f in res["flags"])
    await pr.set_flag("SKU1", pr.FlagSet(made_in_house=False), user={}, db=db)
    res = await pr.list_flags(user={}, db=db)
    assert not any(f["sku"] == "SKU1" for f in res["flags"])


async def test_batch_lifecycle_stamps_completion(db):
    created = await pr.create_batch(
        pr.BatchCreate(product_name="Lemonade 2 oz", sku="LEM2", planned_qty=40, status="planned"),
        user={}, db=db,
    )
    assert created["status"] == "planned"
    assert created["completed_at"] is None

    updated = await pr.update_batch(
        created["id"], pr.BatchUpdate(status="done", produced_qty=38, qa_check=True), user={}, db=db,
    )
    assert updated["status"] == "done"
    assert updated["completed_at"] is not None
    assert updated["qa_check"] is True
    assert updated["produced_qty"] == 38

    # Reverting out of done clears the completion stamp.
    reverted = await pr.update_batch(created["id"], pr.BatchUpdate(status="ready"), user={}, db=db)
    assert reverted["completed_at"] is None

    await pr.delete_batch(created["id"], user={}, db=db)
    res = await pr.list_batches(user={}, db=db)
    assert not any(b["id"] == created["id"] for b in res["batches"])


async def test_create_batch_rejects_bad_status(db):
    with pytest.raises(Exception):
        await pr.create_batch(
            pr.BatchCreate(product_name="X", status="bogus"), user={}, db=db,
        )


async def test_plan_empty_without_flags(db):
    res = await pr.production_plan(months=3, user={}, db=db)
    assert res["items"] == []
    assert res["meta"]["flagged"] == 0


async def test_plan_subtracts_open_batches(db, monkeypatch):
    # Flag a product and stub Smart PAR to report it needs 100 units.
    await pr.set_flag("SKU-A", pr.FlagSet(made_in_house=True, product_name="Widget"), user={}, db=db)

    async def fake_smart_par(months, user, db):
        return {
            "products": [{
                "sku": "SKU-A", "name": "Widget", "categories": ["Edibles"],
                "total_stock": 5, "units_sold": 200, "units_per_month": 50,
                "order_qty": 100,
            }],
            "meta": {"days_of_data": 120},
        }

    monkeypatch.setattr(pr, "smart_par", fake_smart_par)

    res = await pr.production_plan(months=3, user={}, db=db)
    item = res["items"][0]
    assert item["needed"] == 100
    assert item["to_produce"] == 100

    # An open batch of 30 reduces to_produce to 70; a done batch does not.
    await pr.create_batch(pr.BatchCreate(product_name="Widget", sku="SKU-A", planned_qty=30, status="in_production"), user={}, db=db)
    await pr.create_batch(pr.BatchCreate(product_name="Widget", sku="SKU-A", planned_qty=999, status="done"), user={}, db=db)

    res = await pr.production_plan(months=3, user={}, db=db)
    item = res["items"][0]
    assert item["already_planned"] == 30
    assert item["to_produce"] == 70
