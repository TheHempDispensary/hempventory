"""Tests for the production planner: in-house flag matching, plan derivation
from Smart PAR, and batch tracking CRUD."""
import aiosqlite
import pytest

from app.database import DB_PATH, init_db
from app.routers import production_router as pr
from app.routers.inventory_router import _normalise_sales_name


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


async def test_plan_lists_all_products_flagged_or_not(db, monkeypatch):
    # Only SKU-A is flagged made-in-house; both products must still appear.
    await pr.set_flag("SKU-A", pr.FlagSet(made_in_house=True, product_name="Widget"), user={}, db=db)

    async def fake_smart_par(months, user, db):
        return {
            "products": [
                {"sku": "SKU-A", "name": "Widget", "categories": ["Edibles"],
                 "total_stock": 5, "units_sold": 200, "units_per_month": 50, "order_qty": 45},
                {"sku": "SKU-B", "name": "Bought Thing", "categories": ["Flower"],
                 "total_stock": 1, "units_sold": 30, "units_per_month": 8, "order_qty": 7},
            ],
            "meta": {"days_of_data": 120},
        }

    monkeypatch.setattr(pr, "smart_par", fake_smart_par)

    res = await pr.production_plan(months=3, user={}, db=db)
    by_sku = {i["sku"]: i for i in res["items"]}
    assert set(by_sku) == {"SKU-A", "SKU-B"}
    assert by_sku["SKU-A"]["made_in_house"] is True
    assert by_sku["SKU-B"]["made_in_house"] is False
    assert by_sku["SKU-B"]["to_produce"] == 7
    assert res["meta"]["flagged"] == 1


async def test_done_adds_to_hq_inventory(db, monkeypatch):
    calls = []

    async def fake_add(sku, qty, name=""):
        calls.append((sku, qty))
        return {"ok": True, "previous": 5, "new": 5 + qty, "added": qty, "item_id": "X"}

    monkeypatch.setattr(pr, "_add_to_hq_inventory", fake_add)

    created = await pr.create_batch(
        pr.BatchCreate(product_name="Gummies", sku="G-100", planned_qty=50, status="planned"),
        user={}, db=db,
    )
    done = await pr.update_batch(
        created["id"], pr.BatchUpdate(status="done", produced_qty=48), user={}, db=db,
    )
    assert calls == [("G-100", 48)]
    assert done["inventoried"] is True
    assert done["inventoried_qty"] == 48
    assert done["inventory_result"]["ok"] is True

    # Re-saving a done+inventoried batch must not add stock again.
    calls.clear()
    again = await pr.update_batch(created["id"], pr.BatchUpdate(notes="touch"), user={}, db=db)
    assert calls == []
    assert again["inventoried"] is True


async def test_done_respects_skip_and_missing_sku(db, monkeypatch):
    calls = []

    async def fake_add(sku, qty, name=""):
        calls.append((sku, qty))
        return {"ok": True, "added": qty}

    monkeypatch.setattr(pr, "_add_to_hq_inventory", fake_add)

    # add_to_inventory=False skips.
    b1 = await pr.create_batch(pr.BatchCreate(product_name="A", sku="A1", planned_qty=10), user={}, db=db)
    r1 = await pr.update_batch(b1["id"], pr.BatchUpdate(status="done", add_to_inventory=False), user={}, db=db)
    assert calls == []
    assert r1["inventoried"] is False

    # No SKU but a product name → falls back to matching HQ stock by name.
    b2 = await pr.create_batch(pr.BatchCreate(product_name="B", planned_qty=10), user={}, db=db)
    r2 = await pr.update_batch(b2["id"], pr.BatchUpdate(status="done"), user={}, db=db)
    assert calls == [("", 10)]
    assert r2["inventoried"] is True


