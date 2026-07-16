# ToolObserver + SkillMiner Design

**Date:** 2026-06-09
**Status:** Draft
**Motivation:** Assistant's fact observer only captures user+assistant messages — it misses tool calls and results. Tool messages contain rich signal about agent behavior (error patterns, success patterns, repeated sequences) that could drive tool and skill optimization.

Two-layer architecture:
- **CoreMem (OSS):** Observes tool messages, outputs structured `tool_summary` observations. Purely observational — no tool catalog, no skill registry, no domain knowledge.
- **EA SkillMiner (proprietary):** Reads CoreMem observations, enriches with EA domain context (tool catalog, skill registry, user goals), produces actionable recommendations.

---

## 1. Architecture

```
EA Agent Loop
  │
  ├── ingest()  ──→  MemoryCore (messages table)
  │                    roles: user, assistant, tool
  │
  ├── session_end()  ──→  MemoryCore (observations table)
  │                    kind: "tool_summary"
  │                    memory_type: "agent_behavior"
  │
  └── EA SkillMiner ── reads CoreMem observations
        │                + EA tool catalog
        │                + EA skill registry
        │                + EA user goals
        │
        ├── tool_recommendation (improve tool description/docs)
        └── skill_suggestion (create new SKILL.md)
```

---

## 2. CoreMem ToolExtractor (OSS)

### 2.1 New observation kind: `tool_summary`

Stored in the existing `observations` table alongside fact observations — same table, different `kind`.

```python
{
    "id": str,                    # uuid
    "kind": "tool_summary",
    "content": str,               # narrative summary (LLM-generated)
    "observation_ts": str,        # ISO timestamp
    "user_id": str,
    "session_id": str,            # links to the session
    "importance": 0.7,            # tool patterns are high importance
    "memory_type": "agent_behavior",
    "metadata": json.dumps({       # JSON dict — deterministic analysis
        "user_goal": str,                     # from preceding user message
        "n_tool_calls": int,
        "n_errors": int,
        "agent_behavior": {
            # Error frequency per tool (deterministic)
            "error_by_tool": {
                "files_edit": ["lint_failure", "lint_failure"],
                "web_scrape": ["timeout"]
            },
            # Tool call count per tool (deterministic)
            "tool_coverage": ["files_read", "files_grep_search", "files_edit"],
            # Recovery patterns: error→retry→success (deterministic)
            "recovery_by_tool": {
                "files_edit": ["retry_with_lint_fix"],
                "web_scrape": ["refined_search_query"]
            },
            # Most common tool sequences of length 2-3 (deterministic)
            "sequences": {
                "files_read→files_grep_search→files_edit": 2,
                "shell_execute→browser_open": 1
            },
        },
"active_skills": [],      # opaque list, provided by caller
    "error_classification": "heuristic",
}),
    "embedding": str,              # for similarity search across tool patterns
}
```

**Note:** `metadata.agent_behavior` is purely deterministic (error string matching, tool_call_id pairing, sequence counting). No LLM required. The `content` field is the only LLM-generated part (optional narrative).

### 2.2 `ToolExtractor` pipeline

New pipeline class alongside `ObserverPipeline` and `ReflectorPipeline`:

