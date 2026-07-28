"""Tests for the product scraper's manufacturer/domain resolution."""

from app.routers.scraper_router import (
    BLOCK_STATUSES,
    MANUFACTURER_CATALOG,
    _extract_domain,
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
