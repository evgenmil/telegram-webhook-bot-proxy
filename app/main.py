"""Точка входа: ASGI-приложение, тонкие маршруты, клиенты в lifespan."""

import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette import status
from starlette.background import BackgroundTask

from app.main_app_client import MainAppClient
from app.openrouter_forwarder import OpenRouterForwarder
from app.settings import get_settings
from app.telegram_api_forwarder import (
    TelegramApiForwarder,
    redact_bot_token_in_text,
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


class RedactingFormatter(logging.Formatter):
    """Оборачивает существующий Formatter и маскирует bot token в финальной строке.

    Это финальный рубеж перед emit: маскирует и сообщение, и traceback (formatException),
    и stack_info — независимо от того, какой логгер записал запись. Покрывает в т.ч.
    `httpx`, `uvicorn.access`, `uvicorn.error` и ошибочные запросы.
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


# Логгеры, чьи handlers нужно обернуть редактирующим formatter'ом.
# uvicorn пишет URL в access/error; httpx/httpcore — в сообщениях запросов;
# root покрывает все приложение-логгеры (включая ошибочные запросы через app.*).
_REDACT_TARGET_LOGGERS: tuple[str, ...] = (
    "",  # root
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
)


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

    for logger_name in _REDACT_TARGET_LOGGERS:
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            _wrap_formatter_once(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    tg = TelegramApiForwarder(s)
    main_app = MainAppClient(s)
    openrouter = OpenRouterForwarder(s)
    app.state.telegram_forwarder = tg
    app.state.main_app = main_app
    app.state.openrouter = openrouter
    app.state.proxy_access_token = s.proxy_access_token
    if s.proxy_access_token is None:
        log.warning("PROXY_ACCESS_TOKEN не задан: исходящие маршруты открыты для всех")
    log.info("Сервис стартовал: исходящий Bot API — прокси /bot<token>/method без хранения токена")
    log.info("Пересылка вебхука к основному приложению: %s", main_app.forward_url_display)
    log.info("Прокси OpenRouter: /openrouter/<path> → https://openrouter.ai/<path>")
    yield
    await tg.aclose()
    await main_app.aclose()
    await openrouter.aclose()
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
            redact_bot_token_in_text(request.url.path),
        )
        return await call_next(request)

    s = get_settings()
    # Важно: эти пути — до router с /{a}/{b}, иначе POST /webhook/... уйдёт в forward.
    # Хвост после пути вебхука (например id бота) пробрасывается в основное приложение как есть.
    base = s.proxy_incoming_webhook_path.rstrip("/")
    for path in (base, base + "/{suffix:path}"):
        app.add_api_route(path, telegram_incoming_webhook, methods=["POST"])
    app.add_api_route(
        "/openrouter/{path:path}",
        forward_openrouter,
        methods=_TELEGRAM_FORWARD_METHODS,
        dependencies=[Depends(require_proxy_access)],
        tags=["outgoing-openrouter"],
        summary="Прозрачный прокси в openrouter.ai (ключ в Authorization клиента)",
    )
    app.include_router(router)
    return app


def get_telegram_forwarder(request: Request) -> TelegramApiForwarder:
    return request.app.state.telegram_forwarder


def require_proxy_access(request: Request) -> None:
    """Если задан PROXY_ACCESS_TOKEN — исходящие маршруты только с верным X-Proxy-Access-Token."""
    expected: str | None = request.app.state.proxy_access_token
    if expected is None:
        return
    got = request.headers.get("X-Proxy-Access-Token", "")
    if not secrets.compare_digest(got.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="неверный или отсутствующий X-Proxy-Access-Token",
        )


@router.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.api_route(
    "/{bot_segment}/{api_method}",
    methods=_TELEGRAM_FORWARD_METHODS,
    dependencies=[Depends(require_proxy_access)],
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
    suffix = request.path_params.get("suffix", "").strip("/")
    if suffix and any(seg in ("", ".", "..") for seg in suffix.split("/")):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="некорректный хвост пути вебхука",
        )
    rid = str(uuid.uuid4())[:8]
    x_secret_in = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    raw = await request.body()
    ct = request.headers.get("content-type")
    try:
        up = await main_app.forward_incoming(
            body=raw,
            content_type=ct,
            x_telegram_secret=x_secret_in,
            suffix=suffix,
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


async def forward_openrouter(request: Request, path: str) -> Response:
    forwarder: OpenRouterForwarder = request.app.state.openrouter
    if any(seg in (".", "..") for seg in path.split("/")):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="некорректный путь OpenRouter",
        )
    rid = str(uuid.uuid4())[:8]
    try:
        up = await forwarder.send(request, path)
    except httpx.ConnectTimeout:
        log.warning("[%s] OpenRouter connect timeout", rid)
        return JSONResponse(
            {"error": "openrouter_connect_timeout"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.ReadTimeout:
        log.warning("[%s] OpenRouter read timeout", rid)
        return JSONResponse(
            {"error": "openrouter_read_timeout"},
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except httpx.RequestError as e:
        log.warning("[%s] OpenRouter request error: %s", rid, type(e).__name__)
        return JSONResponse(
            {
                "error": "openrouter_unreachable",
                "detail": type(e).__name__,
            },
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    return StreamingResponse(
        up.aiter_bytes(),
        status_code=up.status_code,
        headers=OpenRouterForwarder.response_headers(up),
        background=BackgroundTask(up.aclose),
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
