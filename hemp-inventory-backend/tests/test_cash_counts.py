"""System Cash Count is filled from Clover cash, split at the 2:30 PM shift change,
without ever touching a cell staff already filled in."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app import cash_counts as cc

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 9)
HEADER = ["DATE", "AM/PM", "BUDTENDER", "SYSTEM CASH COUNT", "PHYSICAL CASH COUNT"]


def _pay(day, hh, mm, cents, tender=cc.CASH_TENDER, result="SUCCESS", refunds=()):
    ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)
    return {
        "createdTime": int(ts.timestamp() * 1000),
        "amount": cents,
        "result": result,
        "tender": {"labelKey": tender},
        "refunds": {"elements": [{"amount": r} for r in refunds]},
    }


def test_shift_totals_splits_at_shift_change_and_ignores_non_cash():
    d = date(2026, 9, 2)
    payments = [
        _pay(d, 8, 11, 1331),
        _pay(d, 14, 1, 2663),
        _pay(d, 14, 30, 3408),
        _pay(d, 19, 53, 1331, refunds=[331]),
        _pay(d, 12, 0, 5000, tender="com.clover.tender.credit_card"),
        _pay(d, 12, 0, 5000, result="FAIL"),
    ]
    assert cc.shift_totals(payments) == {d: (3994, 4408)}


def test_shift_totals_uses_eastern_day():
    late = _pay(date(2026, 9, 2), 23, 30, 500)  # 03:30 UTC on 9/3
    assert cc.shift_totals([late]) == {date(2026, 9, 2): (0, 500)}


def test_fills_both_empty_shifts():
    rows = [HEADER, ["9/10/2026", "AM", "", ""], ["9/10/2026", "PM", "", ""]]
    totals = {date(2026, 9, 10): (12345, 6789)}
    assert cc.plan_updates(rows, totals, TODAY) == {2: 12345, 3: 6789}


def test_pm_marked_na_puts_whole_day_in_am():
    rows = [HEADER, ["9/10/2026", "AM", "Moe", ""], ["9/10/2026", "PM", "N/A", " N/A ", "N/A"]]
    totals = {date(2026, 9, 10): (30000, 35955)}
    assert cc.plan_updates(rows, totals, TODAY) == {2: 65955}


def test_never_overwrites_filled_cells_and_balances_the_other_shift():
    rows = [
        HEADER,
        ["9/9/2026", "AM", "Kayla", 157.81],
        ["9/9/2026", "PM", "Moe", ""],
        ["9/8/2026", "AM", "Seamus", 13.31],
        ["9/8/2026", "PM", "Tracy", 141.16],
    ]
    totals = {date(2026, 9, 9): (15000, 13828), date(2026, 9, 8): (1000, 14447)}
    assert cc.plan_updates(rows, totals, TODAY) == {3: 28828 - 15781}


def test_skips_today_future_and_impossible_remainders():
    rows = [
        HEADER,
        ["10/9/2026", "AM", "", ""],
        ["10/9/2026", "PM", "", ""],
        ["10/1/2026", "AM", "", 500.0],
        ["10/1/2026", "PM", "", ""],
    ]
    totals = {date(2026, 10, 9): (100, 100), date(2026, 10, 1): (100, 100)}
    assert cc.plan_updates(rows, totals, TODAY) == {}


def test_day_with_no_cash_is_zero():
    rows = [HEADER, ["9/12/2026", "AM", "", ""], ["9/12/2026", "PM", "", ""]]
    assert cc.plan_updates(rows, {}, TODAY) == {2: 0, 3: 0}


class _FakeDB:
    async def execute(self, *_):
        class _C:
            async def fetchall(self):
                return [("East", "M1", "T1")]
        return _C()


@pytest.mark.asyncio
async def test_fill_writes_only_planned_cells(monkeypatch):
    tabs = {
        "September SHE": [HEADER, ["9/10/2026", "AM", "", ""], ["9/10/2026", "PM", "", 1.0]],
        "October SHE": [HEADER, ["10/9/2026", "AM", "", ""]],
    }
    writes = []
    monkeypatch.setattr(cc, "_existing_tabs_sync", lambda: set(tabs))
    monkeypatch.setattr(cc, "_read_tab_sync", lambda tab: tabs[tab])
    monkeypatch.setattr(cc, "_write_sync", lambda data: writes.extend(data))

    async def fake_fetch(*_):
        return [_pay(date(2026, 9, 10), 10, 0, 1000), _pay(date(2026, 9, 10), 16, 0, 250)]

    monkeypatch.setattr(cc, "_fetch_payments", fake_fetch)
    result = await cc.fill_cash_counts(_FakeDB(), today=TODAY)
    assert writes == [{"range": "'September SHE'!D2", "values": [[11.5]]}]
    assert result["missing_tabs"] == []


@pytest.mark.asyncio
async def test_dry_run_and_missing_tab(monkeypatch):
    monkeypatch.setattr(cc, "_existing_tabs_sync", lambda: {"September SHE"})
    monkeypatch.setattr(cc, "_read_tab_sync", lambda tab: [HEADER, ["9/30/2026", "AM", "", ""]])
    monkeypatch.setattr(cc, "_write_sync", lambda data: pytest.fail("dry run wrote"))

    async def fake_fetch(*_):
        return []

    monkeypatch.setattr(cc, "_fetch_payments", fake_fetch)
    result = await cc.fill_cash_counts(_FakeDB(), today=TODAY, dry_run=True)
    assert result["written"] == [{"tab": "September SHE", "row": 2, "amount": 0.0}]
    assert result["missing_tabs"] == ["October SHE"]
