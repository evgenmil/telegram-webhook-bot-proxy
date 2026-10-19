"""Прозрачный прокси к https://openrouter.ai — API-ключ только в Authorization клиента, не храним."""

import logging

import httpx
from starlette.requests import Request

from app.settings import Settings

log = logging.getLogger(__name__)

UPSTREAM = "https://openrouter.ai"

# Заголовки запроса, которые не дублируем на upstream (Authorization пересылаем — это ключ клиента)
_SKIP_REQUEST_HEADERS: frozenset[str] = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "x-proxy-access-token",  # секрет доступа к самому прокси
    }
)

# Заголовки ответа, которые не отдаём клиенту: тело уже распаковано httpx, длину считает Starlette
_SKIP_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-encoding",
        "content-length",
    }
)


class OpenRouterForwarder:
    """`/openrouter/<path>` → `https://openrouter.ai/<path>`; ответ стримится (SSE для stream=true)."""

    def __init__(self, settings: Settings) -> None:
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.openrouter_read_timeout,
            write=settings.openrouter_read_timeout,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def upstream_url(path: str, query: str) -> str:
        base = f"{UPSTREAM}/{path.lstrip('/')}"
        if query:
            return f"{base}?{query}"
        return base

    @staticmethod
    def response_headers(up: httpx.Response) -> dict[str, str]:
        return {
            k: v for k, v in up.headers.items() if k.lower() not in _SKIP_RESPONSE_HEADERS
        }

    async def send(self, request: Request, path: str) -> httpx.Response:
        """Отправляет запрос и возвращает ответ со стримом; вызывающий обязан закрыть `aclose()`."""
        url = self.upstream_url(path, request.url.query)
        log.debug("Upstream OpenRouter: /%s", path)
        headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _SKIP_REQUEST_HEADERS
        }
        body = await request.body()
        req = self._client.build_request(request.method, url, headers=headers, content=body)
        return await self._client.send(req, stream=True)
