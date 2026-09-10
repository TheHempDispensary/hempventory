"""LeafLife sync links the partner sheet's COA PDF to each LF- SKU."""
import aiosqlite
import pytest

from app.routers import inventory_router as ir

COA_URL = "https://leaflifewi.com/wp-content/uploads/2026/06/Illemonati-sno-caps-COA.pdf"


def _flower_row(strain, tier, prices, coa=COA_URL, thca="59.60%"):
    row = [""] * 16
    row[1] = "1000"
    row[2] = tier
    row[3] = strain
    row[4] = thca
    row[8] = "Hybrid"
    row[9] = coa
    row[12], row[13], row[14], row[15] = prices
    return row


def test_desired_carries_coa_url_and_thca():
    desired = ir._build_leaflife_desired({
        "Retail Flower Menu": [_flower_row("ILLEMONATI", "SNOWCAPS", ("$220", "$120", "$65", "$35"))],
        "Retail Concentrate Menu": [],
    })
    want = desired["LF-ILLEMONATI-3.5"]
    assert want["coa_url"] == COA_URL
    assert want["thca_pct"] == "59.60%"


def test_non_url_coa_cell_is_ignored():
    desired = ir._build_leaflife_desired({
        "Retail Flower Menu": [_flower_row("HEADBAND", "ESSENTIAL", ("$60", "$40", "$30", "$25"), coa="pending")],
        "Retail Concentrate Menu": [],
    })
    assert desired["LF-HEADBAND-3.5"]["coa_url"] == ""


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        """CREATE TABLE coa_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sample_accession TEXT NOT NULL,
            order_number TEXT, batch_no TEXT, business_name TEXT, product_name TEXT,
            product_type TEXT, consumption_type TEXT, description TEXT, test_purpose TEXT,
            sample_status TEXT, order_date TEXT, test_start_date TEXT, coa_approved_date TEXT,
            postal_code TEXT, extracted_from TEXT, coa_approved_filepath TEXT,
            source TEXT DEFAULT 'ACS', synced_at TIMESTAMP, UNIQUE(sample_accession))"""
    )
    await conn.execute(
        """CREATE TABLE coa_sku_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT NOT NULL,
            sample_accession TEXT NOT NULL, linked_at TIMESTAMP,
            UNIQUE(sku, sample_accession))"""
    )
    yield conn
    await conn.close()


async def _links(db, sku):
    cur = await db.execute(
        "SELECT sample_accession FROM coa_sku_links WHERE sku = ? ORDER BY sample_accession", (sku,)
    )
    return [r[0] for r in await cur.fetchall()]


async def test_upsert_links_manual_coa_idempotently(db):
    await ir._upsert_leaflife_coa(db, "LF-X-3.5", "X 3.5 GRAMS", COA_URL, "59.60%")
    await ir._upsert_leaflife_coa(db, "LF-X-3.5", "X 3.5 GRAMS", COA_URL, "59.60%")
    await ir._upsert_leaflife_coa(db, "LF-X-7 G", "X 7 GRAMS", COA_URL, "59.60%")

    cur = await db.execute("SELECT * FROM coa_results")
    rows = await cur.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "manual"
    assert row["coa_approved_filepath"] == COA_URL
    assert row["business_name"] == "LeafLife"
    assert row["description"] == "THCa 59.60%"
    assert row["sample_accession"].startswith("LEAFLIFE-")

    assert len(await _links(db, "LF-X-3.5")) == 1
    assert len(await _links(db, "LF-X-7 G")) == 1


async def test_changed_url_swaps_link_and_blank_removes_it(db):
    await ir._upsert_leaflife_coa(db, "LF-X-3.5", "X", COA_URL, "")
    old = (await _links(db, "LF-X-3.5"))[0]

    await ir._upsert_leaflife_coa(db, "LF-X-3.5", "X", COA_URL + "?v=2", "")
    links = await _links(db, "LF-X-3.5")
    assert len(links) == 1 and links[0] != old

    await ir._upsert_leaflife_coa(db, "LF-X-3.5", "X", "", "")
    assert await _links(db, "LF-X-3.5") == []
