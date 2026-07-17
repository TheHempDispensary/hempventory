from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import aiosqlite
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

from app.auth import get_current_user
from app.database import get_db
from app.clover_client import CloverClient

router = APIRouter(prefix="/api/sales", tags=["sales"])

# The business operates in Florida (US Eastern). Sales must be bucketed by local
# time so "days" and the hourly chart line up with when the stores were actually open.
EASTERN = ZoneInfo("America/New_York")


async def _get_locations(db: aiosqlite.Connection):
    cursor = await db.execute(
        "SELECT id, name, merchant_id, api_token FROM locations WHERE LOWER(name) NOT LIKE '%virtual%' AND LOWER(name) NOT LIKE '%central%'"
    )
    return await cursor.fetchall()


async def _fetch_orders_for_location(merchant_id: str, api_token: str, start_ms: int, end_ms: int) -> list:
    """Fetch all paid orders for a location within a time range."""
    client = CloverClient(merchant_id, api_token)
    all_orders = []
    offset = 0
    limit = 100
    while True:
        data = await client.get_orders(
            limit=limit,
            offset=offset,
            filters=[
                "payType!=NULL",
                f"createdTime>={start_ms}",
                f"createdTime<={end_ms}",
            ],
            expand="lineItems",
        )
        elements = data.get("elements", [])
        all_orders.extend(elements)
        if len(elements) < limit:
            break
        offset += limit
        await asyncio.sleep(0.3)
    return all_orders


async def _fetch_refunds_for_location(merchant_id: str, api_token: str, start_ms: int, end_ms: int) -> list:
    """Fetch all refund records for a location within a time range."""
    client = CloverClient(merchant_id, api_token)
    data = await client.get_refunds_in_range(start_ms, end_ms)
    return data.get("elements", [])