```python
class ToolExtractor:
    """Session-end tool message analyzer.

    Reads all role='tool' messages for a session, produces
    a structured tool_summary observation. Handles long sessions
    with streaming chunk summarization for the optional narrative.

    OSS boundary: No tool catalog, no skill registry, no domain
    knowledge. Just message content analysis.
    """

    def __init__(
        self,
        memory: MemoryCore,
        session_id: str,
        user_id: str,
        max_input_tokens: int = 32000,
        max_tokens_per_chunk: int = 8000,
        min_tool_messages: int = 5,
        active_skills: list[str] | None = None,
        provider: Any = None,  # LLM provider (optional, for narrative)
        generate_narrative: bool = False,  # LLM narrative is opt-in
    ):
        ...

    async def extract(self) -> dict | None:
        """Run tool extraction. Returns the tool_summary dict or None if no tool messages."""
        # 1. Fetch tool messages
        assistant_msgs = self.memory.get_messages(
            session_id=self.session_id, role="assistant",
            has_tool_calls=True,
        )
        tool_msgs = self.memory.get_messages(
            session_id=self.session_id, role="tool",
        )
        if not tool_msgs or len(tool_msgs) < self.min_tool_messages:
            return None

        # 2. Pair tool_call_id across assistant.tool_calls and tool results
        #    This gives us tool_name + arguments + result content per call
        trace = self._build_trace(assistant_msgs, tool_msgs)

        # 3. Deterministic analysis (no LLM)
        metadata = self._analyze_deterministic(trace)

        # 4. Optional LLM narrative
        narrative = ""
        if self.generate_narrative and self._provider:
            narrative = await self._llm_narrative(trace, metadata)
        metadata["narrative"] = narrative

        # 5. Store observation
        self.memory.insert_observations([{
            "kind": "tool_summary",
            "content": metadata["narrative"],
            "observation_ts": datetime.now(UTC).isoformat(),
            "user_id": self.user_id,
            "session_id": self.session_id,
            "importance": 0.7,
            "memory_type": "agent_behavior",
            "metadata": json.dumps({
                "user_goal": self._extract_user_goal(assistant_msgs),
                "n_tool_calls": len(trace),
                "n_errors": metadata["n_errors"],
                "agent_behavior": {
                    "error_by_tool": metadata["error_by_tool"],
                    "tool_coverage": metadata["tool_coverage"],
                    "recovery_by_tool": metadata["recovery_by_tool"],
                    "sequences": metadata["sequences"],
                },
                "error_classification": "heuristic",
            }),
            "embedding": "",
        }])
```

### 2.3 Deterministic analysis (the core value — no LLM)

```python
import re

_ERROR_KEYWORDS = re.compile(
    r"(Error:|error:|failed|not found|could not)", re.IGNORECASE
)

def _classify_error(self, content: str) -> tuple[bool, str | None]:
    """Heuristic error classification. Returns (is_error, error_type)."""
    if not content:
        return False, None
    match = _ERROR_KEYWORDS.search(content)
    if match:
        return True, match.group(0).strip().rstrip(":")
    return False, None


def _build_trace(self, assistant_msgs: list[dict], tool_msgs: list[dict]) -> list[dict]:
    """Pair tool calls with their results using tool_call_id.

    Returns a chronological trace where each entry has:
    - tool_name: str
    - arguments: str (JSON from assistant.tool_calls[i].arguments)
    - result_content: str (from tool message content)
    - success: bool
    - error_type: str | None
    - error_classification: str — "heuristic" or None
    - call_id: str
    - recovery_call_id: str | None (if this was a retry)
    """
    tool_results = {m["tool_call_id"]: m for m in tool_msgs if m.get("tool_call_id")}

    trace = []
    for msg in assistant_msgs:
        for tc in msg.get("tool_calls", []):
            call_id = tc["id"]
            result = tool_results.get(call_id, {})
            content = result.get("content", "")
            is_error, error_type = self._classify_error(content)
            trace.append({
                "call_id": call_id,
                "tool_name": tc["name"],
                "arguments": tc.get("arguments", "{}"),
                "result_content": content,
                "success": not is_error,
                "error_type": error_type,
                "error_classification": "heuristic" if is_error else None,
            })

    # Detect recoveries: call_id → call_i_retry with same tool_name and success=True
    for i, entry in enumerate(trace):
        if not entry["success"]:
            for j in range(i + 1, min(i + 3, len(trace))):
                if (trace[j]["tool_name"] == entry["tool_name"]
                        and trace[j]["success"]):
                    entry["recovery_call_id"] = trace[j]["call_id"]
                    break

    return trace


def _analyze_deterministic(self, trace: list[dict]) -> dict:
    error_by_tool: dict[str, list[str]] = {}
    tool_coverage: set[str] = set()
    recovery_by_tool: dict[str, list[str]] = {}
    sequence_counts: dict[str, int] = {}
    n_errors = 0

    # Build sequences of tool names (length 2 and 3)
    tool_names = [e["tool_name"] for e in trace]
    for seq_len in [2, 3]:
        for i in range(len(tool_names) - seq_len + 1):
            seq = "→".join(tool_names[i:i + seq_len])
            sequence_counts[seq] = sequence_counts.get(seq, 0) + 1

    for entry in trace:
        tool_coverage.add(entry["tool_name"])

        if not entry["success"]:
            n_errors += 1
            err = entry["error_type"] or "unknown"
            error_by_tool.setdefault(entry["tool_name"], []).append(err)

        if entry.get("recovery_call_id"):
            recovery_by_tool.setdefault(entry["tool_name"], []).append(
                f"recovered_via_retry"
            )

    return {
        "n_errors": n_errors,
        "error_by_tool": error_by_tool,
        "tool_coverage": sorted(tool_coverage),
        "recovery_by_tool": recovery_by_tool,
        "sequences": dict(sorted(
            sequence_counts.items(), key=lambda x: -x[1]
        )[:10]),  # top 10 most frequent sequences
    }
```

