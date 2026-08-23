"""SDK Agent Runner — creates and runs AgentLoop with proper wiring.

This is the bridge between the HTTP layer and the SDK AgentLoop.
It handles:
  - Creating LLM provider from config
  - Loading SDK-native tools
  - Loading MCP tools via MCPToolBridge
  - Assembling SDK middlewares (memory, summarization)
  - Converting between WS protocol messages and StreamChunks
  - Thread-safe per-user agent instances

Skills are now discovery-based: available skill names are embedded
in the skills_list tool description dynamically, not in the system prompt.
"""

from __future__ import annotations

import asyncio
import collections
import collections.abc
import dataclasses
import hashlib
import json
import time
from pathlib import Path
from typing import Any, cast

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.capabilities import load_user_capabilities, resource_enabled
from src.sdk.compression import (
    CompressionArtifact,
    CompressionContext,
    PersistenceStatus,
    SummaryPersistenceResult,
)
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk
from src.sdk.middleware_summarization import SummarizationMiddleware
from src.sdk.native_tools import get_native_tools
from src.sdk.providers.factory import create_model_from_config, get_cached_model_provider
from src.sdk.tools import ToolDefinition
from src.sdk.user_prompt import load_user_prompt
from src.storage.paths import DataPaths

logger = get_logger()

_MAX_LOOP_CACHE = 50
_loop_cache: collections.OrderedDict[str, AgentLoop] = collections.OrderedDict()
_loop_lock = asyncio.Lock()

_user_loops: dict[str, AgentLoop] = {}


def _normalize_session_id(session_id: str | None) -> str:
    if session_id is None:
        return "default"
    normalized = session_id.strip()
    return normalized or "default"


def _active_loop_key(user_id: str, session_id: str | None = None) -> str:
    return f"{user_id}:session:{_normalize_session_id(session_id)}"


def register_user_loop(user_id: str, loop: AgentLoop, session_id: str | None = None) -> None:
    _user_loops[_active_loop_key(user_id, session_id)] = loop


def unregister_user_loop(
    user_id: str, loop: AgentLoop | None = None, session_id: str | None = None
) -> None:
    key = _active_loop_key(user_id, session_id)
    if loop is not None and _user_loops.get(key) is not loop:
        return
    _user_loops.pop(key, None)


