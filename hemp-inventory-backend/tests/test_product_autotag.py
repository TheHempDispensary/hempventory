"""AI product tagging must not re-bill for the same products forever.

The product cache refreshes every few minutes and on every inventory edit, and
each refresh used to re-send every product the model couldn't classify to
Anthropic, so the API bill grew without any product ever getting tagged.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "thd_autotag_test.db"))

import pytest
import pytest_asyncio

from app.database import DB_PATH, init_db
from app.routers import ecommerce_router as er


@pytest_asyncio.fixture
async def clean_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    await init_db()
    er._autotag_last_run = 0.0
    er._autotag_running = False
    yield
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def _products(count: int) -> list:
    return [
        {"sku": f"SKU-{i}", "name": f"PRODUCT {i}", "categories": ["Flower"], "description": ""}
        for i in range(count)
    ]


async def _tag(products: list) -> None:
    await er._auto_tag_new_products(products, {}, {}, {}, {})


@pytest.mark.asyncio
async def test_untaggable_products_are_not_retried_forever(clean_db, monkeypatch):
    calls: list[str] = []

    async def unhelpful_model(product: dict) -> dict:
        calls.append(product["name"])
        return {}

    monkeypatch.setattr(er, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(er, "_call_anthropic_for_tag", unhelpful_model)

    for _ in range(er.AUTOTAG_MAX_ATTEMPTS + 2):
        er._autotag_last_run = 0.0
        await _tag(_products(2))

    assert len(calls) == 2 * er.AUTOTAG_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_cache_refreshes_do_not_start_a_pass_each_time(clean_db, monkeypatch):
    calls: list[str] = []

    async def unhelpful_model(product: dict) -> dict:
        calls.append(product["name"])
        return {}

    monkeypatch.setattr(er, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(er, "_call_anthropic_for_tag", unhelpful_model)

    await _tag(_products(3))
    await _tag(_products(3))
    await _tag(_products(3))

    assert len(calls) == 3


@pytest.mark.asyncio
async def test_a_pass_is_capped(clean_db, monkeypatch):
    calls: list[str] = []

    async def unhelpful_model(product: dict) -> dict:
        calls.append(product["name"])
        return {}

    monkeypatch.setattr(er, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(er, "_call_anthropic_for_tag", unhelpful_model)

    await _tag(_products(er.AUTOTAG_MAX_PER_RUN * 4))

    assert len(calls) == er.AUTOTAG_MAX_PER_RUN


@pytest.mark.asyncio
async def test_spend_cap_stops_the_whole_pass(clean_db, monkeypatch):
    calls: list[str] = []

    async def capped(product: dict) -> dict:
        calls.append(product["name"])
        raise er._AutoTagUnavailable("HTTP 400")

    monkeypatch.setattr(er, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(er, "_call_anthropic_for_tag", capped)

    await _tag(_products(er.AUTOTAG_MAX_PER_RUN))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_kill_switch_stops_tagging(clean_db, monkeypatch):
    calls: list[str] = []

    async def model(product: dict) -> dict:
        calls.append(product["name"])
        return {}

    monkeypatch.setattr(er, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(er, "AUTOTAG_ENABLED", False)
    monkeypatch.setattr(er, "_call_anthropic_for_tag", model)

    await _tag(_products(3))

    assert calls == []
