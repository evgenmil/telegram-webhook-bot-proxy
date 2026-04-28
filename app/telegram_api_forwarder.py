"""Прозрачный прокси к https://api.telegram.org — токен только в path запроса, в памяти не храним."""

import logging
import re

import httpx
from starlette.requests import Request

from app.settings import Settings

log = logging.getLogger(__name__)

UPSTREAM = "https://api.telegram.org"

# Заголовки, которые не дублируем на upstream
_SKIP_REQUEST_HEADERS: frozenset[bytes] = frozenset(
    {
        b"host",
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
        b"authorization",  # не пересылаем клиентский Authorization в api.telegram.org
    }
)

_BOT_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"bot\d+:[A-Za-z0-9_-]+"),
    re.compile(r"bot\d+%3A[A-Za-z0-9_-]+", re.IGNORECASE),
)


def redact_bot_token_in_text(value: str) -> str:
    """Маскирует bot token в произвольной строке/URL для логов."""
    out = value
    for pattern in _BOT_TOKEN_PATTERNS:
        out = pattern.sub("bot<redacted>", out)
    return out


def redact_telegram_path_for_log(path: str) -> str:
    """Первая часть /bot<token>/… → /bot<redacted>/… для логов."""
    if not path or path == "/":
        return path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 1 and parts[0].startswith("bot") and len(parts[0]) > 3:
        parts[0] = "bot<redacted>"
    return redact_bot_token_in_text("/" + "/".join(parts))


class TelegramApiForwarder:
    """Соединение: тот же path/query/body, что к api.telegram.org; токен в первом сегменте (bot...)."""

    def __init__(self, settings: Settings) -> None:
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_read_timeout,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _upstream_url(self, request: Request) -> str:
        path = request.url.path
        q = request.url.query
        base = f"{UPSTREAM}{path}"
        if q:
            return f"{base}?{q}"
        return base

    @staticmethod
    def _request_headers_to_upstream(request: Request) -> dict[str, str]:
        h: dict[str, str] = {}
        for name in request.headers:
            nlow = name.encode("latin-1").lower()
            if nlow in _SKIP_REQUEST_HEADERS:
                continue
            h[name] = request.headers[name]
        return h

    async def forward(self, request: Request) -> httpx.Response:
        url = self._upstream_url(request)
        log.debug("Upstream Telegram: %s", redact_telegram_path_for_log(request.url.path))
        headers = self._request_headers_to_upstream(request)
        body: bytes = await request.body()
        return await self._client.request(
            request.method,
            url,
            headers=headers,
            content=body,
        )
