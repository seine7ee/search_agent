"""Structured console and rotating-file logging for deep-search runs."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_STANDARD_LOG_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


class JsonLogFormatter(logging.Formatter):
    """Write one machine-readable JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    log_file: str | Path,
    *,
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    logger_name: str = "deep_search_claude",
) -> logging.Logger:
    """Configure the ``deep_search_claude`` logger and return it.

    Console output is concise. The rotating file uses JSON Lines so it can be
    filtered by run_id, turn_id, search_goal_id, agent, stage, or event.
    """
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"invalid log level: {level}")

    path = Path(log_file).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(logger_name)
    logger.setLevel(numeric_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(numeric_level)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    rotating_file = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    rotating_file.setLevel(numeric_level)
    rotating_file.setFormatter(JsonLogFormatter())

    logger.addHandler(console)
    logger.addHandler(rotating_file)
    logger.info(
        "logging_configured",
        extra={
            "event": "logging_configured",
            "log_file": str(path),
            "log_level": level.upper(),
            "max_bytes": max_bytes,
            "backup_count": backup_count,
            "logger_name": logger_name,
        },
    )
    return logger
