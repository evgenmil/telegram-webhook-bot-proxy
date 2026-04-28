"""Точка входа: ASGI-приложение, тонкие маршруты, клиенты в lifespan."""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette import status

from app.main_app_client import MainAppClient
from app.settings import get_settings
from app.telegram_api_forwarder import (
    TelegramApiForwarder,
    redact_bot_token_in_text,
    redact_telegram_path_for_log,
)

log = logging.getLogger(__name__)

router = APIRouter()

# Те же HTTP-методы, что и у api.telegram.org для Bot API
_TELEGRAM_FORWARD_METHODS = [
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "HEAD",
    "OPTIONS",
]


class RedactTelegramTokenLogFilter(logging.Filter):
    """Маскирует bot token в сообщениях/аргументах/traceback логгеров uvicorn/httpx/app."""

    @staticmethod
    def _redact_obj(value: object) -> object:
        if isinstance(value, str):
            return redact_bot_token_in_text(value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact_obj(record.msg)
        if isinstance(record.args, dict):
            record.args = {k: self._redact_obj(v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(self._redact_obj(v) for v in record.args)
        elif record.args:
            record.args = self._redact_obj(record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_bot_token_in_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_bot_token_in_text(record.stack_info)
        return True


class RedactingFormatter(logging.Formatter):
    """Оборачивает существующий Formatter; маскирует bot token в финальной строке.

    Дополнительный (и самый надёжный) рубеж: даже если фильтр на logger обойдён
    (например, запись пришла от дочернего/сторонего логгера), здесь токен будет
    замаскирован уже в готовой к выводу строке.
    """

    def __init__(self, inner: logging.Formatter) -> None:
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return redact_bot_token_in_text(self._inner.format(record))

    def formatException(self, ei) -> str:  # noqa: ANN001, N802 — сигнатура stdlib
        return redact_bot_token_in_text(self._inner.formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802 — сигнатура stdlib
        return redact_bot_token_in_text(self._inner.formatStack(stack_info))


# Логгеры, в которых может всплыть bot token: uvicorn пишет URL в access/error,
# httpx и httpcore — в HTTP-сообщениях, root — на случай ошибочных запросов через app.*
_REDACT_TARGET_LOGGERS: tuple[str, ...] = (
    "",  # root
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
)


def _attach_filter_once(target: logging.Logger | logging.Handler) -> None:
    for f in target.filters:
        if isinstance(f, RedactTelegramTokenLogFilter):
            return
    target.addFilter(RedactTelegramTokenLogFilter())


def _wrap_formatter_once(handler: logging.Handler) -> None:
    current = handler.formatter
    if isinstance(current, RedactingFormatter):
        return
    inner = current or logging.Formatter("%(message)s")
    handler.setFormatter(RedactingFormatter(inner))


def configure_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), None)
    if not isinstance(lvl, int):
        lvl = logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=lvl,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    else:
        root.setLevel(lvl)
        for h in root.handlers:
            h.setLevel(lvl)

    # Двойная защита от утечки токена в логах:
    #   1) фильтр редактирует record.msg/args/exc_text/stack_info до форматирования;
    #   2) RedactingFormatter — финальный рубеж: маскирует уже отформатированную
    #      строку перед emit (важно, т.к. uvicorn после нашего setup всё равно
    #      зовёт dictConfig для своих логгеров и создаёт новые handlers).
    for logger_name in _REDACT_TARGET_LOGGERS:
        logger = logging.getLogger(logger_name)
        _attach_filter_once(logger)
        for handler in logger.handlers:
            _attach_filter_once(handler)
            _wrap_formatter_once(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    tg = TelegramApiForwarder(s)
    main_app = MainAppClient(s)
    app.state.telegram_forwarder = tg
    app.state.main_app = main_app
    log.info("Сервис стартовал: исходящий Bot API — прокси /bot<token>/method без хранения токена")
    log.info("Пересылка вебхука к основному приложению: %s", main_app.forward_url_display)
    yield
    await tg.aclose()
    await main_app.aclose()
    log.info("Сервис остановлен.")


def create_app() -> FastAPI:
    get_settings()  # ошибка валидации — до uvicorn, понятный stderr
    app = FastAPI(
        title="telegram-webhook-bot-proxy",
        description="Прокси между основным приложением и Telegram Bot API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def log_request_path(request: Request, call_next):
        log.info(
            "%s %s",
            request.method,
            redact_telegram_path_for_log(request.url.path),
        )
        return await call_next(request)

    s = get_settings()
    # Важно: этот путь — до router с /{a}/{b}, иначе POST /webhook/... уйдёт в forward
    app.add_api_route(
        s.proxy_incoming_webhook_path,
        telegram_incoming_webhook,
        methods=["POST"],
    )
    app.include_router(router)
    return app


def get_telegram_forwarder(request: Request) -> TelegramApiForwarder:
    return request.app.state.telegram_forwarder


@router.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.api_route(
    "/{bot_segment}/{api_method}",
    methods=_TELEGRAM_FORWARD_METHODS,
    tags=["outgoing-telegram"],
    summary="Прозрачный прокси в api.telegram.org (как в Bot API, токен в path)",
)
async def forward_telegram_bot_api(
    request: Request,
    bot_segment: str,
    api_method: str,
    forwarder: Annotated[TelegramApiForwarder, Depends(get_telegram_forwarder)],
) -> Response:
    if not bot_segment.startswith("bot") or len(bot_segment) <= 3:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ожидается путь Bot API: /bot<token>/method",
        )
    if not str(api_method).strip():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="некорректное имя метода Bot API",
        )
    rid = str(uuid.uuid4())[:8]
    try:
        up = await forwarder.forward(request)
    except httpx.ConnectTimeout:
        log.warning("[%s] Telegram connect timeout", rid)
        return JSONResponse(
            {"error": "telegram_connect_timeout"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.ReadTimeout:
        log.warning("[%s] Telegram read timeout", rid)
        return JSONResponse(
            {"error": "telegram_read_timeout"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.RequestError as e:
        log.warning("[%s] Telegram request error: %s", rid, type(e).__name__)
        return JSONResponse(
            {
                "error": "telegram_unreachable",
                "detail": type(e).__name__,
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(
        content=up.content,
        status_code=up.status_code,
        media_type=up.headers.get("content-type") or "text/plain",
    )


async def telegram_incoming_webhook(
    request: Request,
) -> Response:
    main_app: MainAppClient = request.app.state.main_app
    rid = str(uuid.uuid4())[:8]
    x_secret_in = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    raw = await request.body()
    ct = request.headers.get("content-type")
    try:
        up = await main_app.forward_incoming(
            body=raw,
            content_type=ct,
            x_telegram_secret=x_secret_in,
        )
    except httpx.ConnectTimeout:
        log.warning("[%s] main app connect timeout", rid)
        return JSONResponse(
            {"error": "main_app_connect_timeout"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.ReadTimeout:
        log.warning("[%s] main app read timeout", rid)
        return JSONResponse(
            {"error": "main_app_read_timeout"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.RequestError as e:
        log.warning("[%s] main app request error: %s", rid, type(e).__name__)
        return JSONResponse(
            {
                "error": "main_app_unreachable",
                "detail": type(e).__name__,
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(
        content=up.content,
        status_code=up.status_code,
        media_type=up.headers.get("content-type") or "text/plain",
    )


app = create_app()


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    uvicorn.run(
        "app.main:app",
        host=s.host,
        port=s.port,
        log_level=s.log_level.lower(),
    )


if __name__ == "__main__":
    main()