### 2.4 Optional LLM narrative

If `generate_narrative=True`, the LLM produces a human-readable summary:

**Prompt:**
```
You are analyzing an AI agent's tool usage during a session.
Summarize what happened, focusing on patterns that would help
the agent do better next time.

User's apparent goal: {user_goal}

Tool trace:
{formatted_trace}

Respond with a 2-3 sentence narrative.
```

### 2.5 Budget controls

| Parameter | Default | Notes |
|-----------|---------|-------|
| `min_tool_messages` | 5 | Skip sessions with <5 tool calls |
| `generate_narrative` | False | LLM narrative is opt-in |
| `max_input_tokens` | 32000 | For LLM narrative only |

### 2.6 Trigger — `session_end()` hook on MemoryCore

```python
class MemoryCore:
    async def session_end(
        self,
        session_id: str,
        user_id: str,
        active_skills: list[str] | None = None,
    ) -> None:
        """Called when a session ends. Triggers ToolExtractor.

        Fire-and-forget: launches async task, caller doesn't wait.

        Args:
            session_id: Session identifier
            user_id: User identifier
            active_skills: Opaque list of skill names loaded during session.
                           Provided by caller (e.g. Assistant's AgentLoop).
                           CoreMem stores them but has no knowledge of them.
        """
        if not self._tool_extractor_enabled:
            return
        extractor = ToolExtractor(
            memory=self,
            session_id=session_id,
            user_id=user_id,
            min_tool_messages=self._tool_min_messages,
            active_skills=active_skills,
            generate_narrative=self._tool_generate_narrative,
            provider=self._tool_provider,
        )
        asyncio.create_task(extractor.extract())
```

---

## 3. EA Recommendation Output (not stored in CoreMem)

Assistant's `SkillMiner` reads `tool_summary` observations from CoreMem, enriches with domain context, and produces transient recommendations. These are consumed by the agent or presented to the user — **not written back to CoreMem**.

### 3.1 Recommendation types

**Tool recommendation** — suggest improving a tool's description or behavior:

```json
{
    "type": "tool_recommendation",
    "tool": "files_edit",
    "confidence": 0.8,
    "finding": "lint_failure after edit — 3 occurrences across sessions",
    "evidence": [
        {"session_id": "ses_abc", "n_occurrences": 2},
        {"session_id": "ses_def", "n_occurrences": 1}
    ],
    "current_description": "Edit a file at a given path",
    "suggested_description": "Edit a file. After editing, the user may need to fix lint errors — suggest running the linter after editing.",
    "recommendation_type": "improve_description"
}
```

**Skill suggestion** — propose creating a new skill when a tool sequence repeats across sessions:

```json
{
    "type": "skill_suggestion",
    "confidence": 0.7,
    "name": "python-refactoring",
    "pattern": "files_read → files_grep_search → files_edit × 2",
    "evidence": [3 sessions with same 3-tool sequence],
    "draft_skill": {
        "name": "python-refactoring",
        "description": "Extract functions, rename symbols, and restructure Python files",
        "steps": [
            "Read the file with files_read",
            "Find relevant code with files_grep_search",
            "Make changes with files_edit",
            "Run linter after to catch issues"
        ]
    },
    "recommendation_type": "create_skill"
}
```

### 3.2 `SkillMiner` class

