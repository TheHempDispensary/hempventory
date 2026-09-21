"""Clover allows 5 requests/second per token, and a stock deduction is two calls
per line. Orders HD-6AA50883-5699, HD-6AA73538-2405 and HD-6AAD5598-6269 each
had only their first one or two lines deducted: every later call came back
429 Too Many Requests and the line was silently skipped. Deduction must wait
out the throttle instead of giving up.
"""

import httpx
import pytest

from app.routers import ecommerce_router as er
from app.routers.ecommerce_router import OrderItem


class ThrottledClover:
    """Fakes Clover's per-token limiter: after `burst` calls, 429 until the
    caller has waited at least once."""

    def __init__(self, stock: dict[str, float], burst: int = 5):
        self.stock = dict(stock)
        self.burst = burst
        self.calls_since_wait = 0
        self.requests: list[tuple[str, str]] = []
        self.throttled = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        item_id = request.url.path.rsplit("/", 1)[1]
        self.requests.append((request.method, item_id))
        self.calls_since_wait += 1
        if self.calls_since_wait > self.burst:
            self.throttled += 1
            return httpx.Response(
                429, json={"message": "429 Too Many Requests"},
                headers={"retry-after": "0", "x-ratelimit-tokenlimit": "5"},
            )
        if item_id not in self.stock:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST":
            import json
            self.stock[item_id] = json.loads(request.content)["quantity"]
        return httpx.Response(200, json={"item": {"id": item_id}, "quantity": self.stock[item_id]})


@pytest.fixture
def clover(monkeypatch):
    fake = ThrottledClover({"A": 3, "B": 7, "C": 1, "D": 33, "E": 2, "F": 174})
    transport = httpx.MockTransport(fake.handler)
    real_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(er.httpx, "AsyncClient", patched_client)

    async def fast_sleep(_seconds):
        fake.calls_since_wait = 0

    monkeypatch.setattr(er.asyncio, "sleep", fast_sleep)
    return fake


def _items(*ids):
    return [OrderItem(product_id=i, name=f"Item {i}", sku=f"SKU-{i}", price=100, quantity=1) for i in ids]


@pytest.mark.asyncio
async def test_six_line_order_is_fully_deducted_despite_429s(clover):
    written = await er._deduct_stock_for_order(_items("A", "B", "C", "D", "E", "F"), "ship")

    assert written == [True] * 6
    assert clover.throttled > 0, "the fake never throttled — test is not exercising the limiter"
    assert clover.stock == {"A": 2, "B": 6, "C": 0, "D": 32, "E": 1, "F": 173}


@pytest.mark.asyncio
async def test_missing_item_is_reported_not_retried(clover):
    written = await er._deduct_stock_for_order(_items("A", "GONE", "B"), "ship")

    assert written == [True, False, True]
    assert clover.stock["A"] == 2 and clover.stock["B"] == 6


@pytest.mark.asyncio
async def test_gives_up_after_persistent_429(monkeypatch):
    def always_429(request):
        return httpx.Response(429, headers={"retry-after": "0"})

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(er.asyncio, "sleep", no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(always_429)) as client:
        resp = await er._clover_request(client, "GET", "https://x/item_stocks/A")
    assert resp.status_code == 429
