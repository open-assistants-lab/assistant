# OAuth & ConnectKit Setup

Recap of fixes and configuration for Google Workspace OAuth (PKCE + client_secret) integration with ConnectKit and the agent SDK.

---

## 1. Fernet Vault Key (CONNECTKIT_VAULT_KEY)

**Problem**: Tokens encrypted with ephemeral key per process. Restart → `InvalidToken` → connector tools silently fail to load (0 workspace tools in agent).

**Fix**: Set `CONNECTKIT_VAULT_KEY` in `.env`:
```
CONNECTKIT_VAULT_KEY=ea-connectkit-persistent-vault-key-v1
```

The key is read by `connectkit/vault.py:_get_or_create_key()`. When not set, Fernet key is generated per process and cached in `_VAULT_KEY` module variable — tokens survive process-lifetime but not restarts.

## 2. Google OAuth Desktop Client Requires client_secret

**Problem**: Google's OAuth Desktop client type requires `client_secret` in the token exchange, even with PKCE. PKCE `code_verifier` is defense-in-depth, not a replacement.

**Fix**: 
- `client_secret` added to `google-workspace.yaml` `required_fields` with a shipped default
- Shipped secret is per Google's guidance: *"don't expect secrets to stay secret"* (RFC 8252 §8.5)
- Users can override with their own Google Cloud project credentials
- Both `code_verifier` AND `client_secret` are sent in the token exchange body

**Relevant files**:
- `packages/connectkit/connectors/google-workspace.yaml` — `client_secret` default
- `packages/connectkit/connectkit/vault.py` — Fernet key caching in `_get_or_create_key()`
- `packages/connectkit/connectkit/oauth.py` — token exchange sends both `code_verifier` + `client_secret`

## 3. Redirect URI Must Not Have Query Params

**Problem**: Google rejects query params in the redirect URI for Desktop OAuth clients. Was using `?service=google-workspace` on the callback URL.

**Fix**: Removed `?service=` query param from `redirect_uri`. Service name is identified from the Fernet-encrypted OAuth state token instead.

**Relevant files**:
- `connectkit/oauth.py` — login handler strips query params from redirect_uri
- `connectkit/vault.py` — `create_oauth_state()` / `validate_oauth_state()` carry `extra` dict with service name

## 4. AgentLoop Cache Invalidation on Connect/Disconnect

**Problem**: After OAuth connect or disconnect, the cached AgentLoop still has the old tool list (no connector tools or stale tools).

**Fix**: Added `on_connect` hook to `create_oauth_router()` that calls `reset_user_sdk_loops(user_id)` after token is stored. Same invalidation added to:
- Connector connect endpoint (API key auth)
- Connector disconnect endpoint

**Relevant files**:
- `src/http/main.py:165-170` — wires `on_connect=reset_user_sdk_loops` to OAuth router
- `src/http/routers/connectors.py:77-80` — direct `reset_user_sdk_loops` call on connect/disconnect
- `src/sdk/runner.py:465-473` — `reset_user_sdk_loops()` clears loop cache for all workspaces of a user
- `connectkit/oauth.py:create_oauth_router()` — calls `on_connect(user_id)` after token exchange

## 5. CLI Adapter: Tools from YAML Descriptions (Not --help Parsing)

**Problem**: `discover_tools()` parsed `--help` output for tool discovery, which produced false positives (e.g., garbage subcommands from help text) and missed the `users` resource in Gmail commands (e.g., `gmail:messages:list` vs `gmail:users:messages:list`).

**Fix**: 
- `discover_tools()` now uses `tool_descriptions` from YAML as authoritative when present
- `_tools_from_descriptions()` builds tools from YAML descriptions, falling back to `--help` parsing only when no descriptions exist
- Deduplication via `seen` set prevents duplicate tools
- Added `command` field to `ToolDescription` model for explicit CLI commands

