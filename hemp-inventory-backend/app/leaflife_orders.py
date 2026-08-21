"""Automatic writer for the LeafLife "Order Sheet" tab.

When a website "Ship to Me" order contains LeafLife (LF-) products, the THD
staff used to copy it into a shared Google Sheet by hand. This module builds
those rows automatically and appends them via the Google Sheets API, matching
the sheet's existing format exactly.

Only the THD-owned columns (A–Z) are written. LeafLife fills the rest
(shipping cost, cost/profit split) themselves.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

# Same workbook the LeafLife menu sync reads.
SHEET_ID = os.environ.get("LEAFLIFE_SHEET_ID", "1gztJ_rdLf2EIbXWeRHu_GSexSJU1xObdXYKvkZ5TEV4")
ORDER_TAB = "Order Sheet"
_MENU_TABS = {
    "flower": "Retail Flower Menu",
    "concentrate": "Retail Concentrate Menu",
    "bulk": "Bulk Flower Menu",
}
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_EASTERN = ZoneInfo("America/New_York")

# Column layout of the Order Sheet (0-indexed). Only THD columns are written.
COL_GROUP = 0        # order # repeated on every row of the group
COL_DATE = 1
COL_ORDER_NO = 2
COL_STATUS = 3
COL_FLOWER = 4
COL_FLOWER_QTY = 5
COL_CONC = 6
COL_CONC_QTY = 7
COL_BULK = 8
COL_BULK_QTY = 9
COL_FLOWER_COA = 10
COL_CONC_COA = 11
COL_BULK_COA = 12
COL_FIRST = 13
COL_MIDDLE = 14
COL_LAST = 15
COL_STREET = 16
COL_CITY = 17
COL_STATE = 18
COL_ZIP = 19
COL_ORDER_LINK = 20
COL_NOTES = 21
COL_TOTAL = 22
COL_CARD_FEE = 23
COL_SHIP_METHOD = 24
COL_LABEL = 25       # "Shipping Details (LL) Label/Tracking #"
_ROW_WIDTH = 26

# Order Status (column D) values LeafLife reads. An order is only ready to ship
# once its label is in column Z, so it starts as awaiting one.
STATUS_AWAITING_LABEL = "Awaiting Label"
STATUS_SHIPPED = "Shipped"


def is_configured() -> bool:
    """True when a service-account key is available to write the sheet."""
    return bool(os.environ.get("GOOGLE_SHEETS_SA_JSON") or os.environ.get("GOOGLE_SHEETS_SA_FILE"))


def _money(cents: int) -> str:
    """Plain numeric dollar string as the sheet stores it (e.g. 100 -> '100.00')."""
    return f"{cents / 100:.2f}"


# The sheet spells state names out ("Florida", not "FL").
_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def state_name(state: str) -> str:
    """Expand a 2-letter state code to the full name the sheet uses."""
    s = (state or "").strip()
    return _US_STATES.get(s.upper(), s)


def card_fee_cents(total_cents: int) -> int:
    """Card processing fee = 2% of the order total + $0.35 (matches the sheet)."""
    return round(total_cents * 0.02) + 35


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def parse_size(name: str) -> str:
    """Extract a purchase size like '28g' or '3.5g' from a LeafLife item name.

    Handles both '28 gram(s)' and the compact '28g' form (but not 'mg'/'kg').
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:grams?|g)(?![a-z])", (name or "").lower())
    if not m:
        return ""
    num = m.group(1)
    if num.endswith(".0"):
        num = num[:-2]
    return f"{num}g"


def short_order_no(order_number: str) -> str:
    """The sheet uses the short hex form (e.g. 'HD-6A653011-1234' -> '6A653011')."""
    parts = (order_number or "").split("-")
    if len(parts) == 3 and parts[0].upper() == "HD":
        return parts[1]
    return order_number


def usps_method(shipping_service: str) -> str:
    s = (shipping_service or "").lower()
    if "priority" in s:
        return "Priority"
    if "ground" in s:
        return "Ground"
    return shipping_service or ""


async def _fetch_csv(tab: str) -> list[list[str]]:
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}"
    )
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    text = resp.text
    if text.lstrip().startswith("<"):
        raise ValueError(f"Sheet tab '{tab}' not accessible (got HTML)")
    return list(csv.reader(io.StringIO(text)))


def _find_col(header: list[str], name: str) -> Optional[int]:
    for i, h in enumerate(header):
        if h.strip().lower() == name.lower():
            return i
    return None


