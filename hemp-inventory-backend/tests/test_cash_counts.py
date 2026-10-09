"""System Cash Count is filled from Clover cash, split at the scheduled shift change
(2:30 PM when there is no schedule), without ever touching a cell staff already filled in."""
from datetime import date, datetime, time
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


def test_schedule_shift_changes():
    rows = [
        ("2026-09-01", "07:00", "14:30"), ("2026-09-01", "07:00", "14:30"), ("2026-09-01", "14:30", "22:00"),
        ("2026-09-11", "07:00", "16:30"), ("2026-09-11", "17:00", "22:00"),
        ("2026-09-16", "09:00", "15:30"), ("2026-09-16", "15:30", "22:00"),
        ("2026-09-17", "09:00", "22:00"),
        ("2026-09-28", "14:00", "22:00"), ("2026-09-28", "09:00", "14:00"),
        ("2026-09-29", "bad", "22:00"),
    ]
    assert cc.schedule_shift_changes(rows) == {
        date(2026, 9, 1): time(14, 30),
        date(2026, 9, 11): time(17, 0),
        date(2026, 9, 16): time(15, 30),
        date(2026, 9, 17): None,
        date(2026, 9, 28): time(14, 0),
    }


def test_shift_totals_uses_scheduled_split_and_single_budtender_days():
    wed, solo, unscheduled = date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)
    payments = [
        _pay(wed, 15, 23, 1598), _pay(wed, 15, 30, 500),
        _pay(solo, 10, 0, 100), _pay(solo, 20, 0, 200),
        _pay(unscheduled, 14, 29, 1), _pay(unscheduled, 14, 30, 2),
    ]
    changes = {wed: time(15, 30), solo: None}
    assert cc.shift_totals(payments, changes) == {wed: (1598, 500), solo: (300, 0), unscheduled: (1, 2)}


def test_single_budtender_day_fills_am_and_marks_pm_na():
    rows = [HEADER, ["10/1/2026", "AM", "", ""], ["10/1/2026", "PM", "", ""]]
    totals = {date(2026, 10, 1): (20950, 0)}
    assert cc.plan_updates(rows, totals, TODAY, frozenset({date(2026, 10, 1)})) == {2: 20950, 3: "N/A"}


def test_single_budtender_day_keeps_staff_pm_entry():
    rows = [HEADER, ["10/1/2026", "AM", "", ""], ["10/1/2026", "PM", "Moe", 50.0]]
    totals = {date(2026, 10, 1): (20950, 0)}
    assert cc.plan_updates(rows, totals, TODAY, frozenset({date(2026, 10, 1)})) == {2: 15950}


class _FakeDB:
    def __init__(self, schedules=()):
        self.schedules = list(schedules)

    async def execute(self, sql, *_):
        rows = self.schedules if "date_schedules" in sql else [("East", "M1", "T1")]

        class _C:
            async def fetchall(self):
                return rows
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


@pytest.mark.asyncio
async def test_fill_uses_schedule(monkeypatch):
    tabs = {
        "September SHE": [HEADER, ["9/16/2026", "AM", "", ""], ["9/16/2026", "PM", "", ""],
                          ["9/17/2026", "AM", "", ""], ["9/17/2026", "PM", "", ""]],
        "October SHE": [HEADER],
    }
    writes = []
    monkeypatch.setattr(cc, "_existing_tabs_sync", lambda: set(tabs))
    monkeypatch.setattr(cc, "_read_tab_sync", lambda tab: tabs[tab])
    monkeypatch.setattr(cc, "_write_sync", lambda data: writes.extend(data))

    async def fake_fetch(*_):
        return [_pay(date(2026, 9, 16), 15, 0, 1000), _pay(date(2026, 9, 16), 16, 0, 250),
                _pay(date(2026, 9, 17), 18, 0, 700)]

    monkeypatch.setattr(cc, "_fetch_payments", fake_fetch)
    db = _FakeDB([("2026-09-16", "09:00", "15:30"), ("2026-09-16", "15:30", "22:00"), ("2026-09-17", "09:00", "22:00")])
    await cc.fill_cash_counts(db, today=TODAY)
    assert writes == [
        {"range": "'September SHE'!D2", "values": [[10.0]]},
        {"range": "'September SHE'!D3", "values": [[2.5]]},
        {"range": "'September SHE'!D4", "values": [[7.0]]},
        {"range": "'September SHE'!D5", "values": [["N/A"]]},
    ]
