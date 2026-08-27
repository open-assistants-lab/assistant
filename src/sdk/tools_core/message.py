"""Message tools — search, count, and history for raw conversation data."""

import os
import re
from datetime import date, timedelta
from typing import Any, cast

from coremem.query import expand_queries

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.storage.messages import SearchResult, get_message_store
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()
_coremem_cache: dict[str, Any] = {}


# ── message core factory ──────────────────────────────────────────────────────

def _get_message_core(user_id: str, workspace_id: str = "personal") -> Any:
    """Get or create a cached MemoryCore instance for the given user/workspace.

    Uses the SAME HybridDB path as MessageStore so messages imported
    via /conversation/import are immediately searchable.

    Configures an LLM provider for query expansion when available.
    LLM expansion is disabled by default — enabled by setting
    MEMORY_EXPANSION_MODEL env var.
    """
    from coremem.core import MemoryCore

    from src.storage.paths import get_paths

    cache_key = f"{user_id}:coremem"
    if cache_key not in _coremem_cache:
        paths = get_paths(user_id)
        conv_path = str(paths.conversation_dir())

        llm_provider = _try_create_llm_provider()
        _coremem_cache[cache_key] = MemoryCore(
            path=conv_path,
            llm_provider=llm_provider,
        )
    return _coremem_cache[cache_key]


def _try_create_llm_provider() -> Any | None:
    """Try to create an LLM provider for query expansion.

    Returns None if MEMORY_EXPANSION_MODEL is not set or creation fails.
    The provider object has a chat(messages) method.
    """
    model_str = os.environ.get("MEMORY_EXPANSION_MODEL")
    if not model_str:
        return None
    try:
        from src.sdk.providers.factory import create_model_from_config

        return create_model_from_config(model_str)
    except Exception:
        return None


def _list_workspace_ids(user_id: str) -> list[str]:
    """List workspace IDs that have conversation data for a user."""
    from src.storage.paths import DataPaths

    workspace_ids: list[str] = []
    ws_base = DataPaths().root / "Workspaces"
    if not ws_base.exists():
        return workspace_ids
    for entry in sorted(ws_base.iterdir()):
        if entry.is_dir():
            conv_path = entry / "conversation.app.db"
            if conv_path.exists():
                workspace_ids.append(entry.name)
    return workspace_ids


def _fetch_session_ids(conversation: Any, msg_ids: list[str]) -> dict[str, str]:
    """Batch-lookup session_ids from the messages table.

    Uses the session_id column as the source of truth and falls back to
    metadata JSON for older rows.
    """
    import json as _json

    result: dict[str, str] = {}
    if not msg_ids:
        return result

    def _query(cursor: Any) -> list[Any]:
        placeholders = ",".join("?" * len(msg_ids))
        columns = {row[1] for row in cursor.execute("PRAGMA table_info('messages')").fetchall()}
        session_expr = "session_id" if "session_id" in columns else "'' AS session_id"
        return cast(
            list[Any],
            cursor.execute(
                f"SELECT id, metadata, {session_expr} FROM messages WHERE id IN ({placeholders})",
                tuple(msg_ids),
            ).fetchall(),
        )

    rows: list[Any]
    if hasattr(conversation, "_connect"):
        try:
            conn = conversation._connect()
            try:
                rows = _query(conn)
            finally:
                conn.close()
        except Exception:
            return result
    else:
        core = getattr(conversation, "core", None)
        db = getattr(core, "db", None)
        connect = getattr(db, "_connect", None)
        if connect is None:
            return result
        try:
            with connect() as cursor:
                rows = _query(cursor)
        except Exception:
            return result

    for row in rows:
        msg_id = row[0]
        raw = row[1] or ""
        sid = row[2] or ""
        if not sid and raw:
            try:
                meta = _json.loads(raw)
                sid = meta.get("session_id", "")
            except (_json.JSONDecodeError, TypeError):
                pass
        result[msg_id] = sid

    return result