async def build_menu_lookup() -> dict[str, dict]:
    """Map normalized strain name -> {display, coa, kind} from the three menu tabs.

    Longer strain names first so the most specific match wins.
    """
    lookup: dict[str, dict] = {}
    for kind, tab in _MENU_TABS.items():
        try:
            rows = await _fetch_csv(tab)
        except Exception as e:  # noqa: BLE001 - non-fatal, sheet is best-effort
            print(f"[leaflife-orders] menu fetch failed for {tab}: {e}")
            continue
        # The header is the first row whose cells include a "Strain" column.
        header_idx = next(
            (i for i, r in enumerate(rows) if _find_col(r, "Strain") is not None),
            None,
        )
        if header_idx is None:
            continue
        header = rows[header_idx]
        s_idx = _find_col(header, "Strain")
        c_idx = _find_col(header, "COA")
        for r in rows[header_idx + 1:]:
            if s_idx is None or s_idx >= len(r):
                continue
            strain = r[s_idx].strip()
            if not strain:
                continue
            coa = r[c_idx].strip() if (c_idx is not None and c_idx < len(r)) else ""
            key = _norm(strain)
            if key and key not in lookup:
                lookup[key] = {"display": strain, "coa": coa, "kind": kind}
    return lookup


def match_strain(item_name: str, lookup: dict[str, dict]) -> Optional[dict]:
    """Find the menu entry whose strain name appears in the order item name."""
    norm_name = _norm(item_name)
    best: Optional[dict] = None
    best_len = 0
    for key, entry in lookup.items():
        if key and key in norm_name and len(key) > best_len:
            best = entry
            best_len = len(key)
    return best


def build_rows(
    *,
    order_number: str,
    order_date: str,
    status: str,
    first_name: str,
    last_name: str,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    notes: str,
    ship_method: str,
    lf_items: list[dict],
    lookup: dict[str, dict],
) -> list[list[str]]:
    """Build the order-info row + one row per LeafLife product unit.

    `lf_items` is a list of {name, sku, price(cents), quantity}. Only LF- items
    should be passed in. `order total` = sum(price*qty) over these items.

    The group column (A) and the COA columns (K/L/M) are left blank: the sheet
    fills them itself via array formulas (A carries the order # down; K/L/M
    VLOOKUP the product name against the 'Pricing Archive' tab). Those columns
    are protected, so we must not write them.
    """
    total_cents = sum(int(it["price"]) * int(it["quantity"]) for it in lf_items)

    def blank_row() -> list[str]:
        return [""] * _ROW_WIDTH

    # Order-info row (customer + totals; product columns blank).
    info = blank_row()
    info[COL_DATE] = order_date
    info[COL_ORDER_NO] = short_order_no(order_number)
    info[COL_STATUS] = status
    info[COL_FIRST] = first_name
    info[COL_LAST] = last_name
    info[COL_STREET] = street
    info[COL_CITY] = city
    info[COL_STATE] = state_name(state)
    info[COL_ZIP] = zip_code
    info[COL_NOTES] = notes
    info[COL_TOTAL] = _money(total_cents)
    info[COL_CARD_FEE] = _money(card_fee_cents(total_cents))
    info[COL_SHIP_METHOD] = ship_method
    rows = [info]

    # One row per product unit. COA columns are left blank (auto-filled).
    for it in lf_items:
        entry = match_strain(it["name"], lookup)
        display = entry["display"] if entry else it["name"]
        kind = entry["kind"] if entry else "flower"
        size = parse_size(it["name"])
        for _ in range(max(1, int(it["quantity"]))):
            r = blank_row()
            if kind == "concentrate":
                r[COL_CONC], r[COL_CONC_QTY] = display, size
            elif kind == "bulk":
                r[COL_BULK], r[COL_BULK_QTY] = display, size
            else:
                r[COL_FLOWER], r[COL_FLOWER_QTY] = display, size
            rows.append(r)
    return rows


def today_eastern() -> str:
    return datetime.now(_EASTERN).strftime("%m/%d/%Y")


def _credentials():
    raw = os.environ.get("GOOGLE_SHEETS_SA_JSON")
    from google.oauth2 import service_account  # lazy import

    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    path = os.environ.get("GOOGLE_SHEETS_SA_FILE")
    if path:
        return service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
    raise RuntimeError("No Google service-account credentials configured")


def _sheets_service():
    from googleapiclient.discovery import build  # lazy import

    return build("sheets", "v4", credentials=_credentials(), cache_discovery=False)


def _existing_order_numbers_sync() -> set[str]:
    """Order #s already present in the sheet (column C), read via the API."""
    resp = (
        _sheets_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"'{ORDER_TAB}'!C:C")
        .execute()
    )
    values = resp.get("values", [])
    return {row[0].strip() for row in values if row and row[0].strip()}


def _append_rows_sync(rows: list[list[str]]) -> int:
    """Write rows into the first empty rows, skipping the protected columns.

    The Order Sheet protects column A and the COA columns (K/L/M) because they
    are array formulas that auto-extend to new rows. So we can't use
    values.append (it would write a full contiguous block over A/K/L/M and be
    rejected). Instead we find the next empty row and write only the two
    unprotected column segments — B:J and N:Z — at explicit ranges. The
    protected formulas fill A/K/L/M for the new rows on their own.
    """
    service = _sheets_service()
    # len of the used range = last row that has any data (column A's formula
    # fills every data row, so nothing is missed).
    used = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"'{ORDER_TAB}'!A:Y")
        .execute()
        .get("values", [])
    )
    start = len(used) + 1
    end = start + len(rows) - 1
    data = [
        {"range": f"'{ORDER_TAB}'!B{start}:J{end}", "values": [r[1:10] for r in rows]},
        {"range": f"'{ORDER_TAB}'!N{start}:Z{end}", "values": [r[13:26] for r in rows]},
    ]
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    return start