def get_user_loop(user_id: str, session_id: str | None = None) -> AgentLoop | None:
    if session_id is not None:
        return _user_loops.get(_active_loop_key(user_id, session_id))
    default_loop = _user_loops.get(_active_loop_key(user_id))
    if default_loop is not None:
        return default_loop
    prefix = f"{user_id}:session:"
    matches = [loop for key, loop in _user_loops.items() if key.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None
    return None


def _loop_cache_key(
    user_id: str,
    workspace_id: str,
    model: str | None,
    provider_keys: dict[str, str] | None = None,
    session_id: str | None = None,
) -> str:
    del workspace_id  # Compatibility-only; loop state is bounded by user session.
    key = f"{user_id}:model:{model or 'default'}:session:{_normalize_session_id(session_id)}"
    if provider_keys:
        encoded = json.dumps(provider_keys, sort_keys=True, separators=(",", ":"))
        key_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
        key = f"{key}:keys:{key_hash}"
    return key


def _seed_default_workspace() -> None:
    """Create the default Personal workspace if it doesn't exist."""
    try:
        from src.sdk.workspace_models import Workspace, load_workspace, save_workspace
        from src.storage.paths import DataPaths
        ws = load_workspace("personal")
        if ws is None:
            ws = Workspace.from_name("Personal")
            ws.description = "Default personal workspace"
            save_workspace(ws)
            dp = DataPaths(workspace_id="personal")
            dp.workspace_files_dir()
            dp.workspace_memory_dir()
            dp.workspace_subagents_dir()
    except Exception:
        pass


def _get_user_prompt_context(user_id: str) -> str:
    """Build user prompt context for the system prompt."""
    try:
        prompt = load_user_prompt(user_id)
        if not prompt:
            return ""
        return f"\n\n## User Instructions\n{prompt}"
    except Exception:
        return ""


def _ensure_prompt_seeded(user_id: str) -> None:
    """Seed Prompt.md from seeds/prompts/ on first access."""
    prompt_path = DataPaths(user_id=user_id).user_prompt_path()
    marker = prompt_path.parent / ".prompt_seeded"
    if prompt_path.exists() or marker.exists():
        return
    seed = Path("seeds/prompts/Prompt.md")
    if seed.exists():
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    marker.write_text("", encoding="utf-8")


def _get_system_prompt(user_id: str, workspace_id: str | None = None) -> str:
    del workspace_id

    _ensure_prompt_seeded(user_id)
    user_prompt_context = _get_user_prompt_context(user_id)
    skills_context = _get_skills_context(user_id)
    caps = _load_user_capabilities(user_id)
    memory_context = _get_memory_context(caps)
    file_ops_guideline = _get_file_ops_guideline(caps)

    sections = [
        user_prompt_context,
        skills_context,
        memory_context,
        file_ops_guideline,
    ]
    sections = [s for s in sections if s]
    body = "\n".join(sections)
    return body + f"\n\nuser_id: {user_id}"


# Tool-selection guidance for the memory section, keyed by tool name.
# Only tools that are actually enabled are listed (Pi-style conditional
# guidelines: never advertise a tool the agent cannot call).
_MEMORY_TOOL_GUIDANCE: list[tuple[str, str | None, str]] = [
    (
        "message_search",
        "use FIRST, before saying you don't know",
        "Full session context for specific facts, names, dates, plans, past decisions",
    ),
    (
        "message_count",
        'use FOR "how many" questions',
        "Deterministic counting of distinct items across sessions",
    ),
    (
        "message_timeline",
        "use FOR temporal reasoning",
        'Find dates of events, calculate "how many days between X and Y"',
    ),
    ("memory_profile", None, "Recent conversation context digest (recurring topics, preferences, personal facts)"),
]


def _get_memory_context(caps: dict[str, Any]) -> str:
    """Build the memory recall strategy section, listing only enabled tools."""
    lines = ["## Memory Recall Strategy", "### Tool selection"]
    any_tool = False
    for name, usage, description in _MEMORY_TOOL_GUIDANCE:
        if not _resource_enabled(caps, "tools", name):
            continue
        any_tool = True
        if usage:
            lines.append(f"- **{name}** ({usage}) — {description}")
        else:
            lines.append(f"- **{name}** — {description}")
    if not any_tool:
        return ""
    lines.append("")
    lines.append(
        "Rule: When the user asks about past conversations, search first — "
        "don't answer from model knowledge alone."
    )
    return "\n".join(lines)


def _get_file_ops_guideline(caps: dict[str, Any]) -> str:
    """Shell-based file-ops guideline, only when dedicated search tools are off."""
    has_shell = _resource_enabled(caps, "tools", "shell_execute")
    has_file_search = _resource_enabled(caps, "tools", "files_glob_search") or _resource_enabled(
        caps, "tools", "files_grep_search"
    )
    if has_shell and not has_file_search:
        return "Use shell_execute for file operations like ls, rg, find"
    return ""


def _get_workspace_context(workspace_id: str | None) -> str:
    """Build workspace context for the system prompt."""
    if not workspace_id:
        return ""
    try:
        from src.sdk.workspace_models import load_workspace
        ws = load_workspace(workspace_id)
        if ws is None or ws.id == "personal":
            return ""
        lines = [f"\n\n## Current Workspace: {ws.name}"]
        if ws.prompt:
            lines.append(ws.prompt)
        return "\n".join(lines)
    except Exception:
        return ""


SKILL_DESC_BUDGET = 1536


def _load_user_capabilities(user_id: str) -> dict[str, Any]:
    try:
        return load_user_capabilities(user_id)
    except Exception:
        return {"tools": {}, "skills": {}, "subagents": {}}


def _resource_enabled(caps: dict[str, Any], section: str, name: str) -> bool:
    return resource_enabled(caps, section, name)


def _get_skills_context(user_id: str, workspace_id: str = "personal") -> str:
    """Build a concise skills reference for the system prompt.

    Description text is capped at SKILL_DESC_BUDGET characters total.
    When over budget, skills with the lowest load count are dropped first.
    """
    try:
        from src.skills.registry import get_skill_registry

        registry = get_skill_registry(user_id=user_id)
        skills = registry.get_all_skills()
        if not skills:
            return ""

        from src.storage.paths import get_paths as _get_paths

        paths = _get_paths(user_id, workspace_id=workspace_id)
        caps = _load_user_capabilities(user_id)
        visible_skills = [
            s
            for s in skills
            if str(s.get("metadata", {}).get("disable_model_invocation", "")).lower()
            not in ("true", "1", "yes")
            and _resource_enabled(caps, "skills", s.get("name", ""))
        ]
        if not visible_skills:
            return ""

        # Sort by load count descending (most-used first), then alphabetically as tiebreaker
        def _sort_key(s: Any) -> tuple[Any, ...]:
            name = s.get("name", "")
            count = registry.get_load_count(name)
            return (-count, name)

        visible_skills.sort(key=_sort_key)

        # Account for header overhead toward budget
        header_lines = [
            "\n\n<available_skills>",
            "When a task matches a skill description below, call skills_load(name) "
            "first to get detailed instructions. After creating, editing, or deleting "
            "a SKILL.md file via files_* tools, call skills_reload() to refresh.",
            "",
            f"Skills directory (use files_write here to create skills): {paths.user_skills_dir()}",
            f"Subagents directory (use files_write here to create subagents): {paths.user_subagents_dir()}",
        ]
        header_overhead = sum(len(line) + 1 for line in header_lines) + 1  # +1 newlines

        entries: list[tuple[str, str]] = []
        total_chars = header_overhead
        for s in visible_skills:
            name = s.get("name", "")
            desc = s.get("description", "")
            entry = f"- **{name}**: {desc}"
            entry_len = len(entry) + 1  # +1 for trailing newline
            if total_chars + entry_len > SKILL_DESC_BUDGET:
                break
            entries.append((name, desc))
            total_chars += entry_len

        if not entries:
            return ""

        lines = list(header_lines)
        lines.append("")
        for name, desc in entries:
            lines.append(f"- **{name}**: {desc}")
        lines.append("</available_skills>")
        return "\n".join(lines)
    except Exception:
        return ""


async def create_sdk_loop(
    user_id: str,
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    session_id: str | None = None,
) -> AgentLoop:
    """Create an AgentLoop for a user with all wiring."""
    del workspace_id
    runtime_workspace_id = "personal"
    runtime_session_id = _normalize_session_id(session_id)
    _seed_default_workspace()
    t0 = time.monotonic()
    settings = get_settings()

    provider = get_cached_model_provider(model, provider_keys=provider_keys, user_id=user_id)
    provider_model: str = cast(
        str,
        getattr(provider, "model", None)
        or model
        or getattr(settings.agent, "model", "ollama:minimax-m2.5"),
    )
    provider_id = str(getattr(provider, "provider_id", None) or "ollama")
    model_id = f"{provider_id}:{provider_model}"
    t1 = time.monotonic()

    caps = _load_user_capabilities(user_id)
    tools = [td for td in get_native_tools() if _resource_enabled(caps, "tools", td.name)]

    t2 = time.monotonic()
    mcp_tools: list[Any] = []
    mcp_bridge = None
    try:
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        mcp_bridge = MCPToolBridge(user_id=user_id)
        mcp_count = await mcp_bridge.discover()
        if mcp_count > 0:
            mcp_tools = [
                td
                for td in mcp_bridge.get_tool_definitions()
                if _resource_enabled(caps, "tools", td.name)
            ]
            logger.info("sdk_runner.mcp_tools", {"count": mcp_count}, user_id=user_id)
    except Exception as e:
        logger.warning("sdk_runner.mcp_failed", {"error": str(e)}, user_id=user_id)

    all_tools = tools + mcp_tools
    t3 = time.monotonic()

    logger.info("sdk_runner.tools_loaded", {"count": len(all_tools)}, user_id=user_id)

    # Build tool index and separate core from searchable tools
    from src.sdk.tool_index import get_or_create_index
    from src.sdk.tools_core.tool_reload import tool_reload

    # Register tool_search and tool_reload as core tools
    from src.sdk.tools_core.tool_search import tool_search
    from src.sdk.tools_custom import CORE_TOOL_NAMES, is_core_tool

    core_tool_defs: list[ToolDefinition] = []

    for td in all_tools:
        if is_core_tool(td.name) or td.name in CORE_TOOL_NAMES:
            core_tool_defs.append(td)

    if _resource_enabled(caps, "tools", tool_search.name):
        core_tool_defs.append(tool_search)
    if _resource_enabled(caps, "tools", tool_reload.name):
        core_tool_defs.append(tool_reload)

    from src.storage.paths import get_paths as _get_paths

    paths = _get_paths(user_id, workspace_id=runtime_workspace_id)
    user_tools_dir = paths.user_tools_dir()
    workspace_tools_dir = None
    mcp_config = paths.user_mcp_config()

    idx = get_or_create_index(
        user_tools_dir, workspace_tools_dir, mcp_config,
        user_id=user_id, workspace_id=runtime_workspace_id,
    )

    # NOTE: do NOT call idx.clear() here. get_or_create_index already clears
    # when source hashes change (needs_reindex). A blanket clear here wipes the
    # persisted index on every new session, forcing a ~23s chromadb re-embedding
    # of all 82 tools. The idx.count() == 0 check below correctly skips
    # re-indexing when the persisted index already has data.

    if idx.count() == 0:
        for td in tools:
            if not is_core_tool(td.name):
                idx.index_tool(td, tool_type="native", namespace="native")

        # Index custom (TOOL.md) tools
        from src.sdk.tools_custom import find_tool_file, load_tool_meta, scan_tools_dir
        for td in scan_tools_dir(user_tools_dir):
            if not is_core_tool(td.name) and _resource_enabled(caps, "tools", td.name):
                tool_file = find_tool_file(td.name, user_tools_dir, workspace_tools_dir)
                reconstruct_data = {"command": "", "install": [], "tool_dir": ""}
                if tool_file:
                    meta = load_tool_meta(tool_file)
                    if meta:
                        reconstruct_data = {
                            "command": meta.get("command", ""),
                            "install": meta.get("install", []),
                            "tool_dir": str(tool_file.parent),
                        }
                idx.index_tool(td, tool_type="custom", namespace="custom",
                               reconstruct=reconstruct_data)

        # Index MCP tools
        for td in mcp_tools:
            if not is_core_tool(td.name):
                parts = td.name.split("__", 2)
                server_name = parts[1] if len(parts) == 3 else ""
                reconstruct = {"server_name": server_name, "mcp_tool_name": td.name}
                idx.index_tool(td, tool_type="mcp", namespace=f"mcp__{server_name}",
                               reconstruct=reconstruct)

    summary_config = settings.memory.summarization

    # User-configured summarization model wins over the host config; falls
    # back to the agent model when neither is set.
    from src.config.user_settings_service import load_saved_user_settings

    _saved_settings = load_saved_user_settings(user_id)
    summarization_model = (
        _saved_settings.summarization_model
        if _saved_settings is not None and _saved_settings.summarization_model
        else (summary_config.model or model_id)
    )

    middlewares: list[Any] = []

    if summary_config.enabled:
        from src.storage.messages import get_message_store

        async def _persist_summary(
            context: CompressionContext, artifact: CompressionArtifact
        ) -> SummaryPersistenceResult:
            if context.session_id != runtime_session_id:
                logger.warning(
                    "summarization.persist_failed",
                    {
                        "status": PersistenceStatus.FAILED.value,
                        "summarized_count": artifact.summarized_message_count,
                        "preserved_count": artifact.preserved_message_count,
                        "error_type": "session_mismatch",
                    },
                    user_id=user_id,
                )
                return SummaryPersistenceResult(status=PersistenceStatus.FAILED)
            if not artifact.persistence_eligible:
                return SummaryPersistenceResult(status=PersistenceStatus.NOT_REQUESTED)
            try:
                store = get_message_store(user_id)
                summary_id = store.add_summary_message(
                    artifact.summary,
                    session_id=runtime_session_id,
                    metadata={
                        "source": "summarization_middleware",
                        "compression_reason": context.reason.value,
                        "summarized_message_ids": list(artifact.summarized_message_ids),
                        "preserved_message_ids": list(artifact.preserved_message_ids),
                    },
                )
                if not summary_id:
                    raise ValueError("summary store returned an empty ID")
                logger.info(
                    "summarization.persisted",
                    {
                        "status": PersistenceStatus.SUCCEEDED.value,
                        "summarized_count": artifact.summarized_message_count,
                        "preserved_count": artifact.preserved_message_count,
                    },
                    user_id=user_id,
                )
                return SummaryPersistenceResult(
                    status=PersistenceStatus.SUCCEEDED, summary_id=summary_id
                )
            except Exception as exc:
                logger.warning(
                    "summarization.persist_failed",
                    {
                        "status": PersistenceStatus.FAILED.value,
                        "summarized_count": artifact.summarized_message_count,
                        "preserved_count": artifact.preserved_message_count,
                        "error_type": type(exc).__name__,
                    },
                    user_id=user_id,
                )
                return SummaryPersistenceResult(status=PersistenceStatus.FAILED)

        middlewares.append(
            SummarizationMiddleware(
                model=summarization_model,
                trigger=summary_config.get_trigger(),
                keep=summary_config.get_keep(),
                trim_tokens_to_summarize=summary_config.trim_tokens_to_summarize,
                prompt_file=summary_config.prompt_file,
                user_id=user_id,
                summary_sink=_persist_summary,
            )
        )

    # Verification (rubric) grading is handled by RunService, which creates its
    # own RubricMiddleware and calls grade() after the main loop runs. RubricMiddleware
    # is not a Middleware subclass (no name/wrap_tool_call), so it must NOT be added
    # to the loop's middlewares list — doing so crashes every tool call.

    t4 = time.monotonic()

    loop = AgentLoop(
        provider=provider,
        tools=core_tool_defs,
        system_prompt=_get_system_prompt(user_id),
        middlewares=middlewares,
        user_id=user_id,
        workspace_id=runtime_workspace_id,
        model_id=model_id,
    )

    # The flow identity (used by compression contexts and middleware reruns)
    # must carry the session this loop was created for; the legacy
    # run_sdk_agent/run_sdk_agent_stream set these after get_sdk_loop, but the
    # RunService path never did — leaving _flow_session_id at its "default"
    # fallback and failing summarization persistence with session_mismatch.
    loop._flow_user_id = user_id  # type: ignore[attr-defined]
    loop._flow_session_id = runtime_session_id  # type: ignore[attr-defined]
    loop._flow_model = model_id  # type: ignore[attr-defined]
    loop._flow_attempt = 1  # type: ignore[attr-defined]

    loop._tool_index = idx
    total_in_index = idx.count()
    if total_in_index > 0:
        tool_hint = (
            f"\n\nYou have access to {total_in_index} additional tools across all categories. "
            "Use tool_search(description='what you need') to find and load a specific tool."
        )
        loop.system_prompt = (loop.system_prompt or "") + tool_hint

    # Tool preference hints: steer the model toward the right tool for common tasks
    # so it doesn't default to shell_execute for things that have dedicated tools.
    tool_prefs = (
        "\n\n## Tool Preferences\n"
        "- For fetching a URL or web page: use **web_fetch**, NOT shell_execute with curl.\n"
        "- For web search: use **web_search**, NOT shell_execute with curl.\n"
        "- For reading files: use **files_read**, NOT shell_execute with cat.\n"
        "- For listing files: use **files_list** or **files_glob_search**, NOT shell_execute with ls.\n"
        "- For writing files: use **files_write**, NOT shell_execute with echo/tee.\n"
        "- For searching file contents: use **files_grep_search**, NOT shell_execute with grep.\n"
        "- Use shell_execute only for commands that have no dedicated tool."
    )
    loop.system_prompt = (loop.system_prompt or "") + tool_prefs

    if mcp_bridge:
        loop._mcp_bridge = mcp_bridge  # type: ignore[attr-defined]

    t5 = time.monotonic()
    logger.info(
        "sdk_runner.create_timing",
        {
            "provider": f"{t1-t0:.3f}s",
            "tools": f"{t2-t1:.3f}s",
            "mcp": f"{t3-t2:.3f}s",
            "middleware": f"{t4-t3:.3f}s",
            "agentloop": f"{t5-t4:.3f}s",
            "total": f"{t5-t0:.3f}s",
        },
        user_id=user_id,
    )

    # Wrap with Langfuse if enabled
    lf_settings = get_settings()
    if (
        lf_settings.langfuse.enabled
        and lf_settings.langfuse.public_key
        and lf_settings.langfuse.secret_key
    ):
        from src.sdk.langfuse_tracer import LangfuseTracer

        if not LangfuseTracer.is_enabled():
            LangfuseTracer.init(
                public_key=lf_settings.langfuse.public_key,
                secret_key=lf_settings.langfuse.secret_key,
                host=lf_settings.langfuse.host,
            )
        if LangfuseTracer.is_enabled():
            loop = LangfuseTracer.wrap_loop(
                loop, user_id=user_id, session_id=runtime_session_id
            )

    return loop


async def get_sdk_loop(user_id: str, workspace_id: str = "personal", model: str | None = None, provider_keys: dict[str, str] | None = None, session_id: str | None = None) -> AgentLoop:
    """Get or create an AgentLoop for a user+workspace+model+session (cached).

    session_id isolates concurrent chat sessions per user — each session gets
    its own AgentLoop instance so that self.state and self.cancel_event are not
    clobbered across concurrent run_stream calls.
    """
    runtime_session_id = _normalize_session_id(session_id)
    cache_key = _loop_cache_key(
        user_id, workspace_id, model, provider_keys, runtime_session_id
    )
    async with _loop_lock:
        if cache_key not in _loop_cache:
            _loop_cache[cache_key] = await create_sdk_loop(
                user_id,
                workspace_id,
                model=model,
                provider_keys=provider_keys,
                session_id=runtime_session_id,
            )
            logger.info(
                "sdk_runner.loop_created",
                {
                    "user_id": user_id,
                    "workspace_id": workspace_id,
                    "model": model,
                    "session_id": runtime_session_id,
                },
                user_id=user_id,
            )
            _loop_cache.move_to_end(cache_key)
            if len(_loop_cache) > _MAX_LOOP_CACHE:
                _loop_cache.popitem(last=False)
        else:
            _loop_cache.move_to_end(cache_key)
        return _loop_cache[cache_key]


def _messages_from_conversation(messages: list[Any]) -> list[Message]:
    """Convert conversation store messages to SDK Messages.

    Tool messages without a preceding assistant tool_calls are skipped —
    the OpenAI/DeepSeek API requires that tool role messages follow an
    assistant message with tool_calls, and orphan tool results cause 400 errors.
    """
    sdk_messages: list[Message] = []
    pending_reasoning: str | None = None
    pending_reasoning_storage_id: str | None = None
    pending_reasoning_storage_ts: str | None = None
    pending_reasoning_storage_session_id: str | None = None
    pending_reasoning_source: str | None = None
    last_assistant_had_tool_calls = False
    for m in messages:
        role = getattr(m, "role", "user")
        content = getattr(m, "content", "")
        meta = getattr(m, "metadata", {}) or {}
        source = meta.get("source")
        ts = getattr(m, "ts", None)
        storage_id = getattr(m, "id", "")
        storage_ts = str(ts.isoformat()) if ts is not None else None
        storage_session_id = getattr(m, "session_id", "")
        if role == "user":
            sdk_messages.append(
                Message(
                    role="user",
                    content=content,
                    source=source,
                    storage_id=storage_id,
                    storage_ts=storage_ts,
                    storage_session_id=storage_session_id,
                )
            )
            pending_reasoning = None
            pending_reasoning_storage_id = None
            pending_reasoning_storage_ts = None
            pending_reasoning_storage_session_id = None
            pending_reasoning_source = None
            last_assistant_had_tool_calls = False
        elif role == "summary":
            sdk_messages.append(
                Message(
                    role="user",
                    content=f"[SUMMARY OF PREVIOUS CONVERSATION]\n{content}",
                    source=source or "summarization_middleware",
                    storage_id=storage_id,
                    storage_ts=storage_ts,
                    storage_session_id=storage_session_id,
                )
            )
            pending_reasoning = None
            pending_reasoning_storage_id = None
            pending_reasoning_storage_ts = None
            pending_reasoning_storage_session_id = None
            pending_reasoning_source = None
            last_assistant_had_tool_calls = False
        elif role == "system":
            sdk_messages.append(
                Message(
                    role="system",
                    content=content,
                    source=source,
                    storage_id=storage_id,
                    storage_ts=storage_ts,
                    storage_session_id=storage_session_id,
                )
            )
            pending_reasoning = None
            pending_reasoning_storage_id = None
            pending_reasoning_storage_ts = None
            pending_reasoning_storage_session_id = None
            pending_reasoning_source = None
            last_assistant_had_tool_calls = False
        elif role == "tool":
            meta = getattr(m, "metadata", {}) or {}
            tool_name = meta.get("tool_name") or meta.get("tool") or "unknown"
            tool_call_id = meta.get("tool_call_id") or meta.get("call_id") or ""
            if last_assistant_had_tool_calls:
                sdk_messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tool_call_id,
                        content=str(content or ""),
                        name=tool_name,
                        source=source,
                        storage_id=storage_id,
                        storage_ts=storage_ts,
                        storage_session_id=storage_session_id,
                    )
                )
            else:
                sdk_messages.append(
                    Message(
                        role="user",
                        content=f"[TOOL RESULT: {tool_name}]\n{content}",
                        source=source,
                        storage_id=storage_id,
                        storage_ts=storage_ts,
                        storage_session_id=storage_session_id,
                    )
                )
            pending_reasoning = None
            pending_reasoning_storage_id = None
            pending_reasoning_storage_ts = None
            pending_reasoning_storage_session_id = None
            pending_reasoning_source = None
            last_assistant_had_tool_calls = False
        elif role == "reasoning":
            pending_reasoning = content or None
            pending_reasoning_storage_id = storage_id
            pending_reasoning_storage_ts = storage_ts
            pending_reasoning_storage_session_id = storage_session_id
            pending_reasoning_source = source
        else:
            sdk_messages.append(
                Message(
                    role="assistant",
                    content=content,
                    reasoning=pending_reasoning,
                    source=source or pending_reasoning_source,
                    storage_id=storage_id or pending_reasoning_storage_id,
                    storage_ts=storage_ts or pending_reasoning_storage_ts,
                    storage_session_id=storage_session_id or pending_reasoning_storage_session_id,
                )
            )
            pending_reasoning = None
            pending_reasoning_storage_id = None
            pending_reasoning_storage_ts = None
            pending_reasoning_storage_session_id = None
            pending_reasoning_source = None
            last_assistant_had_tool_calls = False
    return sdk_messages


