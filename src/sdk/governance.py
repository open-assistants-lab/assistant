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

from src.app_logging import get_logger
from src.sdk.audit import AuditEvent
from src.storage.paths import DataPaths

Tier = str  # "autonomous" | "show_then_auto_send" | "explicit" | "hard_block"

_services: dict[str, GovernanceService] = {}
_lock = threading.Lock()


def _now_minus(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def governance_enabled() -> bool:
    from src.config.settings import get_settings

    gov = getattr(get_settings(), "governance", None)
    return bool(getattr(gov, "enabled", False))


logger = get_logger()


class GovernanceService:
    """Tier resolution + durable pending proposals + receipts."""

    def __init__(self, data_root: str | None = None) -> None:
        self._paths = DataPaths() if data_root is None else DataPaths(data_root=data_root)
        self._lock = threading.Lock()
        self._recent: list[AuditEvent] = []  # receipt ring buffer (process-local)

    def _db_path(self, user_id: str) -> Path:
        from src.storage.paths import _validate_path_id

        _validate_path_id(user_id, "user_id")
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
                expires_at TEXT,
                session_id TEXT
            )
            """
        )
        # Migration-safe: pre-session-log DBs lack the column.
        try:
            conn.execute("ALTER TABLE proposals ADD COLUMN session_id TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_stats (
                tool TEXT PRIMARY KEY,
                proposals INTEGER NOT NULL DEFAULT 0,
                overrides INTEGER NOT NULL DEFAULT 0,
                approvals INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()
        return conn

    # -- tier resolution ---------------------------------------------------

    def resolve_tier(self, user_id: str, tool_name: str) -> Tier:
        # Capabilities profile first (plan M4-1: tier source is the user's
        # capabilities.yaml governance_tiers section), then deployment
        # settings, then annotation default.
        try:
            from src.sdk.capabilities import (
                load_capabilities,
                user_capabilities_root,
            )

            caps = load_capabilities(user_capabilities_root(user_id))
            cap_tiers = caps.get("governance_tiers") or {}
            if tool_name in cap_tiers:
                return str(cap_tiers[tool_name])
        except FileNotFoundError:
            pass  # no capabilities file — normal fallback chain
        except Exception as exc:
            # Bug-hunt P2: a corrupt capabilities.yaml must not silently
            # downgrade tiers to autonomous (fail closed -> conservative
            # explicit pending until the admin fixes the file).
            logger.warning(
                "governance.capabilities_load_failed",
                {"error": str(exc), "tool": tool_name},
                user_id=user_id,
            )
            return "explicit"
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
        session_id: str | None = None,
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
                "INSERT INTO proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal_id,
                    datetime.now(UTC).isoformat(),
                    tool,
                    json.dumps(arguments or {}, sort_keys=True),
                    tier,
                    "pending",
                    expiry,
                    session_id,
                ),
            )
            # M4-2 anti-fatigue: proposals_created per tool.
            conn.execute(
                "INSERT INTO tool_stats (tool, proposals) VALUES (?, 1)"
                " ON CONFLICT(tool) DO UPDATE SET proposals = proposals + 1",
                (tool,),
            )
            conn.commit()
        self._emit_receipt(
            user_id, f"proposal:{tool}:{proposal_id[:8]}", tool,
            correlation=proposal_id,
        )
        return proposal_id

    def record_override(self, user_id: str, tool: str) -> None:
        """M4-2 anti-fatigue: count a tier override for a tool (user acted
        against the configured tier — approving after flagging, or editing
        args before approve)."""
        with self._conn(user_id) as conn:
            conn.execute(
                "INSERT INTO tool_stats (tool, overrides) VALUES (?, 1)"
                " ON CONFLICT(tool) DO UPDATE SET overrides = overrides + 1",
                (tool,),
            )
            conn.commit()

    def tool_stats(self, user_id: str) -> list[dict[str, Any]]:
        """M4-2: per-tool proposal/override/approval counts + override_rate
        (overrides / proposals, 0.0 when no proposals). Read-time computed —
        feeds the owner dashboard fatigue tuning (M4-2/D1-1)."""
        with self._conn(user_id) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT tool, proposals, overrides, approvals FROM tool_stats ORDER BY tool"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            proposals = int(d.get("proposals") or 0)
            overrides = int(d.get("overrides") or 0)
            d["override_rate"] = round(overrides / proposals, 2) if proposals else 0.0
            out.append(d)
        return out

    def list_pending_ids(self, user_id: str) -> list[str]:
        """All proposal ids for the user (any status)."""
        with self._conn(user_id) as conn:
            rows = conn.execute("SELECT proposal_id FROM proposals").fetchall()
        return [r[0] for r in rows]

    def get_pending(self, user_id: str, proposal_id: str) -> dict[str, Any] | None:
        with self._conn(user_id) as conn:
            row = conn.execute(
                "SELECT proposal_id, tool, arguments, tier, status, expires_at,"
                " session_id FROM proposals WHERE proposal_id = ?",
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
            "session_id": row[6],
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
            self._emit_receipt(
            user_id, f"approved:{proposal_id}", tool="", correlation=proposal_id
        )
        if newly:
            row = self.get_pending(user_id, proposal_id) or {}
            tool = str(row.get("tool") or "")
            with self._conn(user_id) as conn:
                # M4-2: approvals per tool; approving a show_then_auto_send
                # early IS the override of the auto-send window.
                override = row.get("tier") == "show_then_auto_send"
                conn.execute(
                    "INSERT INTO tool_stats (tool, approvals, overrides) VALUES (?, 1, ?)"
                    " ON CONFLICT(tool) DO UPDATE SET approvals = approvals + 1,"
                    " overrides = overrides + ?",
                    (tool, 1 if override else 0, 1 if override else 0),
                )
                conn.commit()
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

    async def execute_approved(
        self,
        user_id: str,
        proposal_id: str,
        registry: Any | None = None,
    ) -> dict[str, Any]:
        """Deterministic execution leg (M4-1 review P0): run the approved
        tool call EXACTLY once via the tool registry, then mark executed.

        Idempotent: only an approved proposal executes; approved->executed
        transition emits the execution receipt with the proposal id as
        call_id (proposal -> approval -> execution chain on the bus)."""
        row = self.get_pending(user_id, proposal_id)
        if row is None:
            return {"status": "missing"}
        if row["status"] == "executed":
            return {"status": "executed", "already": True}
        if row["status"] != "approved":
            return {"status": row["status"]}

        tool = row["tool"]
        arguments = row["arguments"] or {}
        try:
            # Bug-hunt P1: the execution leg re-checks (a) tool enablement for
            # this user and (b) the CURRENT tier — a pending created before a
            # tool was disabled or moved to hard_block must not execute.
            from src.sdk.capabilities import (
                load_capabilities,
                resource_enabled,
                user_capabilities_root,
            )

            caps = load_capabilities(user_capabilities_root(user_id))
            if not resource_enabled(caps, "tools", tool):
                result = {
                    "content": (
                        f"Tool '{tool}' is disabled for this user — approval "
                        "did not execute it."
                    ),
                    "structured_content": {
                        "executed": False, "error": "tool disabled",
                    },
                    "is_error": True,
                }
                with self._lock, self._conn(user_id) as conn:
                    conn.execute(
                        "UPDATE proposals SET status='executed'"
                        " WHERE proposal_id=? AND status='approved'",
                        (proposal_id,),
                    )
                    conn.commit()
                self._emit_receipt(
                    user_id, f"executed:{proposal_id}", tool=tool, correlation=proposal_id
                )
                return result
            tier_now = self.resolve_tier(user_id, tool)
            if tier_now == "hard_block":
                result = {
                    "content": (
                        f"Tool '{tool}' is now hard_block tier — approval "
                        "refused (tier re-checked at execution time)."
                    ),
                    "structured_content": {
                        "executed": False, "error": "tier changed",
                    },
                    "is_error": True,
                }
                with self._lock, self._conn(user_id) as conn:
                    conn.execute(
                        "UPDATE proposals SET status='executed'"
                        " WHERE proposal_id=? AND status='approved'",
                        (proposal_id,),
                    )
                    conn.commit()
                self._emit_receipt(
                    user_id, f"executed:{proposal_id}", tool=tool, correlation=proposal_id
                )
                return result
            if registry is None:
                from src.sdk.native_tools import get_native_tools

                registry = get_native_tools()
            td = next((x for x in registry if x.name == tool), None)
            if td is None:
                result = {
                    "content": f"Tool not found for execution: {tool}",
                    "structured_content": {"executed": False, "error": "unknown tool"},
                    "is_error": True,
                }
            else:
                out = await td.ainvoke(arguments)
                result = {
                    "content": out if isinstance(out, str) else json.dumps(out, default=str),
                    "structured_content": {"executed": True, "tool": tool},
                    "is_error": False,
                }
        except Exception as exc:  # receipt the failure, never raise
            result = {
                "content": f"Governed execution failed: {exc}",
                "structured_content": {"executed": False, "error": str(exc)},
                "is_error": True,
            }

        with self._lock, self._conn(user_id) as conn:
            conn.execute(
                "UPDATE proposals SET status='executed'"
                " WHERE proposal_id=? AND status='approved'",
                (proposal_id,),
            )
            conn.commit()
        self._emit_receipt(
            user_id, f"executed:{proposal_id}", tool=tool, correlation=proposal_id
        )
        return result

    async def replay_resume(
        self,
        user_id: str,
        proposal_id: str,
        registry: Any | None = None,
        executor: Any | None = None,
    ) -> dict[str, Any]:
        """M4-1 upgrade (session-log payoff, P1-T10..T12): approve-after-
        restart replays the run IN-PLACE when the session log has the run's
        events — the approved tool executes exactly once and the continuation
        seeds from deriveMessages history. Falls back to the deterministic
        execution leg when the session log is unavailable (flag off / no
        session linkage / no events). Exactly-once is preserved either way by
        the approved->executed conditional UPDATE."""
        row = self.get_pending(user_id, proposal_id)
        if row is None:
            return {"status": "missing"}
        session_id = row.get("session_id")
        can_replay = False
        derived: list[Any] = []
        if session_id:
            from src.sdk.session_events import (
                deriveMessages,
                get_session_event_store,
                session_log_enabled,
            )

            if session_log_enabled():
                events = get_session_event_store(user_id).events(session_id)
                if events:
                    can_replay = True
                    derived = deriveMessages(session_id, user_id)
        execute = executor or self.execute_approved
        if not can_replay:
            # Deterministic fallback — expose the executed contract uniformly.
            exec_row = await execute(user_id, proposal_id, registry)
            if "status" not in exec_row:
                exec_row = {"status": "executed", **exec_row}
            return exec_row

        exec_row = await execute(user_id, proposal_id, registry)
        if exec_row.get("already"):
            # Replay-resume must not double-execute across restarts.
            return {"status": "replayed", "execution": exec_row,
                    "derived_history_len": len(derived)}
        return {
            "status": "replayed",
            "session_id": session_id,
            "derived_history_len": len(derived),
            "execution": exec_row,
        }

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

    def _emit_receipt(
        self, user_id: str, detail: str, tool: str = "", correlation: str | None = None
    ) -> None:
        from src.sdk.audit import AuditEvent, default_capture_bus

        ev = AuditEvent(
            kind="approve", user_id=user_id, tool=tool, detail=detail,
            call_id=correlation,
        )
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