**Relevant files**:
- `packages/connectkit/connectkit/backends/cli.py:133-160` — `_tools_from_descriptions()`, `discover_tools()`
- `packages/connectkit/connectkit/spec.py:103` — `ToolDescription.command` field
- `packages/connectkit/connectors/google-workspace.yaml` — explicit `command` fields (e.g. `gmail:users:messages:list`)

## 6. Connector Tool Annotations

**Problem**: All connector tools were marked `destructive: True, read_only: False`, causing the AgentLoop to interrupt for approval on list/get operations.

**Fix**: Auto-detect tool type from name suffix:
- Tools ending in `_list`, `_get`, `_search` → `read_only: True, destructive: False, idempotent: True`
- All others → `read_only: False, destructive: True, idempotent: False`

**Relevant files**:
- `packages/connectkit/connectkit/backends/cli.py:236-247` — annotation logic in `_build_connector_tool()`

## 7. System Prompt: Connected SaaS Connectors

**Problem**: Model didn't know connector tools were already authorized and kept asking for approval.

**Fix**: `_get_connector_context()` injects a "Connected SaaS Connectors" section into the system prompt:
```
## Connected SaaS Connectors
IMPORTANT: All connectors below are ALREADY authorized and ready to use.
Do NOT ask the user to approve or connect — just use the available tools directly.

- **google-workspace**: tools named `google_workspace__*` are ready to call (e.g. `google_workspace__gmail_messages_list`)
```

**Relevant files**:
- `src/sdk/runner.py:189-212` — `_get_connector_context()`

## 8. Tool Name Format

Connector tools use namespace prefix: `{connector_name.replace('-', '_')}__{tool_name}`.

Examples:
- `google_workspace__gmail_messages_list`
- `google_workspace__calendar_events_list`
- `google_workspace__drive_files_list`

The namespace prefix is derived from `ConnectorSpec.name` (`google-workspace` → `google_workspace`).

## 9. Known: DeepSeek V4 Flash Tool Calling

DeepSeek V4 Flash (`deepseek-v4-flash` via `https://api.deepseek.com/v1`) supports tool calling normally. Direct API tests confirm it correctly calls `google_workspace__gmail_messages_list` for "check my email". The earlier "needs approval" behavior was caused by the tools not being present in the tool list (due to vault decryption failure), not by model refusal.

## 10. Data Flow

```
Flutter UI → HTTP POST /message → runner.create_sdk_loop()
  → connectkit_bridge.get_tool_definitions()
    → ConnectorRuntime.get_tools()
      → vault.is_connected("google-workspace")? 
        → CLIAdapter.discover_tools(namespace)
          → YAML tool_descriptions (authoritative)
      → _build_connector_tool() -> dict with closures
  → _connector_dicts_to_defs() -> ToolDefinition[]
  → merge with native + MCP tools
  → AgentLoop(tools=all_tools)
```

## 11. File Reference

| File | Purpose |
|------|---------|
| `packages/connectkit/connectkit/vault.py` | Fernet-encrypted credential storage |
| `packages/connectkit/connectkit/oauth.py` | PKCE OAuth flow with `on_connect` hook |
| `packages/connectkit/connectkit/spec.py` | `ToolDescription.command` field |
| `packages/connectkit/connectkit/backends/cli.py` | CLI adapter, tool discovery, build, annotations |
| `packages/connectkit/connectkit/runtime.py` | ConnectorRuntime orchestrator |
| `packages/connectkit/connectors/google-workspace.yaml` | Spec with `tool_descriptions` and `command` fields |
| `src/sdk/runner.py` | `_get_connector_context()`, `reset_user_sdk_loops()`, `_connector_dicts_to_defs()` |
| `src/http/main.py` | OAuth router wiring with `on_connect` |
| `src/http/routers/connectors.py` | Connect/disconnect endpoints with cache invalidation |
| `.env` | `CONNECTKIT_VAULT_KEY`, `DEEPSEEK_API_KEY` |
| `data/users/{user_id}/connectkit/vault.db` | Encrypted credential store (SQLite + Fernet) |