@router.get("/report")
async def get_sales_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """Generate a comprehensive sales report from Clover order data."""
    locations = await _get_locations(db)
    if not locations:
        raise HTTPException(status_code=400, detail="No locations configured")

    # Default to today (Eastern) if no dates provided
    now = datetime.now(EASTERN)
    if not start_date:
        start_date = now.strftime("%Y-%m-%d")
    if not end_date:
        end_date = now.strftime("%Y-%m-%d")

    # Interpret the requested day range in Eastern time, then convert to UTC ms
    # for Clover so day boundaries match when the stores were actually open.
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=EASTERN)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    # Fetch orders from all locations in parallel
    location_data = []
    for loc in locations:
        loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
        location_data.append((loc_name, merchant_id, api_token))

    order_tasks = [
        _fetch_orders_for_location(mid, token, start_ms, end_ms)
        for _, mid, token in location_data
    ]
    refund_tasks = [
        _fetch_refunds_for_location(mid, token, start_ms, end_ms)
        for _, mid, token in location_data
    ]
    results = await asyncio.gather(*order_tasks, return_exceptions=True)
    refund_results = await asyncio.gather(*refund_tasks, return_exceptions=True)

    # Aggregate data
    total_gross = 0        # order totals incl. tax, before refunds
    total_tax = 0
    total_refunds = 0
    total_orders = 0
    total_items_sold = 0
    by_location = {}
    by_hour = defaultdict(lambda: {"revenue": 0, "orders": 0})
    by_day = defaultdict(lambda: {"revenue": 0, "orders": 0, "refunds": 0})
    by_item = defaultdict(lambda: {"name": "", "quantity": 0, "revenue": 0, "by_location": defaultdict(int)})
    by_category = defaultdict(lambda: {"revenue": 0, "quantity": 0})
    recent_orders = []

    # Tally refunds per location and per Eastern day so revenue can be adjusted.
    refunds_by_location: dict[str, int] = defaultdict(int)
    for idx, (loc_name, _, _) in enumerate(location_data):
        refunds = refund_results[idx]
        if isinstance(refunds, Exception):
            continue
        for refund in refunds:
            amount = refund.get("amount", 0) or 0
            if amount <= 0:
                continue
            total_refunds += amount
            refunds_by_location[loc_name] += amount
            created_time = refund.get("createdTime", 0)
            refund_dt = datetime.fromtimestamp(created_time / 1000, tz=timezone.utc).astimezone(EASTERN)
            by_day[refund_dt.strftime("%Y-%m-%d")]["refunds"] += amount

    for idx, (loc_name, _, _) in enumerate(location_data):
        orders = results[idx]
        if isinstance(orders, Exception):
            by_location[loc_name] = {"revenue": 0, "orders": 0, "avg_order": 0, "error": str(orders)}
            continue

        loc_gross = 0
        loc_orders = 0

        for order in orders:
            if order.get("deletedTime"):
                continue  # Skip deleted orders
            order_total = order.get("total", 0)
            if order_total <= 0:
                continue  # Skip refunds/voids/empty orders

            created_time = order.get("createdTime", 0)
            order_dt = datetime.fromtimestamp(created_time / 1000, tz=timezone.utc).astimezone(EASTERN)

            total_gross += order_total
            total_tax += order.get("taxAmount", 0) or 0
            total_orders += 1
            loc_gross += order_total
            loc_orders += 1

            # By hour (Eastern)
            hour_key = order_dt.strftime("%H:00")
            by_hour[hour_key]["revenue"] += order_total
            by_hour[hour_key]["orders"] += 1

            # By day (Eastern)
            day_key = order_dt.strftime("%Y-%m-%d")
            by_day[day_key]["revenue"] += order_total
            by_day[day_key]["orders"] += 1

            # Line items
            line_items = order.get("lineItems", {}).get("elements", [])
            for li in line_items:
                if li.get("refunded") or li.get("isRefund"):
                    continue
                item_name = li.get("name", "Unknown")
                item_price = li.get("price", 0)
                item_qty = 1  # Clover creates separate line items per quantity
                total_items_sold += item_qty

                item_key = item_name.upper().strip()
                by_item[item_key]["name"] = item_name
                by_item[item_key]["quantity"] += item_qty
                by_item[item_key]["revenue"] += item_price
                by_item[item_key]["by_location"][loc_name] += item_qty

            # Recent orders (collect all, sort later)
            recent_orders.append({
                "id": order.get("id"),
                "total": order_total,
                "location": loc_name,
                "time": order_dt.isoformat(),
                "items": len(line_items),
            })

        loc_refunds = refunds_by_location.get(loc_name, 0)
        loc_revenue = loc_gross - loc_refunds
        avg_order = round(loc_gross / loc_orders) if loc_orders > 0 else 0
        by_location[loc_name] = {
            "revenue": loc_revenue,
            "gross": loc_gross,
            "refunds": loc_refunds,
            "orders": loc_orders,
            "avg_order": avg_order,
        }

    # Sort recent orders by time descending, keep last 50
    recent_orders.sort(key=lambda x: x["time"], reverse=True)
    recent_orders = recent_orders[:50]

    # Sort top items by revenue
    top_items_raw = sorted(by_item.values(), key=lambda x: x["revenue"], reverse=True)[:25]
    top_items = []
    for ti in top_items_raw:
        top_items.append({
            "name": ti["name"],
            "quantity": ti["quantity"],
            "revenue": ti["revenue"],
            "by_location": dict(ti["by_location"]),
        })

    # Sort categories by revenue
    # Note: Clover line items don't include category in order data,
    # so we'll group by item name patterns for now
    category_list = sorted(by_category.values(), key=lambda x: x["revenue"], reverse=True)

    # Build hourly data (all 24 hours)
    hourly_data = []
    for h in range(24):
        key = f"{h:02d}:00"
        hourly_data.append({
            "hour": key,
            "label": datetime.strptime(key, "%H:%M").strftime("%-I %p"),
            "revenue": by_hour[key]["revenue"],
            "orders": by_hour[key]["orders"],
        })

    # Build daily data (fill gaps)
    daily_data = []
    current = start_dt
    while current <= end_dt:
        day_str = current.strftime("%Y-%m-%d")
        daily_data.append({
            "date": day_str,
            "label": current.strftime("%b %d"),
            "revenue": by_day[day_str]["revenue"] - by_day[day_str]["refunds"],
            "gross": by_day[day_str]["revenue"],
            "refunds": by_day[day_str]["refunds"],
            "orders": by_day[day_str]["orders"],
        })
        current += timedelta(days=1)

    # Revenue is gross (incl. tax) less refunds. Net sales additionally excludes tax.
    total_revenue = total_gross - total_refunds
    net_sales = total_gross - total_tax - total_refunds
    avg_order_value = round(total_gross / total_orders) if total_orders > 0 else 0

    return {
        "summary": {
            "total_revenue": total_revenue,
            "gross_sales": total_gross,
            "net_sales": net_sales,
            "total_tax": total_tax,
            "total_refunds": total_refunds,
            "total_orders": total_orders,
            "total_items_sold": total_items_sold,
            "avg_order_value": avg_order_value,
            "start_date": start_date,
            "end_date": end_date,
        },
        "by_location": by_location,
        "hourly": hourly_data,
        "daily": daily_data,
        "top_items": top_items,
        "recent_orders": recent_orders,
    }
