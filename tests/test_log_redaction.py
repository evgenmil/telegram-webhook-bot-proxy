"""Маскирование bot token в логах: универсальная функция и финальный formatter."""

import logging

from app.main import RedactingFormatter, configure_logging
from app.telegram_api_forwarder import redact_bot_token_in_text

_TOKEN_URL = "https://api.telegram.org/bot123456:ABCdef_123/sendMessage"
_REDACTED_URL = "https://api.telegram.org/bot<redacted>/sendMessage"


def _make_record(msg: object, args: object = None, exc_info: object = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


def test_redact_bot_token_plain_and_encoded() -> None:
    encoded = "/bot123456%3AABCdef_123/sendMessage"
    assert redact_bot_token_in_text(_TOKEN_URL) == _REDACTED_URL
    assert redact_bot_token_in_text(encoded) == "/bot<redacted>/sendMessage"


def test_formatter_masks_message_with_token() -> None:
    formatter = RedactingFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    record = _make_record(
        msg='HTTP Request: GET %s "HTTP/1.1 400 Bad Request"',
        args=(_TOKEN_URL,),
    )
    out = formatter.format(record)
    assert _TOKEN_URL not in out
    assert "bot<redacted>" in out


def test_formatter_masks_traceback_for_failed_request() -> None:
    """Ошибочный запрос с exc_info: токен не должен утечь в traceback."""
    formatter = RedactingFormatter(logging.Formatter("%(message)s"))
    try:
        raise RuntimeError(f"upstream failure for url {_TOKEN_URL}")
    except RuntimeError:
        import sys

        record = _make_record(msg="boom", exc_info=sys.exc_info())
    out = formatter.format(record)
    assert _TOKEN_URL not in out
    assert "bot<redacted>" in out


def test_configure_logging_wraps_handlers_idempotently() -> None:
    """RedactingFormatter ставится на root handler и не дублируется при повторном вызове."""
    configure_logging("INFO")
    configure_logging("INFO")
    root = logging.getLogger()
    assert root.handlers, "root logger must have at least one handler"
    for handler in root.handlers:
        assert isinstance(handler.formatter, RedactingFormatter)
        assert not isinstance(handler.formatter._inner, RedactingFormatter)  # noqa: SLF001
