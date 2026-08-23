# Assistant — Project Plan

> Custom agent SDK replacing LangChain/LangGraph. Test-driven. Incremental. Zero regression.

---

## Status Summary

| Phase | Description | Status |
|-------|------------|--------|
| **0** | Test Harness & Baseline | ✅ Done |
| **0.5** | API Contracts + WS Protocol | ✅ Done |
| **1** | Core SDK (Messages, Tools, State) | ✅ Done |
| **2** | LLM Provider Abstraction | ✅ Done |
| **3** | Agent Loop | ✅ Done |
| **4** | Middleware + SDK HTTP Wiring | ✅ Done |
| **5** | Structured Streaming + Tool Annotations | ✅ Done |
| **6** | Guardrails, Handoffs, Tracing | ✅ Done |
| **models.dev** | Dynamic Model Registry (4172+ models) | ✅ Done |
| **7** | Tool Migration (LangChain → SDK-native) | ✅ Done |
| **7.5** | LangChain Removal | ✅ Done |
| **7.6** | Browser Tool Replacement (Agent-Browser CLI) | ✅ Done |
| **10.1** | Critical Bug Fixes | ✅ Done |
| **10.2** | MCP Tool Bridge | ✅ Done |
| **10.3** | Discovery-Based Skills | ✅ Done |
| **10.4** | Parallel Tool Execution | ✅ Done |
| **10.5** | Architecture Improvements (ToolResult, hooks, usage, provider_options) | ✅ Done |
| **11** | Subagent V1 (work_queue, coordinator, middlewares, 8 tools) | ✅ Done |
| **24** | Companion V1 (scheduler, notifications, memory) | ✅ Done |
| **13** | Skills/Subagents Scoping UI + ScopePicker + API CRUD | ✅ Done |
| **14** | Native App — Tools Page Phase A (built-in tools, connectors, api-key + OAuth flows) | ✅ Done |
| **15** | Native App — Tools in Settings, sidebar cleanup, high-end settings redesign | ✅ Done |
| **12** | API Auth + Connection Modes | 🔲 Next |
| **8** | Data Architecture + Team Layer + Folder Cleanup | 🔲 Future |
| **18** | Event-Driven Triggers, Smart Routing & Self-Evolution | 🔲 Future |
| **9** | Extract & Open Source SDK | 🔲 Planned |
| **19** | Parallel Subagent Execution | 🔲 Planned |
| **20** | Dynamic Subagent Creation (inline spawn) | 🔲 Planned |
| **21** | Agent Teams (multi-agent coordination) | 🔲 Planned |
| **22** | Computer Use (screen control + app automation) | 🔲 Planned |

**470+ SDK tests passing. Agent runs end-to-end on CLI and HTTP (REST + SSE + WebSocket). All LangChain removed.**

### Phase Dependency Graph

```
Phase 12 (Auth) ──────────────┐
                                ├──► Backend API consumers
Phase 8 (Data Architecture) ──┘
Phase 11 (Subagent V1) ──┬── Phase 19 (Parallel)
                         ├── Phase 20 (Dynamic Spawn)
                         ├── Phase 21 (Agent Teams)
                          └── Phase 22 (Computer Use)

    gws/m365 skills ──► Email/Todos backend
```

---

## Completed Work

### Phase 7: Tool Migration — ✅ DONE

All tools migrated from LangChain `@tool` to SDK `@tool` in `src/sdk/tools_core/`:

| Module | Tools | Count |
|--------|-------|-------|
| `time.py` | `time_get` | 1 |
| `shell.py` | `shell_execute` | 1 |
| `filesystem.py` | `files_list`, `files_read`, `files_write`, `files_edit`, `files_delete`, `files_mkdir`, `files_rename` | 7 |
| `file_search.py` | `files_glob_search`, `files_grep_search` | 2 |
| `file_versioning.py` | `files_versions_list`, `files_versions_restore`, `files_versions_delete`, `files_versions_clean` | 4 |
| `memory.py` | `memory_connect`, `memory_get_history`, `memory_search`, `memory_search_all`, `memory_search_insights` | 5 |
| `firecrawl.py` | `scrape_url`, `search_web`, `map_url`, `crawl_url`, `get_crawl_status`, `cancel_crawl`, `firecrawl_status`, `firecrawl_agent` | 8 |
| `browser.py` | `browser_open`, `browser_snapshot`, `browser_click`, `browser_fill`, `browser_screenshot`, `browser_eval` | 6 |
| `apps.py` | `app_create`, `app_list`, `app_schema`, `app_delete`, `app_insert`, `app_update`, `app_delete_row`, `app_column_add`, `app_column_delete`, `app_column_rename`, `app_query`, `app_search_fts`, `app_search_semantic`, `app_search_hybrid` | 14 |
| `mcp.py` | `mcp_list`, `mcp_reload`, `mcp_tools` | 3 |
| `skills.py` | `skills_list`, `skills_search`, `skills_load`, `skill_create`, `sql_write_query` | 5 |

**Note:** Email (8), contacts (6), todos (5) tools are disabled pending redesign (see "Disabled Tools" section).

### Phase 7.5: LangChain Removal — ✅ DONE

- `langchain_adapter.py` — deleted
- `messages.py` — removed `to_langchain()` / `from_langchain()` bridge methods
- `middleware_memory.py` — replaced LangChain imports with SDK provider
- `middleware_summarization.py` — removed LangChain fallback
- `cli/main.py` — rewritten to use SDK runner
- `http/main.py` — lifespan no longer depends on LangChain agent pool
- MCP manager — rewritten to use native `mcp` SDK
- `pyproject.toml` — 7 LangChain deps removed from core; Telegram extra removed (bot being discontinued)
- `tests/sdk/test_conformance.py` — deleted (LangChain parity tests)
- `tests/sdk/test_messages.py` — removed LangChain interop tests

### Phase 7.6: Browser Tool Replacement — ✅ DONE

- `src/sdk/tools_core/browser.py` — new, using Agent-Browser CLI (Rust, ref-based)
- `src/sdk/tools_core/browser_use.py` — deleted (was Browser-Use CLI, Python-based)
- `native_tools.py` — imports updated, tool names updated

---

## Phase 12: API Auth + Connection Modes — 🔲 NEXT

> Prerequisite for any remote Flutter client (LAN, Tailscale, cloud). Currently the HTTP server and WebSocket have zero auth — any connection is trusted. This must be fixed before Phase 13.

### Why Auth Now

1. **The Flutter app connects remotely.** Solo mode (localhost) needs no auth, but LAN, Tailscale, and cloud deployments expose the API to the network. Without auth, anyone on the same network can send messages, approve tool calls, and read conversation history.

2. **The WS protocol passes `user_id` as a plain string.** Any client can impersonate any user by setting `user_id: "alice"`. The server trusts it blindly.

3. **Solo mode should remain zero-config.** Auth must not add friction for localhost users. The solution: auth is required on all connections unless the request comes from localhost (127.0.0.1/::1).

