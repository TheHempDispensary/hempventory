"""Tests for the LeafLife Order Sheet writer.

Pure row-building / parsing logic plus the append orchestration with the
Google Sheets calls mocked out — no live network or Sheet mutation.
"""
import pytest

from app import leaflife_orders as lo


def test_card_fee_matches_existing_rows():
    # Reverse-engineered from the sheet: 2% + $0.35.
    assert lo.card_fee_cents(10000) == 235
    assert lo.card_fee_cents(24000) == 515
    assert lo.card_fee_cents(30000) == 635


def test_parse_size():
    assert lo.parse_size("OG Kush 28 gram") == "28g"
    assert lo.parse_size("Live Resin 1 gram") == "1g"
    assert lo.parse_size("Blend 3.5 gram jar") == "3.5g"
    # Compact "g" form used on the storefront.
    assert lo.parse_size("Illemonati Snowcaps THCA Flower 28g") == "28g"
    assert lo.parse_size("Live Resin 3.5g") == "3.5g"
    # Must not misread mg/kg.
    assert lo.parse_size("Gummies 100mg") == ""
    assert lo.parse_size("Mystery item") == ""


def test_short_order_no():
    assert lo.short_order_no("HD-6A653011-1234") == "6A653011"
    assert lo.short_order_no("6A653011") == "6A653011"
    assert lo.short_order_no("") == ""


def test_usps_method():
    assert lo.usps_method("USPS Priority Mail") == "Priority"
    assert lo.usps_method("Ground Advantage") == "Ground"
    assert lo.usps_method("") == ""


def test_leaflife_items_filters_lf_only():
    items = [
        {"name": "OG Kush", "sku": "LF-OG-28", "price": 10000, "quantity": 1},
        {"name": "House Gummy", "sku": "THD-GUM-1", "price": 2000, "quantity": 2},
        {"name": "no sku", "sku": "", "price": 100, "quantity": 1},
        {"name": "lower prefix", "sku": "lf-abc", "price": 100, "quantity": 1},
    ]
    lf = lo.leaflife_items(items)
    assert [i["sku"] for i in lf] == ["LF-OG-28", "lf-abc"]


def test_state_name():
    assert lo.state_name("FL") == "Florida"
    assert lo.state_name("fl") == "Florida"
    assert lo.state_name("Florida") == "Florida"
    assert lo.state_name("ZZ") == "ZZ"


def test_match_strain_prefers_longest():
    lookup = {
        lo._norm("Blue Dream"): {"display": "Blue Dream", "coa": "c1", "kind": "flower"},
        lo._norm("Super Blue Dream"): {"display": "Super Blue Dream", "coa": "c2", "kind": "flower"},
    }
    m = lo.match_strain("Super Blue Dream 28 gram", lookup)
    assert m is not None and m["display"] == "Super Blue Dream"


def _lookup():
    return {
        lo._norm("Blue Dream"): {"display": "Blue Dream", "coa": "flower-coa", "kind": "flower"},
        lo._norm("Live Resin"): {"display": "Live Resin", "coa": "conc-coa", "kind": "concentrate"},
        lo._norm("Bulk OG"): {"display": "Bulk OG", "coa": "bulk-coa", "kind": "bulk"},
    }


def test_build_rows_column_placement_and_totals():
    lf_items = [
        {"name": "Blue Dream 28 gram", "sku": "LF-BD-28", "price": 10000, "quantity": 1},
        {"name": "Live Resin 1 gram", "sku": "LF-LR-1", "price": 3500, "quantity": 2},
        {"name": "Bulk OG 448 gram", "sku": "LF-BOG", "price": 50000, "quantity": 1},
    ]
    rows = lo.build_rows(
        order_number="HD-6A653011-1234",
        order_date="07/02/2026",
        status="Processing",
        first_name="Jane",
        last_name="Doe",
        street="1 Main St",
        city="Spring Hill",
        state="FL",
        zip_code="34608",
        notes="leave at door",
        ship_method="Priority",
        lf_items=lf_items,
        lookup=_lookup(),
    )
    # 1 info row + 1 flower + 2 concentrate units + 1 bulk = 5
    assert len(rows) == 5
    info = rows[0]
    assert info[lo.COL_ORDER_NO] == "6A653011"
    assert info[lo.COL_STATUS] == "Processing"
    assert info[lo.COL_FIRST] == "Jane"
    assert info[lo.COL_ZIP] == "34608"
    assert info[lo.COL_SHIP_METHOD] == "Priority"
    # State code is expanded to the full name the sheet uses.
    assert info[lo.COL_STATE] == "Florida"
    # Total = LF subtotal = 100 + 35*2 + 500 = 670.00, stored as a plain number.
    assert info[lo.COL_TOTAL] == "670.00"
    assert info[lo.COL_CARD_FEE] == lo._money(lo.card_fee_cents(67000))
    # Order-details/PDF column is intentionally left blank.
    assert info[lo.COL_ORDER_LINK] == ""
    # Column A (group) is left blank — the sheet's array formula fills it.
    assert info[lo.COL_GROUP] == ""

    flower = rows[1]
    assert flower[lo.COL_FLOWER] == "Blue Dream"
    assert flower[lo.COL_FLOWER_QTY] == "28g"
    # COA columns are left blank — the sheet VLOOKUPs them from the product name.
    assert flower[lo.COL_FLOWER_COA] == ""

    conc = rows[2]
    assert conc[lo.COL_CONC] == "Live Resin"
    assert conc[lo.COL_CONC_COA] == ""

    bulk = rows[4]
    assert bulk[lo.COL_BULK] == "Bulk OG"
    assert bulk[lo.COL_BULK_COA] == ""

    assert all(len(r) == lo._ROW_WIDTH for r in rows)