async def test_manual_add_to_inventory_endpoint(db, monkeypatch):
    async def fake_add(sku, qty, name=""):
        return {"ok": True, "previous": 0, "new": qty, "added": qty, "item_id": "Z"}

    monkeypatch.setattr(pr, "_add_to_hq_inventory", fake_add)
    b = await pr.create_batch(pr.BatchCreate(product_name="C", sku="C1", produced_qty=7, status="done", add_to_inventory=False), user={}, db=db)
    assert b["inventoried"] is False
    res = await pr.add_batch_to_inventory(b["id"], user={}, db=db)
    assert res["inventoried"] is True
    assert res["inventoried_qty"] == 7
    with pytest.raises(Exception):
        await pr.add_batch_to_inventory(b["id"], user={}, db=db)  # already done


async def test_plan_reports_full_need_without_deducting_open_batches(db, monkeypatch):
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

    # Open batches are surfaced in "already_planned" but do NOT reduce
    # to_produce — the plan always shows the full Smart PAR need.
    await pr.create_batch(pr.BatchCreate(product_name="Widget", sku="SKU-A", planned_qty=30, status="in_production"), user={}, db=db)
    await pr.create_batch(pr.BatchCreate(product_name="Widget", sku="SKU-A", planned_qty=999, status="done"), user={}, db=db)

    res = await pr.production_plan(months=3, user={}, db=db)
    item = res["items"][0]
    assert item["already_planned"] == 30
    assert item["to_produce"] == 100


def test_normalise_sales_name_ignores_strain_type():
    # A renamed title (with the strain type) matches its historical sales name.
    assert (
        _normalise_sales_name("THC FLOWER NERDS HYBRID 1 GRAM")
        == _normalise_sales_name("THC FLOWER NERDS 1 GRAM")
    )
    assert (
        _normalise_sales_name("THC FLOWER TAHOE OG INDICA 3.5 GRAMS")
        == _normalise_sales_name("THC FLOWER TAHOE OG 3.5 GRAMS")
    )
    # Non-type words are preserved; different products stay distinct.
    assert (
        _normalise_sales_name("THC FLOWER NERDS HYBRID 1 GRAM")
        != _normalise_sales_name("THC FLOWER GUAVA HYBRID 1 GRAM")
    )


async def test_reorder_persists_sort_order(db):
    a = await pr.create_batch(pr.BatchCreate(product_name="A", planned_qty=1, status="planned"), user={}, db=db)
    b = await pr.create_batch(pr.BatchCreate(product_name="B", planned_qty=1, status="planned"), user={}, db=db)
    c = await pr.create_batch(pr.BatchCreate(product_name="C", planned_qty=1, status="planned"), user={}, db=db)

    await pr.reorder_batches(pr.BatchReorder(ids=[c["id"], a["id"], b["id"]]), user={}, db=db)

    res = await pr.list_batches(status="planned", user={}, db=db)
    assert [x["product_name"] for x in res["batches"]] == ["C", "A", "B"]


async def test_add_to_hq_inventory_name_fallback(monkeypatch):
    import app.routers.production_router as prod

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def get_items(self, expand=""):
            return {"elements": [
                {"id": "HQID1", "sku": "", "name": "THC FLOWER SKYWALKER OG INDICA 1 GRAM",
                 "itemStock": {"quantity": 4}},
            ]}

        async def update_item_stock(self, item_id, qty):
            self.updated = (item_id, qty)

    monkeypatch.setattr(prod, "CloverClient", FakeClient)
    monkeypatch.setattr(
        "app.routers.ecommerce_router.HQ_MERCHANT_ID", "M", raising=False
    )
    monkeypatch.setattr(
        "app.routers.ecommerce_router.HQ_API_TOKEN", "T", raising=False
    )
    monkeypatch.setattr(
        "app.routers.ecommerce_router.invalidate_product_cache", lambda: None, raising=False
    )

    # SKU/id don't match, but the (type-stripped) name does.
    res = await prod._add_to_hq_inventory(
        "Z6NE10A9F5WM8", 10, "THC FLOWER SKYWALKER OG 1 GRAM"
    )
    assert res["ok"] is True
    assert res["item_id"] == "HQID1"
    assert res["new"] == 14