### Design: API Key Auth (Not JWT)

| Aspect | Decision | Why |
|--------|----------|-----|
| **Method** | Bearer token (API key) | Simplest auth that works for all connection modes. No token refresh, no OIDC flow, no session management. |
| **Why not JWT** | JWT adds issuance, rotation, expiry, revocation infrastructure. EA is per-user (one container = one user). A static API key per container is sufficient — JWT solves multi-user single-process auth which we don't have. |
| **Why not OAuth** | OAuth requires an authorization server, redirect flows, and scope management. Overkill for a single-user agent API. |
| **Storage** | `EA_API_KEY` env var or `api_key` in `config.yaml`. Hashed with SHA-256 for comparison. | Same pattern as DEPLOYMENT.md team mode (`EA_AUTH_JWT_SECRET`). |
| **Solo bypass** | Requests from 127.0.0.1 or ::1 skip auth. | Zero-config for localhost. No API key needed for `assistant http` on your own machine. |
| **Client** | API key sent as `Authorization: Bearer <key>` header (REST) and `{"type": "auth", "api_key": "<key>"}` first WS message. | Matches DEPLOYMENT.md connection modes. |

### Implementation

| # | Task | Details |
|---|------|---------|
| 1 | Add `AuthConfig` to `src/config/settings.py` | `api_key: str = ""` (empty = auth disabled for solo), `solo_bypass: bool = True` (skip auth for localhost) |
| 2 | Create `src/http/auth.py` | `verify_api_key(key: str) -> bool` — SHA-256 compare against configured key. `get_api_key_hash() -> str` — hash for storage. `is_localhost(request: Request) -> bool` — check client IP. |
| 3 | Create `src/http/middleware.py` | FastAPI `Depends` dependency. If `auth_config.api_key` is empty → allow all. If `auth_config.solo_bypass` and `is_localhost(request)` → allow. Otherwise → validate `Authorization: Bearer <key>` header. Returns 401 on failure. |
| 4 | Add auth dependency to all REST routers | `conversation_router`, `memories_router`, `workspace_router`, `skills_router`, `subagents_router` — all get `Depends(auth_middleware)`. Health endpoints remain unauthenticated. |
| 5 | Add auth to WebSocket handshake | On `ws/conversation` connect, client must send `{"type": "auth", "api_key": "<key>"}` as first message. Server validates and responds with `{"type": "auth_ok"}` or `{"type": "error", "code": "AUTH_FAILED"}` and closes. If `EA_API_KEY` is empty and client connects from localhost → skip auth. |
| 6 | Add auth message type to `ws_protocol.py` | `AuthMessage(api_key: str)` → client-to-server. `AuthOkMessage()` → server-to-client. |
| 7 | Document WS auth flow | Client sends `AuthMessage` as first message if key is non-empty. Handle `auth_ok` and `auth_failed` responses. |
| 8 | Document REST auth flow | Add `Authorization: Bearer <key>` header to all requests if key is non-empty. |
| 9 | Add connection mode detection | `ConnectionMode` enum: `localhost` (no auth needed), `lan` (need API key), `tailscale` (need API key), `cloud` (need API key). Auto-detect: if host is 127.0.0.1 or localhost → `localhost`. Otherwise → `lan` (require key). |
| 10 | Settings API | Expose connection mode indicator, API key field (masked), model display. |
| 11 | Add REST endpoint `GET /auth/verify` | Returns `{"valid": true}` if API key is correct. Client calls this on connect to validate the stored key. Unauthenticated if auth is disabled (solo mode). |

### Solo vs Remote Auth Flow

```
Solo (localhost):
  Client connects → no EA_API_KEY → no auth required → proceeds
  Client connects → EA_API_KEY set → localhost bypass → proceeds

Remote (LAN/Tailscale/Cloud):
  Client connects → EA_API_KEY is set → auth required
  REST: Authorization: Bearer <key> → middleware validates → 401 or proceed
  WS: {"type": "auth", "api_key": "<key>"} → server validates → auth_ok or close
  Client connects → EA_API_KEY empty → 401/forbidden (remote without key = unsafe)
```

---


## Phase 8: Data Architecture + App Sharing + Folder Cleanup — 🔲 FUTURE

See [DATA_ARCHITECTURE.md](./DATA_ARCHITECTURE.md) for the full data architecture design.

### 8.1 DataPaths Class + Config

Add deployment-aware data path resolution:

```python
class DataPaths:
    deployment: str          # "solo" | "multi-user"
    base: Path              # data/

    def private(self) -> Path: ...       # data/private/
    def shared(self) -> Path: ...        # data/shared/
    def templates(self) -> Path: ...     # data/templates/
    def personal_apps(self) -> Path: ... # data/private/apps/
    def shared_apps(self) -> Path: ...   # data/shared/apps/
```

- Add `deployment` and `data_path` to `src/config/settings.py`
- Solo: `data/shared/` may not exist (no one to share with)
- Multi-user: `data/shared/` is a mounted volume visible to all containers

**Why `private/` + `shared/` instead of `users/{user_id}/`**: Each deployment serves exactly one user. The container IS the isolation boundary. A `users/` directory adds path complexity for no benefit.

### 8.2 Migrate Storage Classes to DataPaths

Update all storage classes to use `DataPaths` instead of hardcoded `data/users/{user_id}/`:

| Class | Current Path | New Path |
|-------|-------------|----------|
| `AppStorage` | `data/users/{user_id}/apps/` | `DataPaths.personal_apps()` |
| `ConversationStorage` | `data/users/{user_id}/conversation/` | `DataPaths.private()/conversation/` |
| `EmailDB` | `data/users/{user_id}/email/` | `DataPaths.private()/email/` |
| `ContactsDB` | `data/users/{user_id}/contacts/` | `DataPaths.private()/contacts/` |
| `TodosDB` | `data/users/{user_id}/todos/` | `DataPaths.private()/todos/` |
| `MemoryDB` | `data/users/{user_id}/memory/` | `DataPaths.private()/memory/` |

Auto-migration: if `data/private/` doesn't exist but `data/users/` does, move contents.

### 8.3 App Sharing — Schema + Access Control

Add `_app_shares` table to shared apps for role-based access:

```sql
CREATE TABLE _app_shares (
    share_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,  -- 'viewer' | 'editor' | 'admin'
    created_at  INTEGER NOT NULL
);
```

New tools:

| Tool | Role | Description |
|------|------|-------------|
| `app_share` | admin | Grant a user access (viewer/editor/admin) |
| `app_unshare` | admin | Revoke a user's access |
| `app_shares_list` | admin | List who has access |
| `app_export` | admin | Export app as `.ea-app` tarball |
| `app_import` | any | Import `.ea-app` tarball (creates fork) |
| `app_template` | admin | Export schema-only template |
| `app_create_from_template` | any | Create app from a template |

### 8.4 Folder Cleanup — Delete Dead Code

Current dead code (LangChain tool wrappers and infrastructure):

