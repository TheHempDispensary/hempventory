import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_db, DB_PATH
from app.acs_client import ACSLabClient
from app.routers.ecommerce_router import invalidate_product_cache

router = APIRouter(prefix="/api/coa", tags=["coa"])

# COA PDFs/images are stored next to the SQLite DB on the persistent volume
# (Fly mounts /data) so uploads survive redeploys.
_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.abspath(DB_PATH)) or ".", "coa_uploads"
)
_MAX_COA_BYTES = 25 * 1024 * 1024  # 25 MB
_COA_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _get_acs_client() -> ACSLabClient:
    api_key = os.environ.get("ACS_LAB_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ACS_LAB_API_KEY not configured")
    return ACSLabClient(api_key)


# ── Sync from ACS Lab ──────────────────────────────────────────────


async def run_coa_sync(db) -> dict:
    """Shared sync logic used by both the HTTP endpoint and the background job.

    Uses the ``/all`` endpoint (batch-level, 359 records) for the sample
    listing, and the alternate analyte endpoint for detailed test results.
    """
    client = _get_acs_client()

    # 1. Fetch all samples from /all endpoint (batch-level records)
    all_sample_records = await client.get_all_samples()
    if not all_sample_records:
        return {"synced_samples": 0, "synced_analytes": 0}

    samples: dict[str, dict] = {}
    for r in all_sample_records:
        acc = r.get("sample_accession", "")
        if not acc:
            continue
        samples[acc] = {
            "sample_accession": acc,
            "order_number": r.get("number", ""),
            "batch_no": r.get("batch_no", ""),
            "business_name": "",
            "product_name": r.get("product_name", ""),
            "product_type": r.get("product_type_name", ""),
            "consumption_type": "",
            "description": r.get("description", ""),
            "test_purpose": r.get("test_purpose", ""),
            "sample_status": r.get("sample_status", ""),
            "order_date": r.get("order_date", ""),
            "test_start_date": r.get("test_start_date", ""),
            "coa_approved_date": r.get("coa_approved_date", ""),
            "postal_code": "",
            "extracted_from": r.get("extracted_from", ""),
            "coa_approved_filepath": r.get("coa_approved_filepath", ""),
        }

    # 2. Fetch analyte results from the alternate endpoint
    all_analyte_rows = await client.get_all_analyte_results_alternate()
    analytes: list[dict] = []

    # Metric suffixes ordered longest-first to avoid partial matches
    _METRIC_SUFFIXES = [
        "_analyte_remark",
        "_concentration",
        "_result_unit",
        "_conc_unit",
        "_result",
    ]

    for r in all_analyte_rows:
        acc = r.get("sample_accession", "")
        if not acc:
            continue

        # Back-fill any sample fields from the analyte data if we have them
        if acc in samples and not samples[acc]["business_name"]:
            samples[acc]["business_name"] = r.get("business_name", "")
            samples[acc]["product_type"] = r.get("product_type_name", "") or samples[acc]["product_type"]
            samples[acc]["consumption_type"] = r.get("consumption_type", "")
            samples[acc]["postal_code"] = r.get("postal_code", "")

        panel_name = r.get("panel_name", "")
        if not panel_name:
            continue

        # Derive inline field prefix from panel_name:
        # lowercase, spaces→'_', dashes→'_', other chars stay
        panel_slug = panel_name.lower().replace(" ", "_").replace("-", "_")
        prefix = panel_slug + "_"
        panel_remark = r.get("panel_remark", "")
        panel_identifier = r.get("panel_identifier", "")

        # Parse ALL inline analyte fields from this row
        inline_analytes: dict[str, dict] = {}
        for key, val in r.items():
            if not key.startswith(prefix):
                continue
            after_prefix = key[len(prefix):]
            slug = None
            metric = None
            for sfx in _METRIC_SUFFIXES:
                if after_prefix.endswith(sfx):
                    slug = after_prefix[: -len(sfx)]
                    metric = sfx[1:]  # strip leading '_'
                    break
            if not slug or not metric:
                continue
            if slug not in inline_analytes:
                inline_analytes[slug] = {}
            inline_analytes[slug][metric] = val

        # Create one analyte record per inline field group
        for slug, metrics in inline_analytes.items():
            result_val = metrics.get("result", "")
            if not result_val and not metrics.get("concentration"):
                continue
            analytes.append({
                "sample_accession": acc,
                "panel_name": panel_name,
                "panel_identifier": panel_identifier,
                "analyte_abbreviation": "",
                "analyte_identifier": slug,
                "concentration": metrics.get("concentration", 0) or 0,
                "conc_unit": metrics.get("conc_unit", ""),
                "result": str(result_val) if result_val else "",
                "result_unit": metrics.get("result_unit", ""),
                "analyte_remark": metrics.get("analyte_remark", ""),
                "panel_remark": panel_remark,
            })

    # Clear-and-replace ONLY ACS-sourced data so manually-added (non-ACS) COAs
    # survive the sync. The empty-response guard above already prevents wiping
    # on a total API failure.
    await db.execute(
        """DELETE FROM coa_analyte_results
           WHERE sample_accession IN (
               SELECT sample_accession FROM coa_results
               WHERE source = 'ACS' OR source IS NULL
           )"""
    )
    await db.execute("DELETE FROM coa_results WHERE source = 'ACS' OR source IS NULL")

    # Insert samples
    for s in samples.values():
        await db.execute(
            """INSERT INTO coa_results
                (sample_accession, order_number, batch_no, business_name,
                 product_name, product_type, consumption_type, description,
                 test_purpose, sample_status, order_date, test_start_date,
                 coa_approved_date, postal_code, extracted_from,
                 coa_approved_filepath, source, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACS', CURRENT_TIMESTAMP)
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
                 coa_approved_filepath=excluded.coa_approved_filepath,
                 source='ACS',
                 synced_at=CURRENT_TIMESTAMP""",
            (
                s["sample_accession"], s["order_number"], s["batch_no"],
                s["business_name"], s["product_name"], s["product_type"],
                s["consumption_type"], s["description"], s["test_purpose"],
                s["sample_status"], s["order_date"], s["test_start_date"],
                s["coa_approved_date"], s["postal_code"], s["extracted_from"],
                s["coa_approved_filepath"],
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

    # Drop links pointing at accessions that no longer exist (keeps manual
    # COA links intact because their accessions remain in coa_results).
    await db.execute(
        "DELETE FROM coa_sku_links WHERE sample_accession NOT IN "
        "(SELECT sample_accession FROM coa_results)"
    )

    await db.commit()
    invalidate_product_cache()
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
                    MIN(cr.source) AS source,
                    MAX(cr.coa_approved_filepath) AS coa_approved_filepath,
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
    invalidate_product_cache()
    return {"linked": True, "sku": req.sku, "sample_accession": req.sample_accession}


@router.delete("/link")
async def unlink_sku_from_coa(sku: str, sample_accession: str, db=Depends(get_db)):
    """Remove link between a SKU and a COA sample."""
    await db.execute(
        "DELETE FROM coa_sku_links WHERE sku = ? AND sample_accession = ?",
        (sku, sample_accession),
    )
    await db.commit()
    invalidate_product_cache()
    return {"unlinked": True}


# ── Manual (non-ACS) COAs ──────────────────────────────────────────


class ManualAnalyte(BaseModel):
    panel_name: str = ""
    analyte_identifier: str = ""
    analyte_abbreviation: str = ""
    result: str = ""
    result_unit: str = ""
    concentration: float = 0.0
    conc_unit: str = ""
    analyte_remark: str = ""
    panel_remark: str = ""


class ManualCoaRequest(BaseModel):
    product_name: str = ""
    description: str = ""
    batch_no: str = ""
    business_name: str = ""  # lab / source name (e.g. "Green Scientific Labs")
    product_type: str = ""
    test_purpose: str = ""
    sample_status: str = ""
    coa_approved_date: str = ""
    coa_url: str = ""  # link to the COA PDF
    analytes: list[ManualAnalyte] = []
    skus: list[str] = []  # optional SKUs to link on create


async def _replace_manual_analytes(db, accession: str, analytes: list[ManualAnalyte]) -> int:
    """Delete and re-insert the analyte rows for a manual COA accession."""
    await db.execute(
        "DELETE FROM coa_analyte_results WHERE sample_accession = ?", (accession,)
    )
    count = 0
    for a in analytes:
        identifier = (a.analyte_identifier or a.analyte_abbreviation or "").strip()
        if not identifier and not (a.result or "").strip():
            continue
        await db.execute(
            """INSERT INTO coa_analyte_results
                (sample_accession, panel_name, panel_identifier,
                 analyte_abbreviation, analyte_identifier,
                 concentration, conc_unit, result, result_unit,
                 analyte_remark, panel_remark)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sample_accession, analyte_identifier, panel_name) DO UPDATE SET
                 analyte_abbreviation=excluded.analyte_abbreviation,
                 concentration=excluded.concentration,
                 conc_unit=excluded.conc_unit,
                 result=excluded.result,
                 result_unit=excluded.result_unit,
                 analyte_remark=excluded.analyte_remark,
                 panel_remark=excluded.panel_remark""",
            (
                accession, a.panel_name or "Results", "",
                a.analyte_abbreviation, identifier,
                a.concentration or 0, a.conc_unit, a.result, a.result_unit,
                a.analyte_remark, a.panel_remark,
            ),
        )
        count += 1
    return count


async def _require_manual(db, accession: str) -> None:
    """404 if the accession is missing, 403 if it belongs to an ACS sync."""
    cursor = await db.execute(
        "SELECT source FROM coa_results WHERE sample_accession = ?", (accession,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="COA not found")
    if (row["source"] or "ACS") != "manual":
        raise HTTPException(status_code=403, detail="Only manually-added COAs can be edited")


@router.post("/upload")
async def upload_coa_file(file: UploadFile = File(...)):
    """Store an uploaded COA (PDF/image) on the persistent volume and return a
    URL to reference it from a manual COA."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _COA_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload a PDF or image.",
        )
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    stored_name = uuid.uuid4().hex + ext
    dest = os.path.join(_UPLOAD_DIR, stored_name)
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_COA_BYTES:
                out.close()
                os.remove(dest)
                raise HTTPException(status_code=413, detail="File too large (max 25 MB)")
            out.write(chunk)
    return {"url": f"/api/coa/file/{stored_name}", "filename": file.filename}


@router.get("/file/{filename}")
async def get_coa_file(filename: str):
    """Serve a previously uploaded COA file."""
    safe_name = os.path.basename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _COA_MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="Not found")
    path = os.path.join(_UPLOAD_DIR, safe_name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type=_COA_MEDIA_TYPES[ext],
        content_disposition_type="inline",
    )


@router.post("/manual")
async def create_manual_coa(req: ManualCoaRequest, db=Depends(get_db)):
    """Add a COA from a non-ACS lab. Stored with source='manual' so the ACS
    sync never overwrites it."""
    if not (req.product_name.strip() or req.description.strip()):
        raise HTTPException(status_code=400, detail="Product name or description is required")

    accession = "MANUAL-" + uuid.uuid4().hex[:12].upper()
    await db.execute(
        """INSERT INTO coa_results
            (sample_accession, order_number, batch_no, business_name,
             product_name, product_type, consumption_type, description,
             test_purpose, sample_status, order_date, test_start_date,
             coa_approved_date, postal_code, extracted_from,
             coa_approved_filepath, source, synced_at)
           VALUES (?, '', ?, ?, ?, ?, '', ?, ?, ?, '', '', ?, '', '', ?, 'manual', CURRENT_TIMESTAMP)""",
        (
            accession, req.batch_no, req.business_name, req.product_name,
            req.product_type, req.description, req.test_purpose,
            req.sample_status, req.coa_approved_date, req.coa_url,
        ),
    )
    await _replace_manual_analytes(db, accession, req.analytes)
    for sku in req.skus:
        if sku.strip():
            await db.execute(
                "INSERT OR IGNORE INTO coa_sku_links (sku, sample_accession) VALUES (?, ?)",
                (sku.strip(), accession),
            )
    await db.commit()
    invalidate_product_cache()
    return {"created": True, "sample_accession": accession}


@router.put("/manual/{accession}")
async def update_manual_coa(accession: str, req: ManualCoaRequest, db=Depends(get_db)):
    """Update a manually-added COA. ACS-synced COAs cannot be edited."""
    await _require_manual(db, accession)
    if not (req.product_name.strip() or req.description.strip()):
        raise HTTPException(status_code=400, detail="Product name or description is required")

    await db.execute(
        """UPDATE coa_results SET
             batch_no=?, business_name=?, product_name=?, product_type=?,
             description=?, test_purpose=?, sample_status=?,
             coa_approved_date=?, coa_approved_filepath=?,
             synced_at=CURRENT_TIMESTAMP
           WHERE sample_accession=?""",
        (
            req.batch_no, req.business_name, req.product_name, req.product_type,
            req.description, req.test_purpose, req.sample_status,
            req.coa_approved_date, req.coa_url, accession,
        ),
    )
    await _replace_manual_analytes(db, accession, req.analytes)
    await db.commit()
    invalidate_product_cache()
    return {"updated": True, "sample_accession": accession}


@router.delete("/manual/{accession}")
async def delete_manual_coa(accession: str, db=Depends(get_db)):
    """Delete a manually-added COA and its analytes/links."""
    await _require_manual(db, accession)
    await db.execute("DELETE FROM coa_analyte_results WHERE sample_accession = ?", (accession,))
    await db.execute("DELETE FROM coa_sku_links WHERE sample_accession = ?", (accession,))
    await db.execute("DELETE FROM coa_results WHERE sample_accession = ?", (accession,))
    await db.commit()
    invalidate_product_cache()
    return {"deleted": True}


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
