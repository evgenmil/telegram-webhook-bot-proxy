"""Async-клиент к основному приложению: пересылка вебхука на настроенный путь."""

import logging

import httpx

from app.settings import Settings

log = logging.getLogger(__name__)


def _join_base_and_path(base: str, path: str) -> str:
    """Склейка base URL и пути без urljoin (на Windows path/urljoin ведут себя неожиданно)."""
    root = base.rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return f"{root}{p}"


class MainAppClient:
    def __init__(self, settings: Settings) -> None:
        self._url = _join_base_and_path(
            settings.main_app_base_url,
            settings.main_app_incoming_path,
        )
        self._bearer = settings.main_app_bearer_token
        timeout = httpx.Timeout(
            connect=settings.http_connect_timeout,
            read=settings.http_read_timeout,
            write=settings.http_read_timeout,
            pool=5.0,
        )
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    @property
    def forward_url_display(self) -> str:
        """URL без query и секретов — для логов."""
        return self._url

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forward_incoming(
        self,
        *,
        body: bytes,
        content_type: str | None,
        x_telegram_secret: str | None,
    ) -> httpx.Response:
        """Пересылает сырой payload на основной сервис."""
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if x_telegram_secret is not None:
            headers["X-Telegram-Bot-Api-Secret-Token"] = x_telegram_secret
        if self._bearer is not None:
            headers["Authorization"] = f"Bearer {self._bearer}"
        return await self._client.post(self._url, content=body, headers=headers)
