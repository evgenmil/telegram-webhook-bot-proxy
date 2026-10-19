"""OpenRouter: /openrouter/<path> → https://openrouter.ai/<path>, ключ клиента в Authorization."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client_and_calls():
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path.endswith("/boom"):
            raise httpx.ConnectError("boom", request=req)
        if b'"stream":true' in req.content:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b'data: {"x":1}\n\ndata: [DONE]\n\n',
            )
        return httpx.Response(200, json={"id": "gen-1"})

    with TestClient(app) as client:
        app.state.openrouter._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        yield client, calls


def test_openrouter_forwards_path_query_body_and_auth(client_and_calls) -> None:
    client, calls = client_and_calls
    resp = client.post(
        "/openrouter/api/v1/chat/completions?a=1",
        content=b'{"model":"m"}',
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-or-test"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": "gen-1"}
    assert len(calls) == 1
    assert str(calls[0].url) == "https://openrouter.ai/api/v1/chat/completions?a=1"
    assert calls[0].content == b'{"model":"m"}'
    assert calls[0].headers["Authorization"] == "Bearer sk-or-test"
    assert calls[0].headers["host"] == "openrouter.ai"


def test_openrouter_streams_sse(client_and_calls) -> None:
    client, _ = client_and_calls
    resp = client.post(
        "/openrouter/api/v1/chat/completions",
        content=b'{"stream":true}',
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.text == 'data: {"x":1}\n\ndata: [DONE]\n\n'


def test_openrouter_unreachable_returns_502(client_and_calls) -> None:
    client, _ = client_and_calls
    resp = client.get("/openrouter/boom")
    assert resp.status_code == 502
    assert resp.json()["error"] == "openrouter_unreachable"


def test_openrouter_rejects_dot_segments(client_and_calls) -> None:
    client, calls = client_and_calls
    resp = client.get("/openrouter/api/%2E%2E/x")
    assert resp.status_code == 404
    assert calls == []
