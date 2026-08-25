import json
import logging
import re
import sys
from datetime import datetime, timezone

_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stacklevel",
        "taskName",
        "thread",
        "threadName",
    }
)

# Patterns for secrets that should never appear in logs
_SECRET_PATTERNS = (
    re.compile(r'(?i)(api[_-]?key|secret|password|token|authorization)[\s:=]+[\w\-_\.]{8,}'),
    re.compile(r'Bearer\s+[\w\-\._~+/]+=*'),
    re.compile(r'sk-[\w\-]{20,}'),  # OpenAI API key pattern
    re.compile(r'(redis://[^:]+:)[^@]+@'),  # Redis URL with password
    re.compile(r'(postgresql://[^:]+:)[^@]+@'),  # Postgres URL with password
)


def _redact_secrets(text: str) -> str:
    """Redact common secret patterns from log output."""
    result = text
    for pattern in _SECRET_PATTERNS:
        # Replace the secret value part (after the captured prefix) with [REDACTED]
        result = pattern.sub(lambda m: m.group(1) + "[REDACTED]" if m.lastindex and m.lastindex >= 1 else "[REDACTED]", result)
    return result


class JsonFormatter(logging.Formatter):
    def __init__(self, redact_secrets: bool = True):
        super().__init__()
        self.redact_secrets = redact_secrets

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                if self.redact_secrets and isinstance(value, str):
                    payload[key] = _redact_secrets(value)
                else:
                    payload[key] = value
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if self.redact_secrets:
                exc_text = _redact_secrets(exc_text)
            payload["exception"] = exc_text
        return json.dumps(payload, default=str)


def configure_logging(level: str, redact_secrets: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(redact_secrets=redact_secrets))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").handlers.clear()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
