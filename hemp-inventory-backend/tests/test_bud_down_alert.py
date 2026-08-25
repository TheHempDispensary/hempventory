from types import SimpleNamespace

import aiosqlite
import pytest

from app.database import DB_PATH, init_db
from app.routers import chat_router as cr


@pytest.fixture
async def db():
    await init_db()
    connection = await aiosqlite.connect(DB_PATH)
    yield connection
    await connection.close()


@pytest.fixture
def reset_health_state():
    cr._claude_consecutive_failures = 0
    cr._claude_last_error = None
    cr._claude_last_failure_at = None
    cr._claude_last_success_at = None
    cr._claude_last_alert_at = None
    yield
    cr._claude_consecutive_failures = 0
    cr._claude_last_error = None
    cr._claude_last_failure_at = None
    cr._claude_last_success_at = None
    cr._claude_last_alert_at = None


class FailingMessages:
    async def create(self, **kwargs):
        raise RuntimeError("You have reached your specified API usage limits.")


class SuccessfulMessages:
    async def create(self, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text="A helpful answer.")])


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


async def _record_alert(alerts, subject, html):
    alerts.append((subject, html))
    return True


@pytest.mark.asyncio
async def test_first_failure_alerts_and_returns_fallback(db, reset_health_state, monkeypatch):
    alerts = []
    monkeypatch.setattr(
        cr,
        "send_service_alert_email",
        lambda db, subject, html: _record_alert(alerts, subject, html),
    )
    monkeypatch.setattr(cr.anthropic, "AsyncAnthropic", lambda api_key: FakeClient(FailingMessages()))

    result = await cr._call_claude("system", [{"role": "user", "content": "Hi"}], db)

    assert result == cr.FALLBACK_MESSAGE
    assert len(alerts) == 1
    assert alerts[0][0] == "Bud is offline"
    assert "API usage limits" in alerts[0][1]
    assert cr._claude_consecutive_failures == 1


@pytest.mark.asyncio
async def test_failure_alert_is_throttled_then_repeated(db, reset_health_state, monkeypatch):
    alerts = []
    now = [1000.0]
    monkeypatch.setattr(cr.time, "time", lambda: now[0])
    monkeypatch.setattr(
        cr,
        "send_service_alert_email",
        lambda db, subject, html: _record_alert(alerts, subject, html),
    )
    monkeypatch.setattr(cr.anthropic, "AsyncAnthropic", lambda api_key: FakeClient(FailingMessages()))

    await cr._call_claude("system", [], db)
    await cr._call_claude("system", [], db)
    assert len(alerts) == 1

    now[0] += cr.CLAUDE_ALERT_INTERVAL + 1
    await cr._call_claude("system", [], db)
    assert len(alerts) == 2
    assert all(subject == "Bud is offline" for subject, _ in alerts)


@pytest.mark.asyncio
async def test_success_sends_recovery_alert_and_resets_counter(db, reset_health_state, monkeypatch):
    alerts = []
    monkeypatch.setattr(
        cr,
        "send_service_alert_email",
        lambda db, subject, html: _record_alert(alerts, subject, html),
    )
    clients = iter([FakeClient(FailingMessages()), FakeClient(SuccessfulMessages())])
    monkeypatch.setattr(cr.anthropic, "AsyncAnthropic", lambda api_key: next(clients))

    await cr._call_claude("system", [], db)
    result = await cr._call_claude("system", [], db)

    assert result == "A helpful answer."
    assert [subject for subject, _ in alerts] == ["Bud is offline", "Bud is back online"]
    assert cr._claude_consecutive_failures == 0