def _group_results_by_session(results: list[SearchResult], session_ids: dict[str, str]) -> list[tuple[str, int, list[SearchResult]]]:
    """Group search results by session.

    Returns ordered groups: [(session_id, message_count, results), ...]
    Groups with no session_id are grouped as individual entries.
    """
    from collections import OrderedDict

    groups: OrderedDict[str, list[SearchResult]] = OrderedDict()
    no_session: list[SearchResult] = []

    for r in results:
        sid = session_ids.get(r.id, "")
        if sid:
            if sid not in groups:
                groups[sid] = []
            groups[sid].append(r)
        else:
            no_session.append(r)

    # Convert to list of tuples, ordered by first occurrence
    output: list[tuple[str, int, list[SearchResult]]] = []
    for sid, entries in groups.items():
        output.append((sid, len(entries), entries))
    for r in no_session:
        output.append(("", 1, [r]))

    return output


@tool
def message_history(
    days: int = 7,
    date_str: str | None = None,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Get conversation history for progressive disclosure.

    Use this tool when user asks about past conversations, what was discussed,
    or wants to recall previous interactions.

    Args:
        days: Number of days to look back (default: 7)
        date_str: Specific date in YYYY-MM-DD format (optional)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Formatted conversation history
    """
    conversation = get_message_store(user_id, workspace_id)

    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
            messages = conversation.get_messages(
                start_date=target_date,
                end_date=target_date,
            )
            if not messages:
                if conversation.count_messages() == 0:
                    return (
                        "No persisted messages found. Conversation history has not been persisted "
                        "for this user in the conversation store or session context."
                    )
                return f"No messages found for {date_str}"

            result = f"Conversation on {date_str}:\n"
            for msg in messages:
                result += f"- {msg.role}: {msg.content}\n"
            return result

        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD."

    start_date = date.today() - timedelta(days=days)
    messages = conversation.get_messages(start_date=start_date, limit=200)

    if not messages:
        if conversation.count_messages() == 0:
            return (
                "No persisted messages found. Conversation history has not been persisted "
                "for this user in the conversation store or session context."
            )
        return f"No messages in the last {days} days."

    result = f"Recent conversation (last {days} days):\n\n"
    for msg in messages:
        timestamp = msg.ts.strftime("%Y-%m-%d %H:%M")
        result += f"- {msg.role} [{timestamp}]: {msg.content}\n"

    return result


message_history.annotations = ToolAnnotations(
    title="Get Message History", read_only=True, idempotent=True
)





@tool
def message_search(
    query: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    limit: int = 5,
) -> str:
    """Search conversation history — semantic + keyword, full session context.

    PRIMARY tool for fact recall. Always use this before telling the user
    you don't have information about a past conversation.

    Returns full conversations (all turns) for each matching session.
    Automatically expands your query and reranks results for best recall.

    Use this for finding specific facts, names, dates, plans from past
    conversations. Use message_count for counting/aggregation questions
    ("how many X..."), and message_timeline for temporal reasoning
    ("how many days between...", "when did I...").

    Args:
        query: What to search for
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)
        limit: Max results (default 5)

    Returns:
        Full conversation context for each matching session
    """
    from src.storage.messages import get_message_store

    core = _get_message_core(user_id, workspace_id)
    store = get_message_store(user_id, workspace_id)

    is_counting = query.lower().startswith("how many") or "total" in query.lower()
    effective_limit = max(limit, 30 if is_counting else 10)

    all_results = core.recall(query, strategy="episodic", limit=effective_limit)

    # Deduplicate by session
    query_words = set(query.lower().split())
    seen_sessions: set[str] = set()
    seen_nosession: set[str] = set()
    matched: list[tuple[str, Any]] = []  # (session_id, first_match_result)

    for r in all_results:
        if r.memory.role in ("tool", "summary"):
            continue

        content_stripped = r.memory.content.strip().lower()
        result_words = set(content_stripped.split())
        query_overlap = len(query_words & result_words) / max(len(query_words), 1)
        has_data = bool(re.findall(r"\d+|[A-Z][a-z]{2,}", r.memory.content))
        if query_overlap > 0.7 and not has_data:
            continue

        sid = r.memory.session_id or ""
        if sid:
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)
        else:
            content_key = content_stripped[:100]
            if content_key in seen_nosession:
                continue
            seen_nosession.add(content_key)

        matched.append((sid, r))
        if len(matched) >= effective_limit:
            break

    if not matched:
        return f"No messages found for '{query}'"

    # 4. Build session-level context blocks
    output_parts: list[str] = []
    for sid, first in matched:
        if not sid:
            content = first.memory.content[:500]
            ts_str = first.memory.ts.strftime("%Y-%m-%d") if first.memory.ts else "?"
            output_parts.append(f"── Message ──\n[{first.memory.role}] {ts_str}\n{content}")
            continue

        session_msgs = store.get_messages_by_session_id(sid, limit=50)
        if not session_msgs:
            output_parts.append(first.memory.content[:500])
            continue

        lines = [f"── Session {sid[:12]} ──"]
        for m in session_msgs:
            content = (m.content or "")[:500]
            ts_str = m.ts.strftime("%Y-%m-%d") if m.ts else "?"
            role = m.role or "?"
            lines.append(f"[{role}] {ts_str} {content}")
        output_parts.append("\n".join(lines))

    output = (
        f"## Search Results: '{query}'\n"
        f"Found {len(matched)} relevant conversations.\n\n"
    ) + "\n\n".join(output_parts)

    output += (
        "\n\n---\n"
        "INSTRUCTIONS: Read the above conversations and extract the answer. "
        "If counting, count each distinct item across ALL results. "
        "Give only the final answer — do NOT repeat the search results."
    )
    return output


message_search.annotations = ToolAnnotations(
    title="Search Past Conversations (Full Context)", read_only=True, idempotent=True
)


@tool
def message_count(
    query: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Count distinct items — call this for "how many" questions.

    For aggregation like "how many doctor visits?", "how many different
    skincare products?", "how many times did I mention X?".
    Extracts distinct items programmatically across all sessions and
    returns a definitive count with item names.

    Use message_search for finding specific facts, not counting.
    Use message_timeline for "what happened between dates" questions.

    Args:
        query: What to count (e.g., "model kits", "doctors", "projects")
        user_id: User identifier
        workspace_id: Current workspace ID
    """
    import re as _re
    from collections import defaultdict as _defaultdict

    conversation = get_message_store(user_id, workspace_id)
    search_limit = 100

    queries = expand_queries(query, llm_provider=_try_create_llm_provider())
    seen_ids: set[str] = set()
    all_results: list[SearchResult] = []

    for q in queries:
        results = conversation.search_hybrid(q, limit=search_limit)
        for r in results:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                all_results.append(r)

    # Fetch session metadata
    msg_ids = [r.id for r in all_results]
    session_ids: dict[str, str] = _fetch_session_ids(conversation, msg_ids)

    # Group by session
    session_groups = _group_results_by_session(all_results, session_ids)
    groups_with_sessions = [(sid, count, entries) for sid, count, entries in session_groups if sid]

    # Extract distinct items: look for capitalized multi-word phrases,
    # numbers+units, and quoted strings — the same nouns a human would count
    item_mentions: _defaultdict[str, list[str]] = _defaultdict(list)
    for sid, count, entries in groups_with_sessions:
        combined = " ".join(e.content for e in entries)
        # Named entities: capitalized multi-word phrases (2-4 words)
        named = _re.findall(
            r"\b([A-Z][a-zA-Z0-9\-\.&']*(?:\s+(?:[A-Z][a-zA-Z0-9\-\.&']*|(?:\d+(?:\.\d+)?\s*)?(?:scale|mm|cm|in|inch|ft|foot|gallon|liter|hour|day|week|month|year)s?)){1,3})\b",
            combined,
        )
        # Numbers with units as context
        num_units = _re.findall(
            r"\b(\d+(?:\.\d+)?\s*(?:hours?|days?|weeks?|months?|years?|dollars?|\$?\d+[kKmM]?|items?|kits?|plants?|tanks?|pieces?|doctors?|weddings?|festivals?|breaks?|fruits?|types?|different|total|projects?))\b",
            combined,
            _re.IGNORECASE,
        )
        candidates = {p.strip().rstrip(".,;:!?") for p in named + num_units if 3 < len(p.strip()) < 80}
        for item in candidates:
            item_lower = item.lower()
            if item_lower in {"you", "your", "they", "their", "would", "could", "should",
                               "have", "been", "this", "that", "these", "those", "with", "from",
                               "about", "there", "which", "because", "however", "though", "although",
                               "through", "during", "between", "without", "within", "something"}:
                continue
            item_mentions[item_lower].append(item)

    # Deduplicate: pick the canonical form (longest/most specific)
    deduped: dict[str, str] = {}
    for item_lower, forms in item_mentions.items():
        canonical = max(set(forms), key=lambda f: (len(f), f))
        deduped[item_lower] = canonical

    # Further dedup: merge items that are subsets of each other
    items = sorted(deduped.items(), key=lambda x: -len(x[0].split()))
    final: dict[str, str] = {}
    for key, display in items:
        absorbed = False
        for existing_key in list(final.keys()):
            if key in existing_key or existing_key in key:
                if len(existing_key) >= len(key):
                    absorbed = True
                    break
                else:
                    del final[existing_key]
                    final[key] = display
                    absorbed = True
                    break
        if not absorbed:
            final[key] = display

    # Build output
    # Honest reporting (audit B17): only the current workspace's store is
    # queried, so the output must not claim cross-workspace coverage.
    output = f"Searched 1 workspace: {workspace_id}\n"
    output += f"Analyzed {len(session_groups)} sessions ({len(all_results)} raw matches)\n\n"

    if final:
        output += f"**Distinct items: {len(final)}**\n\n"
        for i, (key, display) in enumerate(sorted(final.items(), key=lambda x: x[0]), 1):
            mention_count = len(item_mentions[key])
            output += f"{i}. {display} ({mention_count} mentions)\n"
    else:
        output += "No distinct items could be identified. Try a more specific query.\n"

    return output


