"""Pins the HTTP shape of removing a category from an item.

Clover has no DELETE /category_items/{id} resource — associations are deleted
through the same collection endpoint that creates them, with ?delete=true and
the tuple in the body. Getting this wrong fails silently at the API level, so
the request itself is asserted here.
"""
import json

import httpx
import pytest

from app import clover_client


@pytest.mark.asyncio
async def test_unassign_category_posts_with_delete_flag(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.read())
        return httpx.Response(200, json={})

    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(clover_client.httpx, "AsyncClient", fake_client)

    client = clover_client.CloverClient("M1", "token")
    await client.unassign_category("ITEM1", "CAT1")

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/merchants/M1/category_items?delete=true")
    assert seen["body"] == {
        "elements": [{"item": {"id": "ITEM1"}, "category": {"id": "CAT1"}}]
    }


@pytest.mark.asyncio
async def test_unassign_category_raises_on_rejection(monkeypatch):
    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(405, text="Method Not Allowed")
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(clover_client.httpx, "AsyncClient", fake_client)

    client = clover_client.CloverClient("M1", "token")
    with pytest.raises(httpx.HTTPStatusError):
        await client.unassign_category("ITEM1", "CAT1")