def test_build_rows_one_row_per_unit():
    rows = lo.build_rows(
        order_number="6ABC",
        order_date="07/02/2026",
        status="Processing",
        first_name="A",
        last_name="B",
        street="",
        city="",
        state="",
        zip_code="",
        notes="",
        ship_method="",
        lf_items=[{"name": "Blue Dream 28 gram", "sku": "LF-BD", "price": 100, "quantity": 3}],
        lookup=_lookup(),
    )
    assert len(rows) == 4  # info + 3 units


async def test_sync_order_no_lf_items():
    res = await lo.sync_order(
        order_number="HD-1-1",
        first_name="A", last_name="B", street="", city="", state="", zip_code="",
        notes="", shipping_service="", items=[{"name": "x", "sku": "THD-1", "price": 1, "quantity": 1}],
    )
    assert res["ok"] is False and res["written"] == 0


async def test_sync_order_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SHEETS_SA_FILE", raising=False)
    res = await lo.sync_order(
        order_number="HD-1-1",
        first_name="A", last_name="B", street="", city="", state="", zip_code="",
        notes="", shipping_service="", items=[{"name": "OG", "sku": "LF-OG", "price": 1, "quantity": 1}],
    )
    assert res["ok"] is False and "not configured" in res["reason"]


async def test_sync_order_appends_with_mocks(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SA_JSON", "{}")
    captured = {}

    def fake_existing():
        return set()

    def fake_append(rows):
        captured["rows"] = rows

    async def fake_lookup():
        return _lookup()

    monkeypatch.setattr(lo, "_existing_order_numbers_sync", fake_existing)
    monkeypatch.setattr(lo, "_append_rows_sync", fake_append)
    monkeypatch.setattr(lo, "build_menu_lookup", fake_lookup)

    res = await lo.sync_order(
        order_number="HD-6A653011-1234",
        first_name="Jane", last_name="Doe",
        street="1 Main St", city="Spring Hill", state="FL", zip_code="34608",
        notes="", shipping_service="USPS Priority",
        items=[{"name": "Blue Dream 28 gram", "sku": "LF-BD-28", "price": 10000, "quantity": 1}],
    )
    assert res["ok"] is True
    assert res["written"] == 2  # info + 1 product
    assert captured["rows"][0][lo.COL_ORDER_NO] == "6A653011"


async def test_sync_order_idempotent_when_present(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SA_JSON", "{}")
    appended = {"called": False}

    def fake_existing():
        return {"6A653011"}

    def fake_append(rows):
        appended["called"] = True

    async def fake_lookup():
        return _lookup()

    monkeypatch.setattr(lo, "_existing_order_numbers_sync", fake_existing)
    monkeypatch.setattr(lo, "_append_rows_sync", fake_append)
    monkeypatch.setattr(lo, "build_menu_lookup", fake_lookup)

    res = await lo.sync_order(
        order_number="HD-6A653011-1234",
        first_name="Jane", last_name="Doe",
        street="", city="", state="", zip_code="",
        notes="", shipping_service="",
        items=[{"name": "Blue Dream 28 gram", "sku": "LF-BD-28", "price": 10000, "quantity": 1}],
    )
    assert res["ok"] is True and res["written"] == 0
    assert res["reason"] == "already present"
    assert appended["called"] is False
