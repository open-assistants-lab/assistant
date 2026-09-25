"""Subagent coordinator — creates, invokes, supervises subagents via work_queue.

Replaces SubagentManager with work_queue-backed orchestration.
Each invoke() creates a fresh AgentLoop with SubagentContext,
runs it with timeout and cost limits, and stores structured results in work_queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentprofile.models import AgentProfile
from agentprofile.parser import dumps_profile

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.agent_validation import _is_denied_memory_tool, validate_agent_def
from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.messages import Message
from src.sdk.subagent_context import SubagentCancelledError, SubagentContext
from src.sdk.subagent_models import (
    SubagentResult,
    TaskCancelledError,
    TaskStatus,
)
from src.sdk.subagent_work_queue import USER_LEVEL_WORKSPACE_ID, SubagentWorkQueueDB, get_work_queue
from src.sdk.tools import ToolResult
from src.storage import paths as _paths

# Alias: used by callers (e.g. tests) that patch src.sdk.coordinator.get_paths
get_paths = _paths.get_paths

logger = get_logger()

_active: dict[str, SubagentContext] = {}

# Skill loading is included only when no precomputed launch manifest was supplied.
OPTIONAL_SKILL_LOAD_TOOL = "skills_load"
DENIED_SKILL_MANAGEMENT_TOOLS = {"skill_delete", "skill_update"}
CHILD_SCOPED_FILE_TOOLS = frozenset(
    {"files_list", "files_read", "files_glob_search", "files_grep_search"}
)
CHILD_SCOPED_IDENTITY_TOOLS = CHILD_SCOPED_FILE_TOOLS | {"skills_load", "skills_reload"}


def _validate_child_file_path(
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str,
    workspace_id: str,
) -> None:
    """Keep curated child file access relative to its frozen workspace."""
    path_value = arguments.get("path", ".")
    path = Path(path_value) if path_value is not None else Path(".")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Child file tools accept workspace-relative paths only")

    root = get_paths(user_id=user_id, workspace_id=workspace_id).workspace_files_dir().resolve()
    if not (root / path).resolve().is_relative_to(root):
        raise ValueError("Child file path resolves outside its requested workspace")

    if tool_name == "files_glob_search":
        pattern = Path(str(arguments.get("pattern", "**/*")))
        if pattern.is_absolute() or ".." in pattern.parts:
            raise ValueError("Child file tools accept workspace-relative paths only")


def _bind_child_tool_identity(
    tool_def: Any,
    user_id: str,
    workspace_id: str,
) -> Any:
    """Hide model-visible identity inputs and bind the coordinator's scope."""
    parameters = copy.deepcopy(tool_def.parameters)
    properties = parameters.get("properties", {})
    bound_fields = {key for key in ("user_id", "workspace_id") if key in properties}
    for key in bound_fields:
        properties.pop(key, None)
    if "required" in parameters:
        parameters["required"] = [
            key for key in parameters["required"] if key not in bound_fields
        ]
    function = tool_def.function
    if function is None:
        return tool_def.model_copy(update={"parameters": parameters})

    def call(arguments: dict[str, Any]) -> Any:
        for key in ("user_id", "workspace_id"):
            arguments.pop(key, None)
        if tool_def.name in CHILD_SCOPED_FILE_TOOLS:
            _validate_child_file_path(tool_def.name, arguments, user_id, workspace_id)
        if "user_id" in bound_fields:
            arguments["user_id"] = user_id
        if "workspace_id" in bound_fields:
            arguments["workspace_id"] = workspace_id
        return function(**arguments)

    def scoped_function(**arguments: Any) -> Any:
        return call(arguments)

    return tool_def.model_copy(
        update={"parameters": parameters, "function": scoped_function}
    )


def _load_user_caps(user_id: str) -> dict[str, Any]:
    try:
        return load_user_capabilities(user_id)
    except Exception:
        return {"tools": {}, "skills": {}, "subagents": {}}


def _subagent_enabled(user_id: str, name: str) -> bool:
    return resource_enabled(_load_user_caps(user_id), "subagents", name)


def _infer_tool_selection_mode(profile: AgentProfile) -> Any:
    """Preserve omitted, explicit-empty, and named tool selections."""
    from src.sdk.subagent_capabilities import ToolSelectionMode

    if "tools" not in getattr(profile, "model_fields_set", set()):
        return ToolSelectionMode.SAFE_DEFAULT
    return ToolSelectionMode.ALLOWLIST if profile.tools else ToolSelectionMode.NONE


def _coerce_tool_selection_mode(value: Any) -> Any:
    """Reject the legacy sentinel outside the one-time migration reader."""
    from src.sdk.subagent_capabilities import ToolSelectionMode

    mode = ToolSelectionMode(value)
    if mode is ToolSelectionMode.LEGACY:
        raise ValueError("legacy tool selection is migration-only")
    return mode


