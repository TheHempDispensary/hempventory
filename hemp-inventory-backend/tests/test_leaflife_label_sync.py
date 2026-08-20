"""Tests for writing the printable label link into the Order Sheet (column Z).

The Google Sheets calls are mocked — no live network or Sheet mutation.
"""
from app import leaflife_orders as lo
from app.routers.shipping_router import _label_token, print_label_url


def test_label_cell_is_hyperlink_labelled_with_tracking():
    cell = lo.label_cell("https://api/print-label/6A653011/abc", "9300120845500063955601")
    assert cell == '=HYPERLINK("https://api/print-label/6A653011/abc","9300120845500063955601")'


def test_label_cell_falls_back_to_tracking_only():
    assert lo.label_cell("", "93001208455") == "93001208455"
    assert lo.label_cell("", "") == ""


def test_label_column_is_last_written_column():
    # Z is the label column; AA onwards is LeafLife's and must stay untouched.
    assert lo.COL_LABEL == 25
    assert lo._ROW_WIDTH == 26


async def test_sync_label_writes_row(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SA_JSON", "{}")
    captured = {}

    monkeypatch.setattr(lo, "_order_row_sync", lambda short: 641 if short == "6A653011" else None)
    monkeypatch.setattr(lo, "_label_at_sync", lambda row: "")
    monkeypatch.setattr(
        lo, "_write_label_sync", lambda row, value: captured.update(row=row, value=value)
    )

    res = await lo.sync_label(
        order_number="HD-6A653011-1234",
        label_url="https://api/print-label/6A653011/abc",
        tracking_number="9300120845500063955601",
    )
    assert res["ok"] is True and res["written"] == 1
    assert captured["row"] == 641
    assert "9300120845500063955601" in captured["value"]


async def test_sync_label_skips_when_already_filled(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SA_JSON", "{}")
    wrote = {"called": False}

    monkeypatch.setattr(lo, "_order_row_sync", lambda short: 641)
    monkeypatch.setattr(lo, "_label_at_sync", lambda row: "9300120845500063955601.png")
    monkeypatch.setattr(
        lo, "_write_label_sync", lambda row, value: wrote.update(called=True)
    )

    res = await lo.sync_label(
        order_number="HD-6A653011-1234", label_url="https://api/x", tracking_number="930012"
    )
    assert res["ok"] is True and res["written"] == 0
    assert wrote["called"] is False


async def test_sync_label_when_order_not_on_sheet(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SA_JSON", "{}")
    monkeypatch.setattr(lo, "_order_row_sync", lambda short: None)

    res = await lo.sync_label(
        order_number="HD-6A653011-1234", label_url="https://api/x", tracking_number="930012"
    )
    assert res["ok"] is False and res["reason"] == "order not on sheet"


async def test_sync_label_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SHEETS_SA_FILE", raising=False)
    res = await lo.sync_label(
        order_number="HD-1-1", label_url="https://api/x", tracking_number="930012"
    )
    assert res["ok"] is False and "not configured" in res["reason"]


def test_print_label_url_uses_short_order_no_and_token(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://api.example.com")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    url = print_label_url("HD-6A653011-1234")
    assert url == f"https://api.example.com/api/shipping/print-label/6A653011/{_label_token('6A653011')}"


def test_label_tokens_differ_per_order(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    assert _label_token("6A653011") != _label_token("6A653012")
    assert len(_label_token("6A653011")) == 20
