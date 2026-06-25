import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.acs_client import ACSLabClient

router = APIRouter(prefix="/api/coa", tags=["coa"])


def _get_acs_client() -> ACSLabClient:
    api_key = os.environ.get("ACS_LAB_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ACS_LAB_API_KEY not configured")
    return ACSLabClient(api_key)


# ── Sync from ACS Lab ──────────────────────────────────────────────


async def run_coa_sync(db) -> dict:
    """Shared sync logic used by both the HTTP endpoint and the background job."""
    client = _get_acs_client()
    all_results = await client.get_all_analyte_results_alternate()

    if not all_results:
        return {"synced_samples": 0, "synced_analytes": 0}

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

        # Extract inline homogeneity THC/CBD from alternate-format fields
        for suffix, label in [
            ("thc", "Total Active THC"),
            ("cbd", "Total Active CBD"),
        ]:
            prefix = f"homogeneity_total_active_{suffix}"
            h_result = r.get(f"{prefix}_result", "")
            if h_result and h_result != "0.00":
                analytes.append({
                    "sample_accession": acc,
                    "panel_name": "Homogeneity",
                    "panel_identifier": "",
                    "analyte_abbreviation": "",
                    "analyte_identifier": label,
                    "concentration": r.get(f"{prefix}_concentration", 0),
                    "conc_unit": r.get(f"{prefix}_conc_unit", ""),
                    "result": h_result,
                    "result_unit": r.get(f"{prefix}_result_unit", ""),
                    "analyte_remark": r.get(f"{prefix}_analyte_remark", ""),
                    "panel_remark": "",
                })

    # Guard against partial API failures: only clear-and-replace if we
    # fetched a reasonable number of samples.  If the new count is less
    # than half the existing DB count (and the DB already has data), skip
    # the wipe and fall through to the upsert path so we don't lose
    # previously-synced results.
    cursor = await db.execute("SELECT COUNT(*) FROM coa_results")
    existing_count = (await cursor.fetchone())[0]

    if existing_count == 0 or len(samples) >= existing_count // 2:
        await db.execute("DELETE FROM coa_analyte_results")
        await db.execute("DELETE FROM coa_results")
        # Also clean up orphaned sku links whose accessions are no longer
        # in the fresh data.
        fresh_accessions = set(samples.keys())
        if fresh_accessions:
            placeholders = ",".join("?" for _ in fresh_accessions)
            await db.execute(
                f"DELETE FROM coa_sku_links WHERE sample_accession NOT IN ({placeholders})",
                tuple(fresh_accessions),
            )

    # Insert samples
    for s in samples.values():
        await db.execute(
            """INSERT INTO coa_results
                (sample_accession, order_number, batch_no, business_name,
                 product_name, product_type, consumption_type, description,
                 test_purpose, sample_status, order_date, test_start_date,
                 coa_approved_date, postal_code, extracted_from, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(sample_accession) DO UPDATE SET
                 order_number=excluded.order_number,
                 batch_no=excluded.batch_no,
                 business_name=excluded.business_name,
                 product_name=excluded.product_name,
                 product_type=excluded.product_type,
                 consumption_type=excluded.consumption_type,
                 description=excluded.description,
                 test_purpose=excluded.test_purpose,
                 sample_status=excluded.sample_status,
                 order_date=excluded.order_date,
                 test_start_date=excluded.test_start_date,
                 coa_approved_date=excluded.coa_approved_date,
                 postal_code=excluded.postal_code,
                 extracted_from=excluded.extracted_from,
                 synced_at=CURRENT_TIMESTAMP""",
            (
                s["sample_accession"], s["order_number"], s["batch_no"],
                s["business_name"], s["product_name"], s["product_type"],
                s["consumption_type"], s["description"], s["test_purpose"],
                s["sample_status"], s["order_date"], s["test_start_date"],
                s["coa_approved_date"], s["postal_code"], s["extracted_from"],
            ),
        )

    # Upsert analyte results
    for a in analytes:
        await db.execute(
            """INSERT INTO coa_analyte_results
                (sample_accession, panel_name, panel_identifier,
                 analyte_abbreviation, analyte_identifier,
                 concentration, conc_unit, result, result_unit,
                 analyte_remark, panel_remark)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sample_accession, analyte_identifier, panel_name) DO UPDATE SET
                 panel_identifier=excluded.panel_identifier,
                 analyte_abbreviation=excluded.analyte_abbreviation,
                 concentration=excluded.concentration,
                 conc_unit=excluded.conc_unit,
                 result=excluded.result,
                 result_unit=excluded.result_unit,
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
    return {"synced_samples": len(samples), "synced_analytes": len(analytes)}


@router.post("/sync")
async def sync_coa_results(db=Depends(get_db)):
    """Pull all analyte results from ACS Lab (paginated) and cache locally."""
    return await run_coa_sync(db)


# ── Read cached COA data ───────────────────────────────────────────


@router.get("/samples")
async def list_coa_samples(db=Depends(get_db), view: str = "products"):
    """List cached COA data.

    ``view=products`` (default) groups accessions by description+batch so
    that homogeneity sub-samples appear as one row with a sample count.
    ``view=accessions`` returns every individual accession.
    """
    if view == "products":
        cursor = await db.execute(
            """SELECT
                    cr.description,
                    cr.batch_no,
                    cr.business_name,
                    cr.product_type,
                    cr.order_number,
                    cr.sample_status,
                    MAX(cr.coa_approved_date) AS coa_approved_date,
                    COUNT(DISTINCT cr.sample_accession) AS sample_count,
                    MIN(cr.sample_accession) AS first_accession,
                    GROUP_CONCAT(DISTINCT csl.sku) AS linked_skus
               FROM coa_results cr
               LEFT JOIN coa_sku_links csl
                 ON cr.sample_accession = csl.sample_accession
               GROUP BY cr.description, cr.batch_no
               ORDER BY coa_approved_date DESC"""
        )
    else:
        cursor = await db.execute(
            """SELECT cr.*,
                      GROUP_CONCAT(DISTINCT csl.sku) AS linked_skus
               FROM coa_results cr
               LEFT JOIN coa_sku_links csl
                 ON cr.sample_accession = csl.sample_accession
               GROUP BY cr.sample_accession
               ORDER BY cr.coa_approved_date DESC"""
        )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@router.get("/samples/{accession}")
async def get_coa_sample(accession: str, db=Depends(get_db)):
    """Get a single COA sample with all its analyte results."""
    cursor = await db.execute(
        "SELECT * FROM coa_results WHERE sample_accession = ?", (accession,)
    )
    sample = await cursor.fetchone()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")

    cursor = await db.execute(
        "SELECT * FROM coa_analyte_results WHERE sample_accession = ? ORDER BY panel_name, analyte_abbreviation",
        (accession,),
    )
    analytes = await cursor.fetchall()

    cursor = await db.execute(
        "SELECT sku FROM coa_sku_links WHERE sample_accession = ?", (accession,)
    )
    linked_skus = [r["sku"] for r in await cursor.fetchall()]

    return {
        "sample": dict(sample),
        "analytes": [dict(a) for a in analytes],
        "linked_skus": linked_skus,
    }


@router.get("/by-sku/{sku}")
async def get_coa_by_sku(sku: str, db=Depends(get_db)):
    """Get all COA results linked to a product SKU."""
    cursor = await db.execute(
        """SELECT cr.*, GROUP_CONCAT(DISTINCT csl2.sku) as linked_skus
           FROM coa_sku_links csl
           JOIN coa_results cr ON csl.sample_accession = cr.sample_accession
           LEFT JOIN coa_sku_links csl2 ON cr.sample_accession = csl2.sample_accession
           WHERE csl.sku = ?
           GROUP BY cr.sample_accession
           ORDER BY cr.coa_approved_date DESC""",
        (sku,),
    )
    samples = [dict(r) for r in await cursor.fetchall()]

    for sample in samples:
        cursor = await db.execute(
            "SELECT * FROM coa_analyte_results WHERE sample_accession = ? ORDER BY panel_name, analyte_abbreviation",
            (sample["sample_accession"],),
        )
        sample["analytes"] = [dict(a) for a in await cursor.fetchall()]

    return samples


# ── Link / unlink SKUs to COA samples ──────────────────────────────


class LinkRequest(BaseModel):
    sku: str
    sample_accession: str


@router.post("/link")
async def link_sku_to_coa(req: LinkRequest, db=Depends(get_db)):
    """Link an inventory SKU to a COA sample accession."""
    await db.execute(
        "INSERT OR IGNORE INTO coa_sku_links (sku, sample_accession) VALUES (?, ?)",
        (req.sku, req.sample_accession),
    )
    await db.commit()
    return {"linked": True, "sku": req.sku, "sample_accession": req.sample_accession}


@router.delete("/link")
async def unlink_sku_from_coa(sku: str, sample_accession: str, db=Depends(get_db)):
    """Remove link between a SKU and a COA sample."""
    await db.execute(
        "DELETE FROM coa_sku_links WHERE sku = ? AND sample_accession = ?",
        (sku, sample_accession),
    )
    await db.commit()
    return {"unlinked": True}


# ── ACS Lab connection status ─────────────────────────────────────


@router.get("/status")
async def acs_status():
    """Check if ACS Lab API key is configured and valid."""
    api_key = os.environ.get("ACS_LAB_API_KEY", "")
    if not api_key:
        return {"connected": False, "reason": "ACS_LAB_API_KEY not set"}
    try:
        client = ACSLabClient(api_key)
        user = await client.get_user()
        return {
            "connected": True,
            "user": user.get("full_name", ""),
            "email": user.get("email", ""),
        }
    except Exception as e:
        return {"connected": False, "reason": str(e)}
