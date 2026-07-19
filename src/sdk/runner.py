"""SDK Agent Runner — creates and runs AgentLoop with proper wiring.

This is the bridge between the HTTP layer and the SDK AgentLoop.
It handles:
  - Creating LLM provider from config
  - Loading SDK-native tools (no more LangChain adapter)
  - Loading MCP tools via MCPToolBridge
  - Loading connector tools via ConnectKitBridge
  - Assembling SDK middlewares (memory, summarization)
  - Converting between WS protocol messages and StreamChunks
  - Thread-safe per-user agent instances

Skills are now discovery-based: available skill names are embedded
in the skills_list tool description dynamically, not in the system prompt.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.loop import AgentLoop
from src.sdk.messages import Message, StreamChunk
from src.sdk.middleware_summarization import SummarizationMiddleware
from src.sdk.native_tools import get_native_tools
from src.sdk.providers.factory import create_model_from_config
from src.sdk.tools import ToolAnnotations, ToolDefinition
from src.sdk.user_prompt import load_user_prompt
from src.storage.paths import DataPaths

logger = get_logger()

import collections

_MAX_LOOP_CACHE = 50
_loop_cache: collections.OrderedDict[str, AgentLoop] = collections.OrderedDict()
_loop_lock = asyncio.Lock()

_user_loops: dict[str, AgentLoop] = {}


def register_user_loop(user_id: str, loop: AgentLoop) -> None:
    _user_loops[user_id] = loop


def unregister_user_loop(user_id: str) -> None:
    _user_loops.pop(user_id, None)


def get_user_loop(user_id: str) -> AgentLoop | None:
    return _user_loops.get(user_id)


def _loop_cache_key(
    user_id: str,
    workspace_id: str,
    model: str | None,
    provider_keys: dict[str, str] | None = None,
    session_id: str | None = None,
) -> str:
    key = f"{user_id}:{workspace_id}:{model or 'default'}"
    if session_id:
        key = f"{key}:session:{session_id}"
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
    """Seed AGENTS.md from src/prompt_seed/ on first access."""
    prompt_path = DataPaths(user_id=user_id).user_prompt_path()
    marker = prompt_path.parent / ".prompt_seeded"
    if prompt_path.exists() or marker.exists():
        return
    seed = Path("src/prompt_seed/AGENTS.md")
    if seed.exists():
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    marker.write_text("", encoding="utf-8")


def _get_system_prompt(user_id: str, workspace_id: str | None = None) -> str:
    w_id = workspace_id or "personal"

    _ensure_prompt_seeded(user_id)
    user_prompt_context = _get_user_prompt_context(user_id)
    skills_context = _get_skills_context(user_id, w_id)
    workspace_context = _get_workspace_context(workspace_id)
    connector_context = _get_connector_context(user_id)

    memory_context = """\