def _build_tools_for_subagent(
    profile: AgentProfile,
    user_id: str | None = None,
    effective_tool_names: list[str] | tuple[str, ...] | None = None,
    workspace_id: str | None = None,
) -> list[Any]:
    """Build the filtered tool list for a subagent."""
    from src.sdk.native_tools import get_native_tools

    all_native = get_native_tools()
    tool_map = {t.name: t for t in all_native}

    allowed = (
        set(effective_tool_names)
        if effective_tool_names is not None
        else set(profile.tools) if profile.tools else set(tool_map.keys())
    )
    final = set(allowed)
    if effective_tool_names is None:
        final = {
            name
            for name in final
            if not name.startswith("subagent_")
            and not _is_denied_memory_tool(name)
            and name not in DENIED_SKILL_MANAGEMENT_TOOLS
        }
    if effective_tool_names is None and profile.skills:
        final.add(OPTIONAL_SKILL_LOAD_TOOL)

    if user_id and effective_tool_names is None:
        caps = _load_user_caps(user_id)
        disabled_tools = {
            name for name in caps.get("tools", {}) if not resource_enabled(caps, "tools", name)
        }
        final.difference_update(disabled_tools)

    resolved_tools = [tool_map[name] for name in sorted(final) if name in tool_map]
    if user_id and workspace_id:
        resolved_tools = [
            _bind_child_tool_identity(tool_def, user_id, workspace_id)
            if tool_def.name in CHILD_SCOPED_IDENTITY_TOOLS
            or {"user_id", "workspace_id"}.intersection(
                tool_def.parameters.get("properties", {})
            )
            else tool_def
            for tool_def in resolved_tools
        ]
    return resolved_tools


def _build_system_prompt(
    profile: AgentProfile,
    user_id: str,
    workspace_id: str = "personal",
    effective_skill_names: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Build the system prompt for a subagent, including loaded skill content."""
    parts: list[str] = []

    if profile.system_prompt:
        parts.append(profile.system_prompt)
    else:
        parts.append(f"You are {profile.name}, a specialized subagent.")
        if profile.description:
            parts.append(profile.description)

    if profile.skills:
        from src.skills.registry import get_skill_registry

        sr = get_skill_registry(user_id=user_id)
        caps = _load_user_caps(user_id)
        skill_entries = []
        skill_names = effective_skill_names if effective_skill_names is not None else profile.skills
        for skill_name in skill_names:
            if effective_skill_names is None and not resource_enabled(caps, "skills", skill_name):
                continue
            skill = sr.get_skill(skill_name)
            if skill is None:
                raise ValueError(f"Preflighted skill '{skill_name}' is no longer in the registry")
            desc = skill.get("description", "")
            skill_entries.append(f"- **{skill_name}**: {desc}")
        parts.insert(
            0,
            "## Available Skills\n"
            "Use skills_load(name=...) before following a skill's instructions.\n"
            + "\n".join(skill_entries),
        )

    return "\n\n".join(parts)


def _schema_provider_options(
    _provider: Any,
    _model_str: str,
    existing: dict[str, Any] | None,
    _schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Keep provider options explicit; schema enforcement is provider-agnostic.

    Each provider has a distinct structured-output API, so forwarding an
    OpenAI-shaped ``response_format`` to every backend breaks non-OpenAI
    agents. The coordinator validates the returned JSON locally instead.
    """
    return dict(existing) if existing else None


def _parse_and_validate_output(output: str, schema: dict[str, Any]) -> Any:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"schema validation failed: output is not valid JSON: {exc}") from exc
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError

        Draft202012Validator(schema).validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"schema validation failed: {exc.message}") from exc
    return parsed