# ── Verification engine (single grading + rerun mechanism) ───────────────────
#
# Every agent execution path (RunService chat paths, webhooks, scheduler
# triggers) funnels through these two functions. The engine owns the
# attempt loop: run agent → grade → queue a rerun AgentEvent (producer) →
# fire it through the trigger registry (consumer) → the registered 'rerun'
# handler appends the revision feedback → the engine re-runs the agent in
# its own mode. Streaming executors stream the revision attempt natively.


@dataclasses.dataclass
class AttemptResult:
    """One agent attempt: its result messages plus the grader's verdict."""

    attempt: int
    messages: list[Message]
    evaluation: dict[str, Any] | None = None
    feedback: str | None = None


@dataclasses.dataclass
class VerificationResult:
    """Outcome of a full verification run (all attempts)."""

    attempts: list[AttemptResult]
    rubric_status: str = "off"  # TerminalRubricStatus value, or "off" when disabled
    rubric_available: bool = False
    max_attempts: int = 1
    grader_model_id: str | None = None


class ChunkItem:
    __slots__ = ("attempt", "chunk")

    def __init__(self, attempt: int, chunk: StreamChunk) -> None:
        self.attempt = attempt
        self.chunk = chunk


class GradeStartItem:
    __slots__ = ("attempt", "max_attempts")

    def __init__(self, attempt: int, max_attempts: int) -> None:
        self.attempt = attempt
        self.max_attempts = max_attempts


