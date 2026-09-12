"""Logging module for Assistant - Best practices implementation."""

import json
import logging as stdlib_logging
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from src.config import get_settings  # noqa: E402


class LogLevel(IntEnum):
    """Log levels in order of severity."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class Logger:
    """Logger for Assistant - logs to JSONL and Langfuse."""

    # Fields to redact (sensitive data)
    REDACTED_FIELDS = {"api_key", "password", "secret", "token", "key"}

    def __init__(self) -> None:
        settings = get_settings()
        config = settings.observability.logging

        self.enabled = config.enabled
        self.level = LogLevel[config.level.upper()]
        self.json_dir = Path(config.json_dir)

        # Stats - must be initialized first
        self._log_count = 0

        if self.enabled:
            self.json_dir.mkdir(parents=True, exist_ok=True)

        # OB-0: the Logger never constructed anything with its old Langfuse
        # client (logging is JSONL-only), yet its early import won OTel's
        # write-once provider slot away from the shared lifecycle. Langfuse
        # initialization now lives solely in src.sdk.observability.

    def _redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive fields from data."""
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if any(field in key.lower() for field in self.REDACTED_FIELDS):
                redacted[key] = "***REDACTED***"
            elif isinstance(value, dict):
                redacted[key] = self._redact(value)
            else:
                redacted[key] = value
        return redacted

    def _should_log(self, level: LogLevel) -> bool:
        """Check if we should log this level."""
        return level >= self.level

    def _log(
        self, level: int, event: str, data: dict[str, Any], user_id: str = "default_user", channel: str = "cli"
    ) -> None:
        """Internal log method - handles filtering and formatting."""
        if not self.enabled:
            return

        log_level = LogLevel(level)
        if not self._should_log(log_level):
            return

        # Add standard fields - match original format
        log_entry = {
            "timestamp": datetime.now().astimezone().isoformat().replace("+00:00", "Z"),
            "user_id": user_id,
            "event": event,
            "level": log_level.name.lower(),
            "channel": channel,
            "data": self._redact(data),
        }

        # Write to JSONL
        try:
            with open(self._get_log_file(), "a") as f:
                f.write(json.dumps(log_entry) + "\n")
            self._log_count += 1
        except Exception as e:
            stdlib_logging.error(f"Failed to write log: {e}")

    def _get_log_file(self) -> Path:
        """Get today's log file path."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.json_dir / f"{today}.jsonl"

    def debug(self, event: str, data: dict[str, Any], user_id: str = "default_user", channel: str = "cli") -> None:
        """Log debug level event."""
        self._log(LogLevel.DEBUG, event, data, user_id, channel)

    def info(self, event: str, data: dict[str, Any], user_id: str = "default_user", channel: str = "cli") -> None:
        """Log info level event."""
        self._log(LogLevel.INFO, event, data, user_id, channel)

    def warning(self, event: str, data: dict[str, Any], user_id: str = "default_user", channel: str = "cli") -> None:
        """Log warning level event."""
        self._log(LogLevel.WARNING, event, data, user_id, channel)

    def error(self, event: str, data: dict[str, Any], user_id: str = "default_user", channel: str = "cli") -> None:
        """Log error level event."""
        self._log(LogLevel.ERROR, event, data, user_id, channel)

    @contextmanager
    def timer(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        user_id: str = "default_user",
        channel: str = "cli",
        level: int = LogLevel.INFO,
    ) -> Generator[None, None, None]:
        """Context manager for timing operations."""
        if not self.enabled:
            yield
            return

        data = data or {}
        start_time = time.time()
        run_id = str(uuid.uuid4())

        data["run_id"] = run_id
        data["started_at"] = datetime.now().isoformat()

        self._log(level, f"{event}.start", data, user_id, channel)

        try:
            yield
        except Exception as e:
            error_data = {
                **data,
                "error": str(e),
                "error_type": type(e).__name__,
            }
            self._log(LogLevel.ERROR, f"{event}.error", error_data, user_id, channel)
            raise
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            data["duration_ms"] = duration_ms
            data["completed_at"] = datetime.now().isoformat()
            self._log(level, f"{event}.end", data, user_id, channel)


# Global logger instance
_logger: Logger | None = None


def get_logger() -> Logger:
    """Get or create logger instance."""
    global _logger
    if _logger is None:
        _logger = Logger()
    return _logger


def log_event(event: str, data: dict[str, Any], user_id: str = "default_user", channel: str = "cli") -> None:
    """Log an event."""
    get_logger().info(event, data, user_id, channel)


def timer(
    event: str,
    data: dict[str, Any] | None = None,
    user_id: str = "default_user",
    channel: str = "cli",
    level: int = LogLevel.INFO,
) -> Any:
    """Timer context manager for logging duration."""
    return get_logger().timer(event, data, user_id, channel, level)