```python
class SkillMiner:
    """Reads tool_summary observations from CoreMem, enriches with EA context.

    Input:
        - CoreMem observations (kind="tool_summary")
        - EA ToolCatalog (tool_name → ToolDefinition with description)
        - EA SkillRegistry (existing skills)
        - EA provider (for LLM enrichment)

    Output:
        - list[dict] recommendations — never stored in CoreMem
    """

    def __init__(
        self,
        core: MemoryCore,
        tool_catalog: dict[str, Any],
        skill_registry: Any,
        provider: Any,
    ):
        ...

    async def recommend_tools(self, user_id: str) -> list[dict]:
        """Find error patterns per tool → suggest description improvements.

        Cross-references deterministic error_by_tool from CoreMem
        with Assistant's tool catalog descriptions.
        """
        summaries = self.core.get_observations(
            kind="tool_summary", user_id=user_id, limit=50,
        )

        # Aggregate errors across sessions
        errors_by_tool: dict[str, list[str]] = {}
        for s in summaries:
            meta = json.loads(s.get("metadata", "{}"))
            behavior = meta.get("agent_behavior", {})
            for tool_name, errors in behavior.get("error_by_tool", {}).items():
                errors_by_tool.setdefault(tool_name, []).extend(errors)

        recommendations = []
        for tool_name, errors in errors_by_tool.items():
            if len(errors) < 3:
                continue  # Not enough evidence
            td = self.tool_catalog.get(tool_name)
            if not td:
                continue

            recommendations.append({
                "type": "tool_recommendation",
                "tool": tool_name,
                "confidence": min(len(errors) / 10, 1.0),
                "finding": f"{errors[0]} after {tool_name} — {len(errors)} occurrences",
                "evidence": [{"error": e} for e in errors],
                "current_description": td.description,
                "suggested_description": await self._improve_description(
                    td, errors,
                ),
                "recommendation_type": "improve_description",
            })

        return recommendations

    async def suggest_skills(self, user_id: str, min_sessions: int = 3) -> list[dict]:
        """Find repeated successful tool sequences → suggest new skills."""
        summaries = self.core.get_observations(
            kind="tool_summary", user_id=user_id, limit=50,
        )
        if len(summaries) < min_sessions:
            return []

        # Aggregate sequences across sessions
        sequence_sessions: dict[str, list[str]] = {}
        for s in summaries:
            meta = json.loads(s.get("metadata", "{}"))
            behavior = meta.get("agent_behavior", {})
            for seq in behavior.get("sequences", {}):
                session_id = s.get("session_id", "?")
                sequence_sessions.setdefault(seq, []).append(session_id)

        suggestions = []
        for seq, sessions in sequence_sessions.items():
            if len(sessions) < min_sessions:
                continue
            tools_in_seq = seq.split("→")
            suggestions.append({
                "type": "skill_suggestion",
                "confidence": min(len(sessions) / 5, 1.0),
                "name": "-".join(tools_in_seq[:2]),
                "pattern": seq,
                "evidence": len(sessions),
                "draft_skill": await self._draft_skill(tools_in_seq, seq),
                "recommendation_type": "create_skill",
            })

        return suggestions
```

### 3.3 Files

| File | Purpose |
|------|---------|
| `src/sdk/skill_miner.py` | SkillMiner class, cross-session analysis, tool recommendations |
| `src/sdk/tools_core/skill_miner.py` | Optional tools `skill_miner_suggest` / `skill_miner_analyze` |

---

## 4. Synthetic Test Fixture Generator (CoreMem test suite)

### 4.1 `ToolSessionGenerator` class

(See section 3.1 in the full spec for the generator implementation — omitted here for brevity.)

5 scenarios: `file_edit_session`, `web_search_session`, `multi_skill_session`, `browser_session`, `data_query_session`.

### 4.2 Test assertions

```python
async def test_deterministic_analysis(self, memory_core):
    """No LLM needed — pure deterministic analysis works."""
    ...
    obs = memory_core.get_observations(kind="tool_summary")
    metadata = json.loads(obs[0]["metadata"])
    behavior = metadata["agent_behavior"]

    assert metadata["n_errors"] >= 1
    assert "files_edit" in behavior["tool_coverage"]
    assert "lint_failure" in behavior["error_by_tool"]["files_edit"]
    assert "files_read→files_grep_search" in behavior["sequences"]

async def test_recovery_detection(self, memory_core):
    """Error→retry→success should produce recovery_by_tool."""
    ...
    behavior = metadata["agent_behavior"]
    assert behavior["recovery_by_tool"]["files_edit"] == ["recovered_via_retry"]

async def test_skips_small_sessions(self, memory_core):
    """Sessions with <5 tool calls produce no tool_summary."""
    ...
    obs = memory_core.get_observations(kind="tool_summary")
    assert len(obs) == 0

async def test_empty_tool_call_ids(self, memory_core):
    """Resilient to missing tool_call_id on tool messages."""
    messages = ToolSessionGenerator().generate("file_edit_session")
    # Remove tool_call_id from first tool message
    messages[6].pop("tool_call_id", None)  # Third message is first tool result
    for msg in messages:
        memory_core.ingest(msg, session_id="test_resilient")
    await memory_core.session_end("test_resilient", user_id="test_user")
    obs = memory_core.get_observations(kind="tool_summary")
    assert len(obs) == 1  # Should still work, just skip unpairable
```