class GradeEndItem:
    __slots__ = ("attempt", "evaluation", "feedback", "terminal", "max_attempts")

    def __init__(
        self,
        attempt: int,
        evaluation: dict[str, Any] | None,
        feedback: str | None,
        terminal: bool,
        max_attempts: int,
    ) -> None:
        self.attempt = attempt
        self.evaluation = evaluation
        self.feedback = feedback
        self.terminal = terminal
        self.max_attempts = max_attempts


class AttemptItem:
    __slots__ = (
        "attempt",
        "messages",
        "evaluation",
        "feedback",
        "rubric_status",
        "rubric_available",
        "max_attempts",
        "grader_model_id",
    )

    def __init__(
        self,
        attempt: int,
        messages: list[Message],
        evaluation: dict[str, Any] | None,
        feedback: str | None,
        rubric_status: str,
        rubric_available: bool,
        max_attempts: int,
        grader_model_id: str | None,
    ) -> None:
        self.attempt = attempt
        self.messages = messages
        self.evaluation = evaluation
        self.feedback = feedback
        self.rubric_status = rubric_status
        self.rubric_available = rubric_available
        self.max_attempts = max_attempts
        self.grader_model_id = grader_model_id


async def _fire_pending_reruns(
    loop: AgentLoop,
    user_id: str,
    session_id: str,
    model: str | None,
) -> list[list[Message]]:
    """Fire queued rerun events via the trigger registry.

    Returns the feedback-appended message lists of the fired rerun events
    (in order). The registered 'rerun' handler appends the feedback to
    event.metadata["previous_messages"]; the executor uses the last list as
    the next attempt's input.
    """
    if loop.state is None:
        return []
    extra = getattr(loop.state, "extra", None)
    if extra is None:
        return []
    events = extra.pop("_pending_rerun_events", [])
    if not events:
        return []
    from src.sdk.loops.events import get_trigger_registry

    registry = get_trigger_registry()
    message_lists: list[list[Message]] = []
    for event in events:
        event.user_id = user_id
        event.session_id = session_id
        event.model = model
        try:
            await registry.fire(event)
        except KeyError:
            logger.warning(
                "sdk_runner.no_rerun_handler",
                {"trigger_type": event.trigger_type},
                user_id=user_id,
            )
            continue
        except Exception as exc:
            logger.error(
                "sdk_runner.rerun_failed", {"error": str(exc)}, user_id=user_id
            )
            continue
        if event.trigger_type == "rerun":
            prev = event.metadata.get("previous_messages")
            if prev is not None:
                message_lists.append(list(prev))
    return message_lists


