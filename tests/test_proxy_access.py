"""PROXY_ACCESS_TOKEN: исходящие маршруты закрыты заголовком, вебхук и /health — нет."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client_and_calls():
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"ok": True})

    with TestClient(app) as client:
        mock = httpx.MockTransport(handler)
        app.state.openrouter._client = httpx.AsyncClient(transport=mock)
        app.state.telegram_forwarder._client = httpx.AsyncClient(transport=mock)
        app.state.main_app._client = httpx.AsyncClient(transport=mock)
        app.state.proxy_access_token = "s3cret"
        yield client, calls


@pytest.mark.parametrize("path", ["/openrouter/api/v1/models", "/bot123:ABC/getMe"])
def test_outbound_requires_token(client_and_calls, path: str) -> None:
    client, calls = client_and_calls
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Proxy-Access-Token": "wrong"}).status_code == 401
    assert calls == []

    resp = client.get(path, headers={"X-Proxy-Access-Token": "s3cret"})
    assert resp.status_code == 200
    assert len(calls) == 1
    assert "x-proxy-access-token" not in calls[0].headers


def test_webhook_and_health_not_protected(client_and_calls) -> None:
    client, calls = client_and_calls
    assert client.get("/health").status_code == 200
    assert client.post("/webhook/telegram/1", content=b"{}").status_code == 200
    assert len(calls) == 1
