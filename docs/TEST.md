# Assistant — Integration Test Plan

> SDK-native architecture. No LangChain. 470+ unit tests. HTTP integration via 25 personas.

---

## Architecture Overview

| Layer | Implementation |
|-------|---------------|
| **Agent Loop** | `AgentLoop` — async ReAct with guardrails, handoffs, tracing |
| **Tools** | 70+ SDK-native `ToolDefinition` in `src/sdk/tools_core/` |
| **Streaming** | Block-structured (`text_start/delta/end`, `tool_input_start/delta/end`, `reasoning_start/delta/end`, `tool_result`, `interrupt`, `done`) |
| **Middleware** | `MemoryMiddleware`, `SummarizationMiddleware`, `ProgressMiddleware`, `InstructionMiddleware` |
| **MCP** | `MCPToolBridge` — tools registered as `mcp__{server}__{tool}`, degraded-mode |
| **Subagents** | SQLite `WorkQueueDB` + `SubagentCoordinator` + 8 V1 tools |
| **Providers** | OpenAI, Anthropic, Gemini, Ollama (local + cloud) |
| **Parallel Tools** | Classified (parallel_safe / sequential / interrupt), `asyncio.gather()` |
| **Storage** | HybridDB (SQLite + FTS5 + ChromaDB) per-user |

---

## Tool Inventory (70+)

### Core (3)

| Tool | Module | Annotations |
|------|--------|-------------|
| `time_get` | `tools_core/time.py` | readOnly, idempotent |
| `shell_execute` | `tools_core/shell.py` | destructive, openWorld |
| `memory_connect` | `tools_core/memory.py` | idempotent |

### Filesystem (11)

| Tool | Module | Annotations |
|------|--------|-------------|
| `files_list` | `tools_core/filesystem.py` | readOnly, idempotent |
| `files_read` | `tools_core/filesystem.py` | readOnly, idempotent |
| `files_write` | `tools_core/filesystem.py` | destructive |
| `files_edit` | `tools_core/filesystem.py` | destructive, idempotent |
| `files_delete` | `tools_core/filesystem.py` | destructive |
| `files_mkdir` | `tools_core/filesystem.py` | idempotent |
| `files_rename` | `tools_core/filesystem.py` | destructive |
| `files_glob_search` | `tools_core/file_search.py` | readOnly, idempotent |
| `files_grep_search` | `tools_core/file_search.py` | readOnly, idempotent |
| `files_versions_list` | `tools_core/file_versioning.py` | readOnly, idempotent |
| `files_versions_restore` | `tools_core/file_versioning.py` | destructive |
| `files_versions_delete` | `tools_core/file_versioning.py` | destructive |
| `files_versions_clean` | `tools_core/file_versioning.py` | destructive |

### Memory (4)

| Tool | Module | Annotations |
|------|--------|-------------|
| `memory_get_history` | `tools_core/memory.py` | readOnly, idempotent |
| `memory_search` | `tools_core/memory.py` | readOnly, idempotent |
| `memory_search_all` | `tools_core/memory.py` | readOnly, idempotent |
| `memory_search_insights` | `tools_core/memory.py` | readOnly, idempotent |

### Firecrawl / Web (8)

| Tool | Module | Annotations |
|------|--------|-------------|
| `scrape_url` | `tools_core/firecrawl.py` | readOnly, idempotent, openWorld |
| `search_web` | `tools_core/firecrawl.py` | readOnly, idempotent, openWorld |
| `map_url` | `tools_core/firecrawl.py` | readOnly, idempotent, openWorld |
| `crawl_url` | `tools_core/firecrawl.py` | openWorld |
| `get_crawl_status` | `tools_core/firecrawl.py` | readOnly, idempotent |
| `cancel_crawl` | `tools_core/firecrawl.py` | destructive |
| `firecrawl_status` | `tools_core/firecrawl.py` | readOnly, idempotent |
| `firecrawl_agent` | `tools_core/firecrawl.py` | openWorld |

### Browser (22)

