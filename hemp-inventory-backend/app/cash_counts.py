"""Fill the "System Cash Count" column of the 2026 Weekly Cash Counts sheet.

Each store has a monthly tab ("September SHE", "September SHW") with an AM and a
PM row per day. System Cash Count is the Clover cash taken during that shift;
staff used to type it in by hand. This fills it from Clover so they only enter
the physical count.

Rules:
- Only empty System Cash Count cells are written; nothing staff typed changes.
- Only finished days (before today, Eastern) are filled.
- The AM/PM split comes from the HempVentory schedule (date_schedules) for that
  store and day: PM starts when the closing shift starts (East is usually 2:30
  PM, West Wednesdays 3:30 PM). If one budtender covers the whole day, the whole
  day goes in the AM row and a blank PM System Cash Count is set to "N/A".
  Days with no schedule fall back to a 2:30 PM split.
- A PM row marked "N/A" means one budtender worked all day, so the whole day
  goes in the AM row.
- If one shift was already entered by hand, the other gets the rest of the day,
  so AM + PM always equals Clover's cash for the day.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

SHEET_ID = os.environ.get("CASH_COUNTS_SHEET_ID", "1kbgOlw7AJGqvYSlos23DFvxBm0iEjgd4Aw0NNPgqTa8")
TAB_CODES = {"East": "SHE", "West": "SHW"}
SHIFT_CHANGE = time(14, 30)
SYSTEM_COL = "D"
CASH_TENDER = "com.clover.tender.cash"
_EASTERN = ZoneInfo("America/New_York")
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def tab_name(location: str, month: date) -> str:
    return f"{month.strftime('%B')} {TAB_CODES[location]}"


def _parse_day(value) -> Optional[date]:
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", str(value or ""))
    if not m:
        return None
    return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def _is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


def _is_na(value) -> bool:
    return "N/A" in str(value or "").upper()


def _cents(value) -> Optional[int]:
    if isinstance(value, (int, float)):
        return round(value * 100)
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if text == "-":
        return 0
    try:
        return round(float(text) * 100)
    except ValueError:
        return None


def _net_cash_cents(payment: dict) -> int:
    refunds = (payment.get("refunds") or {}).get("elements") or []
    return int(payment.get("amount") or 0) - sum(int(r.get("amount") or 0) for r in refunds)


def shift_totals(
    payments: list[dict], shift_changes: Optional[dict[date, Optional[time]]] = None
) -> dict[date, tuple[int, int]]:
    """Clover cash per Eastern day, split into (AM, PM) cents at the shift change.

    ``shift_changes`` maps a day to its PM start, or None when one budtender
    worked all day (everything goes to AM). Days not listed use SHIFT_CHANGE.
    """
    shift_changes = shift_changes or {}
    totals: dict[date, list[int]] = {}
    for p in payments:
        if p.get("result") != "SUCCESS" or (p.get("tender") or {}).get("labelKey") != CASH_TENDER:
            continue
        ts = datetime.fromtimestamp(int(p["createdTime"]) / 1000, _EASTERN)
        cut = shift_changes.get(ts.date(), SHIFT_CHANGE)
        slot = totals.setdefault(ts.date(), [0, 0])
        slot[0 if cut is None or ts.time() < cut else 1] += _net_cash_cents(p)
    return {d: (am, pm) for d, (am, pm) in totals.items()}


def schedule_shift_changes(schedules: list[tuple]) -> dict[date, Optional[time]]:
    """PM start per day from (date, start_time, end_time) schedule rows for one store.

    The PM shift is the one that closes (latest end). If it starts with the
    first shift of the day, one budtender worked all day and the value is None.
    """
    by_day: dict[date, list[tuple[time, time]]] = {}
    for day, start, end in schedules:
        try:
            parsed = (time.fromisoformat(str(start).strip()), time.fromisoformat(str(end).strip()))
            key = date.fromisoformat(str(day)[:10])
        except ValueError:
            continue
        by_day.setdefault(key, []).append(parsed)
    changes: dict[date, Optional[time]] = {}
    for day, shifts in by_day.items():
        first_start = min(start for start, _ in shifts)
        closing_start = max(shifts, key=lambda s: (s[1], s[0]))[0]
        changes[day] = closing_start if closing_start > first_start else None
    return changes


def plan_updates(
    rows: list[list],
    totals: dict[date, tuple[int, int]],
    today: date,
    single_shift_days: frozenset = frozenset(),
) -> dict[int, object]:
    """Sheet row number -> cents (or "N/A") to write into System Cash Count.

    ``rows`` are the tab's values from column A to E (row 1 is the header).
    ``single_shift_days`` are days one budtender covered alone.
    """
    shifts: dict[date, dict[str, tuple[int, list]]] = {}
    for i, row in enumerate(rows):
        row = list(row) + [""] * (5 - len(row))
        day, shift = _parse_day(row[0]), str(row[1]).strip().upper()
        if day and shift in ("AM", "PM"):
            shifts.setdefault(day, {})[shift] = (i + 1, row)

    updates: dict[int, int] = {}
    for day, by_shift in shifts.items():
        if day >= today:
            continue
        am_cents, pm_cents = totals.get(day, (0, 0))
        day_cents = am_cents + pm_cents
        am, pm = by_shift.get("AM"), by_shift.get("PM")
        pm_na = pm is not None and any(_is_na(v) for v in pm[1][2:5])

        if am is None:
            continue
        am_blank = _is_blank(am[1][3])
        if pm is None or pm_na:
            if am_blank:
                updates[am[0]] = day_cents
            continue

        pm_blank = _is_blank(pm[1][3])
        if day in single_shift_days and pm_blank:
            updates[pm[0]] = "N/A"
            if am_blank:
                updates[am[0]] = day_cents
            continue
        if am_blank and pm_blank:
            updates[am[0]] = am_cents
            updates[pm[0]] = pm_cents
        elif am_blank:
            entered = _cents(pm[1][3])
            if entered is not None and day_cents - entered >= 0:
                updates[am[0]] = day_cents - entered
        elif pm_blank:
            entered = _cents(am[1][3])
            if entered is not None and day_cents - entered >= 0:
                updates[pm[0]] = day_cents - entered
    return updates


def _credentials():
    from google.oauth2 import service_account  # lazy import

    raw = os.environ.get("GOOGLE_SHEETS_SA_JSON")
    if raw:
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)
    path = os.environ.get("GOOGLE_SHEETS_SA_FILE")
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
    raise RuntimeError("No Google service-account credentials configured")


def _sheets():
    from googleapiclient.discovery import build  # lazy import

    return build("sheets", "v4", credentials=_credentials(), cache_discovery=False).spreadsheets()


def _existing_tabs_sync() -> set[str]:
    meta = _sheets().get(spreadsheetId=SHEET_ID, fields="sheets.properties.title").execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def _read_tab_sync(tab: str) -> list[list]:
    resp = _sheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{tab}'!A:E",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return resp.get("values", [])


def _write_sync(data: list[dict]) -> None:
    _sheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


async def _fetch_payments(merchant_id: str, api_token: str, start: date, end: date) -> list[dict]:
    from app.clover_client import CloverClient

    start_ms = int(datetime.combine(start, time(0), _EASTERN).timestamp() * 1000)
    end_ms = int(datetime.combine(end + timedelta(days=1), time(0), _EASTERN).timestamp() * 1000)
    data = await CloverClient(merchant_id, api_token).get_payments(
        limit=1000,
        filters=[f"createdTime>={start_ms}", f"createdTime<{end_ms}"],
        expand="tender,refunds",
    )
    return data.get("elements", [])


async def _shift_changes(db, location: str, start: date, end: date) -> dict[date, Optional[time]]:
    cursor = await db.execute(
        "SELECT date, start_time, end_time FROM date_schedules"
        " WHERE UPPER(location) = UPPER(?) AND date BETWEEN ? AND ?",
        (location, start.isoformat(), end.isoformat()),
    )
    return schedule_shift_changes([tuple(r) for r in await cursor.fetchall()])


def _months_to_fill(today: date) -> list[date]:
    first = today.replace(day=1)
    previous = (first - timedelta(days=1)).replace(day=1)
    return [previous, first]


async def fill_cash_counts(db, today: Optional[date] = None, dry_run: bool = False) -> dict:
    """Fill empty System Cash Count cells for this month and last month."""
    today = today or datetime.now(_EASTERN).date()
    cursor = await db.execute(
        "SELECT name, merchant_id, api_token FROM locations WHERE name IN ('East', 'West')"
    )
    locations = [tuple(r) for r in await cursor.fetchall()]
    existing_tabs = await asyncio.to_thread(_existing_tabs_sync)

    data: list[dict] = []
    written: list[dict] = []
    missing_tabs: list[str] = []
    for name, merchant_id, api_token in locations:
        for month in _months_to_fill(today):
            tab = tab_name(name, month)
            if tab not in existing_tabs:
                if month <= today:
                    missing_tabs.append(tab)
                continue
            rows = await asyncio.to_thread(_read_tab_sync, tab)
            days = [d for d in (_parse_day(r[0]) for r in rows if r) if d and d < today]
            if not days:
                continue
            payments = await _fetch_payments(merchant_id, api_token, min(days), max(days))
            changes = await _shift_changes(db, name, min(days), max(days))
            single = frozenset(d for d, cut in changes.items() if cut is None)
            updates = plan_updates(rows, shift_totals(payments, changes), today, single)
            for row_number, value in sorted(updates.items()):
                amount = round(value / 100, 2) if isinstance(value, int) else value
                data.append({"range": f"'{tab}'!{SYSTEM_COL}{row_number}", "values": [[amount]]})
                written.append({"tab": tab, "row": row_number, "amount": amount})

    if data and not dry_run:
        await asyncio.to_thread(_write_sync, data)
    return {"written": written, "missing_tabs": missing_tabs, "dry_run": dry_run}
