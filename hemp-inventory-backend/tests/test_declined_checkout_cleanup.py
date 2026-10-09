import httpx

from app.routers import ecommerce_router as ec


def _transport(orders, payments_by_order, payment_orders, deleted):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "DELETE":
            deleted.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={})
        if "/payments/" in path and "/orders/" not in path:
            pid = path.rsplit("/", 1)[1]
            if pid in payment_orders:
                return httpx.Response(200, json={"id": pid, "order": {"id": payment_orders[pid]}})
            return httpx.Response(404, json={})
        if path.endswith("/payments"):
            oid = path.split("/orders/")[1].split("/")[0]
            return httpx.Response(200, json={"elements": payments_by_order.get(oid, [])})
        if "/orders/" in path:
            oid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=next(o for o in orders if o["id"] == oid))
        if path.endswith("/orders"):
            return httpx.Response(200, json={"elements": orders})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


ORDERS = [
    {"id": "FAILED", "title": "ECOMM ORDER", "total": 8218, "state": "open"},
    {"id": "PAID", "title": "ECOMM ORDER", "total": 8218, "state": "locked"},
    {"id": "OTHER", "title": "ECOMM ORDER", "total": 1000, "state": "open"},
]
PAYMENTS = {
    "FAILED": [{"result": "FAIL"}],
    "PAID": [{"result": "SUCCESS"}],
    "OTHER": [{"result": "FAIL"}],
}


async def test_declined_charge_removes_record_and_failed_ticket_by_charge_id():
    deleted = []
    transport = _transport(ORDERS, PAYMENTS, {"PAY1": "FAILED"}, deleted)
    async with httpx.AsyncClient(transport=transport) as client:
        await ec._discard_declined_checkout_orders(
            client, "RECORD", {"error": {"charge": "PAY1", "code": "card_declined"}}, 8218, 0
        )
    assert deleted == ["RECORD", "FAILED"]


async def test_declined_charge_falls_back_to_amount_and_never_touches_paid():
    deleted = []
    transport = _transport(ORDERS, PAYMENTS, {}, deleted)
    async with httpx.AsyncClient(transport=transport) as client:
        await ec._discard_declined_checkout_orders(
            client, "", {"message": "Card declined"}, 8218, 0
        )
    assert deleted == ["FAILED"]