| Tool | Module | Annotations |
|------|--------|-------------|
| `browser_open` | `tools_core/browser.py` | openWorld |
| `browser_snapshot` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_click` | `tools_core/browser.py` | openWorld |
| `browser_fill` | `tools_core/browser.py` | openWorld |
| `browser_type` | `tools_core/browser.py` | openWorld |
| `browser_press` | `tools_core/browser.py` | openWorld |
| `browser_scroll` | `tools_core/browser.py` | idempotent |
| `browser_hover` | `tools_core/browser.py` | openWorld |
| `browser_screenshot` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_eval` | `tools_core/browser.py` | destructive, openWorld |
| `browser_get_title` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_get_text` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_get_html` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_get_url` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_tab_new` | `tools_core/browser.py` | openWorld |
| `browser_tab_close` | `tools_core/browser.py` | destructive |
| `browser_back` | `tools_core/browser.py` | idempotent |
| `browser_forward` | `tools_core/browser.py` | idempotent |
| `browser_wait_text` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_sessions` | `tools_core/browser.py` | readOnly, idempotent |
| `browser_close_all` | `tools_core/browser.py` | destructive |
| `browser_status` | `tools_core/browser.py` | readOnly, idempotent |

### Apps (14)

| Tool | Module | Annotations |
|------|--------|-------------|
| `app_create` | `tools_core/apps.py` | destructive |
| `app_list` | `tools_core/apps.py` | readOnly, idempotent |
| `app_schema` | `tools_core/apps.py` | readOnly, idempotent |
| `app_delete` | `tools_core/apps.py` | destructive |
| `app_insert` | `tools_core/apps.py` | destructive |
| `app_update` | `tools_core/apps.py` | destructive, idempotent |
| `app_delete_row` | `tools_core/apps.py` | destructive |
| `app_column_add` | `tools_core/apps.py` | destructive |
| `app_column_delete` | `tools_core/apps.py` | destructive |
| `app_column_rename` | `tools_core/apps.py` | destructive |
| `app_query` | `tools_core/apps.py` | readOnly |
| `app_search_fts` | `tools_core/apps.py` | readOnly, idempotent |
| `app_search_semantic` | `tools_core/apps.py` | readOnly, idempotent |
| `app_search_hybrid` | `tools_core/apps.py` | readOnly, idempotent |

### Subagent V1 (8)

| Tool | Module | Annotations |
|------|--------|-------------|
| `subagent_create` | `tools_core/subagent.py` | destructive |
| `subagent_update` | `tools_core/subagent.py` | destructive |
| `subagent_start` | `tools_core/subagent.py` | destructive, openWorld |
| `subagent_list` | `tools_core/subagent.py` | readOnly, idempotent |
| `subagent_check` | `tools_core/subagent.py` | readOnly, idempotent |
| `subagent_tasks` | `tools_core/subagent.py` | readOnly, idempotent |
| `subagent_instruct` | `tools_core/subagent.py` | destructive |
| `subagent_cancel` | `tools_core/subagent.py` | destructive |
| `subagent_delete` | `tools_core/subagent.py` | destructive |

### MCP (3)

| Tool | Module | Annotations |
|------|--------|-------------|
| `mcp_list` | `tools_core/mcp.py` | readOnly, idempotent |
| `mcp_reload` | `tools_core/mcp.py` | destructive |
| `mcp_tools` | `tools_core/mcp.py` | readOnly, idempotent |

### Skills (5)

| Tool | Module | Annotations |
|------|--------|-------------|
| `skills_list` | `tools_core/skills.py` | readOnly, idempotent |
| `skills_search` | `tools_core/skills.py` | readOnly, idempotent |
| `skills_load` | `tools_core/skills.py` | readOnly |
| `skill_create` | `tools_core/skills.py` | destructive |
| `sql_write_query` | `tools_core/skills.py` | destructive (skill-gated) |

### Disabled (Pending Redesign)

| Domain | Tools | Count |
|--------|-------|-------|
| Email | `email_connect`, `email_disconnect`, `email_accounts`, `email_list`, `email_get`, `email_search`, `email_send`, `email_sync` | 8 |
| Contacts | `contacts_list`, `contacts_get`, `contacts_add`, `contacts_update`, `contacts_delete`, `contacts_search` | 6 |
| Todos | `todos_list`, `todos_add`, `todos_update`, `todos_delete`, `todos_extract` | 5 |

---

## Middleware Stack

| Middleware | Hook(s) | Purpose |
|-----------|---------|---------|
| `MemoryMiddleware` | `abefore_agent` | Inject relevant memories into context |
| `SummarizationMiddleware` | `aafter_model` | Auto-summarize at token threshold |
| `ProgressMiddleware` | `abefore_model` | Update subagent progress + doom loop detection |
| `InstructionMiddleware` | `abefore_model` | Check cancel/injections before each LLM call |

---

## HTTP Integration Testing Strategy

### Endpoints

| Endpoint | Method | Protocol |
|----------|--------|----------|
| `POST /message` | REST | JSON request/response |
| `POST /message/stream` | SSE | Server-Sent Events (block-structured) |
| `/ws/conversation` | WebSocket | Bidirectional with HITL interrupt/approve/reject |

### Test Runner

```bash
# Start the HTTP server
uv run assistant http

