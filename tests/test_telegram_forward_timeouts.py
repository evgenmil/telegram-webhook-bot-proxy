"""Long poll: таймауты и проброс query без токена и без сети к Telegram."""

import asyncio
import time

import httpx
import pytest
from starlette.requests import Request

from app.settings import Settings
from app.telegram_api_forwarder import TelegramApiForwarder


def test_default_http_read_timeout_covers_get_updates() -> None:
    assert Settings.model_fields["http_read_timeout"].default == 90.0


def test_upstream_url_preserves_query() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/bot123:ABC/getUpdates",
        "query_string": b"timeout=50&offset=1&limit=3",
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req = Request(scope, receive)
    fwd = TelegramApiForwarder(Settings(main_app_base_url="http://127.0.0.1"))
    url = fwd._upstream_url(req)
    assert url == (
        "https://api.telegram.org/bot123:ABC/getUpdates"
        "?timeout=50&offset=1&limit=3"
    )


def _updates_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/bot123:ABC/getUpdates",
        "query_string": b"timeout=50",
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _forwarder_with_mock(
    handler,
    *,
    read_timeout: float,
) -> TelegramApiForwarder:
    settings = Settings(
        main_app_base_url="http://127.0.0.1",
        http_read_timeout=read_timeout,
    )
    fwd = TelegramApiForwarder(settings)
    transport = httpx.MockTransport(handler)
    timeout = fwd._client.timeout
    fwd._client = httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=True,
    )
    return fwd


def test_forwarder_client_uses_settings_read_timeout() -> None:
    fwd = TelegramApiForwarder(
        Settings(main_app_base_url="http://127.0.0.1", http_read_timeout=90.0),
    )
    assert fwd._client.timeout.read == 90.0


@pytest.mark.asyncio
async def test_httpx_read_timeout_cuts_slow_upstream() -> None:
    """Тот же httpx.Timeout, что у forwarder: read=10 с обрывает delay/12."""
    timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        t0 = time.perf_counter()
        with pytest.raises(httpx.ReadTimeout):
            await client.get("https://httpbin.org/delay/12")
        elapsed = time.perf_counter() - t0
    assert 9.0 <= elapsed < 14.0


@pytest.mark.asyncio
async def test_forwarder_allows_delay_within_read_timeout() -> None:
    """read=90 с: upstream отвечает через 2 с — без таймаута."""

    async def quick(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={"ok": True, "result": []})

    fwd = _forwarder_with_mock(quick, read_timeout=90.0)
    resp = await fwd.forward(_updates_request())
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    await fwd.aclose()
