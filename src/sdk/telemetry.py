"""Telemetry flush (Phase 2 D1-1) — thin opportunistic shim.

flush(user_id) recomputes the user's analytics aggregates from the metering
store + review-queue drafts. Callers (loop end, scheduler tick) invoke it
opportunistically — no background thread. Opt-out: TELEMETRY_ENABLED is OFF
by default (self-hosters opt in); flush() is a no-op when disabled.
"""

from __future__ import annotations

from typing import Any

from src.app_logging import get_logger

logger = get_logger()


def telemetry_enabled() -> bool:
    """Owner telemetry gate (settings.telemetry.enabled, default False)."""
    from src.config import get_settings

    telemetry = getattr(get_settings(), "telemetry", None)
    return bool(telemetry is not None and telemetry.enabled)


def flush(user_id: str, turn_seconds: float | None = None) -> dict[str, Any] | None:
    """Flush the user's analytics aggregates. None when telemetry is
    disabled (the shipped default) — callers skip without side effects."""
    if not telemetry_enabled():
        return None
    from src.storage.analytics import flush_user

    return flush_user(user_id, turn_seconds=turn_seconds)
