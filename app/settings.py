"""Параметры из переменных окружения, валидация при загрузке."""

from functools import lru_cache
from typing import Any, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Секреты и URL только из env; не логируем токен и заголовки целиком."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Обязательные для работы прокси (см. .env.example). Токен Bot API в сервисе не храним.
    main_app_base_url: str

    # ASGI
    host: str = "0.0.0.0"
    port: int = 8000

    # Пути: что слушаем / куда в основной сервис
    proxy_incoming_webhook_path: str = "/webhook/telegram"
    main_app_incoming_path: str = "/api/telegram/webhook"

    # Таймауты исходящих HTTP (httpx), секунды.
    # getUpdates: Telegram держит соединение до timeout (макс. 50) — read должен быть с запасом.
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 90.0
    # OpenRouter: генерация без stream может идти минутами — read с запасом
    openrouter_read_timeout: float = 300.0

    log_level: str = "INFO"

    # Опционально: Bearer к основному приложению при пересылке вебхука
    main_app_bearer_token: str | None = None

    # Опционально: общий секрет для исходящих маршрутов (/bot…, /openrouter/…).
    # Если задан — клиент обязан прислать его в заголовке X-Proxy-Access-Token.
    proxy_access_token: str | None = None

    @field_validator("main_app_base_url")
    @classmethod
    def strip_base_url(cls, v: str) -> str:
        s = v.rstrip("/")
        if not s:
            raise ValueError("MAIN_APP_BASE_URL must be non-empty")
        return s

    @field_validator("main_app_bearer_token", "proxy_access_token", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> str | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("proxy_incoming_webhook_path", "main_app_incoming_path")
    @classmethod
    def path_starts_with_slash(cls, v: str) -> str:
        if not v.startswith("/"):
            return f"/{v}"
        return v

    @model_validator(mode="after")
    def check_non_empty_secrets(self) -> Self:
        if not self.main_app_base_url:
            raise ValueError("MAIN_APP_BASE_URL must be non-empty")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
