"""Tests for the product scraper's manufacturer/domain resolution."""

from app.routers.scraper_router import (
    BLOCK_STATUSES,
    MANUFACTURER_CATALOG,
    _extract_domain,
    _is_block_page,
)


def test_extract_domain_accepts_bare_domain():
    assert _extract_domain("marijuanapackaging.com") == "marijuanapackaging.com"
    assert _extract_domain("greentechpackaging.com") == "greentechpackaging.com"


def test_extract_domain_strips_scheme_and_path():
    assert _extract_domain("https://www.greentechpackaging.com/") == "www.greentechpackaging.com"
    assert _extract_domain("http://marijuanapackaging.com/collections/jars") == "marijuanapackaging.com"


def test_extract_domain_ignores_plain_names():
    assert _extract_domain("Chubby Gorilla") is None
    assert _extract_domain("Marijuana Packaging") is None
    assert _extract_domain("") is None


def test_marijuana_packaging_registered_as_shopify():
    entry = MANUFACTURER_CATALOG.get("marijuanapackaging.com")
    assert entry is not None
    assert entry["domain"] == "marijuanapackaging.com"
    assert entry["platform"] == "shopify"


def test_block_statuses_cover_common_bot_protection():
    assert 429 in BLOCK_STATUSES
    assert 403 in BLOCK_STATUSES


def test_greentech_registered_as_shopify():
    entry = MANUFACTURER_CATALOG.get("greentechpackaging.com")
    assert entry is not None
    assert entry["domain"] == "www.greentechpackaging.com"
    assert entry["platform"] == "shopify"


def test_is_block_page_detects_challenge_interstitials():
    assert _is_block_page("<html><h1>Robot or human?</h1></html>")
    assert _is_block_page("<title>Just a moment...</title>")
    assert _is_block_page("<div>Please verify you are a human</div>")


def test_is_block_page_passes_real_product_pages():
    assert not _is_block_page("<html><h1>1/4 Ounce Child Resistant Bags</h1></html>")
    assert not _is_block_page("")
