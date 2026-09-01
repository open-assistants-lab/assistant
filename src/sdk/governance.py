"""M4 governance service — durable approval-gated tools (issue #6, plan M4-1).

Tiers (declared by ToolAnnotations.requires_approval, set per tool via
GOVERNANCE_TIERS settings mapping — read live so changes take effect without
redeploy):
- autonomous: pass through
- show_then_auto_send: durable pending with LAZY expiry (auto-approve unless
  cancelled — evaluated at read time, no scheduler dependency)
- explicit: durable pending until a human approves
- hard_block: synthetic refusal result (never an exception)

Pending proposals + receipts are durable per-user SQLite under
data/private/governance/. Proposal -> approval -> execution events flow on the
CaptureBus as AuditEvent kind="approve". In-run replay-resume is DEFERRED to
the session-log work (R-SL1) by design.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.sdk.audit import AuditEvent
from src.storage.paths import DataPaths

Tier = str  # "autonomous" | "show_then_auto_send" | "explicit" | "hard_block"

_services: dict[str, GovernanceService] = {}
_lock = threading.Lock()


def _now_minus(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


class GovernanceService:
    """Tier resolution + durable pending proposals + receipts."""

    def __init__(self, data_root: str | None = None) -> None:
        self._paths = DataPaths() if data_root is None else DataPaths(data_root=data_root)
        self._lock = threading.Lock()
        self._recent: list[AuditEvent] = []  # receipt ring buffer (process-local)

    def _db_path(self, user_id: str) -> Path:
        d = self._paths.root / "private" / "governance" / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d / "governance.db"

    def _conn(self, user_id: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path(user_id))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                tier TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT
            )
            """
        )
        conn.commit()
        return conn

    # -- tier resolution ---------------------------------------------------

    def resolve_tier(self, user_id: str, tool_name: str) -> Tier:
        from src.config.settings import get_settings

        gov = getattr(get_settings(), "governance", None)
        tiers = getattr(gov, "tiers", None) or {}
        if tool_name in tiers:
            return str(tiers[tool_name])
        # Annotation declares: requires_approval defaults to explicit.
        try:
            from src.sdk.native_tools import get_native_tools

            td = next(
                (x for x in get_native_tools() if x.name == tool_name), None
            )
        except Exception:
            td = None
        if td is not None and getattr(td.annotations, "requires_approval", False):
            return "explicit"
        return "autonomous"

    # -- durable pendings ---------------------------------------------------

    def create_pending(
        self,
        user_id: str,
        tool: str,
        arguments: dict[str, Any],
        tier: str = "explicit",
    ) -> str:
        proposal_id = uuid.uuid4().hex
        # _now_minus(-N) = now + N — routed through the module-level clock
        # helper so tests can freeze/shift time (lazy-expiry contract).
        # _now_minus(-N) = now + N — single clock helper so tests shift time
        # by patching this one function (lazy-expiry contract).
        expiry = (
            _now_minus(-self._expiry_seconds()).isoformat()
            if tier == "show_then_auto_send"
            else None
        )
        with self._conn(user_id) as conn:
            conn.execute(
                "INSERT INTO proposals VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal_id,
                    datetime.now(UTC).isoformat(),
                    tool,
                    json.dumps(arguments or {}, sort_keys=True),
                    tier,
                    "pending",
                    expiry,
                ),
            )
            conn.commit()
        self._emit_receipt(user_id, f"proposal:{tool}:{proposal_id[:8]}", tool)
        return proposal_id

    def get_pending(self, user_id: str, proposal_id: str) -> dict[str, Any] | None:
        with self._conn(user_id) as conn:
            row = conn.execute(
                "SELECT proposal_id, tool, arguments, tier, status, expires_at"
                " FROM proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "proposal_id": row[0],
            "tool": row[1],
            "arguments": json.loads(row[2]),
            "tier": row[3],
            "status": row[4],
            "expires_at": row[5],
        }

    def approve(self, user_id: str, proposal_id: str) -> bool:
        """Idempotent approve: True only on the transition pending->approved."""
        with self._lock, self._conn(user_id) as conn:
            cur = conn.execute(
                "UPDATE proposals SET status='approved'"
                " WHERE proposal_id=? AND status='pending'",
                (proposal_id,),
            )
            conn.commit()
            newly = cur.rowcount == 1
        if newly:
            self._emit_receipt(user_id, f"approved:{proposal_id}", tool="")
        return newly

    def resolve_pending(self, user_id: str, proposal_id: str) -> dict[str, Any]:
        """Lazy expiry evaluation for show_then_auto_send (read-time, no
        scheduler): expired => auto-approved unless cancelled."""
        row: dict[str, Any] | None = self.get_pending(user_id, proposal_id)
        if row is None:
            return {"status": "missing"}
        if row["status"] == "pending" and row["tier"] == "show_then_auto_send":
            exp = row.get("expires_at")
            if exp and _now_minus(0) > datetime.fromisoformat(exp):
                self.approve(user_id, proposal_id)
                row = self.get_pending(user_id, proposal_id)
                assert row is not None  # just created it — durable store
        return row

    def cancel(self, user_id: str, proposal_id: str) -> None:
        with self._conn(user_id) as conn:
            conn.execute(
                "UPDATE proposals SET status='cancelled' WHERE proposal_id=? AND status='pending'",
                (proposal_id,),
            )
            conn.commit()

    # -- receipts (CaptureBus audit linkage) ---------------------------------

    def recent_events(self, user_id: str) -> list[AuditEvent]:
        """Recent receipt events from this process (ring buffer; durable
        receipts live in the per-user audit store via the same bus)."""
        return list(self._recent)

    def _expiry_seconds(self) -> int:
        from src.config.settings import get_settings

        gov = getattr(get_settings(), "governance", None)
        return int(getattr(gov, "auto_send_expiry_seconds", 300))

    def _emit_receipt(self, user_id: str, detail: str, tool: str = "") -> None:
        from src.sdk.audit import AuditEvent, default_capture_bus

        ev = AuditEvent(kind="approve", user_id=user_id, tool=tool, detail=detail)
        self._recent.append(ev)
        try:
            default_capture_bus.emit(ev)
        except Exception:
            pass


def get_governance_service(user_id: str = "default_user") -> GovernanceService:
    """Process-wide per-user governance service (tier resolution is always
    available; whether pendings are CREATED is gated by governance.enabled,
    checked by the middleware, not here)."""
    with _lock:
        svc = _services.get(user_id)
        if svc is None:
            svc = GovernanceService()
            _services[user_id] = svc
        return svc
