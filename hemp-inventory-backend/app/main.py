import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from app.database import init_db, get_db, DB_PATH
from app.routers import auth_router, locations_router, inventory_router, par_router, alerts_router, ecommerce_router, loyalty_router, timeclock_router, sales_router, shipping_router, scraper_router, chat_router, coa_router
from app.routers.inventory_router import _do_sync
from app.routers.loyalty_router import _do_bulk_import_customers, _do_sync_orders
from app.routers.ecommerce_router import _sync_clover_online_orders

import aiosqlite

scheduler = AsyncIOScheduler()


async def _connect_db():
    """Create a database connection with WAL mode and busy timeout."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.execute("PRAGMA journal_mode = WAL")
    return db


async def _scheduled_inventory_sync():
    """Background job: sync inventory from Clover and cache it."""
    try:
        db = await _connect_db()
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
        db = await _connect_db()
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
        db = await _connect_db()
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


async def _scheduled_clover_order_sync():
    """Background job: import online orders placed through Clover's native ordering system."""
    try:
        db = await _connect_db()
        try:
            result = await _sync_clover_online_orders(db)
            synced = result.get('synced', 0)
            skipped = result.get('skipped', 0)
            if synced > 0:
                print(f"[auto-sync] Clover online orders: {synced} imported, {skipped} skipped")
        finally:
            await db.close()
    except Exception as e:
        print(f"[auto-sync] Clover order sync failed: {e}")


async def _scheduled_ecommerce_refresh():
    """Background job: refresh the ecommerce product cache from Clover every 10 minutes."""
    try:
        await ecommerce_router._fetch_and_cache_products()
        print("[auto-sync] Ecommerce product cache refreshed")
    except Exception as e:
        print(f"[auto-sync] Ecommerce cache refresh failed: {e}")


async def _scheduled_coa_sync():
    """Background job: sync COA lab results from ACS Laboratory."""
    import os
    if not os.environ.get("ACS_LAB_API_KEY"):
        return
    try:
        db = await _connect_db()
        try:
            from app.acs_client import ACSLabClient
            client = ACSLabClient()
            results = await client.get_analyte_results()
            alt_results = await client.get_analyte_results_alternate()
            all_results = results + alt_results

            samples: dict[str, dict] = {}
            analytes: list[dict] = []
            for r in all_results:
                acc = r.get("sample_accession", "")
                if not acc:
                    continue
                if acc not in samples:
                    samples[acc] = {
                        "sample_accession": acc,
                        "order_number": r.get("number", ""),
                        "batch_no": r.get("batch_no", ""),
                        "business_name": r.get("business_name", ""),
                        "product_name": r.get("product_name", ""),
                        "product_type": r.get("product_type_name", ""),
                        "consumption_type": r.get("consumption_type", ""),
                        "description": r.get("description", ""),
                        "test_purpose": r.get("test_purpose", ""),
                        "sample_status": r.get("sample_status", ""),
                        "order_date": r.get("order_date", ""),
                        "test_start_date": r.get("test_start_date", ""),
                        "coa_approved_date": r.get("coa_approved_date", ""),
                        "postal_code": r.get("postal_code", ""),
                        "extracted_from": r.get("extracted_from", ""),
                    }
                analyte_id = r.get("analyte_identifier", "")
                panel_name = r.get("panel_name", "")
                if analyte_id or panel_name:
                    analytes.append({
                        "sample_accession": acc,
                        "panel_name": panel_name,
                        "panel_identifier": r.get("panel_identifier", ""),
                        "analyte_abbreviation": r.get("analyte_abbreviation", ""),
                        "analyte_identifier": analyte_id,
                        "concentration": r.get("concentration", 0),
                        "conc_unit": r.get("conc_unit", ""),
                        "result": r.get("result", ""),
                        "result_unit": r.get("result_unit", ""),
                        "analyte_remark": r.get("analyte_remark", ""),
                        "panel_remark": r.get("panel_remark", ""),
                    })

            for s in samples.values():
                await db.execute(
                    """INSERT INTO coa_results
                        (sample_accession, order_number, batch_no, business_name,
                         product_name, product_type, consumption_type, description,
                         test_purpose, sample_status, order_date, test_start_date,
                         coa_approved_date, postal_code, extracted_from, synced_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(sample_accession) DO UPDATE SET
                         sample_status=excluded.sample_status,
                         coa_approved_date=excluded.coa_approved_date,
                         synced_at=CURRENT_TIMESTAMP""",
                    (
                        s["sample_accession"], s["order_number"], s["batch_no"],
                        s["business_name"], s["product_name"], s["product_type"],
                        s["consumption_type"], s["description"], s["test_purpose"],
                        s["sample_status"], s["order_date"], s["test_start_date"],
                        s["coa_approved_date"], s["postal_code"], s["extracted_from"],
                    ),
                )
            for a in analytes:
                await db.execute(
                    """INSERT INTO coa_analyte_results
                        (sample_accession, panel_name, panel_identifier,
                         analyte_abbreviation, analyte_identifier,
                         concentration, conc_unit, result, result_unit,
                         analyte_remark, panel_remark)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(sample_accession, analyte_identifier, panel_name) DO UPDATE SET
                         concentration=excluded.concentration,
                         result=excluded.result,
                         analyte_remark=excluded.analyte_remark,
                         panel_remark=excluded.panel_remark""",
                    (
                        a["sample_accession"], a["panel_name"], a["panel_identifier"],
                        a["analyte_abbreviation"], a["analyte_identifier"],
                        a["concentration"], a["conc_unit"], a["result"], a["result_unit"],
                        a["analyte_remark"], a["panel_remark"],
                    ),
                )
            await db.commit()
            print(f"[auto-sync] COA synced: {len(samples)} samples, {len(analytes)} analytes")
        finally:
            await db.close()
    except Exception as e:
        print(f"[auto-sync] COA sync failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Schedule automatic syncs
    scheduler.add_job(_scheduled_inventory_sync, "interval", minutes=5, id="inventory_sync", replace_existing=True)
    scheduler.add_job(_scheduled_refund_sync, "interval", minutes=15, id="refund_sync", replace_existing=True)
    scheduler.add_job(_scheduled_loyalty_sync, "interval", minutes=10, id="loyalty_sync", replace_existing=True)
    scheduler.add_job(_scheduled_ecommerce_refresh, "interval", minutes=10, id="ecommerce_refresh", replace_existing=True)
    scheduler.add_job(_scheduled_clover_order_sync, "interval", minutes=5, id="clover_order_sync", replace_existing=True)
    scheduler.add_job(_scheduled_coa_sync, "interval", minutes=30, id="coa_sync", replace_existing=True)
    scheduler.start()
    # Run initial inventory sync in background so server starts accepting requests immediately
    asyncio.create_task(_scheduled_inventory_sync())
    # Load disk cache first for instant availability, then refresh from Clover in background
    await ecommerce_router._load_disk_cache()
    asyncio.create_task(ecommerce_router._fetch_and_cache_products())
    # Import any Clover online orders that arrived while the server was down
    asyncio.create_task(_scheduled_clover_order_sync())
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
app.include_router(timeclock_router.router)
app.include_router(sales_router.router)
app.include_router(shipping_router.router)
app.include_router(scraper_router.router)
app.include_router(chat_router.router)
app.include_router(coa_router.router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
