"""Subagent coordinator — creates, invokes, supervises subagents via work_queue.

Replaces SubagentManager with work_queue-backed orchestration.
Each invoke() creates a fresh AgentLoop with ProgressMiddleware + InstructionMiddleware,
runs it with timeout and cost limits, and stores structured results in work_queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import Callable
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
from src.sdk.work_queue import USER_LEVEL_WORKSPACE_ID, WorkQueueDB, get_work_queue
from src.storage import paths as _paths

# Alias: used by callers (e.g. tests) that patch src.sdk.coordinator.get_paths
get_paths = _paths.get_paths

logger = get_logger()

_active: dict[str, SubagentContext] = {}

# Constants for subagent tool filtering
MANDATORY_SUBAGENT_TOOLS = {"message_search"}
OPTIONAL_SKILL_LOAD_TOOL = "skills_load"
DENIED_SKILL_MANAGEMENT_TOOLS = {"skill_delete", "skill_update"}


def _load_user_caps(user_id: str) -> dict[str, Any]:
    try:
        return load_user_capabilities(user_id)
    except Exception:
        return {"tools": {}, "skills": {}, "subagents": {}}


def _subagent_enabled(user_id: str, name: str) -> bool:
    return resource_enabled(_load_user_caps(user_id), "subagents", name)


def _build_tools_for_subagent(profile: AgentProfile, user_id: str | None = None) -> list[Any]:
    """Build the filtered tool list for a subagent."""
    from src.sdk.native_tools import get_native_tools

    all_native = get_native_tools()
    tool_map = {t.name: t for t in all_native}

    allowed = set(profile.tools) if profile.tools else set(tool_map.keys())
    final = {
        name
        for name in allowed
        if not name.startswith("subagent_")
        and not _is_denied_memory_tool(name)
        and name not in DENIED_SKILL_MANAGEMENT_TOOLS
    }
    final.update(MANDATORY_SUBAGENT_TOOLS)
    if profile.skills:
        final.add(OPTIONAL_SKILL_LOAD_TOOL)

    if user_id:
        disabled_tools = {
            name for name, enabled in _load_user_caps(user_id).get("tools", {}).items()
            if not resource_enabled(_load_user_caps(user_id), "tools", name)
        }
        final.difference_update(disabled_tools)

    return [tool_map[n] for n in sorted(final) if n in tool_map]


def _build_system_prompt(
    profile: AgentProfile, user_id: str, workspace_id: str = "personal"
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
        try:
            from src.skills.registry import get_skill_registry

            sr = get_skill_registry(user_id=user_id)
            caps = _load_user_caps(user_id)
            skill_entries = []
            for skill_name in profile.skills:
                if not resource_enabled(caps, "skills", skill_name):
                    continue
                skill = sr.get_skill(skill_name)
                if skill:
                    desc = skill.get("description", "")
                    skill_entries.append(f"- **{skill_name}**: {desc}")
            if skill_entries:
                parts.insert(
                    0,
                    "## Available Skills\n"
                    "Use skills_load(skill_name=...) before following a skill's instructions.\n"
                    + "\n".join(skill_entries),
                )
        except Exception:
            pass

    return "\n\n".join(parts)


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
        self._db: WorkQueueDB | None = None
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
        return count

    async def _get_db(self) -> WorkQueueDB:
        if self._db is None:
            self._db = await get_work_queue(self.user_id, USER_LEVEL_WORKSPACE_ID)
            if self._recovery_task is None:
                self._recovery_task = asyncio.create_task(self._recover_stale_jobs())
        return self._db

    async def create(self, profile: AgentProfile) -> AgentProfile:
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

        # Write PROFILE.md (frontmatter + body)
        (agent_path / "PROFILE.md").write_text(
            dumps_profile(profile)
        )

        # Write companion files
        if profile.provider_options:
            (agent_path / "provider.json").write_text(
                json.dumps(profile.provider_options, indent=2)
            )
        if profile.output_schema_def:
            (agent_path / "output-schema.json").write_text(
                json.dumps(profile.output_schema_def, indent=2)
            )

        logger.info(
            "subagent.created",
            {"name": profile.name, "model": profile.model},
            user_id=self.user_id,
        )
        return profile

    async def update(self, name: str, **kwargs: Any) -> AgentProfile | None:
        current = self.load_def(name)
        if current is None:
            return None

        update_data = {k: v for k, v in kwargs.items() if v is not None}
        updated = current.model_copy(update=update_data)

        agent_path = self.base_path / name

        # Write PROFILE.md
        (agent_path / "PROFILE.md").write_text(dumps_profile(updated))

        # Write companion files
        if updated.provider_options:
            (agent_path / "provider.json").write_text(
                json.dumps(updated.provider_options, indent=2)
            )
        if updated.output_schema_def:
            (agent_path / "output-schema.json").write_text(
                json.dumps(updated.output_schema_def, indent=2)
            )

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
        """DEPRECATED: Use delegate() instead.

        This method skips validate_agent_def() and returns task_id instead of
        result output. Kept for backward compatibility but delegates should use
        delegate() for new code.
        """
        if not _subagent_enabled(self.user_id, agent_name):
            raise ValueError(f"Subagent '{agent_name}' is disabled.")

        profile = self.load_def(agent_name)
        if profile is None:
            raise ValueError(f"Subagent '{agent_name}' not found. Create it first with subagent_create.")

        db = await self._get_db()
        task_id = await db.insert_task(agent_name, task, profile, parent_id)
        await db.set_running(task_id)

        ctx = SubagentContext()
        _active[task_id] = ctx

        try:
            result = await asyncio.wait_for(
                self._run_loop(task_id, profile, task, db, ctx),
                timeout=profile.timeout_seconds,
            )
            completed = await db.set_completed(task_id, result)
            if not completed:
                await self._set_cancelled_if_requested(task_id, db)
        except TaskCancelledError:
            await db.set_cancelled(task_id)
        except SubagentCancelledError:
            await db.set_cancelled(task_id)
        except TimeoutError:
            failed = await db.set_failed(task_id, f"timeout after {profile.timeout_seconds}s")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
        except Exception as e:
            failed = await db.set_failed(task_id, f"{type(e).__name__}: {e}")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
        finally:
            _active.pop(task_id, None)

        return task_id

    async def delegate(
        self,
        agent_name: str,
        task: str,
        parent_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str:
        """Run a subagent synchronously and return the result string.

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

        errors = validate_agent_def(profile, user_id=self.user_id, workspace_id=self.workspace_id)
        if errors:
            raise ValueError("Invalid subagent definition: " + "; ".join(errors))

        effective_timeout = min(
            timeout_seconds or profile.timeout_seconds,
            profile.timeout_seconds,
        )

        db = await self._get_db()
        task_id = await db.insert_task(agent_name, task, profile, parent_id)

        ctx = SubagentContext(on_progress=self._make_progress_cb(task_id))
        task_row = await db.get_task(task_id)
        if task_row and task_row.get("cancel_requested"):
            ctx.cancel_event.set()
        _active[task_id] = ctx
        task_row = await db.get_task(task_id)
        if task_row and task_row.get("cancel_requested"):
            ctx.cancel_event.set()

        try:
            result: SubagentResult = await asyncio.wait_for(
                self._run_loop(task_id, profile, task, db, ctx),
                timeout=effective_timeout,
            )
            completed = await db.set_completed(task_id, result)
            if not completed:
                await self._set_cancelled_if_requested(task_id, db)
            return result.output
        except TaskCancelledError:
            await db.set_cancelled(task_id)
            return "Cancelled: subagent was cancelled during execution."
        except SubagentCancelledError:
            await db.set_cancelled(task_id)
            return "Cancelled: subagent was cancelled during execution."
        except TimeoutError:
            failed = await db.set_failed(task_id, f"timeout after {effective_timeout}s")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
            return f"Timeout: subagent did not complete within {effective_timeout}s."
        except Exception as e:
            failed = await db.set_failed(task_id, f"{type(e).__name__}: {e}")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
            return f"Error: {type(e).__name__}: {e}"
        finally:
            _active.pop(task_id, None)

    async def start(
        self,
        agent_name: str,
        task: str,
        parent_id: str | None = None,
    ) -> str:
        if not _subagent_enabled(self.user_id, agent_name):
            raise ValueError(f"Subagent '{agent_name}' is disabled.")

        profile = self.load_def(agent_name)
        if profile is None:
            raise ValueError(f"Subagent '{agent_name}' not found. Create it first with subagent_create.")

        errors = validate_agent_def(profile, user_id=self.user_id, workspace_id=self.workspace_id)
        if errors:
            raise ValueError("Invalid subagent definition: " + "; ".join(errors))

        db = await self._get_db()
        task_id = await db.insert_task(agent_name, task, profile, parent_id)

        ctx = SubagentContext(on_progress=self._make_progress_cb(task_id))
        task_row = await db.get_task(task_id)
        if task_row and task_row.get("cancel_requested"):
            ctx.cancel_event.set()
        _active[task_id] = ctx
        task_row = await db.get_task(task_id)
        if task_row and task_row.get("cancel_requested"):
            ctx.cancel_event.set()

        background_task = asyncio.create_task(self._run_job(task_id, ctx))
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

    async def _heartbeat_loop(self, task_id: str, worker_id: str, db: WorkQueueDB) -> None:
        while True:
            await asyncio.sleep(5)
            await db.heartbeat(task_id, worker_id)

    async def _run_job(self, task_id: str, ctx: SubagentContext | None = None) -> None:
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
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(task_id, worker_id, db))

        try:
            result = await asyncio.wait_for(
                self._run_loop(task_id, profile, task, db, ctx or SubagentContext()),
                timeout=profile.timeout_seconds,
            )
            latest = await db.get_task(task_id)
            if latest and latest["status"] == TaskStatus.CANCELLING.value:
                await db.set_cancelled(task_id)
            else:
                completed = await db.set_completed(task_id, result)
                if not completed:
                    await self._set_cancelled_if_requested(task_id, db)
        except TaskCancelledError:
            await db.set_cancelled(task_id)
        except TimeoutError:
            failed = await db.set_failed(task_id, f"timeout after {profile.timeout_seconds}s")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
        except Exception as e:
            failed = await db.set_failed(task_id, f"{type(e).__name__}: {e}")
            if not failed:
                await self._set_cancelled_if_requested(task_id, db)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _set_cancelled_if_requested(self, task_id: str, db: WorkQueueDB) -> bool:
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
        db: WorkQueueDB,
        ctx: SubagentContext | None = None,
    ) -> SubagentResult:
        from src.sdk.loop import AgentLoop, RunConfig
        from src.sdk.middleware_summarization import SummarizationMiddleware
        from src.sdk.providers.factory import create_model_from_config

        model_str = profile.model or self.settings.agent.model
        provider = create_model_from_config(model_str, user_id=self.user_id)

        tools = _build_tools_for_subagent(profile, user_id=self.user_id)
        system_prompt = _build_system_prompt(profile, self.user_id, self.workspace_id)

        run_config = RunConfig(
            max_llm_calls=profile.max_llm_calls,
            cost_limit_usd=profile.cost_limit_usd,
            provider_options=profile.provider_options or None,
        )

        summarization_mw = SummarizationMiddleware()
        middlewares = [summarization_mw]

        loop = AgentLoop(
            provider=provider,
            tools=tools,
            system_prompt=system_prompt,
            middlewares=middlewares,  # type: ignore[arg-type]
            run_config=run_config,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )
        loop.subagent_ctx = ctx or SubagentContext()

        messages = [Message.user(task)]
        result_messages = await loop.run(messages)

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

        try:
            from src.sdk.registry import get_model_info
            model_info = get_model_info(model_str)
            cost = model_info.cost if model_info and model_info.cost else None
        except Exception:
            cost = None

        cost_usd = 0.0
        if cost:
            cost_usd = (total_input / 1_000_000) * cost.input + (total_output / 1_000_000) * cost.output
            if cost.reasoning and total_reasoning:
                cost_usd += (total_reasoning / 1_000_000) * cost.reasoning

        output, truncated = _extract_output(result_messages)

        return SubagentResult(
            name=profile.name,
            task=task,
            success=True,
            output=output,
            truncated=truncated,
            cost_usd=cost_usd,
            llm_calls=llm_calls,
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

    def load_def(self, name: str) -> AgentProfile | None:
        profile_path = self.base_path / name / "PROFILE.md"
        if profile_path.exists():
            try:
                from agentprofile.parser import load_profile as _load_ap
                return _load_ap(str(profile_path))
            except Exception as e:
                logger.error("subagent.load_failed", {"name": name, "error": str(e), "error_type": type(e).__name__}, user_id=self.user_id)
        return None

    def is_valid(self, name: str) -> bool:
        """Check if a subagent config exists and is loadable.

        Returns False for: missing agent, corrupt YAML, or invalid AgentProfile.
        Returns True for: valid, loadable AgentProfile.
        """
        return self.load_def(name) is not None


_coordinators: dict[str, SubagentCoordinator] = {}


def get_coordinator(user_id: str, workspace_id: str = "personal") -> SubagentCoordinator:
    key = user_id
    if key not in _coordinators:
        _coordinators[key] = SubagentCoordinator(user_id, workspace_id)
    return _coordinators[key]