| Directory | Status | Action |
|-----------|--------|--------|
| `src/tools/core/` | Dead (LangChain tool wrappers) | Delete entirely |
| `src/tools/apps/tools.py` | Dead | Delete (SDK version in `tools_core/apps.py`) |
| `src/tools/contacts/tools.py` | Dead | Delete |
| `src/tools/email/account.py`, `read.py`, `send.py` | Dead | Delete |
| `src/tools/filesystem/search.py`, `versioning.py` | Dead | Delete |
| `src/tools/mcp/tools.py` | Dead | Delete |
| `src/tools/memory/` | Dead | Delete entirely |
| `src/tools/vault/` | Dead | Delete entirely (no external consumers) |
| `src/storage/checkpoint.py` | Quasi-dead | Delete (LangGraph checkpoints disabled) |
| `src/storage/database.py` | Legacy | Delete (self-documented as unused) |

Still-active files that need relocation before their parent dirs can be deleted:

| File | Consumer | Relocate to |
|------|----------|-------------|
| `src/tools/apps/storage.py` | `sdk/tools_core/apps.py` | `src/sdk/tools_core/apps_storage.py` |
| `src/tools/contacts/storage.py` | `sdk/tools_core/contacts.py` | `src/sdk/tools_core/contacts_storage.py` |
| `src/tools/email/db.py`, `sync.py` | `sdk/tools_core/email.py`, HTTP routers | `src/sdk/tools_core/email_db.py`, `src/sdk/tools_core/email_sync.py` |
| `src/tools/filesystem/cache.py` | HTTP workspace router | `src/http/workspace_cache.py` |
| `src/tools/filesystem/tools.py` | HTTP workspace router | `src/http/workspace_tools.py` |
| `src/tools/mcp/manager.py` | `sdk/tools_core/mcp.py` | `src/sdk/tools_core/mcp_manager.py` |

After relocating active files, delete:

| Directory | What's Left After Relocation | Action |
|-----------|------------------------------|--------|
| `src/tools/` | Nothing — delete entirely | Delete |
| `src/agents/` | LangChain-based agent pool | Delete after confirming no remaining consumers |
| `src/llm/` | LangChain LLM providers | Delete after confirming no remaining consumers |
| `src/middleware/` | LangChain middleware | Delete after confirming no remaining consumers |

**Note**: The Telegram bot (`src/telegram/main.py`) was the last consumer of `src/agents/manager.py` and `src/llm/providers.py`. Since the Telegram channel is being removed, these three directories are no longer blocked and can be deleted once we confirm no remaining imports.

### 8.5 Implementation Order

| Step | Task | Depends on |
|------|------|-----------|
| 1 | Create `DataPaths` class in `src/storage/paths.py` | — |
| 2 | Add `deployment` + `data_path` to `Settings` | Step 1 |
| 3 | Migrate `AppStorage` to use `DataPaths` | Step 2 |
| 4 | Migrate other storage classes to `DataPaths` | Step 2 |
| 5 | Add auto-migration (`data/users/` → `data/private/`) | Steps 3-4 |
| 6 | Relocate active files from `src/tools/` to `src/sdk/tools_core/` or `src/http/` | — |
| 7 | Delete dead code (`src/tools/core/`, `memory/`, `vault/`, etc.) | Step 6 |
| 8 | Add `_app_shares` table to `AppStorage` | Step 3 |
| 9 | Implement `app_share`, `app_unshare`, `app_shares_list` tools | Step 8 |
| 10 | Implement `app_export`, `app_import`, `app_template` tools | Step 8 |
| 11 | Update `AppStorage` operations to check `_app_shares` for shared apps | Steps 8-9 |
| 12 | Delete `src/storage/checkpoint.py` and `database.py` | — |

Steps 6-7 can proceed now. Steps 1-5 are data architecture. Steps 8-11 are app sharing.

---

## External Tool Upgrades & Integrations

### ripgrep — Replace Pure Python `files_grep_search`

**Decision: Add as core tool (CLI adapter, like firecrawl/browser)**

Our agent is general-purpose — fast code search is table stakes. The current `files_grep_search` in `src/sdk/tools_core/file_search.py` is pure Python (reads every file, slow). ripgrep is 5-13x faster and the industry standard for agent code search.

**Implementation:** Replace the pure Python grep with `rg` CLI calls via `CLIToolAdapter` (same pattern as firecrawl/browser-use). Fallback to pure Python if `rg` not found.

| Aspect | Current | After |
|--------|---------|-------|
| Tool name | `files_grep_search` | `files_grep_search` (same) |
| Backend | Pure Python `re` | `rg` CLI (fallback: pure Python) |
| Speed | ~1x | 5-13x faster |
| Dependency | None (built-in) | `ripgrep` binary (brew/pkg install) |
| Pattern | N/A | `CLIToolAdapter` (like `firecrawl.py`) |

**Reference:** https://github.com/BurntSushi/ripgrep

### Pandoc — Document Format Conversion (Skill, Not Core)

**Decision: Include as a skill, not a core tool**

Pandoc converts between 50+ document formats (Markdown↔HTML↔PDF↔DOCX↔LaTeX↔EPUB...). It's powerful but niche — most users don't need it on every session. Making it a skill means:
- Loaded on demand (`skills_load pandoc`) — zero startup cost
- Doesn't bloat the core tool registry
- Users who need it get full pandoc power

**Implementation:** Create `src/skills/pandoc/` skill that wraps the `pandoc` CLI. Skill instructs the agent how to invoke `pandoc` via `shell_execute` — no new tool needed.

| Aspect | Detail |
|--------|--------|
| Install | `brew install pandoc` / `apt install pandoc` |
| Skill name | `pandoc` |
| Activation | `skills_load pandoc` |
| Tools used | Existing `shell_execute` (no new tool) |
| MCP alternative | `vivekVells/mcp-pandoc` (MCP server wrapping pandoc) |

**References:**
- https://github.com/jgm/pandoc
- https://github.com/vivekVells/mcp-pandoc (MCP server)

### Google Workspace CLI (`gws`) — Full Google Workspace Access

**Decision: Adopt as skill (on-demand), covers Gmail, Drive, Calendar, Sheets, Docs, Chat, Admin, Tasks, and more**

The Google Workspace CLI (`gws`) is a Rust-based CLI that dynamically generates commands from Google's Discovery Service — meaning it covers **every** Google Workspace API, not just email.

**Implementation:** Create `src/skills/google-workspace/` skill. The skill instructs the agent how to invoke `gws` CLI commands via `shell_execute`. No new tools needed. The CLI's built-in SKILL.md files (100+) provide curated recipes like `gmail +send`, `calendar +agenda`, `drive +upload`, `workflow +standup-report`.

