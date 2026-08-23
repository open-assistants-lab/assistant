# Assistant - Agent Guidelines

This document provides guidelines for agents working on this codebase.

---

## 1. Build, Lint, and Test Commands

### Installation
```bash
# Install with all dependencies
uv sync --extra dev

# Install specific extras
uv sync              # runtime only
uv sync --extra dev  # runtime + development tools
```

### Running the Application
```bash
uv run assistant http      # Start HTTP server (with SSE streaming)
```

### Linting and Type Checking
```bash
# Run ruff linter
uv run ruff check src/

# Auto-fix linting issues
uv run ruff check src/ --fix

# Run mypy type checker
uv run mypy src/
```

### Testing (TDD - Test Driven Development)
```bash
# Run all tests
uv run pytest

# Run SDK tests only
uv run pytest tests/sdk/ -v

# Run a single test file
uv run pytest tests/sdk/test_tools.py

# Run a single test function
uv run pytest tests/sdk/test_tools.py::TestToolDecorator::test_basic_decoration

# Run tests with coverage
uv run pytest --cov=src --cov-report=html

# Run persona evaluation (25 personas)
uv run python tests/evaluation/evaluate.py
```

### Native App (Zig) Tests
```bash
# Zig unit tests (native-sdk-experiment, 90 tests)
uv run native test            # run from native-sdk-experiment/

# Frontend automation suite (51 tests, drives the app via native automate)
bash tests/frontend_suite.sh --all          # full suite
bash tests/frontend_suite.sh --tools        # Settings → Tools section only
bash tests/frontend_suite.sh --settings     # Settings panel only
bash tests/frontend_suite.sh --connectform  # 4-field credential-form regression
```

### Docker
```bash
# Start PostgreSQL
cd docker && docker compose up -d

# Stop PostgreSQL
cd docker && docker compose down

# Build and run app in Docker
cd docker && docker compose up --build
```

---

## 2. Code Style Guidelines

### Python Version
- Minimum: Python 3.11, Maximum: Python <3.14 (capped in pyproject.toml)
- Use modern Python features (type hints, structural pattern matching)

### Imports (PEP 8 + Ruff)
```python
# Standard library first
import os
import json
from pathlib import Path
from typing import Any, Optional

# Third-party libraries
from pydantic import Field
from fastapi import FastAPI

# Local imports (absolute)
from src.config import get_settings
from src.sdk.messages import Message, StreamChunk

# Sort imports with: uv run ruff check src/ --fix
```

### Formatting
- Line length: 100 characters
- Use Black-compatible formatting via Ruff
- 4 spaces for indentation (no tabs)

### Type Hints (Required)
```python
# Use type hints for all function signatures
def process_message(message: str, user_id: str = "default") -> dict[str, Any]:
    ...

# Use | for unions (Python 3.10+)
def get_value(key: str | None) -> str:
    ...

# Use Optional for nullable
def find_item(name: Optional[str]) -> Item | None:
    ...
```