def _extract_final_output(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if hasattr(msg, "role") and msg.role == "assistant" and msg.content:
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _extract_output(messages: list[Any], max_chars: int = 2000) -> tuple[str, bool]:
    output = ""
    for msg in reversed(messages):
        if hasattr(msg, "role") and msg.role == "assistant" and msg.content:
            content = msg.content
            if isinstance(content, str) and content.strip():
                if len(output) + len(content) > max_chars:
                    output = content[:max_chars - len(output)] + "..."
                    return output, True
                output = content + "\n" + output
    return output.strip(), False


class SubagentCoordinator:
    """Creates, invokes, and supervises subagents via work_queue."""

    def __init__(self, user_id: str, workspace_id: str = "personal"):
        self.user_id = user_id
        self.requested_workspace_id = workspace_id
        self.workspace_id = USER_LEVEL_WORKSPACE_ID
        self.settings = get_settings()
        self.base_path = get_paths(user_id=self.user_id).user_subagents_dir()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._db: SubagentWorkQueueDB | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._recovery_task: asyncio.Task[Any] | None = None

    async def _recover_stale_jobs(self, max_age_seconds: int = 300) -> int:
        """Mark stale RUNNING/CANCELLING tasks as FAILED. Call on first DB access."""
        try:
            db = await self._get_db()
            count = await db.mark_stale_running_failed(max_age_seconds)
        except Exception:
            return 0
        if count > 0:
            logger.warning(
                "subagent.stale_tasks_recovered",
                {"count": count, "user_id": self.user_id, "workspace_id": self.workspace_id},
                user_id="system",
            )
        await self.drain_completion_events()
        return count

    async def drain_completion_events(self, session_id: str | None = None) -> int:
        """Replay undelivered terminal events under the shared per-user drain lock."""
        db = await self._get_db()
        async with db.completion_drain_lock:
            return await self._drain_completion_events(db, session_id)

    async def _drain_completion_events(
        self, db: SubagentWorkQueueDB, session_id: str | None
    ) -> int:
        """Publish and acknowledge pending outbox rows while holding the queue lock."""
        from src.sdk.subagent_completion import SubagentCompletion, completion_bus

        delivered_count = 0
        for row in await db.list_undelivered_completion_events():
            parent_session_id = row.get("parent_session_id")
            if not parent_session_id:
                if await db.mark_completion_event_delivered(row["task_id"]):
                    delivered_count += 1
                continue
            if session_id and session_id != parent_session_id:
                continue
            try:
                raw_result = row.get("result")
                launch_plan = row.get("launch_plan") or {}
                canonical_manifest = launch_plan.get("canonical_manifest") or launch_plan
                workspace_candidates = (
                    canonical_manifest.get("requested_workspace_id"),
                    launch_plan.get("requested_workspace_id"),
                    launch_plan.get("workspace_id"),
                    row.get("task_workspace_id"),
                    row.get("workspace_id"),
                )
                requested_workspace_id = next(
                    (
                        value
                        for value in workspace_candidates
                        if isinstance(value, str)
                        and value
                        and value != USER_LEVEL_WORKSPACE_ID
                    ),
                    self.requested_workspace_id,
                )
                event = SubagentCompletion(
                    user_id=self.user_id,
                    workspace_id=requested_workspace_id,
                    session_id=parent_session_id,
                    task_id=row["task_id"],
                    agent_name=row.get("agent_name") or "unknown",
                    status=row["status"],
                    result=SubagentResult.model_validate(raw_result) if raw_result else None,
                    error=row.get("error"),
                )
                if await completion_bus.publish(event):
                    if await db.mark_completion_event_delivered(row["task_id"]):
                        delivered_count += 1
                else:
                    await db.record_completion_delivery_attempt(row["task_id"])
            except Exception as exc:
                await db.record_completion_delivery_attempt(row["task_id"])
                logger.warning(
                    "subagent.completion_replay_failed",
                    {"task_id": row["task_id"], "error": str(exc)},
                    user_id=self.user_id,
                )
        return delivered_count

    async def _get_db(self) -> SubagentWorkQueueDB:
        if self._db is None:
            self._db = await get_work_queue(self.user_id, USER_LEVEL_WORKSPACE_ID)
            if self._recovery_task is None:
                self._recovery_task = asyncio.create_task(self._recover_stale_jobs())
        return self._db

    async def create(
        self,
        profile: AgentProfile,
        tool_selection_mode: Any | None = None,
    ) -> AgentProfile:
        selection_mode = (
            _coerce_tool_selection_mode(tool_selection_mode)
            if tool_selection_mode is not None
            else _infer_tool_selection_mode(profile)
        )
        agent_path = self.base_path / profile.name
        agent_path.mkdir(parents=True, exist_ok=True)

        # Validate profile dict
        profile_data = profile.model_dump()
        try:
            from src.sdk.agent_profile import validate_profile

            errors = validate_profile(profile_data)
            if errors:
                logger.warning(
                    "subagent.profile_validation",
                    {"name": profile.name, "errors": errors},
                    user_id=self.user_id,
                )
        except Exception:
            pass

        # Persist restrictive policy first: a crash cannot expose a profile whose
        # explicit empty tool list was serialized as an omitted default.
        self._write_runtime_policy(agent_path / "runtime-policy.json", selection_mode)
        (agent_path / "PROFILE.md").write_text(dumps_profile(profile))

        # Write profile metadata files
        provider_path = agent_path / "provider.json"
        schema_path = agent_path / "output-schema.json"
        if profile.provider_options:
            provider_path.write_text(json.dumps(profile.provider_options, indent=2))
        else:
            provider_path.unlink(missing_ok=True)
        if profile.output_schema_def:
            schema_path.write_text(json.dumps(profile.output_schema_def, indent=2))
        else:
            schema_path.unlink(missing_ok=True)

        logger.info(
            "subagent.created",
            {"name": profile.name, "model": profile.model},
            user_id=self.user_id,
        )
        return profile

    async def update(
        self,
        name: str,
        tool_selection_mode: Any | None = None,
        **kwargs: Any,
    ) -> AgentProfile | None:
        current = self.load_def(name)
        if current is None:
            return None

        update_data = {k: v for k, v in kwargs.items() if v is not None}
        updated = current.model_copy(update=update_data)

        agent_path = self.base_path / name

        from src.sdk.subagent_capabilities import ToolSelectionMode

        if tool_selection_mode is None:
            if "tools" in update_data:
                selection_mode = _infer_tool_selection_mode(updated)
            else:
                selection_mode = self.load_tool_selection_mode(name)
        else:
            selection_mode = _coerce_tool_selection_mode(tool_selection_mode)

        # Resolve legacy fields before serialization can omit default values.
        # An explicit empty list must take effect before its frontmatter vanishes.
        if selection_mode is ToolSelectionMode.NONE:
            self._write_runtime_policy(agent_path / "runtime-policy.json", selection_mode)
        (agent_path / "PROFILE.md").write_text(dumps_profile(updated))
        if selection_mode is not ToolSelectionMode.NONE:
            self._write_runtime_policy(agent_path / "runtime-policy.json", selection_mode)

        # Write profile metadata files
        provider_path = agent_path / "provider.json"
        schema_path = agent_path / "output-schema.json"
        if updated.provider_options:
            provider_path.write_text(json.dumps(updated.provider_options, indent=2))
        else:
            provider_path.unlink(missing_ok=True)
        if updated.output_schema_def:
            schema_path.write_text(json.dumps(updated.output_schema_def, indent=2))
        else:
            schema_path.unlink(missing_ok=True)

        logger.info(
            "subagent.updated",
            {"name": name, "fields": list(update_data.keys())},
            user_id=self.user_id,
        )
        return updated

    async def invoke(
        self,
        agent_name: str,
        task: str,
        parent_id: str | None = None,
    ) -> str:
        """Deprecated compatibility API; use ``delegate()`` for new callers.

        No in-repository production callers remain, but external consumers are
        unknown, so removal is deferred pending an explicit compatibility review.
        This method retains its historical task-ID return contract.
        """
        if not _subagent_enabled(self.user_id, agent_name):
            raise ValueError(f"Subagent '{agent_name}' is disabled.")
        from src.sdk.subagent_capabilities import SubagentLaunchRejected

        profile = self.load_def(agent_name)
        if profile is None:
            raise ValueError(f"Subagent '{agent_name}' not found. Create it first with subagent_create.")
        errors = validate_agent_def(
            profile, user_id=self.user_id, workspace_id=self.requested_workspace_id
        )
        if errors:
            raise ValueError("Invalid subagent definition: " + "; ".join(errors))
        plan = self.preflight(agent_name)
        if not plan.ready:
            raise SubagentLaunchRejected(plan)
        profile = profile.model_copy(
            update={"tools": list(plan.effective_tools), "skills": list(plan.effective_skills)}
        )

        db = await self._get_db()
        task_id = await db.insert_task(
            agent_name, task, profile, parent_id, launch_plan=plan.to_persisted_dict()
        )
        await db.set_running(task_id)

        ctx = SubagentContext()
        _active[task_id] = ctx

        try:
            result = await asyncio.wait_for(
                self._run_loop(
                    task_id, profile, task, db, ctx,
                    effective_tool_names=plan.effective_tools,
                    effective_skill_names=plan.effective_skills,
                    workspace_id=plan.resolved_workspace_id,
                ),
                timeout=profile.timeout_seconds,
            )
            if result.terminal_reason == "blocked":
                failed = await db.set_failed(
                    task_id,
                    result.error or "Runtime approval required.",
                    error_code=result.error_code or "approval_required",
                    terminal_reason="blocked",
                    result=result,
                )
                if not failed:
                    await self._set_cancelled_if_requested(task_id, db)
            else:
                completed = await db.set_completed(task_id, result)
                if not completed:
                    await self._set_cancelled_if_requested(task_id, db)
        except TaskCancelledError:
            await db.set_cancelled(task_id)
        except SubagentCancelledError:
            await db.set_cancelled(task_id)
        except TimeoutError:
            failed = await db.set_failed(
                task_id,
                f"timeout after {profile.timeout_seconds}s",
                terminal_status=TaskStatus.TIMED_OUT,
                error_code="timeout",
            )
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
        except Exception as e:
            failed = await db.set_failed(task_id, f"{type(e).__name__}: {e}")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
        finally:
            _active.pop(task_id, None)

        return task_id

    async def _register_active_context(
        self,
        task_id: str,
        db: SubagentWorkQueueDB,
        ctx: SubagentContext,
    ) -> None:
        task_row = await db.get_task(task_id)
        if task_row and task_row.get("cancel_requested"):
            ctx.cancel_event.set()
        _active[task_id] = ctx

    def preflight(self, agent_name: str) -> Any:
        """Resolve an immutable launch plan without queue, provider, or LLM side effects."""
        from src.sdk.subagent_capabilities import build_launch_plan

        profile = self.load_def(agent_name)
        if profile is None:
            raise ValueError(
                f"Subagent '{agent_name}' not found. Create it first with subagent_create."
            )
        return build_launch_plan(
            profile,
            self.user_id,
            self.requested_workspace_id,
            self.load_tool_selection_mode(agent_name),
        )

    async def delegate(
        self,
        agent_name: str,
        task: str,
        parent_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ToolResult | str:
        """Run a subagent synchronously.

        Returns the subagent's output text on success, or a
        `ToolResult(is_error=True)` when the run was cancelled, timed out or
        failed — the caller must not treat every return as success.

        Like invoke() but with agent-def validation and full middleware stack.
        Unlike start(), this blocks until the subagent completes.
        No claim_task or heartbeat needed — runs in-process.

        The effective timeout is min(timeout_seconds, profile.timeout_seconds).
        """
        if not _subagent_enabled(self.user_id, agent_name):
            raise ValueError(f"Subagent '{agent_name}' is disabled.")

        profile = self.load_def(agent_name)
        if profile is None:
            raise ValueError(
                f"Subagent '{agent_name}' not found. "
                f"Create it first with subagent_create."
            )

        errors = validate_agent_def(
            profile, user_id=self.user_id, workspace_id=self.requested_workspace_id
        )
        if errors:
            raise ValueError("Invalid subagent definition: " + "; ".join(errors))

        plan = self.preflight(agent_name)
        if not plan.ready:
            rejected = plan.rejected_decisions
            status = (
                "approval_required_before_start"
                if any(item.status == "permission_ask" for item in rejected)
                else "permission_denied_before_start"
                if any(item.status == "permission_deny" for item in rejected)
                else "capability_unavailable"
            )
            return ToolResult(
                content="Subagent launch rejected before execution: declared capabilities are unavailable.",
                structured_content={
                    "status": status,
                    "reason": "capability_unavailable",
                    "tools": [item.name for item in rejected if item.kind == "tool"],
                    "skills": [item.name for item in rejected if item.kind == "skill"],
                    "llm_started": False,
                    "queue_inserted": False,
                },
                is_error=True,
            )

        profile = profile.model_copy(
            update={"tools": list(plan.effective_tools), "skills": list(plan.effective_skills)}
        )
        effective_timeout = min(
            timeout_seconds or profile.timeout_seconds,
            profile.timeout_seconds,
        )

        db = await self._get_db()
        task_id = await db.insert_task(
            agent_name, task, profile, parent_id, launch_plan=plan.to_persisted_dict()
        )

        ctx = SubagentContext(on_progress=self._make_progress_cb(task_id))
        await self._register_active_context(task_id, db, ctx)

        try:
            result: SubagentResult = await asyncio.wait_for(
                self._run_loop(
                    task_id, profile, task, db, ctx,
                    effective_tool_names=plan.effective_tools,
                    effective_skill_names=plan.effective_skills,
                    workspace_id=plan.resolved_workspace_id,
                ),
                timeout=effective_timeout,
            )
            if getattr(result, "terminal_reason", "completed") == "blocked":
                failed = await db.set_failed(
                    task_id,
                    result.error or "Runtime approval required.",
                    error_code=result.error_code or "approval_required",
                    terminal_reason="blocked",
                    result=result,
                )
                if not failed:
                    await self._set_cancelled_if_requested(task_id, db)
                    return ToolResult(
                        content="Cancelled: subagent was cancelled during execution.",
                        is_error=True,
                    )
                return ToolResult(
                    content=result.error or "Runtime approval required.",
                    structured_content={
                        "status": "blocked",
                        "terminal_reason": "blocked",
                        "error_code": result.error_code or "approval_required",
                        "task_id": task_id,
                        "effective_tools": result.effective_tools,
                        "effective_skills": result.effective_skills,
                        "llm_calls": result.llm_calls,
                        "cost_usd": result.cost_usd,
                    },
                    is_error=True,
                )

            completed = await db.set_completed(task_id, result)
            if not completed:
                # The row was cancelled (or is no longer running) while the run
                # finished, so the work_queue says CANCELLED: returning the
                # output would claim a success the queue disagrees with.
                await self._set_cancelled_if_requested(task_id, db)
                return ToolResult(
                    content="Cancelled: subagent was cancelled during execution.",
                    is_error=True,
                )
            return result.output
        except TaskCancelledError:
            await db.set_cancelled(task_id)
            return ToolResult(
                content="Cancelled: subagent was cancelled during execution.",
                is_error=True,
            )
        except SubagentCancelledError:
            await db.set_cancelled(task_id)
            return ToolResult(
                content="Cancelled: subagent was cancelled during execution.",
                is_error=True,
            )
        except TimeoutError:
            error = f"timeout after {effective_timeout}s"
            failed = await db.set_failed(
                task_id, error, terminal_status=TaskStatus.TIMED_OUT
            )
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
            return ToolResult(
                content=f"Timeout: subagent did not complete within {effective_timeout}s.",
                is_error=True,
            )
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            failed = await db.set_failed(task_id, error)
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
            return ToolResult(content=f"Error: {type(e).__name__}: {e}", is_error=True)
        finally:
            _active.pop(task_id, None)

    async def start(
        self,
        agent_name: str,
        task: str,
        parent_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> str:
        if not _subagent_enabled(self.user_id, agent_name):
            raise ValueError(f"Subagent '{agent_name}' is disabled.")
        from src.sdk.subagent_capabilities import SubagentLaunchRejected

        profile = self.load_def(agent_name)
        if profile is None:
            raise ValueError(f"Subagent '{agent_name}' not found. Create it first with subagent_create.")

        errors = validate_agent_def(
            profile, user_id=self.user_id, workspace_id=self.requested_workspace_id
        )
        if errors:
            raise ValueError("Invalid subagent definition: " + "; ".join(errors))
        plan = self.preflight(agent_name)
        if not plan.ready:
            raise SubagentLaunchRejected(plan)
        profile = profile.model_copy(
            update={"tools": list(plan.effective_tools), "skills": list(plan.effective_skills)}
        )

        db = await self._get_db()
        task_id = await db.insert_task(
            agent_name,
            task,
            profile,
            parent_id,
            parent_session_id=parent_session_id,
            launch_plan=plan.to_persisted_dict(),
        )

        ctx = SubagentContext(on_progress=self._make_progress_cb(task_id))
        await self._register_active_context(task_id, db, ctx)

        if parent_session_id is None:
            background_task = asyncio.create_task(self._run_job(task_id, ctx))
        else:
            background_task = asyncio.create_task(self._run_job(task_id, ctx, parent_session_id))
        self._background_tasks.add(background_task)
        background_task.add_done_callback(self._on_background_task_done)
        return task_id

    def _on_background_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        self._consume_background_exception(task)

    @staticmethod
    def _consume_background_exception(task: asyncio.Task[Any]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "subagent.background_failed",
                    {"error": str(exc), "error_type": type(exc).__name__},
                    user_id="system",
                )

    async def _heartbeat_loop(self, task_id: str, worker_id: str, db: SubagentWorkQueueDB) -> None:
        while True:
            await asyncio.sleep(5)
            await db.heartbeat(task_id, worker_id)

    async def _run_job(
        self,
        task_id: str,
        ctx: SubagentContext | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        db = await self._get_db()
        worker_id = f"{self.user_id}:{self.workspace_id}:{id(self)}"
        claimed = await db.claim_task(task_id, worker_id)
        if not claimed:
            return

        row = await db.get_task(task_id)
        if row is None:
            return

        profile = AgentProfile(**json.loads(row.get("config") or "{}"))
        task = row["task"]
        launch_plan = json.loads(row.get("launch_plan") or "{}")
        canonical_manifest = launch_plan.get("canonical_manifest") or launch_plan
        workspace_candidates = (
            canonical_manifest.get("requested_workspace_id"),
            launch_plan.get("requested_workspace_id"),
            launch_plan.get("workspace_id"),
        )
        launch_workspace_id = next(
            (
                value
                for value in workspace_candidates
                if isinstance(value, str)
                and value
                and value != USER_LEVEL_WORKSPACE_ID
            ),
            self.requested_workspace_id,
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(task_id, worker_id, db))

        try:
            if not isinstance(canonical_manifest.get("effective_tools"), list) or not isinstance(
                canonical_manifest.get("effective_skills"), list
            ):
                raise ValueError("task is missing its frozen subagent capability manifest")
            result = await asyncio.wait_for(
                self._run_loop(
                    task_id,
                    profile,
                    task,
                    db,
                    ctx or SubagentContext(),
                    effective_tool_names=canonical_manifest.get("effective_tools"),
                    effective_skill_names=canonical_manifest.get("effective_skills"),
                    workspace_id=launch_workspace_id,
                ),
                timeout=profile.timeout_seconds,
            )
            latest = await db.get_task(task_id)
            if latest and latest["status"] == TaskStatus.CANCELLING.value:
                await db.set_cancelled(task_id)
                await self._publish_completion(
                    task_id,
                    profile.name,
                    TaskStatus.CANCELLED.value,
                    None,
                    "cancelled",
                    parent_session_id,
                )
            elif result.terminal_reason == "blocked":
                error = result.error or "Runtime approval required."
                failed = await db.set_failed(
                    task_id,
                    error,
                    error_code=result.error_code or "approval_required",
                    terminal_reason="blocked",
                    result=result,
                )
                if failed:
                    await self._publish_completion(
                        task_id,
                        profile.name,
                        TaskStatus.FAILED.value,
                        result,
                        error,
                        parent_session_id,
                    )
                elif await self._set_cancelled_if_requested(task_id, db):
                    await self._publish_completion(
                        task_id,
                        profile.name,
                        TaskStatus.CANCELLED.value,
                        None,
                        "cancelled",
                        parent_session_id,
                    )
            else:
                completed = await db.set_completed(task_id, result)
                if completed:
                    await self._publish_completion(
                        task_id, profile.name, TaskStatus.COMPLETED.value, result, None, parent_session_id
                    )
                elif await self._set_cancelled_if_requested(task_id, db):
                    await self._publish_completion(
                        task_id, profile.name, TaskStatus.CANCELLED.value, None, "cancelled", parent_session_id
                    )
        except TaskCancelledError:
            await db.set_cancelled(task_id)
            await self._publish_completion(task_id, profile.name, TaskStatus.CANCELLED.value, None, "cancelled", parent_session_id)
        except TimeoutError:
            error = f"timeout after {profile.timeout_seconds}s"
            failed = await db.set_failed(
                task_id, error, terminal_status=TaskStatus.TIMED_OUT
            )
            if failed:
                await self._publish_completion(
                    task_id, profile.name, TaskStatus.TIMED_OUT.value, None, error, parent_session_id
                )
            elif await self._set_cancelled_if_requested(task_id, db):
                await self._publish_completion(
                    task_id, profile.name, TaskStatus.CANCELLED.value, None, "cancelled", parent_session_id
                )
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            failed = await db.set_failed(
                task_id, error, error_code=type(e).__name__.lower()
            )
            if failed:
                await self._publish_completion(
                    task_id, profile.name, TaskStatus.FAILED.value, None, error, parent_session_id
                )
            elif await self._set_cancelled_if_requested(task_id, db):
                await self._publish_completion(
                    task_id, profile.name, TaskStatus.CANCELLED.value, None, "cancelled", parent_session_id
                )
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _publish_completion(
        self,
        task_id: str,
        agent_name: str,
        status: str,
        result: SubagentResult | None,
        error: str | None,
        parent_session_id: str | None,
    ) -> None:
        if not parent_session_id:
            return
        try:
            await self.drain_completion_events(session_id=parent_session_id)
        except Exception as exc:
            logger.warning(
                "subagent.completion_publish_failed",
                {"task_id": task_id, "error": str(exc)},
                user_id=self.user_id,
            )

    async def _set_cancelled_if_requested(self, task_id: str, db: SubagentWorkQueueDB) -> bool:
        latest = await db.get_task(task_id)
        if latest and (
            latest["cancel_requested"]
            or latest["status"] in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}
        ):
            return await db.set_cancelled(task_id)
        return False

    async def _run_loop(
        self,
        task_id: str,
        profile: AgentProfile,
        task: str,
        db: SubagentWorkQueueDB,
        ctx: SubagentContext | None = None,
        effective_tool_names: list[str] | tuple[str, ...] | None = None,
        effective_skill_names: list[str] | tuple[str, ...] | None = None,
        workspace_id: str | None = None,
    ) -> SubagentResult:
        from src.sdk.loop import AgentLoop, CostTracker, RunConfig
        from src.sdk.middleware_summarization import SummarizationMiddleware
        from src.sdk.providers.factory import create_model_from_config

        model_str = profile.model or self.settings.agent.model
        provider = create_model_from_config(model_str, user_id=self.user_id)

        execution_workspace_id = workspace_id or self.requested_workspace_id
        tools = _build_tools_for_subagent(
            profile,
            user_id=self.user_id,
            effective_tool_names=effective_tool_names,
            workspace_id=execution_workspace_id,
        )
        system_prompt = _build_system_prompt(
            profile,
            self.user_id,
            execution_workspace_id,
            effective_skill_names=(
                effective_skill_names
                if effective_skill_names is not None
                else profile.skills if effective_tool_names is not None else None
            ),
        )
        try:
            from src.sdk.registry import get_model_info

            model_info = get_model_info(model_str)
            model_cost = model_info.cost if model_info and model_info.cost else None
        except Exception:
            model_cost = None

        run_config = RunConfig(
            max_llm_calls=profile.max_llm_calls,
            cost_limit_usd=profile.cost_limit_usd,
            provider_options=_schema_provider_options(
                provider,
                model_str,
                profile.provider_options or None,
                profile.output_schema_def,
            ),
        )

        run_context = ctx or SubagentContext()
        summarization_mw = SummarizationMiddleware(model=model_str)
        middlewares: list[Any] = [summarization_mw]
        # Bug-hunt P1: permissions must hold on delegated loops too —
        # otherwise deny could be bypassed via subagent_delegate.
        from src.sdk.governance import governance_enabled
        from src.sdk.middleware_hitl import HITLMiddleware

        if governance_enabled():
            middlewares.append(
                HITLMiddleware(user_id=self.user_id, subagent_context=run_context)
            )

        # Roadmap P0-T3 follow-up: loops built directly (bypassing
        # create_sdk_loop) must still wire the per-user audit store.
        from src.sdk.audit import ensure_audit_store_subscribed

        ensure_audit_store_subscribed(self.user_id)

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            system_prompt=system_prompt,
            middlewares=middlewares,
            run_config=run_config,
            user_id=self.user_id,
            workspace_id=execution_workspace_id,
        )
        loop.subagent_ctx = run_context
        if effective_tool_names is not None:
            run_context.allowed_skill_names = frozenset(profile.skills)

        messages = [Message.user(task)]
        cost_tracker = CostTracker(
            emit_usage=getattr(loop, "_emit_usage_event", None), model_cost=model_cost
        )
        result_messages = await loop.run(messages, cost_tracker=cost_tracker)
        structured_output: Any | None = None
        if profile.output_schema_def and run_context.runtime_block is None:
            output = _extract_final_output(result_messages)
            try:
                structured_output = _parse_and_validate_output(output, profile.output_schema_def)
            except ValueError:
                if limit := cost_tracker.exceeds_limits(run_config):
                    raise ValueError(
                        f"schema validation failed: no remaining budget for the schema retry ({limit})"
                    )
                retry_messages = [
                    *result_messages,
                    Message.user(
                        "Your previous response did not match the required JSON schema. "
                        "Retry once and return only schema-valid JSON."
                    ),
                ]
                result_messages = await loop.run(retry_messages, cost_tracker=cost_tracker)
                output = _extract_final_output(result_messages)
                structured_output = _parse_and_validate_output(output, profile.output_schema_def)

        total_input = 0
        total_output = 0
        total_reasoning = 0
        llm_calls = 0
        for msg in result_messages:
            if msg.usage:
                total_input += msg.usage.input_tokens
                total_output += msg.usage.output_tokens
                total_reasoning += msg.usage.reasoning_tokens
                llm_calls += 1

        cost_usd = cost_tracker.total_cost_usd

        if run_context.runtime_block is not None:
            block = run_context.runtime_block
            return SubagentResult(
                name=profile.name,
                task=task,
                success=False,
                output=f"Blocked: {block.message}",
                error=block.message,
                error_code=block.error_code,
                terminal_reason=block.terminal_reason,
                cost_usd=cost_usd,
                llm_calls=llm_calls,
                effective_tools=list(effective_tool_names or profile.tools),
                effective_skills=list(profile.skills),
            )

        if profile.output_schema_def:
            output = _extract_final_output(result_messages)
            truncated = False
        else:
            output, truncated = _extract_output(result_messages)
        if not output.strip():
            raise ValueError("subagent produced no final assistant output")

        return SubagentResult(
            name=profile.name,
            task=task,
            success=True,
            output=output,
            truncated=truncated,
            cost_usd=cost_usd,
            llm_calls=llm_calls,
            structured_output=structured_output,
            effective_tools=list(effective_tool_names or profile.tools),
            effective_skills=list(profile.skills),
        )

    def _make_progress_cb(self, task_id: str) -> Callable[..., Any]:
        async def _cb(step: int, phase: str, message: str) -> None:
            try:
                db = await self._get_db()
                await db.update_progress(task_id, {
                    "steps_completed": step,
                    "phase": phase,
                    "message": message,
                })
            except Exception:
                pass
        return _cb

    async def cancel(self, task_id: str) -> bool:
        ctx = _active.get(task_id)
        if ctx:
            ctx.cancel_event.set()
        db = await self._get_db()
        return await db.request_cancel(task_id)

    async def instruct(self, task_id: str, message: str) -> bool:
        ctx = _active.get(task_id)
        if ctx:
            await ctx.instructions.put(message)
        db = await self._get_db()
        return await db.add_instruction(task_id, message)

    async def delete(self, name: str) -> bool:
        profile = self.load_def(name)
        if profile is None:
            return False
        db = await self._get_db()
        await db.request_cancel_active_tasks_for_agent(name)
        agent_path = self.base_path / name
        if agent_path.exists():
            shutil.rmtree(agent_path)
        return True

    async def check_progress(self, parent_id: str | None = None) -> list[dict[str, Any]]:
        db = await self._get_db()
        return await db.check_progress(parent_id=parent_id)

    async def get_result(self, task_id: str) -> SubagentResult | None:
        db = await self._get_db()
        return await db.get_result(task_id)

    async def list_defs(self) -> list[AgentProfile]:
        defs: list[AgentProfile] = []
        if self.base_path.exists():
            for d in self.base_path.iterdir():
                if d.is_dir() and (d / "PROFILE.md").exists():
                    profile = self.load_def(d.name)
                    if profile:
                        defs.append(profile)
        return defs

    async def list_defs_with_scope(self) -> list[tuple[AgentProfile, str]]:
        """Return agent defs from the user-level subagents directory."""
        scoped: list[tuple[AgentProfile, str]] = []

        if self.base_path.exists():
            for d in self.base_path.iterdir():
                if d.is_dir() and (d / "PROFILE.md").exists():
                    profile = self.load_def(d.name)
                    if profile:
                        scoped.append((profile, "user"))

        return scoped

    @staticmethod
    def _write_runtime_policy(
        path: Path, mode: Any, migrated_from: str | None = None
    ) -> None:
        """Atomically persist a versioned, explicit tool-selection policy."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"version": 1, "tool_selection": mode.value}
        if migrated_from is not None:
            payload["migrated_from"] = migrated_from
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary_path).replace(path)
        except Exception:
            Path(temporary_path).unlink(missing_ok=True)
            raise

    def _migrate_legacy_tool_selection_mode(self, name: str) -> Any:
        """Migrate one old profile without restoring implicit all-native access."""
        from src.sdk.subagent_capabilities import ToolSelectionMode

        profile = self.load_def(name)
        if profile is None:
            raise ValueError(f"Cannot migrate runtime policy for missing profile '{name}'")
        fields_set: set[str] = getattr(profile, "model_fields_set", set())
        if "tools" not in fields_set:
            mode = ToolSelectionMode.SAFE_DEFAULT
        elif profile.tools:
            mode = ToolSelectionMode.ALLOWLIST
        else:
            mode = ToolSelectionMode.NONE

        self._write_runtime_policy(
            self.base_path / name / "runtime-policy.json", mode, migrated_from="legacy"
        )
        logger.info(
            "subagent.runtime_policy_migrated",
            {"agent": name, "tool_selection": mode.value, "source": "legacy"},
            user_id=self.user_id,
        )
        return mode

    def load_tool_selection_mode(self, name: str) -> Any:
        """Load or one-time migrate a profile's explicit capability selection."""
        from src.sdk.subagent_capabilities import ToolSelectionMode

        policy_path = self.base_path / name / "runtime-policy.json"
        if not policy_path.exists():
            return self._migrate_legacy_tool_selection_mode(name)
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or type(payload.get("version")) is not int:
                raise ValueError("missing version")
            if payload["version"] != 1:
                raise ValueError(f"unsupported version {payload['version']!r}")
            value = payload.get("tool_selection")
            if value == ToolSelectionMode.LEGACY.value:
                return self._migrate_legacy_tool_selection_mode(name)
            if not isinstance(value, str):
                raise ValueError("tool_selection must be a string")
            mode = ToolSelectionMode(value)
            if mode is ToolSelectionMode.LEGACY:
                return self._migrate_legacy_tool_selection_mode(name)
            return mode
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.error(
                "subagent.runtime_policy_invalid",
                {"agent": name, "error": str(exc)},
                user_id=self.user_id,
            )
            raise ValueError(
                f"Invalid runtime policy for subagent '{name}': {exc}"
            ) from exc

    def load_def(self, name: str) -> AgentProfile | None:
        profile_path = self.base_path / name / "PROFILE.md"
        if profile_path.exists():
            try:
                from agentprofile.parser import load_profile as _load_ap

                profile = _load_ap(str(profile_path))
                agent_path = profile_path.parent
                if not profile.provider_options:
                    provider_path = agent_path / "provider.json"
                    if provider_path.exists():
                        profile.provider_options = json.loads(provider_path.read_text())
                if not profile.output_schema_def:
                    schema_path = agent_path / "output-schema.json"
                    if schema_path.exists():
                        profile.output_schema_def = json.loads(schema_path.read_text())
                return profile
            except Exception as e:
                logger.error("subagent.load_failed", {"name": name, "error": str(e), "error_type": type(e).__name__}, user_id=self.user_id)
        return None

    def is_valid(self, name: str) -> bool:
        """Check if a subagent config exists and is loadable.

        Returns False for: missing agent, corrupt YAML, or invalid AgentProfile.
        Returns True for: valid, loadable AgentProfile.
        """
        return self.load_def(name) is not None


_coordinators: dict[tuple[str, str], SubagentCoordinator] = {}


def get_coordinator(user_id: str, workspace_id: str = "personal") -> SubagentCoordinator:
    """Cache request-scoped coordinators without changing user-level storage."""
    key = (user_id, workspace_id)
    if key not in _coordinators:
        _coordinators[key] = SubagentCoordinator(user_id, workspace_id)
    return _coordinators[key]