## Memory Recall Strategy
### Tool selection
- **message_search** (use FIRST, before saying you don't know) — Full session context for specific facts, names, dates, plans, past decisions
- **message_count** (use FOR "how many" questions) — Deterministic counting of distinct items across sessions
- **message_timeline** (use FOR temporal reasoning) — Find dates of events, calculate "how many days between X and Y"
- **memory_profile** — Observations the Observer collected (may be empty)
- **memory_reflection** — Synthesized patterns from the Reflector (10+ obs, 24h min)

Rule: When the user asks about past conversations, search first — don't answer from model knowledge alone."""

    sections = [
        user_prompt_context,
        skills_context,
        workspace_context,
        connector_context,
        memory_context,
    ]
    sections = [s for s in sections if s]
    body = "\n".join(sections)
    return body + f"\n\nuser_id: {user_id}"


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


def _get_skills_context(user_id: str, workspace_id: str = "personal") -> str:
    """Build a concise skills reference for the system prompt.

    Description text is capped at SKILL_DESC_BUDGET characters total.
    When over budget, skills with the lowest load count are dropped first.
    """
    try:
        from src.skills.registry import get_skill_registry

        registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
        skills = registry.get_all_skills()
        if not skills:
            return ""

        # Filter by item_scopes (All / Selected / None)
        from src.sdk.item_scopes import ItemScopeDB
        from src.storage.paths import get_paths as _get_paths

        paths = _get_paths(user_id, workspace_id=workspace_id)
        scope_db = ItemScopeDB(paths.base)
        excluded = scope_db.get_excluded_names(user_id, "skill")

        skills = [
            s for s in skills
            if s.get("name") not in excluded
        ]

        visible_skills = [
            s
            for s in skills
            if str(s.get("metadata", {}).get("disable_model_invocation", "")).lower()
            not in ("true", "1", "yes")
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


def _get_connector_context(user_id: str) -> str:
    """Inject connected connector info so the LLM knows about available tools."""
    try:
        from connectkit.bridge import ConnectKitBridge

        bridge = ConnectKitBridge(user_id=user_id)
        connected = bridge.connected_services()
        if not connected:
            return ""

        lines = [
            "\n\n## Connected SaaS Connectors",
            "IMPORTANT: All connectors below are ALREADY authorized and ready to use.",
            "Do NOT ask the user to approve or connect — just use the available tools directly.",
            "",
        ]
        for service in connected:
            ns = service.replace("-", "_")
            lines.append(f"- **{service}**: tools named `{ns}__*` are ready to call (e.g. `{ns}__gmail_messages_list`)")
        return "\n".join(lines)
    except ImportError:
        return ""
    except Exception:
        return ""


async def create_sdk_loop(user_id: str, workspace_id: str = "personal", model: str | None = None, provider_keys: dict[str, str] | None = None) -> AgentLoop:
    """Create an AgentLoop for a user with all wiring."""
    import time

    _seed_default_workspace()
    t0 = time.monotonic()
    settings = get_settings()
    model_str: str = cast(str, model or getattr(settings.agent, "model", "ollama:minimax-m2.5"))

    provider = create_model_from_config(model_str, provider_keys=provider_keys)
    t1 = time.monotonic()

    tools = get_native_tools()

    # Filter tools by per-workspace scope via item_scopes table
    from src.sdk.item_scopes import ItemScopeDB
    from src.storage.paths import get_paths as _get_paths

    paths = _get_paths(user_id, workspace_id=workspace_id)
    scope_db = ItemScopeDB(paths.base)
    excluded = scope_db.get_excluded_names(user_id, "tool")
    scoped_available = scope_db.get_available_names(user_id, "tool", workspace_id)

    def _tool_available_by_default(t: Any) -> bool:
        return True  # scope=all for unconfigured tools

    tools = [
        t for t in tools
        if t.name not in excluded and (
            t.name in scoped_available or _tool_available_by_default(t)
        )
    ]
    t2 = time.monotonic()

    mcp_tools: list[Any] = []
    mcp_bridge = None
    try:
        from src.sdk.tools_core.mcp_bridge import MCPToolBridge

        mcp_bridge = MCPToolBridge(user_id=user_id)
        mcp_count = await mcp_bridge.discover()
        if mcp_count > 0:
            mcp_tools = mcp_bridge.get_tool_definitions()
            logger.info("sdk_runner.mcp_tools", {"count": mcp_count}, user_id=user_id)
    except Exception as e:
        logger.warning("sdk_runner.mcp_failed", {"error": str(e)}, user_id=user_id)

    connector_tools: list[Any] = []
    connectkit_bridge = None
    try:
        from connectkit.bridge import ConnectKitBridge

        connectkit_bridge = ConnectKitBridge(user_id=user_id)
        await connectkit_bridge.discover()
        connector_tools = connectkit_bridge.get_tool_definitions()
        if connector_tools:
            logger.info(
                "sdk_runner.connector_tools",
                {"count": len(connector_tools)},
                user_id=user_id,
            )
    except Exception as e:
        logger.warning(
            "sdk_runner.connector_failed", {"error": str(e)}, user_id=user_id
        )

    connectkit_tool_defs = _connector_dicts_to_defs(connector_tools)
    all_tools = tools + mcp_tools + connectkit_tool_defs
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

    # Always include tool_search and tool_reload
    core_tool_defs.append(tool_search)
    core_tool_defs.append(tool_reload)

    paths = _get_paths(user_id, workspace_id=workspace_id)
    user_tools_dir = paths.user_tools_dir()
    workspace_tools_dir = paths.workspace_tools_dir() if workspace_id else None
    mcp_config = paths.user_mcp_config()

    idx = get_or_create_index(
        user_tools_dir, workspace_tools_dir, mcp_config,
        user_id=user_id, workspace_id=workspace_id,
        connectkit_bridge=connectkit_bridge,
    )

    if idx.count() == 0:
        # Index native tools
        for td in tools:
            if not is_core_tool(td.name):
                idx.index_tool(td, tool_type="native", namespace="native")

        # Index custom (TOOL.md) tools
        from src.sdk.tools_custom import find_tool_file, get_custom_tools, load_tool_meta
        for td in get_custom_tools(user_id=user_id, workspace_id=workspace_id):
            if not is_core_tool(td.name):
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

        # Index connector tools
        for td in connectkit_tool_defs:
            if not is_core_tool(td.name):
                namespace = td.name.split("__")[0] if "__" in td.name else "connector"
                reconstruct = {"namespace": namespace, "tool_name": td.name}
                idx.index_tool(td, tool_type="connector", namespace=namespace,
                               reconstruct=reconstruct)

    summary_config = settings.memory.summarization

    middlewares: list[Any] = []

    if summary_config.enabled:
        from src.storage.messages import get_message_store

        async def _persist_summary(content: str) -> None:
            try:
                store = get_message_store(user_id, workspace_id)
                store.add_summary_message(content)
                logger.info(
                    "summarization.persisted",
                    {"summary_length": len(content)},
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(
                    "summarization.persist_failed",
                    {"error": str(e)},
                    user_id=user_id,
                )

        middlewares.append(
            SummarizationMiddleware(
                trigger_tokens=summary_config.trigger_tokens,
                keep_tokens=summary_config.keep_tokens,
                model=model_str,
                on_summarize=_persist_summary,
            )
        )

    t4 = time.monotonic()

    loop = AgentLoop(
        provider=provider,
        tools=core_tool_defs,
        system_prompt=_get_system_prompt(user_id, workspace_id),
        middlewares=middlewares,
        user_id=user_id,
        workspace_id=workspace_id,
    )

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

    if connectkit_bridge:
        loop._connectkit_bridge = connectkit_bridge  # type: ignore[attr-defined]

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

    return loop


async def get_sdk_loop(user_id: str, workspace_id: str = "personal", model: str | None = None, provider_keys: dict[str, str] | None = None, session_id: str | None = None) -> AgentLoop:
    """Get or create an AgentLoop for a user+workspace+model+session (cached).

    session_id isolates concurrent chat sessions per user — each session gets
    its own AgentLoop instance so that self.state and self.cancel_event are not
    clobbered across concurrent run_stream calls.
    """
    cache_key = _loop_cache_key(user_id, workspace_id, model, provider_keys, session_id)
    async with _loop_lock:
        if cache_key not in _loop_cache:
            _loop_cache[cache_key] = await create_sdk_loop(
                user_id, workspace_id, model=model, provider_keys=provider_keys
            )
            logger.info("sdk_runner.loop_created", {"user_id": user_id, "workspace_id": workspace_id, "model": model, "session_id": session_id}, user_id=user_id)
            _loop_cache.move_to_end(cache_key)
            if len(_loop_cache) > _MAX_LOOP_CACHE:
                _loop_cache.popitem(last=False)
        else:
            _loop_cache.move_to_end(cache_key)
        return _loop_cache[cache_key]


def _connector_dicts_to_defs(dicts: list[dict[str, Any]]) -> list[ToolDefinition]:
    """Convert connectkit tool dicts to SDK ToolDefinition objects.

    Connector adapters produce plain dicts (to avoid depending on the EA SDK).
    This function converts them to proper ToolDefinition instances.
    """
    defs = []
    for d in dicts:
        annotations = ToolAnnotations(
            title=d.get("annotations", {}).get("title", d["name"]),
            read_only=d.get("annotations", {}).get("read_only", False),
            destructive=d.get("annotations", {}).get("destructive", False),
            idempotent=d.get("annotations", {}).get("idempotent", False),
        )
        func = d.get("function")
        ainvoke = d.get("ainvoke")

        td = ToolDefinition(
            name=d["name"],
            description=d["description"],
            parameters=d.get("parameters", {}),
            annotations=annotations,
            function=func,
        )
        if ainvoke:
            td._coroutine = ainvoke
        defs.append(td)
    return defs


def _messages_from_conversation(messages: list[Any]) -> list[Message]:
    """Convert conversation store messages to SDK Messages.

    Tool messages without a preceding assistant tool_calls are skipped —
    the OpenAI/DeepSeek API requires that tool role messages follow an
    assistant message with tool_calls, and orphan tool results cause 400 errors.
    """
    sdk_messages: list[Message] = []
    pending_reasoning: str | None = None
    last_assistant_had_tool_calls = False
    for m in messages:
        role = getattr(m, "role", "user")
        content = getattr(m, "content", "")
        if role == "user":
            sdk_messages.append(Message.user(content))
            pending_reasoning = None
            last_assistant_had_tool_calls = False
        elif role == "summary":
            sdk_messages.append(Message.user(f"[SUMMARY OF PREVIOUS CONVERSATION]\n{content}"))
            pending_reasoning = None
            last_assistant_had_tool_calls = False
        elif role == "system":
            sdk_messages.append(Message.system(content))
            pending_reasoning = None
            last_assistant_had_tool_calls = False
        elif role == "tool":
            if not last_assistant_had_tool_calls:
                # Skip orphan tool result — no preceding tool_calls to attach to
                continue
            meta = getattr(m, "metadata", {}) or {}
            tool_name = meta.get("tool_name") or meta.get("tool") or "unknown"
            tool_call_id = meta.get("tool_call_id") or meta.get("call_id") or ""
            sdk_messages.append(Message.tool_result(tool_call_id, content=str(content or ""), name=tool_name))
            pending_reasoning = None
        elif role == "reasoning":
            pending_reasoning = content or None
        else:
            sdk_messages.append(
                Message.assistant(content, reasoning=pending_reasoning)
            )
            pending_reasoning = None
            last_assistant_had_tool_calls = False
    return sdk_messages


async def run_sdk_agent(
    user_id: str,
    messages: list[Message],
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    session_id: str | None = None,
) -> list[Message]:
    """Run the SDK agent loop to completion.

    Args:
        user_id: User identifier.
        messages: Conversation history as SDK Messages.
        workspace_id: Current workspace ID.
        model: Optional model override.
        provider_keys: Optional per-provider API keys from frontend.
        session_id: Optional session ID for per-session loop isolation.

    Returns:
        Final message list from the agent.
    """
    loop = await get_sdk_loop(user_id, workspace_id, model=model, provider_keys=provider_keys, session_id=session_id)
    register_user_loop(user_id, loop)
    try:
        result = await loop.run(messages)
        return result
    finally:
        unregister_user_loop(user_id)


async def run_sdk_agent_stream(
    user_id: str,
    messages: list[Message],
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    cancel_event: asyncio.Event | None = None,
    session_id: str | None = None,
) -> Any:
    loop = await get_sdk_loop(user_id, workspace_id, model=model, provider_keys=provider_keys, session_id=session_id)
    loop.cancel_event = cancel_event
    register_user_loop(user_id, loop)

    try:
        async for chunk in loop.run_stream(messages):
            yield chunk
    except Exception as e:
        logger.error("sdk_runner.stream_error", {"error": str(e)}, user_id=user_id)
        yield StreamChunk.error(message=str(e))
    finally:
        unregister_user_loop(user_id)


def reset_sdk_loop(
    user_id: str = "default_user",
    workspace_id: str = "personal",
    session_id: str | None = None,
) -> None:
    """Reset the SDK agent loop for a user+workspace (and optionally a specific session).

    When session_id is given, only that session's cached loop is removed — other
    concurrently running sessions for the same user are left untouched.
    """
    if session_id:
        cache_key = _loop_cache_key(user_id, workspace_id, model=None, session_id=session_id)
        _loop_cache.pop(cache_key, None)
    else:
        cache_prefix = f"{user_id}:{workspace_id}:"
        for cache_key in list(_loop_cache):
            if cache_key.startswith(cache_prefix):
                del _loop_cache[cache_key]
    logger.info("sdk_runner.loop_reset", {"user_id": user_id, "workspace_id": workspace_id, "session_id": session_id}, user_id=user_id)


def reset_user_sdk_loops(user_id: str) -> None:
    """Reset all cached SDK agent loops for a user across workspaces."""
    cache_prefix = f"{user_id}:"
    for cache_key in list(_loop_cache):
        if cache_key.startswith(cache_prefix):
            del _loop_cache[cache_key]
    logger.info("sdk_runner.user_loops_reset", {"user_id": user_id}, user_id=user_id)


def reset_all_sdk_loops() -> None:
    """Reset all cached agent loops."""
    _loop_cache.clear()
    logger.info("sdk_runner.all_loops_reset", {})