| Aspect | Detail |
|--------|--------|
| Repo | https://github.com/googleworkspace/cli |
| Install | `brew install googleworkspace-cli` (macOS/Linux), npm, cargo, or binary |
| Stars | 24.7k |
| Auth | OAuth2 desktop flow, headless/CI credentials, service accounts, gcloud tokens |
| Key helper commands | `gmail +send`, `calendar +agenda`, `drive +upload`, `drive +download`, `sheets +read`, `docs +create` |
| Skill name | `google-workspace` |
| Activation | `skills_load google-workspace` |
| Tools used | Existing `shell_execute` (no new tool) |

### CLI for Microsoft 365 (`m365`) — Full Microsoft 365 Access

**Decision: Adopt as skill (on-demand), covers Outlook, Teams, SharePoint, OneDrive, Planner, To Do, Power Platform, Entra ID, and more**

CLI for Microsoft 365 (`m365`) is the Microsoft equivalent of `gws` — a TypeScript-based CLI covering the full Microsoft 365 suite. Admin/tenant-focused (SharePoint, Teams, Entra ID) as well as personal Outlook email.

**Implementation:** Create `src/skills/microsoft-365/` skill. The skill instructs the agent how to invoke `m365` CLI commands via `shell_execute`. Especially useful for users with Microsoft 365 work/tenant accounts.

| Aspect | Detail |
|--------|--------|
| Repo | https://github.com/pnp/cli-microsoft365 |
| Docs | https://pnp.github.io/cli-microsoft365/ |
| Install | `npm install -g @pnp/cli-microsoft365` |
| Stars | 1.3k |
| Contributors | 139 |
| Auth | Device code (simplest), certificate, client secret, username+password, managed identity |
| Outlook mail commands | `m365 outlook mail send/list/get`, `m365 outlook message list/get` |
| Key commands | `m365 teams *`, `m365 spo *` (SharePoint), `m365 aad *` (Entra ID), `m365 outlook *`, `m365 planner *`, `m365 todo *` |
| Skill name | `microsoft-365` |
| Activation | `skills_load microsoft-365` |
| Tools used | Existing `shell_execute` (no new tool) |

### Workspace Strategy Summary

| User Scenario | Recommended Tool(s) |
|---------------|---------------------|
| **Gmail only** | `gws` skill (Gmail + Drive + Calendar + more) |
| **Microsoft 365 work account** | `m365` skill (full tenant) |
| **Gmail + Microsoft 365** | `gws` skill + `m365` skill |
| **Generic IMAP/SMTP** | Built-in `email_*` SDK tools |
| **Document conversion** | `pandoc` skill |

**Two layers:**
1. **Core tools** — Built-in `email_*` (local IMAP/SMTP, always available)
2. **Skills** — `gws`, `m365`, `pandoc` (loaded on-demand, cover entire workspace suites)

---

## Phase 9: Extract & Open Source SDK — 🔲 FUTURE

**Goal**: Publish the SDK as a standalone Python package.

### Why Open Source

- **No provider-agnostic, Python-first agent SDK exists.** Vercel AI SDK is TypeScript. OpenAI Agents SDK is OpenAI-first. Google ADK is Gemini-first.
- **MCP convergence.** A Python SDK with native MCP client + tool annotations becomes a universal tool consumer.
- **Differentiation.** Tool annotations from MCP, structured + text results, block streaming, reasoning in messages.

### Prerequisites

1. No LangChain imports (Phase 7.5 ✅ — `langchain_adapter.py` deleted)
2. Stable streaming protocol (Phase 5 ✅)
3. Integration tests against at least OpenAI + Anthropic
4. Docs: README, quickstart, provider guide, 3-5 examples
5. Tool annotations on all built-in tools ✅

### Package Structure

```
agent-sdk/
├── pyproject.toml            # pip install agent-sdk
├── src/agent_sdk/
│   ├── __init__.py
│   ├── messages.py
│   ├── tools.py
│   ├── state.py
│   ├── loop.py
│   ├── middleware.py
│   ├── guardrails.py
│   ├── handoffs.py
│   ├── tracing.py
│   ├── validation.py
│   ├── registry.py
│   └── providers/
│       ├── base.py
│       ├── openai.py
│       ├── anthropic.py
│       ├── gemini.py
│       ├── ollama.py
│       └── factory.py
└── tests/
```

### Dependencies

```toml
dependencies = ["pydantic>=2.0", "httpx>=0.27", "tiktoken>=0.7"]
[project.optional-dependencies]
openai = ["openai>=1.0"]
anthropic = []          # httpx only
gemini = []             # httpx only
mcp = ["mcp>=0.9"]
```

Zero heavy dependencies by default. Providers lazy-imported.

---

## Parallel Tool Calls (Multi-Turn + In-Turn Parallel)

**Two dimensions must work together:**

1. **Multi-turn** (already works): The agent loop iterates — LLM calls tools, gets results, decides to call more tools, gets results, until it responds with text only. This is the ReAct loop already in `AgentLoop`.

2. **In-turn parallel** (missing): When the LLM returns **multiple `tool_calls`** in a single response, they should execute concurrently where safe, then all results are collected and sent back together before the next LLM call. This is the pattern from [Ollama's tool calling docs](https://docs.ollama.com/capabilities/tool-calling) — parallel tool calling + multi-turn agent loop.

**Current state:** The AgentLoop executes tools **sequentially** within a turn (`for tc in response.tool_calls: execute(tc)` at loop.py:336). Multi-turn works — the loop repeats until no tool_calls. But when the LLM returns N tools, they run one at a time.

**Provider support:** All major providers return multiple tool_calls in a single response, and our SDK providers already parse them correctly (using `dict[int, dict]` index-based accumulation across streaming chunks):

| Provider | Multi tool_calls | SDK parsing |
|----------|:---:|---|
| **OpenAI** | ✅ `parallel_tool_calls` param (default true) | `openai.py:142` — index-based `current_tool_calls` |
| **Anthropic** | ✅ Multiple `tool_use` content blocks | `anthropic.py:222` — index-based `current_tool_calls` |
| **Gemini** | ✅ Multiple `functionCall` in parts array | `gemini.py:228` — index-based `current_tool_calls` |
| **Ollama Local** | ✅ OpenAI-compatible `/v1/chat/completions` | `ollama.py:296` — index-based `current_tool_calls` |
| **Ollama Cloud** | ✅ Native `/api/chat` tool_calls array | `ollama.py:88` — index-based `current_tool_calls` |

The **only change needed** is in `AgentLoop`: after collecting tool_calls, group by safety and run independent calls via `asyncio.gather()` instead of `for tc in tool_calls: execute(tc)`. No provider code changes.

**Target state:** When the LLM returns multiple tool_calls in one turn:
1. Classify each call: safe-to-parallel (read-only, non-destructive) vs. must-serialize (destructive, write-side-effects)
2. Execute all safe-to-parallel calls concurrently via `asyncio.gather()`
3. Execute any destructive/sequential calls one at a time after
4. Collect all results, add as tool_result messages, send back to LLM as one batch
5. Continue the multi-turn loop

**Impact on middleware and hooks:**

- **Middleware** runs per-tool (already designed for single invocation). No structural change. In the parallel case, each tool still gets its own `mw.wrap_tool_call()`. Middleware doesn't need to know about parallelism.
- **PreToolUse hooks** need to handle a batch: given N tool calls, return N decisions (allow/deny/modify). Parallel execution only starts after all pre-hooks approve. If any hook denies a call, that call is skipped; other parallel calls proceed.
- **PostToolUse hooks** receive individual results — no change needed.
- **Interrupt handling** changes: when multiple tools are called and one needs HITL approval, we have two options:
  - **A) Eager**: Execute all safe calls in parallel, but queue any interrupts. After all parallel calls complete, yield interrupts one at a time for user resolution, then execute approved calls.
  - **B) Conservative**: If any call would interrupt, skip all parallel execution for that batch and ask the user first. Simpler but slower.
  - **Recommend: Option A** — don't block independent work on a single approval decision.