def label_cell(label_url: str, tracking_number: str) -> str:
    """The column-Z value LeafLife clicks to print the label.

    A HYPERLINK formula labelled with the tracking #, matching how the column is
    already used (the existing entries link to the label file).
    """
    tracking = (tracking_number or "").strip()
    url = (label_url or "").strip()
    if not url:
        return tracking
    text = tracking or "Print Label"
    return f'=HYPERLINK("{url}","{text}")'


def _order_row_sync(short: str) -> Optional[int]:
    """1-based row of the order-info row for `short` (its order # is column C)."""
    resp = (
        _sheets_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"'{ORDER_TAB}'!C:C")
        .execute()
    )
    for i, row in enumerate(resp.get("values", []), start=1):
        if row and row[0].strip() == short:
            return i
    return None


def _label_at_sync(row: int) -> str:
    resp = (
        _sheets_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=SHEET_ID, range=f"'{ORDER_TAB}'!Z{row}")
        .execute()
    )
    values = resp.get("values", [])
    return values[0][0].strip() if values and values[0] else ""


def _write_label_sync(row: int, value: str) -> None:
    """Fill the label cell and mark the order ready to ship."""
    _sheets_service().spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": f"'{ORDER_TAB}'!Z{row}", "values": [[value]]},
                {"range": f"'{ORDER_TAB}'!D{row}", "values": [[STATUS_SHIPPED]]},
            ],
        },
    ).execute()


async def sync_label(
    *,
    order_number: str,
    label_url: str,
    tracking_number: str,
    overwrite: bool = False,
) -> dict:
    """Write the printable label link into column Z of the order's row.

    Idempotent: an existing value is kept unless `overwrite`. Never raises, so
    callers can fire-and-forget right after buying a label.
    """
    if not is_configured():
        return {"ok": False, "reason": "Google Sheets credentials not configured", "written": 0}
    value = label_cell(label_url, tracking_number)
    if not value:
        return {"ok": False, "reason": "no label to write", "written": 0}

    short = short_order_no(order_number)
    try:
        row = await asyncio.to_thread(_order_row_sync, short)
        if row is None:
            return {"ok": False, "reason": "order not on sheet", "written": 0}
        if not overwrite and await asyncio.to_thread(_label_at_sync, row):
            return {"ok": True, "reason": "already present", "written": 0, "order_number": short}
        await asyncio.to_thread(_write_label_sync, row, value)
        return {"ok": True, "written": 1, "row": row, "order_number": short}
    except Exception as e:  # noqa: BLE001 - best-effort; log and report
        print(f"[leaflife-orders] failed to sync label for {short}: {e}")
        return {"ok": False, "reason": str(e), "written": 0}


def leaflife_items(items: list[dict]) -> list[dict]:
    """Filter order line items down to LeafLife (LF-) products."""
    out = []
    for it in items:
        sku = it.get("sku") or ""
        if isinstance(sku, str) and sku.upper().startswith("LF-"):
            out.append(it)
    return out


async def sync_order(
    *,
    order_number: str,
    first_name: str,
    last_name: str,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    notes: str,
    shipping_service: str,
    items: list[dict],
    order_date: Optional[str] = None,
    status: str = STATUS_AWAITING_LABEL,
) -> dict:
    """Append one LeafLife order to the sheet. Idempotent by order #.

    Returns a result dict; never raises for expected conditions (no LF items,
    not configured, already present) so callers can fire-and-forget.
    """
    lf = leaflife_items(items)
    if not lf:
        return {"ok": False, "reason": "no LeafLife items", "written": 0}
    if not is_configured():
        return {"ok": False, "reason": "Google Sheets credentials not configured", "written": 0}

    short = short_order_no(order_number)
    try:
        existing = await asyncio.to_thread(_existing_order_numbers_sync)
        if short in existing:
            return {"ok": True, "reason": "already present", "written": 0, "order_number": short}

        lookup = await build_menu_lookup()
        rows = build_rows(
            order_number=order_number,
            order_date=order_date or today_eastern(),
            status=status,
            first_name=first_name,
            last_name=last_name,
            street=street,
            city=city,
            state=state,
            zip_code=zip_code,
            notes=notes,
            ship_method=usps_method(shipping_service),
            lf_items=lf,
            lookup=lookup,
        )
        await asyncio.to_thread(_append_rows_sync, rows)
        return {"ok": True, "written": len(rows), "order_number": short}
    except Exception as e:  # noqa: BLE001 - best-effort; log and report
        print(f"[leaflife-orders] failed to sync order {short}: {e}")
        return {"ok": False, "reason": str(e), "written": 0}
