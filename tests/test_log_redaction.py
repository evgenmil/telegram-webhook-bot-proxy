"""Проверки маскирования bot token в логах (включая ошибочные запросы и traceback)."""

import logging

from app.main import (
    RedactTelegramTokenLogFilter,
    configure_logging,
)
from app.telegram_api_forwarder import redact_bot_token_in_text

_TOKEN_URL = "https://api.telegram.org/bot123456:ABCdef_123/sendMessage"
_REDACTED_URL = "https://api.telegram.org/bot<redacted>/sendMessage"


def _make_record(
    msg: object,
    args: object = None,
    exc_info: object = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )
    return record


def test_redact_bot_token_in_text_plain_and_encoded() -> None:
    plain = _TOKEN_URL
    encoded = "/bot123456%3AABCdef_123/sendMessage"
    assert redact_bot_token_in_text(plain) == _REDACTED_URL
    assert redact_bot_token_in_text(encoded) == "/bot<redacted>/sendMessage"


def test_log_filter_redacts_message_and_args() -> None:
    filter_ = RedactTelegramTokenLogFilter()
    record = _make_record(
        msg='HTTP Request: %s %s "HTTP/1.1 400 Bad Request"',
        args=("GET", _TOKEN_URL),
    )
    assert filter_.filter(record) is True
    assert record.args[1] == _REDACTED_URL


def test_log_filter_redacts_exc_text_for_failed_request() -> None:
    """Ошибочный запрос с exc_info: токен не должен утечь в traceback."""
    filter_ = RedactTelegramTokenLogFilter()
    try:
        raise RuntimeError(f"upstream failure for url {_TOKEN_URL}")
    except RuntimeError:
        import sys

        record = _make_record(msg="boom", exc_info=sys.exc_info())
    assert filter_.filter(record) is True
    assert record.exc_text is not None
    assert _TOKEN_URL not in record.exc_text
    assert "bot<redacted>" in record.exc_text


def test_configure_logging_does_not_duplicate_filter() -> None:
    """Повторный вызов configure_logging не должен дублировать фильтр."""
    configure_logging("INFO")
    configure_logging("INFO")
    for name in ("", "uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        redact_filters = [
            f for f in logger.filters if isinstance(f, RedactTelegramTokenLogFilter)
        ]
        assert len(redact_filters) <= 1, f"duplicate redact filter on logger {name!r}"
