"""Вебхук: хвост пути после PROXY_INCOMING_WEBHOOK_PATH пробрасывается в основное приложение."""

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
        app.state.main_app._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        yield client, calls


@pytest.mark.parametrize(
    ("path", "expected_url"),
    [
        ("/webhook/telegram", "http://127.0.0.1/api/telegram/webhook"),
        ("/webhook/telegram/111111111", "http://127.0.0.1/api/telegram/webhook/111111111"),
        ("/webhook/telegram/shop/a-1", "http://127.0.0.1/api/telegram/webhook/shop/a-1"),
    ],
)
def test_webhook_forwards_suffix(client_and_calls, path: str, expected_url: str) -> None:
    client, calls = client_and_calls
    resp = client.post(
        path,
        content=b'{"update_id":1}',
        headers={
            "Content-Type": "application/json",
            "X-Telegram-Bot-Api-Secret-Token": "s1",
        },
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert str(calls[0].url) == expected_url
    assert calls[0].content == b'{"update_id":1}'
    assert calls[0].headers["X-Telegram-Bot-Api-Secret-Token"] == "s1"


def test_webhook_rejects_dot_segments(client_and_calls) -> None:
    client, calls = client_and_calls
    resp = client.post("/webhook/telegram/a/%2E%2E/admin", content=b"{}")
    assert resp.status_code == 404
    assert calls == []


def test_webhook_logs_non_2xx_from_main_app(caplog) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="no such route")

    with TestClient(app) as client:
        app.state.main_app._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        with caplog.at_level("WARNING", logger="app.main"):
            resp = client.post("/webhook/telegram/111111111", content=b"{}")

    assert resp.status_code == 404
    assert resp.text == "no such route"
    assert "main app responded 404" in caplog.text
    assert "http://127.0.0.1/api/telegram/webhook/111111111" in caplog.text
    assert "no such route" in caplog.text