---

## 5. Implementation Order

### Phase 1 — CoreMem (OSS)

1. **Deterministic `ToolExtractor`** — `_build_trace()`, `_analyze_deterministic()`, `session_end()` hook. No LLM.
2. **`ToolSessionGenerator`** — Synthetic fixture with 5 scenarios
3. **Tests** — Deterministic analysis, recovery detection, resilience, small-session skip

### Phase 2 — EA SkillMiner (proprietary)

4. **`SkillMiner.recommend_tools()`** — Aggregate `error_by_tool` across sessions, cross-reference tool catalog, produce `tool_recommendation`
5. **`SkillMiner.suggest_skills()`** — Aggregate `sequences` across sessions, produce `skill_suggestion` with draft
6. **Integration test** — Full pipeline: generate session → deterministic analysis → EA enrichment → recommendation

### Phase 3 — Optional

7. **LLM narrative** — `generate_narrative=True` in ToolExtractor for human-readable summaries
8. **Agent-facing tools** — `skill_miner_suggest`, `skill_miner_analyze` accessible from chat

---

## 6. OSS Boundary Summary

| Component | Layer | Domain Knowledge |
|-----------|-------|-----------------|
| `MemoryCore.ingest()` | CoreMem | None — stores messages |
| `ToolExtractor._build_trace()` | CoreMem | None — pairs by tool_call_id |
| `ToolExtractor._analyze_deterministic()` | CoreMem | None — counts errors, sequences |
| `ToolExtractor._llm_narrative()` | CoreMem | None — generic "summarize tool usage" |
| `MemoryCore.session_end()` | CoreMem | None — opaque active_skills passthrough |
| `SkillMiner.recommend_tools()` | EA | Tool catalog, descriptions |
| `SkillMiner.suggest_skills()` | EA | Skill registry, naming conventions |
| `SkillMiner._improve_description()` | EA | Tool catalog, EA provider |
| `SkillMiner._draft_skill()` | EA | Skill format (SKILL.md), EA provider |

---

## 7. Self-Review: Issues Found

| Issue | Fix Applied |
|-------|-------------|
| `_build_trace()` not specified — couldn't pair tool calls with results | Added `_build_trace()` with tool_call_id pairing |
| No recovery detection (error→retry→success) | Added `recovery_by_tool` to deterministic analysis |
| LLM narrative was mandatory | Made `generate_narrative=False` by default |
| `session_end()` was blocking | Changed to `asyncio.create_task()` |
| EA output format was undefined | Added explicit `tool_recommendation` and `skill_suggestion` schemas |
| Skill drift detection was low-value | Deferred to future |
| Active skills tracking unowned | `active_skills` is opaque passthrough — caller provides |
| No empty tool_call_id resilience | Added test for missing tool_call_id |
| `duration_seconds` had no implementation | Removed from deterministic analysis (requires timestamp tracking) |

---

## 8. Open Questions (Resolved)

| # | Question | Decision |
|---|----------|----------|
| 1 | Deterministic narrative — compact text or empty? | **Empty when `generate_narrative=False`.** EA SkillMiner reads `metadata`, not `content`. |
| 2 | Sequence length — track 2, 3, or 4+? | **Lengths 2 and 3 only.** Length-2 for tool chaining hints, length-3 for skill suggestions. 4+ removed. |
| 3 | Error classification — prefix match or heuristic? | **Keyword match:** `"Error:"`, `"error":`, `"failed"`, `"not found"`, `"could not"`, regex for exit code >0. Include `error_classification="heuristic"` in metadata so EA can reclassify with domain knowledge. Raw result content always available for EA enrichment. |