- **Guardrails** already run per-tool — no structural change.

**Impact on streaming protocol:**
- Block-structured events already support multiple concurrent tool calls (each `tool_input_start` / `tool_result` has its own `tool_call_id`)
- For parallel execution, we emit `tool_input_start` for all parallel calls, then emit their `tool_result` events as they complete (order may differ from start order)
- No protocol change needed

---

## Middleware vs Hooks

These are **complementary systems serving different audiences**:

| | Middleware | Hooks |
|--|-----------|-------|
| **What** | Python code running in-process | Shell commands running out-of-process |
| **Who writes it** | Developers (us) | Users (anyone with a shell script) |
| **When it runs** | Agent lifecycle events: `before_agent`, `before_model`, `after_model` | Tool lifecycle events: `PreToolUse`, `PostToolUse` |
| **Can modify** | State, messages, tool arguments, tool results | Tool input, tool output, permission override |
| **Can abort** | Yes (raise exception) | Yes (deny + cancel) |
| **Example** | `SkillMiddleware` injects skill descriptions; `SummarizationMiddleware` compresses history | A bash script that auto-approves `files_read` but blocks `shell_execute` for specific paths |
| **Extensibility** | Requires code change + deployment | User creates `.ea/hooks/pre-tool-use.sh` and it works |

**Why both:** Middleware handles structural transformations (memory injection, summarization, skill discovery) that require deep SDK access. Hooks handle per-tool policy decisions (approve/deny/modify) that users should customize without touching code. They coexist — middleware runs at agent lifecycle boundaries, hooks run at tool execution boundaries.

---

## Skills: Discovery-Based (Removing SkillMiddleware)

**Current:** `SkillMiddleware` injects skill names+descriptions into the **system prompt on every request**. This costs tokens even when the LLM never needs a skill. Three separate `SkillRegistry` instances (middleware, SDK tools, legacy tools) are not synchronized.

**Target:** Kill `SkillMiddleware`. Move skill discovery into the tool description of `skills_list`. The tool's `description` field dynamically includes available skill names. The LLM sees skills as part of the tools list — zero system prompt waste.

Pattern (adopted from Claw Code and OpenCode):

```
Tool: skills_list
Description: |
  List and discover available skills. Current skills:
  - deep-research: Deep research and web content analysis
  - planning-with-files: Multi-step task planning
  - skill-creator: Create and improve skills
  - subagent-manager: Create and manage subagents
  
  Use skills_load(name) to get full instructions for any skill.
```

**Progressive disclosure stays the same:** LLM sees names+descriptions → calls `skills_load("deep-research")` → gets full SKILL.md body → optionally reads bundled scripts/references via `files_read`.

**Benefits:**
- Zero system prompt tokens for skills (was ~100 words per skill per request)
- Single SkillRegistry instance (shared between `skills_list` tool and `skills_load` tool)
- Skills appear naturally as a tool the LLM can call, not injected text
- LLM decides when to explore skills based on relevance, not because they're always in context

---

## Phase 10: Agent Loop Modernization

### 10.1 Critical Bug Fixes

| # | Task | Priority |
|---|------|----------|
| 1 | Delete dead `src/skills/tools.py` (legacy LangChain 153 lines) | High |
| 2 | Fix `UserSkillStorage` stale path (`data/users/` → `DataPaths.skills_dir()`) | High |
| 3 | Unify SkillRegistry instances (one per user, shared between middleware + tools) | High |
| 4 | Fix streaming interrupt inconsistency: both `run()` and `run_stream()` should yield interrupt chunks, never raise `Interrupt` as exception | High |
| 5 | Remove `interrupt_on` parameter from AgentLoop — rely solely on `ToolAnnotations.destructive` | High |
| 6 | Delete CLI (`src/cli/`, `src/__main__.py` CLI entry) — HTTP API is primary interface | High |

### 10.2 MCP Tool Bridge

| # | Task | Priority |
|---|------|----------|
| 7 | Build `MCPToolBridge`: convert MCP `mcp` SDK tool objects → SDK `ToolDefinition` | High |
| 8 | Add `AgentLoop.register_tool()` / `unregister_tool()` for dynamic tool registration | High |
| 9 | Inject discovered MCP tools into main loop as `mcp__{server}__{tool}` | High |
| 10 | Support degraded-mode: partial MCP server failures don't crash the agent | Medium |
| 11 | Replace `MCPManager._run_async()` thread hack with proper async integration | Medium |

### 10.3 Discovery-Based Skills

| # | Task | Priority |
|---|------|----------|
| 12 | Kill `SkillMiddleware` — remove from runner.py middleware stack | High |
| 13 | Move skill descriptions into `skills_list` tool description (dynamically generated) | High |
| 14 | Consolidate SkillRegistry to single per-user instance (shared by all tools) | High |
| 15 | Remove `src/sdk/middleware_skill.py` | High |

### 10.4 Parallel Tool Execution

| # | Task | Priority |
|---|------|----------|
| 16 | Modify `AgentLoop` to identify independent tool calls (non-destructive, no shared state) | High |
| 17 | Execute independent tool calls concurrently via `asyncio.gather()` | High |
| 18 | Execute destructive/dependent calls sequentially after concurrent batch | High |
| 19 | Update PreToolUse hooks to support batch mode (list of tool calls → list of decisions) | Medium |
| 20 | Update streaming to emit parallel `tool_input_start`/`tool_result` events correctly | Medium |

### 10.5 Architecture Improvements

| # | Task | Priority |
|---|------|----------|
| 21 | Implement `ToolResult` properly in `_execute_tool()`: return structured content + human content + audience + is_error | High |
| 22 | Add shell hooks at `PreToolUse` / `PostToolUse` (user-extensible, out-of-process) | High |
| 23 | Add plugin system: `plugin.json` manifest + subprocess execution like Claw Code | Medium |
| 24 | Plumb actual token counts from provider responses into `CostTracker` | Medium |
| 25 | Accept `provider_options` from `RunConfig` or per-request kwargs (currently hardcoded `None`) | Medium |

### 10.6 Advanced (Phase 18+)