### Naming Conventions
- **Variables/functions**: `snake_case` (e.g., `get_logger`, `user_id`)
- **Classes**: `PascalCase` (e.g., `ExecutiveAssistantCLI`, `Logger`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRIES`, `DEFAULT_TIMEOUT`)
- **Private members**: `_leading_underscore` (e.g., `_internal_state`)

### Tool Naming Pattern
All tools must follow `category_{verb}` pattern:
```python
# Email tools
email_connect, email_disconnect, email_accounts
email_list, email_get, email_search
email_send, email_sync

# Contacts tools
contacts_list, contacts_get, contacts_add, contacts_update, contacts_delete, contacts_search

# Todos tools
todos_list, todos_add, todos_update, todos_delete, todos_extract

# File tools
files_glob_search, files_grep_search, files_list, files_read, files_write, files_edit, files_delete

# Other tools
shell_execute, time_get, memory_get_history, memory_search, skills_load, skills_reload
```

### Pydantic Models
```python
from pydantic import BaseModel, Field

class AgentConfig(BaseModel):
    """Agent configuration."""
    name: str = Field(default="Assistant")
    model: str = Field(default="ollama:minimax-m2.5")
    
    class Config:
        env_prefix = "AGENT_"
        extra = "ignore"  # Allow extra fields from env
```

### Error Handling
```python
# Use specific exceptions
try:
    result = await agent.ainvoke({"messages": messages})
except ValueError as e:
    logger.error("validation_error", {"error": str(e)}, user_id=user_id)
    raise
except Exception as e:
    logger.error("unexpected_error", {"error": str(e), "error_type": type(e).__name__}, user_id=user_id)
    raise
```

---

## 3. Current Architecture & Discoveries

### SDK Architecture (Custom)

The codebase has a **custom agent SDK** (`src/sdk/`) as its core runtime.

**SDK Core (~17,200 lines, 66 files, 832+ tests):**

| Module | Lines | Purpose |
|--------|-------|---------|
| `messages.py` | 413 | `Message`, `ToolCall`, `StreamChunk` — unified message types with block-structured streaming |
| `tools.py` | 297 | `@tool`, `ToolDefinition`, `ToolAnnotations`, `ToolResult`, `ToolRegistry` |
| `loop.py` | 1,178 | `AgentLoop` (ReAct), `RunConfig`, `CostTracker`, `Interrupt`, guardrails, handoffs, tracing |
| `providers/` | ~1,365 | `OllamaLocal`, `OllamaCloud`, `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider` |
| `registry.py` | 388 | models.dev integration — 4172+ models, 110+ providers, auto-updated |
| `registry_update.py` | 159 | Auto-update registry from models.dev API |
| `validation.py` | 158 | `normalize_tool_schema()`, `repair_tool_call()` |
| `guardrails.py` | 60 | `InputGuardrail`, `OutputGuardrail`, `ToolGuardrail`, `GuardrailTripwire` |
| `handoffs.py` | 92 | `Handoff`, `HandoffInput` — model-driven agent transfer |
| `tracing.py` | 204 | `TraceProvider`, `Span`, `SpanContext`, `ConsoleTraceProcessor`, `JsonTraceProcessor` |
| `native_tools.py` | 270 | ToolRegistry with get_native_tools() / get_native_tool_names() + category mapping |
| `capabilities.py` | 71 | `load_capabilities`, `merge_capabilities`, `tool_enabled` — per-scope enable state |
| `agent_validation.py` | 65 | `validate_agent_def` — extracted from coordinator (no circular imports) |
| `agent_profile.py` | 51 | EA-specific AgentProfile validation (models.dev + tools + skills) |
| `subagent_models.py` | 94 | `AgentDef`, `SubagentResult`, `TaskStatus`, `TaskCancelledError`. Drops `disallowed_tools`. |
| `work_queue.py` | 441 | `WorkQueueDB` — aiosqlite per-user SQLite work queue |
| `coordinator.py` | 633 | `SubagentCoordinator` — PROFILE.md support, capabilities filtering |
| `middleware_rubric.py` | ~300 | `RubricMiddleware` — verification loop (grader LLM, rubric, retry) |
| `runner.py` | 537 | `create_sdk_loop`, `run_sdk_agent` — capabilities-filtered tool registration |
| `workspace_models.py` | 128 | `Workspace`, workspace-level path models |
| `agent_scheduler.py` | 308 | Background agent scheduling (proactive check-ins) |
| `research.py` | 293 | Deep research orchestration |
| `state.py` | 78 | `AgentState` — simplified agent state |
| `tools_core/` (32 files) | ~11,000 | ★ SDK-native tool implementations (60 registered tools) |

**Key Design Decisions:**
1. **models.dev integration**: Registry fetches from `https://models.dev/api.json`, caches locally at `data/cache/models.json` with 5-min TTL, falls back to built-in subset. 4172+ models vs. old 20 hardcoded.
2. **Scope model (All / Selected / None)**: Replaces old per-workspace capabilities.yaml. `item_scopes` SQLite table governs tool/skill/subagent availability per workspace. Unconfigured items default to `scope=all` (available everywhere).
3. **Skills directory**: Injected into system prompt as `Skills directory: {paths.user_skills_dir()}`. Agent uses `files_write` with absolute path to create SKILL.md files.
4. **Subagents directory**: Injected as `Subagents directory: {paths.user_subagents_dir()}`.
5. **files_write absolute paths**: Accepts absolute paths anywhere under `ea_root`, not just workspace files dir.
6. **Skill catalog injection**: Skill names + descriptions injected into system prompt as `<available_skills>` block. `skills_load` / `skills_reload` are the only skill tools. `skill_create` removed.
7. **Tool availability**: Unconfigured tools default to available (`scope=all`). Destructive annotation no longer enforces disabled-by-default.
8. **Block-structured streaming**: `text_start/delta/end`, `tool_input_start/delta/end`, `reasoning_start/delta/end`, `tool_result`, `interrupt`, `done`, `error`. Backward-compat aliases: `ai_token→text_delta`, `tool_start→tool_input_start`, `reasoning→reasoning_delta`.
9. **ToolAnnotations** (MCP-style): `readOnly`, `destructive`, `idempotent`, `openWorld`, `title`. Auto-approves read-only tools, interrupts on destructive ones.
10. **ToolResult** dual format: `content` (human-readable) + `structured_content` (machine-parseable) + `audience` (user/assistant).
11. **Provider escape hatches**: `provider_options` on inputs (keyed by provider name), `provider_metadata` on outputs. Enables Anthropic `thinking`, Gemini `thinkingConfig`, OpenAI `logprobs` etc.
12. **Reasoning as first-class content**: `Message.reasoning` field persists thinking tokens across turns. Anthropic `thinking` blocks handled in `to_anthropic()`/`from_anthropic_block()`.
13. **Sequential tool execution**: AgentLoop executes tools one at a time (parallel deferred).
8. **No checkpoints**: LangGraph checkpoint system was permanently disabled. Conversation history is managed by `SummarizationMiddleware`.
9. **Parallel tool execution**: `_classify_tool_calls()` splits into `parallel_safe` (read-only or non-destructive), `sequential` (destructive but not needing HITL), and `interrupts` (destructive + not read-only). Concurrent batch via `asyncio.gather()`.
10. **Usage tracking**: `Message.usage` (type `Usage`) carries token counts from provider responses. Providers populate `Usage` with `input_tokens`, `output_tokens`, `reasoning_tokens`, `cache_read_tokens`, `cache_creation_tokens`. `AgentLoop` extracts usage and passes to `CostTracker.add_usage()`. Streaming uses `StreamChunk.usage_event(Usage)` before `done` event.
11. **provider_options on RunConfig**: `RunConfig.provider_options` (dict keyed by provider_id) is now wired through `AgentLoop.run()`, `run_stream()`, and `run_single()` to all provider calls. Previously hardcoded `None`.
12. **MCP Tool Bridge**: `MCPToolBridge` converts MCP `mcp` SDK tool objects → SDK `ToolDefinition` with namespaced names `mcp__{server}__{tool}`. Tool invocations route through `session.call_tool()`. Supports degraded-mode (partial server failures). `mcp_reload` dynamically registers/unregisters tools in the active `AgentLoop` via `register_tool()`/`unregister_tool()`.
13. **Subagent V1 work_queue coordination**: `WorkQueueDB` (aiosqlite) per-user at `data/private/subagents/work_queue.db`. 11 columns, 2 indexes. Config frozen at invocation into `work_queue.config`. `SubagentContext` provides progress updates, doom loop detection (3x same tool+args), cancel signal, and course-correction injection. `SubagentCoordinator.invoke()` wraps `AgentLoop.run()` in `asyncio.wait_for(timeout)`. All failure modes (cancel, timeout, cost exceeded, provider error) result in terminal work_queue status.
14. **Soft duplicate-tool-call guard** (Ralph loop, `84ca8c4`): the loop never re-executes a `(tool, args)` pair already answered this run — it injects a system-message nudge with the previous result and continues. Configurable via `RunConfig.max_duplicate_tool_nudges` (default 3); after K nudges one final call requests a brief text-only answer (~200-token cap, tool calls suppressed). Stateless apart from a counter in `state.extra`. `get_messages_with_summary` filters rows with `metadata["include_in_model_context"] == False`, and `_tool_audit_records` persists only current-run tool rows (provenance via `storage_id`/`storage_ts`). No numeric tool-call budgets — identity check only (per FR-7 non-goal).
15. **Incremental structured summarization** (Pi-style, `2026-08-20`): `SummarizationMiddleware` writes structured checkpoints (`## Goal / Constraints & Preferences / Progress (Done/In Progress/Blocked) / Key Decisions / Next Steps / Critical Context`). On subsequent compressions it finds its own previous summary message (`source="summarization_middleware"`, both in-memory and `[SUMMARY OF PREVIOUS CONVERSATION]` storage framings) and issues an UPDATE prompt over only the new messages (`<conversation>`/`<previous-summary>` tags) — never a full re-summarize. Cut-points never split AI/Tool pairs; cutting mid-turn generates a separate turn-prefix summary (`## Original Request / ## Context for Suffix`). File ops (`files_*` tool calls) are extracted programmatically and appended as `## Files` (Read/Modified). Summary calls reject tool-call responses and retry once on transient errors (`ConnectionError`/`TimeoutError`/`OSError`); deterministic errors propagate.
16. **Steering** (Pi-style, `2026-08-20`): `AgentLoop.steer(message)` queues a mid-turn nudge delivered after the current tool completes; remaining tool calls in the batch get `{"cancelled": true, "reason": "steer"}` results. Drained at tool boundaries in both `run()` and `run_stream()` (streaming loop advances its `iteration` counter explicitly). Steers arriving during text generation stay queued and are delivered as the next turn (follow-up). WS protocol: client `steer` message, server `steer_ack`; the WS layer persists steers at injection time via `loop.set_steer_sink()` (correct transcript position, no double-persist). `RunService` unregisters loops per session.
17. **Browser CLI pattern** (`2026-08-20`): browser tool family reduced 20 → 6 core tools (`browser_open/snapshot/click/fill/screenshot/eval`, ~302 tokens vs ~934) — the long-tail (get text/HTML/URL, tabs, back/forward, scroll, type, press, hover, wait) lives in the `web-automation` seed skill as a stub pointing at the `agent-browser` CLI (`agent-browser skills get core`, version-matched). `agent-browser` added to `shell_tool.allowed_commands` (the shell sandbox: allowlist + metacharacter ban, no chaining). Schema budget guarded by `tests/sdk/test_tool_schema_budget.py`. Seed skills now refresh via hash sidecars (`.seed-hash`) when the seed changes and the user's copy is untouched — user-modified skills are never overwritten.
18. **Skills validation** (Pi-style, `2026-08-20`): skill loading is warnings-not-errors — invalid frontmatter names fall back to the directory name (with a diagnostic), over-long descriptions still load, only a missing description skips. Discovery respects `.gitignore`/`.ignore`/`.fdignore` (with `!` negation), dedupes symlinks via resolved paths, and reports name collisions (`registry.get_diagnostics()`). `disable_model_invocation: true` excludes a skill from the catalog. `load_skill()` is name-aware (finds skills whose frontmatter name differs from their dir).
19. **Conditional system-prompt guidelines** (`2026-08-20`): the memory-recall strategy section lists only tools actually enabled via capabilities (section vanishes if all memory tools are disabled); a shell-based file-ops guideline appears only when `shell_execute` is enabled and `files_glob_search`/`files_grep_search` are not. Shell output spill: truncated output is saved to `.shell_output/output-*.txt` under the workspace files dir with a `Full output: <path>` hint so the agent can `files_read` it back.

**Known Provider Behaviors:**
- OpenAI/Anthropic no longer emit duplicate `tool_end` — fixed by Phase 5 block-structured refactor
- Gemini streaming accumulates tool calls across chunks properly
- `provider_options` are now wired through `RunConfig.provider_options` to all provider calls (previously hardcoded `None`)

### HTTP Layer

Three endpoints, all SDK-powered:
- **REST**: `POST /message` — returns `MessageResponse`
- **SSE**: `POST /message/stream` — Server-Sent Events
- **WebSocket**: `/ws/conversation` — bidirectional with HITL interrupt/approve/reject, cancel, ping, and mid-turn steering (`steer` message → delivered after the current tool, remaining tools cancelled; follow-up steers run as the next turn)

Both SSE and WS routers now handle block-structured events (`text_start/delta/end`, `tool_input_start/delta/end`, `reasoning_start/delta/end`, `tool_result`) alongside backward-compat types (`ai_token`, `tool_start`, `tool_end`, `reasoning`).

### Database/Storage

Per-user storage is split across **two trees** (both must be backed up):

1. **`ea_root`** (default `~/Assistant/`, env `DEPLOYMENT_EA_ROOT`) — bulk user data via `DataPaths`: `Conversation/messages.db`, `Memory/` (ChromaDB), `Files/`, `Email/emails.db`, `Contacts/contacts.db`, `Todos/todos.db`, `Skills/`, `Subagents/`. Non-default users under `ea_root/Users/{user_id}/`; `default_user` uses the root.
2. **`data/users/{user_id}/`** (via `data_path`, env `DEPLOYMENT_DATA_PATH`) — per-user settings/capabilities/vault: `capabilities.yaml`, `item_scopes.db`, `settings.json`, `connectkit/`, seeded prompts (`summarisation_prompt.md`).
3. **`data/` root** — project-level: `cache/`, `logs/`, `jobs.db`, `templates/`, `traces/`.

Decision: **SQLite + ChromaDB per-user even for team/enterprise** (not shared DB).

### Deployment

Three supported modes (see `DEPLOYMENT.md`): **Local** (localhost, auth off), **Solo WAN** (one user, many devices — one server = sessions *and* files in sync; `EA_API_KEY` + Tailscale or VPS), **Multi-tenant** (container per user + Caddy subdomains).

Key facts:
- Entry point is `uv run assistant http` (console script `assistant` — `ea` was never valid). Listens on `0.0.0.0:8000` by default (`API_HOST`/`API_PORT`).
- Auth: `EA_API_KEY` authenticates the *connection* only; `EA_SOLO_BYPASS` (default true) skips auth for localhost. **There is no per-user authentication** — `user_id` is client-supplied (query/body param, default `default_user`). Multi-user deployments are container-per-user and/or trusted-network only.
- **One process per user store**: in-memory per-user caches (MessageStore, AgentLoop, session registry) + single-writer SQLite/ChromaDB mean replicas serving the same user are unsupported. Multi-tenant = N isolated containers, each one user.
- Docker (see `docker/`): image must `COPY seeds/ seeds/`; `DEPLOYMENT_EA_ROOT`/`DEPLOYMENT_DATA_PATH` must point into the mounted volume or user data silently lands in `/root/Assistant` and is lost on recreation.
- Client file caching (partial): `FileCache` in `http/workspace_cache.py` models `cloud_only` / `downloaded` / `pinned` statuses per path.
- Known gaps: no OIDC/per-user tokens, teams are skeleton (`data/teams/` unused), no offline/bidirectional sync, no horizontal scaling per user.

---

## 4. Coding Concerns & Pitfalls to Avoid

### CRITICAL: StreamChunk event types
The `StreamChunk.type` field is a `Literal` with **17 values**. When adding new event handling, always use `chunk.canonical_type` for comparison, not `chunk.type` directly, because backward-compat aliases map:
- `ai_token` → canonical `text_delta`
- `tool_start` → canonical `tool_input_start`
- `reasoning` → canonical `reasoning_delta`
- `usage` → canonical `usage`

### CRITICAL: user_id must be passed as separate parameter
```python
# CORRECT
logger.info("event_name", {"key": "value"}, user_id=user_id)

# WRONG — user_id inside data dict shows "default" in logs
logger.info("event_name", {"key": "value", "user_id": user_id})
```

### CRITICAL: Provider options are keyed by provider_id
When passing provider-specific options:
```python
# CORRECT — only Anthropic sees its options
provider_options={"anthropic": {"thinking": {"type": "enabled", "budget_tokens": 10000}}}

# WRONG — all providers see this
kwargs={"thinking": {"type": "enabled"}}  # leaks to OpenAI/Gemini
```

### CRITICAL: models.dev registry uses lazy loading
The registry (`src/sdk/registry.py`) fetches from `https://models.dev/api.json` on first access, caches to `data/cache/models.json`. If the API is unreachable, it falls back to a built-in subset. **Never hardcode model info — always use `get_model_info()` or `list_models()`.**

### CRITICAL: No parallel tool execution (by design)
The AgentLoop executes tools sequentially. This was a deliberate decision. If you need parallel execution, it requires a separate `ToolExecutor` class with async concurrency — do NOT just add `asyncio.gather()` in the loop.

### Watch out: ToolAnnotations.auto_approval only works for non-destructive tools
The `_should_interrupt()` method checks: if `destructive=True AND read_only=False` → interrupt. A tool that is both `destructive` AND `read_only` won't interrupt (read-only wins). This is intentional — a read-only destructive tool is a contradiction that defaults to safe.

### Watch out: TraceProvider spans are async context managers
```python
# CORRECT — async context manager
async with provider.start_span(SpanType.LLM_CALL, "call_0") as span:
    span.set_meta("tokens", 100)

# For sync-only tests, use start_span_sync/end_span
span = provider.start_span_sync(SpanType.AGENT, "test_run")
span.finish()
provider.end_span(span)
```

### Watch out: Ollama has two provider classes
- `OllamaLocal` — OpenAI-compatible at `/v1/chat/completions` (localhost or custom)
- `OllamaCloud` — Native `/api/chat` with Bearer auth (ollama.com)

Auto-detection happens in `create_model_from_config()`: if `OLLAMA_BASE_URL` points to ollama.com or `OLLAMA_API_KEY` is set, it uses `OllamaCloud`.

### Watch out: Use module-level imports for patchable functions
Tests that use `unittest.mock.patch` need a module-level attribute to target. `from X import Y` creates a local binding that patches can't reach. Use `import src.module as _m` and call `_m.func()` instead. Example: coordinator.py's `_paths.get_paths()` allows `patch("src.storage.paths.get_paths")` to work.

### Watch out: DataPaths.ea_root vs data_path
Workspace-scoped methods (`workspace_subagents_dir`, `user_subagents_dir`) use `self.root` (= `_ea_root`), NOT `self.base` (= `data_path`). Tests that need filesystem isolation MUST pass `ea_root=tmp_path`, not just `data_path=tmp_path`. `data_path` alone only affects template/legacy paths.

### Watch out: AgentLoop constructor changed
The `AgentLoop` now takes `run_config: RunConfig | None = None` instead of just `max_iterations`. If creating loops manually, use:
```python
loop = AgentLoop(
    provider=provider,
    tools=[...],
    system_prompt="...",
    middlewares=[...],
    run_config=RunConfig(max_llm_calls=50, cost_limit_usd=10.0),
)
```

### CRITICAL: user data in containers — DEPLOYMENT_EA_ROOT
Without `DEPLOYMENT_EA_ROOT` pointing into the mounted volume, all bulk user data (conversation, files, memory, email) goes to `/root/Assistant` inside the container and is **silently lost on recreation**. `DEPLOYMENT_DATA_PATH` alone does NOT cover `ea_root`. Same split matters in tests: `ea_root=tmp_path` for user-data isolation, `data_path=tmp_path` for settings/templates.

### CRITICAL: no per-user auth (trust model)
`EA_API_KEY` authenticates the connection, not the user. `user_id` comes from the request payload and is trusted as-is. Never claim per-user security boundaries in multi-user deployments; container-per-user (or a trusted network) is the only isolation today. Adding OIDC/per-user tokens is a prerequisite for public multi-user hosting.

### Watch out: one process per user store
MessageStore/AgentLoop caches and SQLite/ChromaDB writes are per-user and single-writer. Do not run multiple replicas serving the same user_id (sticky sessions do not fix Chroma or in-memory state). Horizontal scaling = a new user's own process, not another replica of an existing user.

---

## 5. Logging Best Practices

### Always Use the Logger
```python
from src.app_logging import get_logger, timer

logger = get_logger()

# Use timer for operations with duration tracking
with timer("operation_name", {"key": "value"}, user_id=user_id, channel="cli") as t:
    result = await do_work()
    
# Log at appropriate levels
logger.debug("detailed_info", {"data": "..."}, user_id=user_id)
logger.info("action_completed", {"result": "..."}, user_id=user_id)
logger.warning("potential_issue", {"warning": "..."}, user_id=user_id)
logger.error("operation_failed", {"error": "..."}, user_id=user_id)
logger.info("system_event", {"info": "..."}, user_id="system")
```

### Log Format
`data/logs/YYYY-MM-DD.jsonl`:
```json
{"timestamp": "2026-02-20T03:00:00Z", "user_id": "alice_test", "event": "agent.response", "level": "info", "channel": "cli", "data": {"response": "Hello!"}}
```

### Sensitive Data
The logger automatically redacts fields containing: `api_key`, `password`, `secret`, `token`, `key`

---

## 6. Project Structure

```
assistant/
├── src/
│   ├── __init__.py
│   ├── __main__.py              # CLI entry point
│   ├── app_logging.py           # Logging with timer
│   ├── http/
│   │   ├── main.py              # FastAPI app
│   │   ├── models.py             # Request/response models
│   │   ├── ws_protocol.py        # WS message types (17+ types, incl. steer/steer_ack)
│   │   └── routers/
│   │       ├── conversation.py   # REST + SSE endpoints
│   │       ├── ws.py             # WebSocket endpoint
│   │       ├── tools.py          # Tools API (metadata, enable/disable)
│   │       ├── capabilities.py   # Capabilities CRUD (tools/skills/subagents)
│   │       └── ...               # Other routers
│   ├── storage/
│   │   ├── conversation.py      # Message storage
│   │   ├── user.py             # User management
│   │   ├── memory.py           # memory_profile tool (recall-based digest)
│   │   ├── messages.py         # MessageStore (CoreMem wrapper)
│   │   └── paths.py            # DataPaths (ea_root, workspace scoping)
│   ├── sdk/                     # ★ Custom Agent SDK (THE CORE)
│   │   ├── __init__.py          # Public API exports (re-exports HybridDB, SearchMode)
│   │   ├── messages.py          # Message, ToolCall, StreamChunk
│   │   ├── tools.py             # @tool, ToolDefinition, ToolAnnotations, ToolResult, ToolRegistry
│   │   ├── state.py             # AgentState
│   │   ├── loop.py              # AgentLoop, Interrupt, RunConfig, CostTracker
│   │   ├── middleware.py             # Middleware ABC
│   │   ├── middleware_rubric.py       # RubricMiddleware (verification loop)
│   │   ├── middleware_summarization.py  # SummarizationMiddleware
│   │   ├── native_tools.py      # ToolRegistry + category mapping
│   │   ├── capabilities.py      # load/merge/save capabilities, tool defaults
│   │   ├── agent_validation.py  # validate_agent_def (no circular imports)
│   │   ├── agent_profile.py     # EA-specific AgentProfile validation
│   │   ├── runner.py             # create_sdk_loop, run_sdk_agent (capabilities-filtered)
│   │   ├── registry.py          # models.dev integration (4172+ models)
│   │   ├── validation.py        # normalize_tool_schema, repair_tool_call
│   │   ├── guardrails.py        # InputGuardrail, OutputGuardrail, ToolGuardrail
│   │   ├── handoffs.py          # Handoff, HandoffInput
│   │   ├── tracing.py           # TraceProvider, Span, ConsoleTraceProcessor
│   │   ├── subagent_models.py   # AgentDef, SubagentResult, TaskCancelledError, TaskStatus
│   │   ├── work_queue.py        # WorkQueueDB (aiosqlite, per-user SQLite)
│   │   ├── coordinator.py       # SubagentCoordinator (PROFILE.md, capabilities filtering)
│   │   ├── tools_core/          # ★ SDK-native tool implementations (60 registered tools)
│   │   │   ├── time.py, shell.py, filesystem.py, file_search.py
│   │   │   ├── file_versioning.py, todos.py, contacts.py, message.py
│   │   │   ├── memory.py, browser.py, browser_agent.py
│   │   │   ├── subagent.py, apps.py, research.py, summarize.py
│   │   │   ├── web.py, user_prompt.py, skills.py
│   │   │   ├── mcp.py, mcp_bridge.py, mcp_manager.py, mcp_config.py
│   │   │   ├── skills.py, research.py, shell.py, cli_adapter.py
│   │   │   ├── todos_storage.py, contacts_storage.py, agent_scheduler_db.py
│   │   │   ├── email_db.py, email_sync.py
│   │   └── providers/
│   │       ├── base.py           # LLMProvider ABC, ModelInfo, ModelCost
│   │       ├── ollama.py         # OllamaLocal + OllamaCloud
│   │       ├── openai.py         # OpenAIProvider
│   │       ├── anthropic.py      # AnthropicProvider (with thinking blocks)
│   │       ├── gemini.py         # GeminiProvider (with thinkingConfig)
│   │       ├── factory.py        # create_provider, create_model_from_config
│   │       └── __init__.py
│   └── skills/                  # Agent Skills system (Agentskills.io compatible)
│       ├── middleware.py         # SkillMiddleware
│       ├── registry.py           # SkillRegistry
│       └── tools.py             # skills_list, skills_load
├── tests/
│   ├── sdk/                     # ★ SDK unit tests (800+ tests)
│   │   ├── test_messages.py, test_tools.py, test_registry.py
│   │   ├── test_providers.py, test_sdk_loop.py
│   │   ├── test_subagent_v1.py, test_subagent_tools_async.py
│   │   ├── test_workspace_isolation.py
│   │   ├── test_capabilities.py, test_agent_profile.py
│   │   └── ...
│   ├── api/                      # HTTP endpoint tests (incl. test_connectors_api.py — ConnectKit contract)
│   ├── storage/                  # Storage tests (paths, messages)
│   └── evaluation/               # Persona evaluation
├── pyproject.toml                # requires-python >=3.11,<3.14
├── native-sdk-experiment/       # ★ Native chat app (Zig + Native SDK) — see "Native App" section
│   ├── src/main.zig              # Model/Msg/update + buildView (~5k lines)
│   ├── src/app.native           # Declarative markup twin (NOT embedded — cosmetic mirror)
│   ├── src/tests.zig            # Zig unit tests (90)
│   └── tests/frontend_suite.sh  # Automation suite (51 tests, native automate)
├── config.yaml
├── DEPLOYMENT.md                # Deployment guide (modes, backups, secrets, gaps)
├── docker/                      # Dockerfile, docker-compose.yaml, Caddyfile, deploy.sh
└── docs/
    └── superpowers/
        ├── specs/                # Design specs
        │   ├── 2026-05-31-messagestore-on-coremem-design.md
        │   ├── 2026-05-31-ollama-routing-design.md
        │   └── 2026-06-01-unified-capabilities-agent-profile-design.md
        └── plans/                # Implementation plans
            ├── 2026-06-01-unified-capabilities-agent-profile-backend.md
```

---

## 7. Configuration

### Environment Variables
- Use `.env` for local development
- Use `.env.example` as template
- All config via `src/config/settings.py`; env prefixes: `EA_` (auth: `EA_API_KEY`, `EA_SOLO_BYPASS`), `DEPLOYMENT_` (`DEPLOYMENT_MODE`, `DEPLOYMENT_DATA_PATH`, `DEPLOYMENT_EA_ROOT`), `API_` (`API_HOST`, `API_PORT`), `LOGGING_` (`LOGGING_LEVEL`, `LOGGING_JSON_DIR`), `AGENT_`, `SUMMARY_`, `LANGFUSE_`, `TOOLS_` (Firecrawl), `OLLAMA_`, `MESSAGES_`

### Config Priority
1. Environment variables (highest)
2. `.env` file
3. `config.yaml`
4. Default values (lowest)

---

## 8. Phase Progress

| Phase | Status | Tests | Description |
|-------|--------|-------|-------------|
| **0** | ✅ Done | 194 | Test harness & baseline |
| **0.5** | ✅ Done | 100 API + 32 WS | API contracts + WS protocol |
| **1** | ✅ Done | 204 | Core SDK (Messages, Tools, State) |
| **2** | ✅ Done | 51 | LLM Provider abstraction |
| **3** | ✅ Done | 48 | Agent Loop |
| **4** | ✅ Done | 347 total | Middleware + SDK HTTP wiring |
| **5** | ✅ Done | +63 new | Structured Streaming + Tool Annotations |
| **6** | ✅ Done | (in 5) | Guardrails, Handoffs, Tracing, RunConfig, CostTracker |
| **models.dev** | ✅ Done | +22 | Dynamic model registry (4172+ models) |
| **7** | ✅ Done | — | Tool Migration (all 81 tools SDK-native) |
| **10.1** | ✅ Done | — | Bug fixes |
| **10.3** | ✅ Done | — | Discovery-based skills |
| **10.4** | ✅ Done | +8 | Parallel tool execution |
| **10.5** | ✅ Done | +26 | ToolResult, shell hooks, usage tracking, provider_options |
| **8** | ✅ Done | — | Cleanup & LangChain removal |
| **9** | 🔲 Future | — | Extract & Open Source SDK |
| **10.2** | ✅ Done | +20 | MCP Tool Bridge |
| **11** | ✅ Done | +38 | Subagent V1 (work_queue, coordinator, middlewares, 8 tools) |
| **12** | ✅ Done | — | Unified Capabilities, AgentProfile, OSS repos |
| **13** | ✅ Done | ~943 SDK+unit | Skills/subagents scoping UI, ScopePicker, API CRUD, workspace isolation |
| **14** | ✅ Done | +14 frontend | Tools page Phase A — built-in tools + connector catalog, api-key connect, OAuth flow (native app) |
| **15** | ✅ Done | — | Tools folded into Settings as a section; sidebar drops Tools/Skills/Subagents rows; high-end settings redesign |

### Native App (native-sdk-experiment)

Zig + Native SDK desktop app (macOS, `src/main.zig` ~5k lines + `src/app.native` markup twin). Backend contract: `http://127.0.0.1:8080`, `user_id=native_sdk_chat`, `workspace_id=personal`.

- **Sidebar**: New chat, search, chat list, then Settings + theme toggle only. Tools/Skills/Subagents rows removed — the Tools page lives **inside Settings** as a third section.
- **Settings sections**: Models (model catalog + role toggle), General (rubric, appearance, about), Tools (built-in tool enable/disable + SaaS connectors).
- **Tools section**: `GET /tools` list with search + per-tool scope toggle (`PATCH /tools/{name}` `{"scope":"all"|"none"}` — server resets loops), `GET /connectors/catalog` (ConnectKit, includes `connected` flag), api_key credential form (full JSON control-char escaping, `appendJsonString`), OAuth2 flow (stores creds → `open` browser `/auth/login` → 2s catalog polling with 60-tick timeout + vanished-service stop + cancel).
- **Panel behavior**: Escape closes settings via SDK `on_key` fallback (modifier-gated); OAuth poll cancelled on close; entrance animation is opacity-only smoothstep; reduced-motion honored.
- **Key SDK APIs (verified)**: `fx.fetch` (NOT `fx.request` — plan-era name, doesn't exist), `fx.startTimer(.mode = .repeating)` + `fx.cancelTimer`, Zig 0.16 `std.process.run(allocator, io, .{.argv})` with `result.term != .exited`.
- **Settings design language**: nested cards (surface + hairline border + radius), eyebrow labels (`upperAscii` micro-headings), shared 12px alignment grid (sidebar and content rows align), segmented role controls, Geist typography.
- **Credential form budget**: `credential_form_nodes = 2 * max_required_fields + 4` (=12) — regression-tested via `--connectform` (4-field fixture served through `CONNECTKIT_SPEC_DIR`).

### Subagent V1 Architecture

SQLite work_queue-backed coordination with supervisor pattern. Full design in `docs/SUBAGENT_RESEARCH.md`.

**New files:**
- `src/sdk/subagent_models.py` — `AgentDef`, `SubagentResult`, `TaskStatus`, `TaskCancelledError`
- `src/sdk/work_queue.py` — `WorkQueueDB` (aiosqlite, per-user at `data/private/subagents/work_queue.db`)
- `src/sdk/subagent_context.py` — `SubagentContext` (replaces middleware-based progress/instruction)
- `src/sdk/coordinator.py` — `SubagentCoordinator` (create, update, invoke, cancel, instruct, delete)
- `tests/sdk/test_subagent_v1.py` — 38 tests

**10 V1 tools** (in `src/sdk/tools_core/subagent.py`):
- `subagent_create` — create AgentDef, persist to disk
- `subagent_update` — amend existing AgentDef (partial update)
- `subagent_start` — insert task into work_queue + run AgentLoop with middlewares
- `subagent_list` — list AgentDefs + active tasks
- `subagent_check` — check one task status/result
- `subagent_tasks` — list task status/progress
- `subagent_instruct` — inject course-correction into running subagent
- `subagent_cancel` — set cancel_requested flag
- `subagent_delete` — remove AgentDef + cancel running tasks
- `subagent_delegate` — run a subagent synchronously and return its output (blocks, parallel-capable)

**Key design decisions:**
- Config frozen at invocation into `work_queue.config` (amendments don't affect running tasks)
- Recursion guard: subagent tools (`subagent_*`) are blocked via `capabilities.yaml` defaults (was `disallowed_tools`)
- `SubagentCoordinator.start()` schedules background execution and returns a task ID
- Progress via `SubagentContext` + polling; SubagentContext checks cancel/instructions before each LLM call
- Doom loop: same tool+args called 3x → `progress.stuck = true` + auto-instruction
- Agent definitions use **PROFILE.md** (frontmatter + Markdown body) matching Agentskills.io convention

### Remaining Work for Phase 5+6 Exit Criteria

- [x] All StreamChunk events use block-structured format
- [x] Backward-compat aliases pass existing tests
- [x] Reasoning persists in Message and conversation history
- [x] `provider_options` flow through to provider calls
- [x] Tool annotations on native tools (`time_get`) + langchain adapter defaults
- [x] Auto-approval based on `ToolAnnotations.destructive`
- [x] `repair_tool_call()` handles malformed JSON
- [x] No duplicate `tool_end` events
- [x] `Message.usage` populated by all providers (OpenAI, Anthropic, Gemini, Ollama)
- [x] `StreamChunk.usage_event()` carries usage data in streaming
- [x] `CostTracker.add_usage()` receives actual token counts from `Message.usage`
- [x] `RunConfig.provider_options` wired through `AgentLoop` to all provider calls
- [ ] Integration test: reasoning model returns thinking content (need live API)
- [x] 470+ SDK tests passing

### Phase 7: Tool Migration Status

**All tools migrated to `src/sdk/tools_core/` (32 files, ~8,500 lines):**

| Module | Tools | Count |
|--------|-------|-------|
| `time.py` | `time_get` | 1 |
| `shell.py` | `shell_execute` | 1 |
| `filesystem.py` | `files_list`, `files_read`, `files_write`, `files_edit`, `files_delete`, `files_mkdir`, `files_rename` | 7 |
| `file_search.py` | `files_glob_search`, `files_grep_search` | 2 |
| `file_versioning.py` | `files_versions_list`, `files_versions_restore`, `files_versions_delete`, `files_versions_clean` | 4 |
| `todos.py` | `todos_list`, `todos_add`, `todos_update`, `todos_delete` | 4 |
| `contacts.py` | `contacts_list`, `contacts_add`, `contacts_update`, `contacts_delete`, `contacts_search` | 5 |
| `memory.py` | `memory_profile` | 1 |
| `browser.py` | `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_eval` | 6 |
| `subagent.py` | `subagent_create`, `subagent_update`, `subagent_start`, `subagent_check`, `subagent_tasks`, `subagent_list`, `subagent_instruct`, `subagent_cancel`, `subagent_delete`, `subagent_delegate` | 10 |
| `apps.py` | `app_create`, `app_list`, `app_schema`, `app_delete`, `app_insert`, `app_update`, `app_delete_row`, `app_column_add`, `app_column_delete`, `app_column_rename`, `app_query`, `app_search_fts` | 12 |
| `skills.py` | `skills_load`, `skills_reload` | 2 |
| `web.py` | `web_fetch`, `web_search` | 2 |
| `message.py` | `message_search`, `message_count`, `message_history`, `message_timeline` | 4 |
| `summarize.py` | `summarize_session` | 1 |
| `user_prompt.py` | `user_prompt_get`, `user_prompt_set` | 2 |
| `research.py` | `research_start`, `research_list` | 2 |
| `tool_search.py` / `tool_reload.py` | `tool_search`, `tool_reload` (added by the runner) | 2 |
| `mcp.py` | `mcp_list`, `mcp_reload`, `mcp_tools` | 3 |

Skills tools were refactored significantly: `skill_create` removed (use `files_write`), `skills_list` removed (catalog injected in system prompt), `sql_write_query` removed.

**NOW AVAILABLE VIA MCP BRIDGE:**
- MCP tools are dynamically discovered and registered as `mcp__{server}__{tool}` via `MCPToolBridge`
- Meta-tools (`mcp_list`, `mcp_reload`, `mcp_tools`) are now native async `ToolDefinition` instances (no `_run_async` hack)

**SKIPPED (MCP tools are now native async ToolDefinitions):**
- `mcp_list`, `mcp_reload`, `mcp_tools` — now in `src/sdk/tools_core/mcp.py` as async `ToolDefinition` instances
- `MCPToolBridge` dynamically creates `ToolDefinition` for discovered MCP server tools as `mcp__{server}__{tool}`

---

## 9. Dependencies

### Adding Dependencies
```bash
uv add package_name          # Runtime dependency
uv add --dev package_name    # Development dependency
```

### Version Pinning
- Use minimum versions in `pyproject.toml` (e.g., `>=1.0.0`)
- Lock versions in `uv.lock` (committed to repo)

### Key OSS Dependencies
| Package | Source | Purpose |
|---------|--------|---------|
| `coremem>=0.13.1` | PyPI | Zero-LLM conversation memory (compiler + dreaming + search; observer/reflector removed in 0.10) |
| `hybriddb>=0.5.6` | PyPI | SQLite + FTS5 + ChromaDB hybrid storage |
| `agentprofile` | local editable | Portable agent definition (PROFILE.md schema + parser) |

### LangChain Dependencies — REMOVED in Phase 8
All LangChain and LangGraph dependencies have been removed:
- `langchain`, `langchain-core`, `langchain-ollama`, `langchain-anthropic`, `langchain-openai` — deleted
- `langgraph`, `langgraph-checkpoint-sqlite`, `langgraph-sdk`, `langgraph-prebuilt`, `langsmith` — deleted
- `langchain-mcp-adapters` — deleted (replaced by native `mcp` SDK via `MCPManager` + `MCPToolBridge`)
- `src/tools/`, `src/agents/`, `src/llm/`, `src/middleware/` directories — deleted
- `src/sdk/langchain_adapter.py` — deleted

## 10. OSS Repositories

The project extracts reusable components into separate OSS repos:

| Repo | GitHub | Purpose |
|------|--------|---------|
| CoreMem | `open-assistants-lab/CoreMem` | Zero-LLM conversation memory |
| HybridDB | `open-assistants-lab/HybridDB` | Hybrid SQLite + FTS5 + ChromaDB storage |
| AgentProfile | `open-assistants-lab/AgentProfile` | Portable agent definition (PROFILE.md) |
