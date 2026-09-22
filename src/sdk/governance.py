"""Durable approval workflow for permission-gated tool calls.

Permission decisions are ``allow``, ``ask``, or ``deny``. This service owns
pending proposals, approval transitions, execution records, and receipts;
policy evaluation lives in :class:`PermissionPolicy`.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.app_logging import get_logger
from src.sdk.audit import AuditEvent
from src.sdk.governance_operations import GovernanceOperationStore, GovernedOperation
from src.sdk.permission_policy import PermissionPolicy
from src.sdk.run_events import ToolResultData, ToolResultEvent
from src.sdk.session_events import (
    get_session_event_store,
    session_log_enabled,
)
from src.sdk.tool_results import KILLED_MARKER, TIMEOUT_MARKER, CommandKilledError
from src.sdk.tools import ToolResult
from src.storage.paths import DataPaths

#: Outcomes a consumed proposal can carry. `status` stays 'executed' — it means
#: "approved and consumed, terminal" and replay_resume depends on it — while
#: this records what the call actually did (issue #32).
#:
#: `outcome is None` means "not applicable": rows written before this column
#: existed, rows that were never executed (pending/approved/cancelled), and
#: proposals consumed by the async leg, whose terminal state lives in the
#: operation ledger. These five values are the frozen persisted vocabulary —
#: renaming one strands historical rows on the old literal.
OUTCOME_SUCCEEDED = "succeeded"
OUTCOME_REFUSED = "refused"
OUTCOME_FAILED = "failed"
# The marker outcomes reuse the codes the receipt already carries, so the
# column and the receipt cannot drift.
OUTCOME_TIMED_OUT = TIMEOUT_MARKER
OUTCOME_KILLED = KILLED_MARKER
# Mirrors the three refusal codes set by the branches below.
_REFUSAL_ERRORS = frozenset({"tool disabled", "permission changed", "unknown tool"})


def outcome_for(result: dict[str, Any]) -> str:
    """One word for what an executed proposal actually did.

    Kept deliberately small so a consumer can switch on it: a clean run, a
    refusal (the tool never ran), a timeout, a signal kill, or a failure.
    """
    if not result.get("is_error"):
        return OUTCOME_SUCCEEDED
    error = str((result.get("structured_content") or {}).get("error") or "")
    if error in _REFUSAL_ERRORS:
        return OUTCOME_REFUSED
    if error in (OUTCOME_TIMED_OUT, OUTCOME_KILLED):
        return error
    return OUTCOME_FAILED

_services: dict[str, GovernanceService] = {}
_lock = threading.Lock()


def governance_enabled() -> bool:
    from src.config.settings import get_settings

    gov = getattr(get_settings(), "governance", None)
    return bool(getattr(gov, "enabled", False))


logger = get_logger()


class GovernanceService:
    """Permission resolution + durable pending proposals + receipts."""

    def __init__(self, data_root: str | None = None) -> None:
        self._paths = DataPaths() if data_root is None else DataPaths(data_root=data_root)
        self._lock = threading.Lock()
        self.operations = GovernanceOperationStore(self._conn, self._lock)
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
                permission TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT,
                session_id TEXT,
                user_id TEXT,
                executor_json TEXT,
                outcome TEXT
            )
            """
        )
        # Migration-safe: older databases lack the newer columns. Historical
        # proposals are conservatively treated as requiring approval.
        for column in ("permission", "session_id", "user_id", "executor_json", "outcome"):
            try:
                conn.execute(f"ALTER TABLE proposals ADD COLUMN {column} TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        conn.execute("UPDATE proposals SET permission = 'ask' WHERE permission IS NULL")
        conn.execute("UPDATE proposals SET user_id = ? WHERE user_id IS NULL", (user_id,))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
        if "tier" in columns:
            conn.execute("ALTER TABLE proposals RENAME TO proposals_legacy")
            conn.execute(
                """
                CREATE TABLE proposals (
                    proposal_id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT,
                    session_id TEXT,
                    user_id TEXT,
                    executor_json TEXT,
                    outcome TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO proposals
                    (proposal_id, ts, tool, arguments, permission, status,
                     expires_at, session_id, user_id, executor_json, outcome)
                SELECT proposal_id, ts, tool, arguments, 'ask', status,
                       expires_at, session_id, user_id, executor_json, outcome
                FROM proposals_legacy
                """
            )
            conn.execute("DROP TABLE proposals_legacy")
        conn.commit()
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

    # -- permission resolution ----------------------------------------------

    def resolve_permission_for_call(
        self, user_id: str, tool_name: str, tool_input: dict[str, Any]
    ) -> str:
        """Resolve the effective allow/ask/deny decision for one call."""
        from src.config.settings import get_settings

        user_permissions: dict[str, Any] = {}
        try:
            from src.sdk.capabilities import load_capabilities, user_capabilities_root

            caps = load_capabilities(user_capabilities_root(user_id))
            user_permissions = caps.get("permissions") or {}
        except FileNotFoundError:
            user_permissions = {}
        except Exception as exc:
            logger.warning(
                "governance.permission_load_failed",
                {"error": str(exc), "tool": tool_name},
                user_id=user_id,
            )
            return "ask"

        governance = getattr(get_settings(), "governance", None)
        admin_permissions = getattr(governance, "permissions", None) or {}
        fallback = self._default_permission(tool_name)
        return PermissionPolicy(admin=admin_permissions, user=user_permissions).resolve(
            tool_name, tool_input, fallback=fallback
        )

    def resolve_permission(self, user_id: str, tool_name: str) -> str:
        """Resolve a tool permission without item-specific arguments."""
        return self.resolve_permission_for_call(user_id, tool_name, {})

    @staticmethod
    def _default_permission(tool_name: str) -> str:
        try:
            from src.sdk.native_tools import get_native_tools

            definition = next((x for x in get_native_tools() if x.name == tool_name), None)
        except Exception:
            definition = None
        if definition is not None and getattr(definition.annotations, "requires_approval", False):
            return "ask"
        return "allow"

    # -- durable pendings ---------------------------------------------------

    def create_pending(
        self,
        user_id: str,
        tool: str,
        arguments: dict[str, Any],
        permission: str = "ask",
        session_id: str | None = None,
        executor: Any | None = None,
    ) -> str:
        proposal_id = uuid.uuid4().hex
        expiry = None
        # The HITL boundary snapshots an async executor from the active loop
        # registry. Do not resolve tool paths here: ordinary synchronous
        # governance proposals must not trigger custom-tool discovery.
        executor_json = json.dumps(executor.model_dump(mode="json"), sort_keys=True) if executor else None
        with self._conn(user_id) as conn:
            conn.execute(
                "INSERT INTO proposals (proposal_id, ts, tool, arguments, permission, status, expires_at, session_id, user_id, executor_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal_id,
                    datetime.now(UTC).isoformat(),
                    tool,
                    json.dumps(arguments or {}, sort_keys=True),
                    permission,
                    "pending",
                    expiry,
                    session_id,
                    user_id,
                    executor_json,
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
        """M4-2 anti-fatigue: count a permission override for a tool."""
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
                "SELECT proposal_id, tool, arguments, permission, status, expires_at,"
                " session_id, executor_json, outcome FROM proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "proposal_id": row[0],
            "tool": row[1],
            "arguments": json.loads(row[2]),
            "permission": row[3],
            "status": row[4],
            "expires_at": row[5],
            "session_id": row[6],
            "executor": json.loads(row[7]) if row[7] else None,
            "outcome": row[8],
        }

    def approve(
        self, user_id: str, proposal_id: str, count_override: bool = True
    ) -> bool:
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
                conn.execute(
                    "INSERT INTO tool_stats (tool, approvals) VALUES (?, 1)"
                    " ON CONFLICT(tool) DO UPDATE SET approvals = approvals + 1",
                    (tool,),
                )
                conn.commit()
        return newly

    def _active_tool_definition(self, user_id: str, tool_name: str) -> Any | None:
        """Resolve through the runner's deployment and user capability ceiling."""
        from src.sdk.runner import get_active_tool_definition

        return get_active_tool_definition(user_id, tool_name)

    def execution_mode_for_tool(self, user_id: str, tool_name: str) -> str:
        definition = self._active_tool_definition(user_id, tool_name)
        return str(getattr(getattr(definition, "annotations", None), "execution_mode", "sync"))

    def external_executor_for_tool(self, user_id: str, tool_name: str) -> Any | None:
        """Return the active, deployment/capability-authorized executor only."""
        definition = self._active_tool_definition(user_id, tool_name)
        return getattr(getattr(definition, "annotations", None), "executor", None)

    def validate_async_approval(self, user_id: str, row: dict[str, Any]) -> Any:
        """Fail closed if current policy/tool metadata no longer matches proposal."""
        definition = self._active_tool_definition(user_id, row["tool"])
        if definition is None or self.execution_mode_for_tool(user_id, row["tool"]) != "async":
            raise ValueError("async tool is no longer enabled")
        if self.resolve_permission(user_id, row["tool"]) != row["permission"] or row["permission"] == "deny":
            raise ValueError("permission changed")
        executor = self.external_executor_for_tool(user_id, row["tool"])
        snapshot = row.get("executor")
        if executor is None or snapshot != executor.model_dump(mode="json"):
            raise ValueError("external executor metadata changed")
        return executor

    def _record_async_approval(self, user_id: str, proposal_id: str, tool: str) -> None:
        """Record the approval receipt/stat only after the atomic transition."""
        self._emit_receipt(user_id, f"approved:{proposal_id}", tool="", correlation=proposal_id)
        with self._conn(user_id) as conn:
            conn.execute(
                "INSERT INTO tool_stats (tool, approvals) VALUES (?, 1)"
                " ON CONFLICT(tool) DO UPDATE SET approvals = approvals + 1",
                (tool,),
            )
            conn.commit()

    def approve_async_operation(
        self, user_id: str, proposal_id: str
    ) -> tuple[GovernedOperation, bool]:
        """Atomically accept an async proposal without invoking its tool body."""
        operation, approved_now = self.operations.approve_pending_and_create_operation(user_id, proposal_id)
        if approved_now:
            self._record_async_approval(user_id, proposal_id, operation.tool_name)
        return operation, approved_now

    def approve_external_operation(
        self, user_id: str, proposal_id: str, executor: Any
    ) -> tuple[Any, bool]:
        """Accept one external operation and create its durable dispatch outbox."""
        created, approved_now = self.operations.approve_pending_and_create_external_operation(
            user_id, proposal_id, executor
        )
        if approved_now:
            self._record_async_approval(user_id, proposal_id, created.operation.tool_name)
        return created, approved_now

    def list_operations(
        self, user_id: str, status: str | None = None
    ) -> list[GovernedOperation]:
        return self.operations.list_operations(user_id, status)

    def request_operation_cancel(
        self, user_id: str, operation_id: str
    ) -> GovernedOperation | None:
        operation = self.operations.get_operation(user_id, operation_id)
        if operation is None:
            return None
        self.operations.request_cancel(user_id, operation_id)
        return self.operations.get_operation(user_id, operation_id)

    def resolve_pending(self, user_id: str, proposal_id: str) -> dict[str, Any]:
        """Read one durable pending without changing its status."""
        row: dict[str, Any] | None = self.get_pending(user_id, proposal_id)
        return row if row is not None else {"status": "missing"}

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
            # Carry the persisted outcome too: a client that only calls approve
            # should see the same headline as GET /pendings.
            return {"status": "executed", "already": True, "outcome": row.get("outcome")}
        if row["status"] != "approved":
            return {"status": row["status"]}

        tool = row["tool"]
        arguments = row["arguments"] or {}
        try:
            # The execution leg re-checks (a) tool enablement for this user
            # and (b) the CURRENT permission — a pending created before a
            # tool was disabled or denied must not execute.
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
                result["outcome"] = outcome_for(result)
                with self._lock, self._conn(user_id) as conn:
                    conn.execute(
                        "UPDATE proposals SET status='executed', outcome=?"
                        " WHERE proposal_id=? AND status='approved'",
                        (result["outcome"], proposal_id),
                    )
                    conn.commit()
                self._emit_receipt(
                    user_id, f"executed:{proposal_id}", tool=tool, correlation=proposal_id
                )
                return result
            permission_now = self.resolve_permission(user_id, tool)
            if permission_now == "deny":
                result = {
                    "content": (
                        f"Tool '{tool}' is now denied — approval "
                        "refused (permission re-checked at execution time)."
                    ),
                    "structured_content": {
                        "executed": False, "error": "permission changed",
                    },
                    "is_error": True,
                }
                result["outcome"] = outcome_for(result)
                with self._lock, self._conn(user_id) as conn:
                    conn.execute(
                        "UPDATE proposals SET status='executed', outcome=?"
                        " WHERE proposal_id=? AND status='approved'",
                        (result["outcome"], proposal_id),
                    )
                    conn.commit()
                self._emit_receipt(
                    user_id, f"executed:{proposal_id}", tool=tool, correlation=proposal_id
                )
                return result
            if registry is None:
                from src.sdk.native_tools import get_native_tools

                registry = get_native_tools()
                # Issue #13: the loop's function list includes the user's
                # custom TOOL.md tools — the execution leg must resolve the
                # same set, not the core-only native registry.
                try:
                    from src.sdk.tools_custom import get_custom_tools

                    custom = get_custom_tools(user_id)
                    have = {x.name for x in registry}
                    registry = list(registry) + [
                        x for x in custom if x.name not in have
                    ]
                except Exception:
                    pass  # custom scan failure -> core-only resolution
            td = next((x for x in registry if x.name == tool), None)
            if td is None:
                result = {
                    "content": f"Tool not found for execution: {tool}",
                    "structured_content": {"executed": False, "error": "unknown tool"},
                    "is_error": True,
                }
            else:
                out = await td.ainvoke(arguments)
                if isinstance(out, ToolResult):
                    # Issue #26: a ToolResult carries its own outcome. Hard-coding
                    # is_error False here receipted a tool that ran and failed as
                    # completed, and json.dumps(default=str) replaced its content
                    # with a pydantic repr. Governance keys win over the tool's
                    # own structured_content so the receipt stays authoritative.
                    result = {
                        "content": out.content,
                        "structured_content": {
                            **(out.structured_content or {}),
                            "executed": True,
                            "tool": tool,
                        },
                        "is_error": out.is_error,
                    }
                else:
                    result = {
                        "content": out if isinstance(out, str) else json.dumps(out, default=str),
                        "structured_content": {"executed": True, "tool": tool},
                        "is_error": False,
                    }
        except Exception as exc:  # receipt the failure, never raise
            if isinstance(exc, subprocess.TimeoutExpired):
                # Issue #23: a cap-killed command must never be recorded as
                # executed — the elapsed detail travels in the exception.
                from src.sdk.tool_results import TIMEOUT_MARKER

                detail = exc.output if isinstance(exc.output, str) else ""
                elapsed = detail.removeprefix(f"{TIMEOUT_MARKER}: ").strip() or str(exc)
                result = {
                    "content": f"Governed execution timed out: {elapsed}",
                    "structured_content": {
                        "executed": False,
                        "error": TIMEOUT_MARKER,
                        "elapsed": elapsed,
                    },
                    "is_error": True,
                }
            elif isinstance(exc, CommandKilledError):
                # Issue #25: killed by a signal (RLIMIT_AS/CPU/NPROC/FSIZE or
                # another signal). The run did not complete, so it must not be
                # receipted as executed.
                from src.sdk.tool_results import KILLED_MARKER

                result = {
                    "content": f"Governed execution was killed: {exc.detail}",
                    "structured_content": {
                        "executed": False,
                        "error": KILLED_MARKER,
                        "signal": exc.signal_number,
                        "elapsed": exc.detail,
                    },
                    "is_error": True,
                }
            else:
                result = {
                    "content": f"Governed execution failed: {exc}",
                    "structured_content": {"executed": False, "error": str(exc)},
                    "is_error": True,
                }

        result["outcome"] = outcome_for(result)
        with self._lock, self._conn(user_id) as conn:
            conn.execute(
                "UPDATE proposals SET status='executed', outcome=?"
                " WHERE proposal_id=? AND status='approved'",
                (result["outcome"], proposal_id),
            )
            conn.commit()
        self._emit_receipt(
            user_id, f"executed:{proposal_id}", tool=tool, correlation=proposal_id
        )
        # P1-1 (review on 3314c7e): the REAL execution result must reach the
        # session log — deriveMessages otherwise keeps feeding the model the
        # synthetic pending-ack as the tool result forever (re-proposal loop).
        self._log_execution_result(user_id, proposal_id, tool, result)
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
            # Shape parity with the executed branch (review P2).
            return {"status": "replayed", "session_id": session_id,
                    "derived_history_len": len(derived), "execution": exec_row}
        return {
            "status": "replayed",
            "session_id": session_id,
            "derived_history_len": len(derived),
            "execution": exec_row,
        }

    def _log_execution_result(
        self, user_id: str, proposal_id: str, tool: str, result: dict[str, Any]
    ) -> None:
        """Append the real executed-tool result to the session log (review
        P1-1): the log's prior entry for this call is the synthetic pending
        ack from the guard; without this the next deriveMessages keeps the
        model in an await-approval loop. No-op when the log is disabled or
        the proposal has no session linkage. Best-effort: never raises."""
        try:
            if not session_log_enabled():
                return
            row = self.get_pending(user_id, proposal_id)
            session_id = (row or {}).get("session_id")
            if not session_id:
                return
            store = get_session_event_store(user_id)
            events = store.events(session_id)
            ack_sig = f"Proposal {proposal_id[:8]}"
            call_id = None
            for ev in reversed(events):
                if ev.type == "tool_result" and getattr(
                    ev.data, "name", None
                ) == tool and ack_sig in str(getattr(ev.data, "content", "")):
                    call_id = ev.data.tool_call_id
                    break
            if call_id is None:
                call_id = f"governed:{proposal_id[:8]}"
            now = datetime.now(UTC)
            event = ToolResultEvent(
                event_id=uuid.uuid4().hex,
                sequence=store.next_sequence(session_id),
                timestamp=now,
                session_id=session_id,
                run_id=f"governed:{proposal_id[:8]}",
                attempt=1,
                data=ToolResultData(
                    block_id=f"blk-{proposal_id[:12]}",
                    tool_call_id=call_id,
                    name=tool,
                    status="completed"
                    if not result.get("is_error")
                    else "failed",
                    content=result.get("content", ""),
                ),
            )
            store.append(event)
        except Exception:  # pragma: no cover - logging never breaks execution
            pass

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


def iter_governance_user_ids() -> list[str]:
    """Return users with an in-memory or durable governance store.

    Lifespan recovery must find queued work after a process restart, not only
    users that happened to issue a request in this process.
    """
    with _lock:
        users = set(_services)
    root = DataPaths().root / "private" / "governance"
    if root.is_dir():
        users.update(path.name for path in root.iterdir() if path.is_dir())
    return sorted(users)


def get_governance_service(user_id: str = "default_user") -> GovernanceService:
    """Process-wide per-user governance service with live permission
    resolution; whether pendings are CREATED is gated by governance.enabled,
    checked by the middleware, not here."""
    with _lock:
        svc = _services.get(user_id)
        if svc is None:
            svc = GovernanceService()
            _services[user_id] = svc
        return svc