# Run persona evaluation (25 personas × 100 queries each)
uv run python tests/evaluation/evaluate.py
```

---

## 25 Personas for HTTP Integration Testing

Each persona tests different interaction styles, edge cases, and feature domains. Run each against `POST /message` and `POST /message/stream` to verify all tools, middlewares, and features work end-to-end.

### Communication Style Personas (p1–p10)

| ID | Name | Style | Tests |
|----|------|-------|-------|
| **p1** | Direct Dave | Terse, no small talk | Tool selection accuracy under minimal input |
| **p2** | Polite Pam | Formal, please/thank you | Agent politeness adaptation |
| **p3** | Casual Chris | Informal, slang, abbreviations | Agent handles informal input correctly |
| **p4** | Questioning Quinn | Asks clarification, inquisitive | Agent provides helpful explanations |
| **p5** | Storytelling Sam | Narrative, gives context | Agent extracts intent from verbose input |
| **p6** | Commanding Chris | Authoritative, expects action | Agent acts decisively |
| **p7** | Emoji Eva | Expressive, uses emojis | Agent handles non-standard input |
| **p8** | Minimalist Mike | Extremely brief, single words | Agent infers intent from minimal cues |
| **p9** | Technical Terry | Precise, parameter-oriented | Agent handles technical requests |
| **p10** | Confused Clara | Uncertain, needs guidance | Agent provides patient help |

### Feature Depth Personas (p11–p20)

| ID | Name | Style | Primary Feature Domain |
|----|------|-------|----------------------|
| **p11** | Analytical Alex | Data-driven, metrics | Apps (query, FTS, semantic, hybrid search) |
| **p12** | Efficient Eddie | Speed-focused | Parallel tool execution, quick queries |
| **p13** | Verbose Victor | Comprehensive, detailed | SummarizationMiddleware, long conversations |
| **p14** | Curious Casey | Follow-ups, explores | Skills (list, search, load), progressive disclosure |
| **p15** | Busy Brian | Multi-task, parallel | Parallel tool calls, subagent batch |
| **p16** | Organized Olivia | Structured, categorized | Files (mkdir, rename, versions), Apps |
| **p17** | Flexible Fran | Changes mind mid-task | InstructionMiddleware, subagent_instruct |
| **p18** | Goal-Oriented Gary | Progress tracking | subagent_check, subagent_tasks, subagent_instruct |
| **p19** | Collaborative Carol | Team-focused | Browser tools (share, screenshot), Apps |
| **p20** | Privacy-First Paul | Security conscious | Memory isolation, file access controls |

### Stress & Edge Case Personas (p21–p25)

| ID | Name | Style | Primary Test Focus |
|----|------|-------|-------------------|
| **p21** | Quick Quinn | Ultra-fast single commands | Response latency, simple tool calls |
| **p22** | Deep Diver | Complex multi-step | Subagent lifecycle, multi-tool workflows |
| **p23** | Error-Prone Eddie | Bad inputs, edge cases | Error handling, `repair_tool_call()`, validation |
| **p24** | Context Carter | References previous turns | MemoryMiddleware, conversation continuity |
| **p25** | Mixed Mike | Random varied queries | General stress test, all features |

---

## Feature Coverage Matrix by Persona

| Feature | p1–p10 (Style) | p11–p20 (Depth) | p21–p25 (Stress) |
|---------|:---:|:---:|:---:|
| Core (time, shell, memory) | ✅ | ✅ | ✅ |
| Filesystem (7 + search + versions) | ✅ | ✅ (p16 deep) | ✅ |
| Memory tools | ✅ | ✅ (p24 deep) | ✅ |
| Firecrawl / Web | ✅ | ✅ (p19) | ✅ |
| Browser (22) | partial | ✅ (p19 deep) | ✅ |
| Apps (14) | partial | ✅ (p11 deep) | ✅ |
| Subagent V1 (8) | partial | ✅ (p15, p17, p18 deep) | ✅ (p22 deep) |
| MCP (3) | partial | ✅ (p14) | ✅ |
| Skills (5) | partial | ✅ (p14 deep) | ✅ |
| Summarization | partial | ✅ (p13 deep) | ✅ |
| Parallel execution | partial | ✅ (p12, p15) | ✅ |
| Interrupt / HITL | partial | ✅ (p20) | ✅ (p23) |
| Streaming (SSE) | ✅ | ✅ | ✅ |
| Error handling | partial | ✅ (p20) | ✅ (p23 deep) |

---

## Test Cases Per Feature Domain

### 1. Core Tools

| # | Test | Method | Expected |
|---|------|--------|----------|
| 1.1 | `time_get` | "what time is it?" | Current time + timezone |
| 1.2 | `time_get` with tz | "what time is it in Tokyo?" | Tokyo time |
| 1.3 | `shell_execute` | "run python3 -c 'print(2+2)'" | "4" |
| 1.4 | `shell_execute` disallowed | "run rm -rf /" | Blocked (not in allowed list) |
| 1.5 | `shell_execute` timeout | "run sleep 60" | Timeout error |
| 1.6 | `memory_get_history` | "what did we talk about?" | Conversation history |
| 1.7 | `memory_search` | "search my memory for python" | Relevant memories |
| 1.8 | `memory_search_all` | "show all my memories" | All stored memories |
| 1.9 | `memory_search_insights` | "any insights from past conversations?" | Extracted insights |
| 1.10 | `memory_connect` | "connect to memory store" | Connection established |

### 2. Filesystem

| # | Test | Method | Expected |
|---|------|--------|----------|
| 2.1 | `files_list` | "list my files" | File listing |
| 2.2 | `files_read` | "read notes.txt" | File content |
| 2.3 | `files_write` | "create test.txt with hello world" | File created |
| 2.4 | `files_edit` | "change test.txt line 1 to hi" | File edited |
| 2.5 | `files_delete` | "delete test.txt" | Interrupt → requires approval |
| 2.6 | `files_mkdir` | "create folder projects" | Directory created |
| 2.7 | `files_rename` | "rename projects to myprojects" | Renamed |
| 2.8 | `files_glob_search` | "find all *.py files" | Python files listed |
| 2.9 | `files_grep_search` | "search for TODO in files" | Matches shown |
| 2.10 | `files_versions_list` | "show file versions" | Version history |
| 2.11 | `files_versions_restore` | "restore previous version of notes.txt" | Restored |
| 2.12 | `files_versions_clean` | "clean old versions" | Old versions removed |

### 3. Firecrawl / Web

| # | Test | Method | Expected |
|---|------|--------|----------|
| 3.1 | `search_web` | "search for python tutorials" | Search results |
| 3.2 | `scrape_url` | "scrape example.com" | Page content |
| 3.3 | `map_url` | "map docs site" | URL list |
| 3.4 | `crawl_url` | "crawl example.com limit 5" | Crawl started |
| 3.5 | `get_crawl_status` | "check crawl status" | Status returned |
| 3.6 | `cancel_crawl` | "cancel the crawl" | Cancelled |
| 3.7 | `firecrawl_status` | "firecrawl status" | Connection status |
| 3.8 | `firecrawl_agent` | "use firecrawl agent to research X" | Agent result |

### 4. Browser

| # | Test | Method | Expected |
|---|------|--------|----------|
| 4.1 | `browser_open` | "open https://example.com" | Page loaded |
| 4.2 | `browser_snapshot` | "take a snapshot" | Accessibility tree |
| 4.3 | `browser_click` | "click the login button" | Element clicked |
| 4.4 | `browser_fill` | "fill the email field with test@test.com" | Field filled |
| 4.5 | `browser_screenshot` | "take a screenshot" | Screenshot returned |
| 4.6 | `browser_get_title` | "what's the page title?" | Title returned |
| 4.7 | `browser_get_text` | "get all text on page" | Text content |
| 4.8 | `browser_scroll` | "scroll down" | Scrolled |
| 4.9 | `browser_tab_new` | "open new tab" | New tab opened |
| 4.10 | `browser_close_all` | "close all browser sessions" | All closed |

### 5. Apps

| # | Test | Method | Expected |
|---|------|--------|----------|
| 5.1 | `app_create` | "create library app with books table" | App created |
| 5.2 | `app_insert` | "insert book: title=1984, description=Dystopian" | Row inserted |
| 5.3 | `app_query` | "query all books" | Results |
| 5.4 | `app_search_fts` | "keyword search books for dystopian" | FTS results |
| 5.5 | `app_search_semantic` | "semantic search books for authoritarian" | Vector results |
| 5.6 | `app_search_hybrid` | "hybrid search books for oppressive regime" | Combined results |
| 5.7 | `app_update` | "update book 1984 add rating 5" | Updated |
| 5.8 | `app_column_add` | "add rating column to books" | Column added |
| 5.9 | `app_delete` | "delete the library app" | App deleted |
| 5.10 | `app_schema` | "show library app schema" | Schema shown |

### 6. Subagent V1

| # | Test | Method | Expected |
|---|------|--------|----------|
| 6.1 | `subagent_create` | "create research subagent with files_read, search_web" | AgentDef created |
| 6.2 | `subagent_update` | "update research subagent add memory_search" | AgentDef updated |
| 6.3 | `subagent_start` | "start research subagent to find AI papers" | Task queued + executed |
| 6.4 | `subagent_list` | "list my subagents" | AgentDefs + active tasks |
| 6.5 | `subagent_check` | "check subagent job for research task" | Status + progress |
| 6.6 | `subagent_instruct` | "tell research subagent to also check arxiv" | Instruction injected |
| 6.7 | `subagent_cancel` | "cancel the research task" | CancelRequested flag set |
| 6.8 | `subagent_delete` | "delete research subagent" | AgentDef + tasks removed |
| 6.9 | Doom loop | Start subagent that loops same tool 3x | auto-instruction injected |
| 6.10 | Cost limit | Start with cost_limit_usd=0.01 | CostLimitExceededError |
| 6.11 | Timeout | Start with timeout=1s | TimeoutError |
| 6.12 | No recursion | Subagent tries subagent_create | Tool not available (disallowed) |

### 7. MCP

| # | Test | Method | Expected |
|---|------|--------|----------|
| 7.1 | `mcp_list` | "list MCP servers" | Server list |
| 7.2 | `mcp_reload` | "reload MCP configuration" | Servers refreshed |
| 7.3 | `mcp_tools` | "show MCP tools" | Dynamic tool list |
| 7.4 | MCP tool bridge | Invoke `mcp__fetch__fetch` | Tool executes via MCP |
| 7.5 | Degraded mode | One MCP server down | Other servers still work |

### 8. Skills

| # | Test | Method | Expected |
|---|------|--------|----------|
| 8.1 | `skills_list` | "what skills are available?" | Skill names + descriptions |
| 8.2 | `skills_search` | "search for planning skills" | Matching skills |
| 8.3 | `skills_load` | "load planning-with-files skill" | Full SKILL.md content |
| 8.4 | `skill_create` | "create skill called analysis" | Skill created |
| 8.5 | `sql_write_query` (gated) | "write SQL query" without skill | Error: load skill first |
| 8.6 | `sql_write_query` (gated) | Load skill, then write SQL | Works |
| 8.7 | Progressive disclosure | Step 1: list → Step 2: load | Correct 2-step flow |

### 9. Middleware

| # | Test | Method | Expected |
|---|------|--------|----------|
| 9.1 | `MemoryMiddleware` | Long conversation, ask about earlier topic | Memory injected into context |
| 9.2 | `SummarizationMiddleware` | ~4000+ token conversation | Auto-summarization triggered |
| 9.3 | `SummarizationMiddleware` failure | LLM returns "too long to summarize" | Original messages preserved |
| 9.4 | `ProgressMiddleware` | Start subagent, check job | Progress updates in work_queue |
| 9.5 | `ProgressMiddleware` doom loop | Same tool+args 3x | `stuck=true` + auto-instruction |
| 9.6 | `InstructionMiddleware` cancel | subagent_cancel during async job | Subagent stops after current LLM call |
| 9.7 | `InstructionMiddleware` inject | subagent_instruct during async job | Instruction appears in next LLM call |

### 10. Streaming (SSE)

| # | Test | Method | Expected |
|---|------|--------|----------|
| 10.1 | Block-structured events | POST /message/stream | `text_start` → `text_delta` → `text_end` |
| 10.2 | Tool streaming | Request that triggers tool | `tool_input_start` → `tool_input_delta` → `tool_result` |
| 10.3 | Reasoning streaming | Anthropic/Gemini with thinking | `reasoning_start` → `reasoning_delta` → `reasoning_end` |
| 10.4 | Usage event | Any streaming request | `usage` event before `done` |
| 10.5 | Backward compat | Legacy event names | `ai_token` → `text_delta`, `tool_start` → `tool_input_start` |
| 10.6 | Interrupt | Destructive tool call | `interrupt` chunk emitted |

### 11. Parallel Tool Execution

| # | Test | Method | Expected |
|---|------|--------|----------|
| 11.1 | Parallel safe | Request 2+ read-only tools | `asyncio.gather()` executes concurrently |
| 11.2 | Sequential | Request destructive tool | Runs one at a time |
| 11.3 | Mixed | 1 readOnly + 1 destructive | Safe runs parallel, destructive after |
| 11.4 | Interrupt in batch | Batch with 1 interrupt tool | Safe tools execute, interrupt queued |

### 12. Provider Options & Cost Tracking

| # | Test | Method | Expected |
|---|------|--------|----------|
| 12.1 | `provider_options` | Anthropic with thinking budget | Thinking blocks returned |
| 12.2 | `provider_options` isolation | OpenAI request sees no Anthropic options | No cross-provider leak |
| 12.3 | `Message.usage` | Any request | Usage with input/output/reasoning tokens |
| 12.4 | `CostTracker` | Multi-turn conversation | Cost accumulates correctly |
| 12.5 | `CostTracker` limit | Exceed cost_limit_usd | CostLimitExceededError |

---

## Persona Test Execution Plan

### Phase 1: Smoke Test (p21 Quick Quinn)

Single-command queries across all tool categories. Verifies basic connectivity and tool availability.

### Phase 2: Style Coverage (p1–p10)

100 queries per persona through `POST /message`. Verifies agent handles different communication styles.

### Phase 3: Feature Depth (p11–p20)

100 queries per persona targeting specific feature domains:
- p11 (Apps), p12 (parallel), p13 (summarization), p14 (skills), p15 (subagents/parallel)
- p16 (files/apps), p17 (subagent_instruct), p18 (subagent_check/subagent_tasks), p19 (browser), p20 (security/isolation)

### Phase 4: Stress & Edge Cases (p21–p25)

100 queries per persona with:
- p21: Latency benchmarks
- p22: Complex multi-subagent workflows
- p23: Bad inputs, invalid tools, edge cases
- p24: Multi-turn context continuity
- p25: Random across all features

### Phase 5: Streaming (all personas)

Same queries via `POST /message/stream`. Verify block-structured events and backward compatibility.

---

## Unit Test Summary

| Suite | Tests | Location |
|-------|-------|----------|
| SDK Core (messages, tools, state) | 204 | `tests/sdk/` |
| Providers | 51 | `tests/sdk/` |
| Agent Loop | 48 | `tests/sdk/` |
| Phase 5–6 (streaming, annotations, guardrails, handoffs, tracing) | 63 | `tests/sdk/` |
| models.dev Registry | 22 | `tests/sdk/` |
| Parallel Tool Execution | 8 | `tests/sdk/` |
| ToolResult, hooks, usage, provider_options | 26 | `tests/sdk/` |
| MCP Tool Bridge | 20 | `tests/sdk/` |
| Subagent V1 | 38 | `tests/sdk/test_subagent_v1.py` |
| HTTP API | ~100 | `tests/api/` |
| **Total** | **470+** | |

---

## Running Tests

```bash
# SDK unit tests
uv run pytest tests/sdk/ -v

# Subagent V1 tests
uv run pytest tests/sdk/test_subagent_v1.py -v

# HTTP API tests
uv run pytest tests/api/ -v

# All tests
uv run pytest

# Persona evaluation (25 personas × 100 queries)
uv run python tests/evaluation/evaluate.py

# Single persona
uv run python tests/evaluation/evaluate.py --persona p1

# Lint + type check
uv run ruff check src/
uv run mypy src/
```