def _last_assistant_message(messages: list[Message]) -> Message | None:
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg
    return None


def _executed_tool_names(messages: list[Message]) -> list[str]:
    """Tool names actually called in the run (from assistant tool calls)."""
    names: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.name and tc.name not in seen:
                    seen.add(tc.name)
                    names.append(tc.name)
    return names


async def _maybe_skip_verification(
    loop: AgentLoop,
    input_messages: list[Message],
    result_messages: list[Message],
    user_id: str,
) -> bool:
    """C11 auto-mode decision: True when the grader should be skipped.

    Deterministic signals only — no extra LLM calls. Logs the skip reason
    for auditability. Best-effort: any failure → verify (fail closed).
    """
    from src.config import get_settings as _get_settings
    from src.sdk.verification_policy import (
        VerificationPolicy,
        VerificationSignals,
        detect_code,
        detect_risk_keywords,
        should_verify,
        skip_reason,
    )

    try:
        vcfg = _get_settings().verification
        policy = VerificationPolicy(
            mode="auto",
            skip_max_response_chars=vcfg.skip_max_response_chars,
            verify_min_history_tokens=vcfg.verify_min_history_tokens,
            verify_min_response_chars=vcfg.verify_min_response_chars,
            risk_keywords=tuple(vcfg.risk_keywords or ()),
        )

        last_assistant = _last_assistant_message(result_messages)
        response_text = (
            last_assistant.content if last_assistant and isinstance(last_assistant.content, str) else ""
        )
        tool_names = _executed_tool_names(result_messages)
        destructive_used = False
        for name in tool_names:
            td = loop._registry.get(name)
            if td is not None and getattr(td, "annotations", None) is not None:
                if getattr(td.annotations, "destructive", False):
                    destructive_used = True
                    break
        prompt_text = " ".join(
            str(m.content) for m in input_messages if m.role == "user" and isinstance(m.content, str)
        )
        history_tokens = 0
        if last_assistant is not None and getattr(last_assistant, "usage", None) is not None:
            history_tokens = int(getattr(last_assistant.usage, "input_tokens", 0) or 0)

        signals = VerificationSignals(
            tool_names=tool_names,
            destructive_tool_used=destructive_used,
            response_chars=len(response_text),
            history_tokens=history_tokens,
            has_code=detect_code(response_text),
            risk_keyword_hit=detect_risk_keywords(
                response_text + " " + prompt_text, policy.risk_keywords
            ),
            run_failed=False,
        )
        if not should_verify(signals, policy):
            reason = skip_reason(signals, policy)
            logger.info(
                "verification.skipped",
                {"reason": reason, "response_chars": len(response_text)},
                user_id=user_id,
            )
            return True
        return False
    except Exception:
        # Fail closed: any error in the decision means verify as usual.
        return False


