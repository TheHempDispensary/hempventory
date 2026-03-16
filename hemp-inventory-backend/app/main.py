from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from app.database import init_db, get_db, DB_PATH
from app.routers import auth_router, locations_router, inventory_router, par_router, alerts_router, ecommerce_router, loyalty_router
from app.routers.inventory_router import _do_sync
from app.routers.loyalty_router import _do_bulk_import_customers, _do_sync_orders

import aiosqlite

scheduler = AsyncIOScheduler()


async def _scheduled_inventory_sync():
    """Background job: sync inventory from Clover and cache it."""
    try:
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            await _do_sync(db)
            print("[auto-sync] Inventory synced successfully")
        finally:
            await db.close()
    except Exception as e:
        print(f"[auto-sync] Inventory sync failed: {e}")


async def _scheduled_loyalty_sync():
    """Background job: import new Clover customers and sync POS orders for loyalty points."""
    try:
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            result = await _do_bulk_import_customers(db)
            print(f"[auto-sync] Loyalty customers imported: {result.get('imported', 0)} new, {result.get('skipped', 0)} skipped")
            orders_result = await _do_sync_orders(db)
            print(f"[auto-sync] Loyalty orders synced: {orders_result.get('orders_processed', 0)} processed, {orders_result.get('points_awarded', 0)} pts awarded")
        finally:
            await db.close()
    except Exception as e:
        print(f"[auto-sync] Loyalty sync failed: {e}")


async def _scheduled_refund_sync():
    """Background job: sync refunds from Clover."""
    from app.routers.inventory_router import sync_refunds as _sync_refunds_endpoint
    try:
        db = await aiosqlite.connect(DB_PATH)
        db.row_factory = aiosqlite.Row
        try:
            from app.routers.inventory_router import _get_locations
            from app.clover_client import CloverClient
            locations = await _get_locations(db)
            if not locations:
                return
            # Simplified refund sync - just call the Clover API and process
            for loc in locations:
                loc_id, loc_name, merchant_id, api_token = loc[0], loc[1], loc[2], loc[3]
                try:
                    client = CloverClient(merchant_id, api_token)
                    refund_data = await client.get_refunds(limit=50)
                    refunds = refund_data.get("elements", [])
                    for order in refunds:
                        order_id = order.get("id", "")
                        cursor = await db.execute(
                            "SELECT id FROM synced_refunds WHERE clover_order_id = ? AND location_merchant_id = ?",
                            (order_id, merchant_id),
                        )
                        if await cursor.fetchone():
                            continue
                        line_items = order.get("lineItems", {}).get("elements", [])
                        for li in line_items:
                            if not (li.get("refunded") or li.get("isRefund")):
                                continue
                            item_ref = li.get("item", {})
                            item_id = item_ref.get("id", "")
                            if not item_id:
                                continue
                            try:
                                item_detail = await client.get_item(item_id)
                                current_stock = item_detail.get("itemStock", {}).get("quantity", 0) if item_detail.get("itemStock") else 0
                                new_stock = current_stock + 1
                                await client.update_item_stock(item_id, int(new_stock))
                            except Exception:
                                pass
                        await db.execute(
                            "INSERT OR IGNORE INTO synced_refunds (clover_order_id, location_merchant_id, location_name, refund_total) VALUES (?, ?, ?, ?)",
                            (order_id, merchant_id, loc_name, order.get("total", 0)),
                        )
                        await db.commit()
                except Exception as e:
                    print(f"[auto-sync] Refund sync error for {loc_name}: {e}")
            print("[auto-sync] Refunds synced successfully")
        finally:
            await db.close()
    except Exception as e:
        print(f"[auto-sync] Refund sync failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Schedule automatic syncs
    scheduler.add_job(_scheduled_inventory_sync, "interval", minutes=5, id="inventory_sync", replace_existing=True)
    scheduler.add_job(_scheduled_refund_sync, "interval", minutes=15, id="refund_sync", replace_existing=True)
    scheduler.add_job(_scheduled_loyalty_sync, "interval", minutes=10, id="loyalty_sync", replace_existing=True)
    scheduler.start()
    # Run initial inventory sync on startup (loyalty sync will run on its scheduled interval
    # to avoid memory spike from running all syncs simultaneously on startup)
    try:
        await _scheduled_inventory_sync()
    except Exception as e:
        print(f"[startup] Initial inventory sync failed: {e}")
    yield
    scheduler.shutdown()


app = FastAPI(title="Hemp Dispensary Inventory Manager", lifespan=lifespan)

# Compress responses for faster transfer
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

app.include_router(auth_router.router)
app.include_router(locations_router.router)
app.include_router(inventory_router.router)
app.include_router(par_router.router)
app.include_router(alerts_router.router)
app.include_router(ecommerce_router.router)
app.include_router(loyalty_router.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