| # | Task | Priority | Status |
|---|------|----------|--------|
| 26 | HITL middleware: adopt interrupt/approve/reject flow — when a destructive tool is called, yield interrupt chunk, wait for user resolution, then continue or skip | High | 🔲 Future |
| 27 | Skill-activated tools: skills can declare tool dependencies that get registered on load | Medium | 🔲 Future |
| 28 | Git-based undo/redo for file changes (like OpenCode snapshots) | Low | 🔲 Future |
| 29 | ~~Subagent system rewrite (LangChain → SDK)~~ | ~~High~~ | ✅ Done (Phase 11) |
| 30 | Worker state machine (Spawning → Ready → Running → Finished) | Medium | 🔲 Future |
| 31 | Email/contacts/todos redesign (currently disabled, pending redesign) | Medium | 🔲 Future |
| 32 | Calendar tools (new, doesn't exist yet) | Medium | 🔲 Future |

---

## Phase 18: Event-Driven Triggers, Smart Routing & Self-Evolution — 🔲 FUTURE

### 18.1 Event-Driven Agent Triggers

The main agent should be activatable by external events, not just user messages. Three trigger types:

| Trigger Type | Mechanism | Examples |
|-------------|-----------|----------|
| **Cron / Scheduled** | APScheduler or `croniter` + asyncio task | "Check email every 30 min", "Daily standup summary at 9am", "Weekly report every Friday" |
| **Webhook** | HTTP endpoint (FastAPI route) that creates an agent run | GitHub push → code review, Stripe payment → receipt, Slack mention → response |
| **File Watch** | `watchfiles` / `inotify` on a directory | New file in `data/inbox/` → process, CSV updated → re-analyze, config change → reload |

**Implementation:**

- New `TriggerManager` in `src/triggers/` — registers, schedules, and dispatches triggers
- Each trigger creates an `AgentLoop.run()` call with the event payload as the initial user message
- Triggers are persisted in `data/private/triggers.db` (SQLite, same pattern as work_queue)
- New tools: `trigger_create` (cron/webhook/file), `trigger_list`, `trigger_delete`
- Webhook triggers expose a unique URL: `POST /webhook/{trigger_id}`
- Cron triggers use `APScheduler` or `croniter` for scheduling
- File watch triggers use `watchfiles` (already a dependency via uvicorn)

### 18.2 Smart Subagent Routing (Duration-Aware Orchestration)

When a task is expected to take 10+ seconds, the main agent should automatically route it to a subagent rather than blocking the main conversation. This requires:

1. **Duration estimation**: LLM assesses task complexity (rough categories: <5s instant, 5-30s moderate, 30s+ heavy)
2. **Auto-delegation**: For moderate/heavy tasks, the main agent creates/invoke a subagent with appropriate tools and a timeout
3. **Progress reporting**: Main agent informs the user that a subagent is working, checks progress, and delivers results when done

**Implementation:**

- Add `estimated_duration` field to tool annotations or a simple heuristic (tool name → expected range)
- Main agent system prompt includes routing guidance: "For tasks expected to take 10+ seconds, create a subagent"
- `subagent_invoke` already supports `max_llm_calls` and `timeout` — these are set based on duration estimate
- No new tools needed — existing `subagent_create` + `subagent_invoke` + `subagent_progress` cover the workflow
- The main agent can poll `subagent_progress` while the subagent works

### 18.3 Self-Evolution (Hermes-Agent Pattern)

Inspired by Hermes-type agents that modify their own behavior, prompts, and tools based on experience. Details to be designed, but high-level goals:

| Capability | Description | Example |
|-----------|-------------|---------|
| **Prompt self-improvement** | Agent refines its own system prompt based on task outcomes | After 5 failed email drafts, update the email-writing prompt template |
| **Tool creation** | Agent creates new tool definitions (Python code) for repetitive tasks | "I keep formatting JSON the same way — let me create a `json_format` tool" |
| **Skill self-improvement** | Agent improves existing skills based on feedback | Better research queries after user corrections |
| **Memory consolidation** | Agent periodically revisits and consolidates its long-term memory | Merge related memories, delete obsolete ones, create insight summaries |
| **Behavioral adaptation** | Agent adjusts its behavior based on user preferences | Learns user prefers brief responses, adjusts style |

**Design considerations (to be finalized):**

- Self-modifications must be auditable (log all changes, allow rollback)
- Prompt changes should be diff-based, not wholesale replacements
- Tool creation should follow the existing `skill_create` pattern (generates SKILL.md + optional scripts)
- Safety rails: destructive self-modifications require approval (HITL for tool creation, auto-approve for prompt tuning if non-destructive)

### 18.x Implementation Priority

| # | Task | Priority | Depends on |
|---|------|----------|-----------|
| 33 | `TriggerManager` + `trigger_create/list/delete` tools + cron scheduling | High | — |
| 34 | Webhook trigger endpoint (`POST /webhook/{trigger_id}`) | High | #33 |
| 35 | File watch triggers (`watchfiles` on directory) | Medium | #33 |
| 36 | Duration estimation heuristic (tool annotations or LLM-based) | Medium | — |
| 37 | Auto-delegation system prompt guidance + routing logic | Medium | #36 |
| 38 | Self-evolution design document | High | — |
| 39 | Prompt self-improvement mechanism | Medium | #38 |
| 40 | Tool creation from agent (auto `skill_create`) | Low | #38 |
| 41 | Memory consolidation agent (periodic revisit) | Medium | — |
| 42 | Behavioral adaptation (user preference learning) | Low | #39, #41 |

---

## Phase 11: Subagent V1 — ✅ DONE

SQLite work_queue-backed coordination with supervisor pattern. Full design in `docs/SUBAGENT_RESEARCH.md`.

### New Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/sdk/subagent_models.py` | 98 | `AgentDef`, `SubagentResult`, `TaskStatus`, `TaskCancelledError`, `MaxCallsExceededError`, `CostLimitExceededError` |
| `src/sdk/work_queue.py` | 254 | `WorkQueueDB` (aiosqlite, per-user at `data/private/subagents/work_queue.db`) |
| `src/sdk/middleware_progress.py` | 85 | `ProgressMiddleware` (progress updates, doom loop detection) |
| `src/sdk/middleware_instruction.py` | 58 | `InstructionMiddleware` (cancel signal, course-correction injection) |
| `src/sdk/coordinator.py` | 327 | `SubagentCoordinator` (create/update/invoke/cancel/instruct/delete) |
| `tests/sdk/test_subagent_v1.py` | — | 38 tests (all passing) |

### 8 V1 Tools (in `src/sdk/tools_core/subagent.py`)

| Tool | Purpose |
|------|---------|
| `subagent_create` | Create AgentDef, persist to disk |
| `subagent_update` | Amend existing AgentDef (partial update) |
| `subagent_invoke` | Insert task into work_queue + run AgentLoop with middlewares |
| `subagent_list` | List AgentDefs + active tasks |
| `subagent_progress` | Check task status/progress |
| `subagent_instruct` | Inject course-correction into running subagent |
| `subagent_cancel` | Set cancel_requested flag |
| `subagent_delete` | Remove AgentDef + cancel running tasks |

### Key Design Decisions

- **Config frozen at invocation**: `work_queue.config` — amendments don't affect running tasks
- **No recursion**: `disallowed_tools` defaults include all `subagent_*` tools
- **Cost tracking**: `SubagentCoordinator.invoke()` uses `AgentLoop.run()`, wrapped in `asyncio.wait_for(timeout)`
- **Doom loop detection**: Same tool+args called 3x → `progress.stuck = true` + auto-instruction
- **Cancel via flag**: `InstructionMiddleware` checks `cancel_requested` before each LLM call
- **Cost limit**: Checked by `ProgressMiddleware` after each model call

### Deferred to Phase 12+

- DAG dependencies between subagents
- Worker pools / priority queue
- Webhooks / callbacks
- Handoff mode / model-driven routing (`delegate_to_{name}` auto-generation)
- `access_memory` / `access_messages` flags
- Structured output in SubagentResult
- Session resumption / AgentDef versioning

---

## HybridDB — ✅ DONE

`src/sdk/hybrid_db.py` (~1143 lines): SQLite + FTS5 + ChromaDB with journal-based self-healing.

All three domain stores now backed by HybridDB:

| Store | File | Status |
|-------|------|--------|
| `MessageStore` | `src/storage/messages.py` | ✅ HybridDB |
| `MemoryStore` | `src/storage/memory.py` | ✅ HybridDB |
| `AppStorage` | `src/sdk/tools_core/apps_storage.py` | ✅ HybridDB |
| `SubagentScheduler` | `src/subagent/scheduler.py` | ❌ Still raw SQLite |

---

## Disabled Tools (Pending Redesign)

Email (8 tools), contacts (6 tools), and todos (5 tools) are disabled pending redesign. They will be reimplemented as skills using external CLI tools rather than built-in IMAP/database tools:

| Domain | Current | Target |
|--------|---------|--------|
| **Email** | Built-in IMAP/SMTP tools | `gws` skill (Google Workspace CLI) or `m365` skill |
| **Contacts** | Built-in SQLite CRUD | Part of `gws`/`m365` skill, or lightweight built-in |
| **Todos** | Built-in SQLite CRUD | Part of app ecosystem, or lightweight built-in |
| **Calendar** | Doesn't exist | `gws` skill or `m365` skill |

---

## Architecture Research — Agent Loops Compared

### Claw Code (Rust, ultraworkers/claw-code)

- **Agent loop**: `ConversationRuntime<C, T>` — generic ReAct with injectable API client + tool executor
- **Permission**: 3-tier (`ReadOnly` < `WorkspaceWrite` < `DangerFullAccess`) + rule-based policy engine + shell hooks
- **Hooks**: PreToolUse / PostToolUse user shell scripts that can approve/deny/modify
- **MCP**: Full lifecycle (spawn → init → discover → invoke → shutdown), namespaced `mcp__server__tool`, degraded-mode for partial failures
- **Skills**: File-based discovery (`.claw/skills/`), loaded on-demand via `Skill` tool
- **Session**: JSONL per-worktree, auto-compaction at 100K tokens
- **Workers**: State machine (Spawning → TrustRequired → ReadyForPrompt → Running → Finished)
- **Parallel tools**: Sequential (one at a time per turn)
- **Plugin system**: `plugin.json` manifest, subprocess execution

### OpenCode (TypeScript, anomalyco/opencode)

- **Agent loop**: ReAct with subagents via `@mention` and `task` tool
- **Permission**: `allow`/`deny`/`ask` per tool + bash glob patterns
- **Skills**: Same SKILL.md format, discovery-based, loaded by `skill({ name })` tool
- **MCP**: Local (stdio) + Remote (HTTP/OAuth), tools appear as regular tools, lazy loading
- **TUI**: SolidJS terminal UI with tool rendering, permission dialogs, session navigation
- **Session**: SQLite + compaction agents + git snapshots for undo/redo
- **Parallel tools**: Sequential within an agent turn
- **Plugin system**: `@opencode-ai/plugin` SDK with Zod schemas, lifecycle hooks, TUI slots

### Our Assistant (Python)

- **Agent loop**: `AgentLoop` async ReAct with guardrails, handoffs, tracing
- **Permission**: `ToolAnnotations` (readOnly, destructive, idempotent, openWorld) + auto-approval + HITL interrupts
- **Middleware**: `MemoryMiddleware`, `SummarizationMiddleware`, `SkillMiddleware` (discovery-based)
- **MCP**: `MCPToolBridge` — MCP tools registered as `mcp__{server}__{tool}`, degraded-mode, dynamic reload
- **Skills**: Discovery-based — `skills_list` tool with dynamic descriptions, `skills_load` for full content
- **Session**: HybridDB MessageStore + SummarizationMiddleware (no checkpoints)
- **Subagents**: SQLite work_queue + `SubagentCoordinator` + `ProgressMiddleware` + `InstructionMiddleware`
- **Parallel tools**: Classified (parallel_safe / sequential / interrupt), `asyncio.gather()` for safe batch
- **Subagents V2**: Parallel execution, dynamic spawn, agent teams (planned below)
- **Workspaces**: Multi-project isolation with scoped conversation, memory, files, and subagents

---

## Phase 23: Workspaces — Multi-Project Isolation

> An assistant handles multiple projects. Each project gets its own Workspace with scoped conversation history, memory, files, subagents, and custom AI instructions. Modeled on Perplexity Spaces + Claude Code project scoping.

**Current state:** One `user_id` = one conversation stream = one memory bank = one flat file directory. Everything bleeds together. The agent searching "Q2 budget" returns facts from "Home Renovation." Files from all projects share one folder. No way to give different AI instructions per project.

**Goal:** Named Workspaces that isolate context, reduce token waste, and make the agent context-aware per project.

### Architecture

```
~/Assistant/
├── Workspaces/
│   ├── Q2 Planning/
│   │   ├── files/              ← budget.xlsx, strategy.md
│   │   ├── conversation.app.db ← scoped chat history
│   │   ├── memory/             ← scoped HybridDB: "Q2 budget is $2.4M"
│   │   ├── subagents/          ← research-agent, writer (workspace-scoped)
│   │   └── instructions.yaml   ← "Respond like a Product Manager. Use AEST."
│   │
│   ├── Home Renovation/
│   │   ├── files/              ← quotes.pdf, floorplan.png
│   │   ├── conversation.app.db
│   │   └── ...
│   │
│   └── Personal/
│       └── ...
│
├── Skills/                     ← global: available to all workspaces
├── Memory/global/              ← user-level facts: "Eddy lives in Melbourne"
├── Subagents/global/           ← user-level agents: research-agent (shared)
└── data/                       ← internal: email, contacts, todos
```

### Scoping Rules

| Resource | Workspace scope | Global scope | Default |
|----------|----------------|-------------|---------|
| **Conversation** | `data/workspaces/{id}/conversation.app.db` | None — always scoped | Workspace |
| **Memory** | `data/workspaces/{id}/memory/` | `data/users/{uid}/global_memory/` | Workspace |
| **Files** | `Workspaces/{name}/files/` | None | Workspace |
| **Subagents** | `data/workspaces/{id}/subagents/` | `data/users/{uid}/subagents/` | Workspace (user-global fallback) |
| **Skills** | — | `Skills/` (always global) | Global |
| **AI Instructions** | Per-workspace `instructions.yaml` | System-wide default | Workspace overrides global |

### LLM Impact: Net Token Reduction

**No additional API calls.** Workspace context is injected into the existing system prompt. The net effect is a token **reduction** for users with multiple projects:

| Factor | Single-session (current) | Multi-workspace (new) |
|--------|-------------------------|----------------------|
| **System prompt** | ~1500 chars (skills + rules) | ~1600 chars (+ workspace name + custom instructions) |
| **Conversation history** | All messages from all topics | Only this workspace's messages |
| **Memory search** | Must filter across all domains | Pre-filtered by workspace |
| **Example: 3 projects, 50 msgs each** | 150 messages in context | 50 messages in context |

The conversation history reduction (150 → 50 messages) saves **far more tokens** than the ~100 extra chars for workspace context. A net win.

### Subagent Scoping (Claude Code Model)

```
Subagent lookup order (highest priority first):
1. Workspace-scoped: data/workspaces/{id}/subagents/
2. User-global:       data/users/{uid}/subagents/

If agent_researcher exists in both → workspace version wins.
If only in user-global → that version is used.
If neither → agent doesn't know about it.
```

Workspace subagents have access to workspace files + memory by default. User-global subagents get current workspace context injected.

### Implementation

| Step | What | Lines |
|------|------|-------|
| 1 | `Workspace` model: id, name, description, custom_instructions, created_at | ~40 |
| 2 | `DataPaths` updated with workspace-scoped paths | ~30 |
| 3 | `workspace_create/list/switch/delete` tools for the agent | ~120 |
| 4 | System prompt includes workspace name + custom instructions | ~20 |
| 5 | Conversation history filtered by workspace_id | ~30 (SQL WHERE clause) |
| 6 | Memory search defaults to workspace scope (`scope` parameter for global) | ~50 |
| 7 | Subagent registry scoped: workspace-first, user-fallback | ~80 |
| 8 | Flutter sidebar: workspace switcher dropdown + "New Workspace" button | ~200 |
| 9 | Flutter WorkspacePanel: shows workspace files, subagents, instructions | ~150 |

### Dependencies

- ✅ `DataPaths` with per-user isolation (exists)
- ✅ `HybridDB` with per-collection scoping (exists)
- ✅ `SubagentCoordinator` with agent defs (exists)
- ✅ Flutter sidebar with icons (exists)
- ❌ `Workspace` model (new)
- ❌ Workspace-scoped conversation/memory/file paths (new)
- ❌ Workspace CRUD tools (new)
- ❌ Flutter workspace switcher (new)

### Edge Cases

| Scenario | Handling |
|----------|----------|
| User deletes workspace | Move files to archive, purge conversation + memory |
| Agent needs cross-workspace info | `memory_search(query, scope="global")` |
| No workspace selected | Auto-create "Default" on first launch |
| Workspace with custom instructions conflicts with system prompt | Workspace instructions appended to system prompt, not replacing it |
| Subagent created in workspace A used in workspace B | User-global agents only. Workspace agents are scoped. |


---

### Phase 24: Companion V1 (Always-On EA) — ✅ Done

Implemented May 1, 2026 per [COMPANION_PROPOSAL.md](./docs/COMPANION_PROPOSAL.md).

**What:** A user-global, background-running companion that checks in every ~15 minutes, reads cross-workspace context, and delivers warm, brief nudges when something deserves attention. V1 is deliberately minimal — no tool calls, pre-computed context only.

**Backend (~200 lines):**
- `src/sdk/tools_core/companion_db.py` — `CompanionNotificationDB` + `CompanionMemoryDB` (aiosqlite, per-user)
- `src/sdk/companion_scheduler.py` — `CompanionScheduler` (AgentLoop with no tools, adaptive interval, workspace-aware context builder)
- `src/http/routers/companion.py` — 7 REST endpoints (notifications CRUD, pause/resume, status, memory CRUD)
- `src/config/settings.py` — `CompanionConfig` with `COMPANION_ENABLED=false` default
- `src/storage/paths.py` — `companion_dir()`, `companion_notifications_db()`, `companion_memory_db()`

**Flutter (~350 lines):**
- `lib/models/companion.dart` — `CompanionNotification`, `CompanionMemoryFact`, `CompanionStatus` models
- `lib/providers/companion_provider.dart` — Riverpod providers + `CompanionNotifier` StateNotifier with 30s polling
- `lib/features/companion/companion_pulse.dart` — Animated breathing circle (Sidebar, Surface 1)
- `lib/features/companion/companion_context_pill.dart` — Compact contextual nudge between messages (Chat panel, Surface 2)
- `lib/features/companion/companion_toast.dart` — Slide-down overlay for urgent items (Surface 3)
- `lib/features/companion/companion_feed.dart` — Full timeline feed with date grouping + workspace badges (Content panel, Surface 4)
- `lib/services/api_client.dart` — 6 companion API methods
- `lib/core/layout/desktop_layout.dart` — `DesktopSidebarItem.companion` enum + pulse in sidebar + context pill in chat panel + toast stack overlay
- `lib/core/router/app_router.dart` — `/companion` route

**Configuration:**
```bash
COMPANION_ENABLED=true  # False by default, opt-in
```

**Zero-risk design:**
- No tool calls in V1 (LLM reads pre-computed context, outputs text only)
- Separate AgentLoop (not in `_loop_cache`)
- Cost-controlled: local model default, `cost_limit_usd=0.01` per cycle
- Adaptive interval: stretches to 30 min if user dismisses 3+ consecutively
- Auto-pause if no engagement for hours

**Tests:** 21 tests in `tests/sdk/test_companion_v1.py` (DB CRUD, categorization, extraction, scheduling, paths)

**Files changed:** 12 files (8 new, 4 modified)


## Cross-Reference Documents

- [DATA_ARCHITECTURE.md](./DATA_ARCHITECTURE.md) — Data paths, app sharing, deployment models
- [DEPLOYMENT.md](./DEPLOYMENT.md) — Self-hosted .dmg/.exe, hosted container architecture, team layer
- [AGENTS.md](./AGENTS.md) — Build/lint/test commands, coding style, current architecture
- [docs/HybridDB/HYBRIDDB_SPEC.md](./docs/HybridDB/HYBRIDDB_SPEC.md) — HybridDB design specification