def _current_turn_messages(messages: list[Message]) -> list[Message]:
    """The current turn: the last user message and everything after it.

    The grader must see ONLY the turn it is grading (the request being
    verified plus the attempt's reasoning/tool/assistant output). Passing
    the whole conversation lets the grader misattribute prior turns to the
    current work — a phantom-verdict loop where an old turn's claims get
    graded against the new request (the Gong Cha joke incident).
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            return messages[i:]
    return list(messages)


async def _verification_engine(
    loop: AgentLoop,
    messages: list[Message],
    user_id: str,
    session_id: str,
    rubric: str | None,
    model: str | None,
    is_cancelled: collections.abc.Callable[[], bool] | None,
    stream: bool,
    mode: str | None = None,
) -> collections.abc.AsyncIterator[Any]:
    """Core verification attempt loop.

    In stream mode, agent chunks are yielded as ChunkItem so the caller
    can forward them live; in non-stream mode the agent runs via loop.run()
    and no chunk items are produced. Reruns always go through the trigger
    registry (queue → fire → handler appends feedback → next attempt).
    """
    from src.sdk import middleware_rubric as _mw
    from src.sdk.run_models import (
        RubricEvaluationResult as _EvalResult,
    )

    # Selective verification (C11): per-request mode wins, else settings.
    if mode is None:
        from src.config import get_settings as _get_settings

        mode = _get_settings().verification.mode or "off"

    rubric_mw = await _mw.load_rubric_middleware(user_id, loop, rubric)
    max_attempts = rubric_mw.max_iterations if rubric_mw else 1
    rubric_available = rubric_mw is not None
    grader_model_id = (
        getattr(rubric_mw, "grader_model_id", None) if rubric_mw else None
    )

    attempt = 1
    evaluations: list[dict[str, Any]] = []
    terminal_status = "not_run"
    input_messages = list(messages)
    # Set when the first grading phase starts; callers use it to map a
    # mid-verification exception (run FAILED) to rubric CANCELLED instead
    # of leaving verification "not_run". Reset per engine run.
    loop._rubric_started = False  # type: ignore[attr-defined]

    while True:
        if is_cancelled is not None and is_cancelled():
            terminal_status = "cancelled"
            yield AttemptItem(
                attempt, input_messages, None, None, terminal_status,
                rubric_available, max_attempts, grader_model_id,
            )
            break

        # 1. Run one attempt.
        result_messages: list[Message] = []
        if stream:
            async for chunk in loop.run_stream(input_messages):
                yield ChunkItem(attempt, chunk)
            result_messages = list(loop.state.messages) if loop.state else []
        else:
            result_messages = await loop.run(input_messages)

        # 2. Drain middleware-queued rerun events from this run.
        fired = await _fire_pending_reruns(loop, user_id, session_id, model)
        if fired:
            input_messages = fired[-1]
            attempt += 1
            continue

        # 3. Grade when verification is active.
        if rubric_mw is None:
            terminal_status = "off"
            yield AttemptItem(
                attempt, result_messages, None, None, terminal_status,
                rubric_available, max_attempts, grader_model_id,
            )
            break

        # No assistant output means the attempt failed — verification never
        # ran, so no grading phase starts (no dangling rubric_start event).
        last_assistant = _last_assistant_message(result_messages)
        if last_assistant is None:
            terminal_status = "failed"
            yield AttemptItem(
                attempt, result_messages, None, None, terminal_status,
                rubric_available, max_attempts, grader_model_id,
            )
            break

        if is_cancelled is not None and is_cancelled():
            terminal_status = "cancelled"
            yield AttemptItem(
                attempt, result_messages, None, None, terminal_status,
                rubric_available, max_attempts, grader_model_id,
            )
            break

        # Selective verification (C11): in auto mode, skip the grader for
        # trivial turns. Deterministic decision from run signals — zero
        # extra LLM calls. The skip happens BEFORE GradeStartItem, so the
        # client never sees a "Checking rubric…" state (native-app stream
        # watchdog unaffected: the stream just ends earlier with done).
        if mode == "auto":
            _skip = await _maybe_skip_verification(
                loop, input_messages, result_messages, user_id
            )
            if _skip:
                terminal_status = "skipped"
                yield AttemptItem(
                    attempt, result_messages, None, None, terminal_status,
                    rubric_available, max_attempts, grader_model_id,
                )
                break

        yield GradeStartItem(attempt, max_attempts)
        loop._rubric_started = True  # type: ignore[attr-defined]

        # Grade ONLY the current turn: the current user prompt plus this
        # attempt's full output (reasoning, tool messages WITH results, and
        # the final answer). Passing the whole conversation lets the grader
        # misattribute prior turns to the current work (phantom verdicts),
        # and omitting the run's tool messages leaves claims unverifiable.
        evaluation = await rubric_mw.grade(
            _current_turn_messages(result_messages), attempt - 1
        )
        evaluations.append(evaluation)
        raw_result = evaluation["result"]
        eval_result = (
            _EvalResult.INVALID_RUBRIC
            if raw_result == "failed"
            else _EvalResult(raw_result)
        )

        if eval_result in (
            _EvalResult.SATISFIED,
            _EvalResult.INVALID_RUBRIC,
            _EvalResult.GRADER_ERROR,
        ) or attempt >= max_attempts:
            terminal_status = {
                _EvalResult.SATISFIED: "satisfied",
                _EvalResult.INVALID_RUBRIC: "invalid_rubric",
                _EvalResult.GRADER_ERROR: "grader_error",
            }.get(eval_result, "max_attempts_reached")
            yield GradeEndItem(attempt, evaluation, None, True, max_attempts)
            yield AttemptItem(
                attempt, result_messages, evaluation, None, terminal_status,
                rubric_available, max_attempts, grader_model_id,
            )
            break

        feedback = _mw._revision_prompt(evaluation)
        yield GradeEndItem(attempt, evaluation, feedback, False, max_attempts)

        # 4. Producer: queue the rerun event for the registry.
        _mw.queue_rerun_event(
            loop,
            feedback,
            user_id,
            session_id,
            model,
            attempt,
            max_attempts,
            input_messages,
            stream=stream,
        )

        # 5. Consumer: fire queued events; the handler appends the feedback.
        fired = await _fire_pending_reruns(loop, user_id, session_id, model)
        if not fired:
            terminal_status = "max_attempts_reached"
            yield AttemptItem(
                attempt, result_messages, evaluation, None, terminal_status,
                rubric_available, max_attempts, grader_model_id,
            )
            break
        input_messages = fired[-1]
        yield AttemptItem(
            attempt, result_messages, evaluation, feedback, "needs_revision",
            rubric_available, max_attempts, grader_model_id,
        )
        attempt += 1

    # Store the verdict for the runner's finally block (loop._verification_verdict).
    if loop.state is not None and hasattr(loop.state, "extra"):
        loop.state.extra["_rubric_status"] = terminal_status
        loop.state.extra["_rubric_iterations"] = len(evaluations)
        loop.state.extra["_rubric_evaluations"] = evaluations


async def run_with_verification_stream(
    loop: AgentLoop,
    messages: list[Message],
    user_id: str,
    session_id: str,
    rubric: str | None = None,
    model: str | None = None,
    is_cancelled: collections.abc.Callable[[], bool] | None = None,
    mode: str | None = None,
) -> collections.abc.AsyncIterator[Any]:
    """Streaming verification run: yields chunks + attempt/grade markers."""
    async for item in _verification_engine(
        loop, messages, user_id, session_id, rubric, model, is_cancelled, stream=True, mode=mode
    ):
        yield item


async def run_with_verification(
    loop: AgentLoop,
    messages: list[Message],
    user_id: str,
    session_id: str,
    rubric: str | None = None,
    model: str | None = None,
    is_cancelled: collections.abc.Callable[[], bool] | None = None,
    mode: str | None = None,
) -> VerificationResult:
    """Non-streaming verification run: returns all attempts + verdict."""
    attempts: list[AttemptResult] = []
    rubric_status = "off"
    rubric_available = False
    max_attempts = 1
    grader_model_id: str | None = None
    async for item in _verification_engine(
        loop, messages, user_id, session_id, rubric, model, is_cancelled, stream=False, mode=mode
    ):
        if isinstance(item, AttemptItem):
            attempts.append(
                AttemptResult(
                    attempt=item.attempt,
                    messages=item.messages,
                    evaluation=item.evaluation,
                    feedback=item.feedback,
                )
            )
            rubric_status = item.rubric_status
            rubric_available = item.rubric_available
            max_attempts = item.max_attempts
            grader_model_id = item.grader_model_id
    return VerificationResult(
        attempts=attempts,
        rubric_status=rubric_status,
        rubric_available=rubric_available,
        max_attempts=max_attempts,
        grader_model_id=grader_model_id,
    )


async def run_sdk_agent(
    user_id: str,
    messages: list[Message],
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    session_id: str | None = None,
    rubric: str | None = None,
) -> list[Message]:
    """Run the SDK agent loop to completion.

    Args:
        user_id: User identifier.
        messages: Conversation history as SDK Messages.
        workspace_id: Current workspace ID.
        model: Optional model override.
        provider_keys: Optional per-provider API keys from frontend.
        session_id: Optional session ID for per-session loop isolation.
        rubric: Optional rubric for verification (overrides user default).

    Returns:
        Final message list from the agent.
    """
    runtime_session_id = _normalize_session_id(session_id)
    loop = await get_sdk_loop(
        user_id,
        workspace_id,
        model=model,
        provider_keys=provider_keys,
        session_id=runtime_session_id,
    )
    register_user_loop(user_id, loop, session_id=runtime_session_id)
    loop.rubric = rubric
    loop._flow_user_id = user_id  # type: ignore[attr-defined]
    loop._flow_session_id = runtime_session_id  # type: ignore[attr-defined]
    loop._flow_model = loop.model_id  # type: ignore[attr-defined]
    loop._flow_attempt = 1  # type: ignore[attr-defined]
    try:
        # Run-level trace root: agent_run and grader_run both nest under it.
        from src.sdk.langfuse_tracer import LangfuseTracer

        with LangfuseTracer.trace_run(user_id, runtime_session_id):
            vresult = await run_with_verification(
                loop,
                messages,
                user_id,
                runtime_session_id,
                rubric=rubric,
                model=loop.model_id,
            )
        result = vresult.attempts[-1].messages if vresult.attempts else messages

        # Persist RunOutcome for loop 4 (hill-climbing)
        await _persist_run_outcome(user_id, runtime_session_id, result, loop, "manual")
        # Flush Langfuse traces if enabled
        LangfuseTracer.flush()
        return result
    finally:
        # Store verification verdict on loop before unregister so router can read it
        if loop.state and loop.state.extra.get("_rubric_status"):
            loop._verification_verdict = {  # type: ignore[attr-defined]
                "status": loop.state.extra.get("_rubric_status"),
                "iterations": loop.state.extra.get("_rubric_iterations", 0),
                "evaluations": loop.state.extra.get("_rubric_evaluations", []),
            }
        else:
            loop._verification_verdict = None  # type: ignore[attr-defined]
        loop.rubric = None
        unregister_user_loop(user_id, loop, session_id=runtime_session_id)


async def run_sdk_agent_stream(
    user_id: str,
    messages: list[Message],
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    cancel_event: asyncio.Event | None = None,
    session_id: str | None = None,
    rubric: str | None = None,
) -> Any:
    runtime_session_id = _normalize_session_id(session_id)
    loop = await get_sdk_loop(
        user_id,
        workspace_id,
        model=model,
        provider_keys=provider_keys,
        session_id=runtime_session_id,
    )
    loop.cancel_event = cancel_event
    loop.rubric = rubric
    loop._flow_user_id = user_id  # type: ignore[attr-defined]
    loop._flow_session_id = runtime_session_id  # type: ignore[attr-defined]
    loop._flow_model = loop.model_id  # type: ignore[attr-defined]
    loop._flow_attempt = 1  # type: ignore[attr-defined]
    register_user_loop(user_id, loop, session_id=runtime_session_id)

    final_messages: list[Message] = list(messages)
    try:
        # Run-level trace root covering the whole stream (agent + grader).
        from src.sdk.langfuse_tracer import LangfuseTracer

        with LangfuseTracer.trace_run(user_id, runtime_session_id):
            async for item in run_with_verification_stream(
                loop,
                messages,
                user_id,
                runtime_session_id,
                rubric=rubric,
                model=loop.model_id,
            ):
                if isinstance(item, ChunkItem):
                    yield item.chunk
                elif isinstance(item, AttemptItem):
                    final_messages = item.messages
    except Exception as e:
        logger.error("sdk_runner.stream_error", {"error": str(e)}, user_id=user_id)
        yield StreamChunk.error(message=str(e))
    finally:
        # Grader providers are created fresh per run (audit S3 keeps them out
        # of the keyed cache) — close them deterministically.
        if rubric_mw is not None:
            try:
                await rubric_mw.aclose()
            except Exception:
                pass
        # Persist RunOutcome for loop 4 (hill-climbing)
        await _persist_run_outcome(
            user_id, runtime_session_id, final_messages, loop, "manual"
        )
        # Store verification verdict on loop so the router can read it
        if loop.state and loop.state.extra.get("_rubric_status"):
            loop._verification_verdict = {  # type: ignore[attr-defined]
                "status": loop.state.extra.get("_rubric_status"),
                "iterations": loop.state.extra.get("_rubric_iterations", 0),
                "evaluations": loop.state.extra.get("_rubric_evaluations", []),
            }
        else:
            loop._verification_verdict = None  # type: ignore[attr-defined]
        # Flush Langfuse traces if enabled
        from src.sdk.langfuse_tracer import LangfuseTracer as _LangfuseTracer

        _LangfuseTracer.flush()
        loop.rubric = None
        unregister_user_loop(user_id, loop, session_id=runtime_session_id)


def reset_sdk_loop(
    user_id: str = "default_user",
    workspace_id: str = "personal",
    session_id: str | None = None,
) -> int:
    """Reset cached SDK agent loops for a user and optionally a specific session.

    workspace_id is compatibility-only. When session_id is given, only that
    session's cached loops are removed; when omitted, all cached loops for the
    user are removed.
    """
    del workspace_id
    removed = 0
    cache_prefix = f"{user_id}:"
    if session_id is not None:
        normalized_session_id = _normalize_session_id(session_id)
        for cache_key in list(_loop_cache):
            if not cache_key.startswith(cache_prefix) or ":session:" not in cache_key:
                continue
            key_session = cache_key.split(":session:", 1)[1].split(":keys:", 1)[0]
            if key_session == normalized_session_id:
                del _loop_cache[cache_key]
                removed += 1
    else:
        for cache_key in list(_loop_cache):
            if cache_key.startswith(cache_prefix):
                del _loop_cache[cache_key]
                removed += 1
    logger.info(
        "sdk_runner.loop_reset",
        {"user_id": user_id, "session_id": session_id, "removed": removed},
        user_id=user_id,
    )
    return removed


def reset_user_sdk_loops(user_id: str, reason: str | None = None) -> int:
    """Reset all cached SDK agent loops for a user."""
    removed = 0
    cache_prefix = f"{user_id}:"
    for cache_key in list(_loop_cache):
        if cache_key.startswith(cache_prefix):
            del _loop_cache[cache_key]
            removed += 1
    logger.info(
        "sdk_runner.user_loops_reset",
        {"user_id": user_id, "reason": reason, "removed": removed},
        user_id=user_id,
    )
    return removed


def reset_all_sdk_loops() -> None:
    """Reset all cached agent loops."""
    _loop_cache.clear()
    logger.info("sdk_runner.all_loops_reset", {})


async def _persist_run_outcome(
    user_id: str,
    session_id: str | None,
    result_messages: list[Message],
    loop: AgentLoop,
    trigger_type: str = "manual",
) -> None:
    """Persist a RunOutcome for loop 4 (hill-climbing analysis)."""
    try:
        from src.sdk.loops.storage import (
            LoopEngineeringDB,
            RunOutcome,
            get_loop_engineering_db_path,
        )

        response_text = ""
        for msg in reversed(result_messages):
            if msg.role == "assistant" and isinstance(msg.content, str):
                response_text = msg.content
                break

        verification_status: str | None = None
        verification_iterations = 0
        verification_evaluations: list[dict[str, Any]] = []
        if loop.state and loop.state.extra:
            verification_status = loop.state.extra.get("_rubric_status")
            verification_iterations = loop.state.extra.get("_rubric_iterations", 0)
            verification_evaluations = loop.state.extra.get("_rubric_evaluations", [])

        import time
        import uuid

        outcome = RunOutcome(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            session_id=_normalize_session_id(session_id),
            trigger_type=trigger_type,
            response=response_text[:1000],
            verification_status=verification_status,
            verification_iterations=verification_iterations,
            verification_evaluations=verification_evaluations,
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
            model=getattr(loop.provider, "model", "unknown"),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

        db = LoopEngineeringDB(get_loop_engineering_db_path(user_id))
        await db.init()
        await db.save_run_outcome(outcome)
    except Exception as e:
        logger.warning("run_outcome.persist_failed", {"error": str(e)}, user_id=user_id)