message_count.annotations = ToolAnnotations(
    title="Count Matching Messages", read_only=True, idempotent=True
)


@tool
def message_timeline(
    query: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    limit: int = 20,
) -> str:
    """Find events in conversation history with their dates — for temporal reasoning.

    Returns a chronological timeline of matching events, each with its
    date and a content snippet. Use this for temporal reasoning:
    - "how many days between X and Y" (extract dates, calculate difference)
    - "when did I visit / go to / buy..." (find event date)
    - "what happened last March / in 2025" (find events in a date range)

    For specific fact recall, use message_search which returns full session
    context. For counting, use message_count.

    Args:
        query: What events to find (e.g., "visit to MoMA", "yoga class")
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)
        limit: Max events to return (default 20)

    Returns:
        Chronological timeline of matching events with dates
    """
    core = _get_message_core(user_id, workspace_id)

    results = core.search_enhanced(query, limit=limit)

    seen_sessions: set[str] = set()
    timeline: list[tuple[str, str, str]] = []  # (date, session_id, snippet)

    for r in results:
        mem = r.memory
        sid = mem.session_id or ""
        if sid:
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)

        if not mem.ts:
            continue

        ts = mem.ts.isoformat() if hasattr(mem.ts, "isoformat") else str(mem.ts)

        snippet = mem.content[:200].replace("\n", " ").strip()
        if len(mem.content) > 200:
            snippet += "..."

        timeline.append((ts, sid, snippet))

    timeline.sort(key=lambda x: x[0])

    if not timeline:
        return "No matching events found."

    output = f"**Timeline: {len(timeline)} events**\n\n"
    for ts, sid, snippet in timeline[:limit]:
        # Convert ISO timestamp to readable date
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(ts)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            date_str = ts[:10]
        sid_display = sid[:12] + "..." if len(sid) > 15 else sid
        output += f"**{date_str}** (session {sid_display})\n{snippet}\n\n"

    total = len(timeline)
    if total > limit:
        output += f"*({total - limit} more events — refine your query for more detail)*\n"

    return output


message_timeline.annotations = ToolAnnotations(
    title="Event Timeline with Dates", read_only=True, idempotent=True
)
