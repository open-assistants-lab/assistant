# Agent Connect — Proposal

> A standalone open-source library for connecting AI agents to user SaaS accounts. One YAML file per service. OAuth, token vault, and tool discovery handled automatically.

**Status:** Proposal / peer review  
**Author:** Eddy Xu  
**Date:** May 1, 2026

---

## 1. Context — Where This Came From

### The EA Platform

Assistant (EA) is a production AI agent platform — custom SDK, ~6,500 lines, 470+ tests. It runs on three tiers:

| Tier | Users | Infrastructure |
|------|-------|---------------|
| Solo Desktop | 1 (localhost) | Single process, zero config |
| Shared Host | Multi-tenant | Per-user Docker containers + gateway |
| Enterprise | Multi-tenant SSO | Per-user VMs, self-hosted |

Currently, the agent has ~93 built-in tools (filesystem, shell, browser, firecrawl, subagents, memory, contacts, todos) plus MCP bridge support. Email tools exist but are deliberately disabled pending GWS integration.

### The Gap

The agent's value proposition depends on connecting to user **SaaS accounts** — Gmail, Calendar, Outlook, Slack, GitHub, Dropbox, Notion, etc. A calendar assistant that can't read your calendar is useless. A memory system that can't pull from your Slack history is shallow.

Today, connecting to a service means:

1. Writing Python tool code (the `@tool` decorator path)
2. Wrapping a CLI tool (the `CLIToolAdapter` path)
3. Configuring an MCP server (the `MCPToolBridge` path)

These three approaches share no infrastructure. CLI adapters are global singletons (every user shares the same Firecrawl API key). MCP servers are per-user but have no credential storage. There's no OAuth flow, no token vault, no unified connector lifecycle.

### The Question

> *"What's the most efficient and effective way to onboard 50+ SaaS connectors, for both solo and enterprise deployment?"*

This proposal is the answer.

---

## 2. Research — How Others Scale Connectors

### n8n (400+ nodes)

Three-tier approach:
- **Declarative DSL** — JSON/YAML descriptions of endpoints + auth, no execute() code
- **Programmatic** — TypeScript for complex integrations
- **Community marketplace** — scaffolding CLI, ecosystem adds the rest

The engine auto-executes API calls from declarative descriptions. Simple REST integrations require zero code — just describe endpoints, params, and auth.

### Zapier (7,000+ integrations)

Doesn't build integrations internally. App companies build their own via a **Developer Platform** (CLI scaffolding, auth frameworks, testing tools). The 7,000 number is an ecosystem play, not an engineering play.

### Perplexity AI & AI-first platforms

Use **MCP servers** as the universal interface. Any MCP server = instant tool for any agent. No custom connector code. The MCP ecosystem handles tool discovery; AI platforms consume tools dynamically.

### Key Insight

The winning pattern across all three: **invest in the platform, not individual connectors.** Build a framework that makes each new connector trivial, then let the ecosystem (or your YAML templates) fill the catalog.

---

## 3. Current State — What Exists in EA

### What's working

| Component | Status | Per-user? |
|-----------|--------|-----------|
| `CLIToolAdapter` | ✅ Working | ❌ Global singleton |
| `MCPToolBridge` | ✅ Working | ✅ Per-user |
| `MCPManager` (server lifecycle) | ✅ Working | ✅ Per-user, lazy start |
| Per-user data isolation (`data/users/{id}/`) | ✅ Working | ✅ |
| `.mcp.json` config per user | ✅ Working | ✅ |
| Subagent system (work_queue + coordinator) | ✅ Working | ✅ |

### What's missing

| Component | Status |
|-----------|--------|
| OAuth callback endpoints | ❌ Not started (Phase 12 planning) |
| Per-user credential vault | ❌ Path exists, no implementation |
| Unified connector abstraction | ❌ CLI, MCP, and @tool are three separate systems |
| Per-user CLI token injection | ❌ FirecrawlCLI reads global settings |
| Connector lifecycle (install/enable/disable) | ❌ No concept exists |
| "Connect" UX in Flutter | ❌ No SaaS connection UI |
| Connector catalog / discovery | ❌ No way to list available services |

### The hard reality

The SDK core (AgentLoop, providers, middlewares) is solid. But the gap between "solo desktop agent" and "multi-user platform with 50+ SaaS connectors" is measured in months, not weeks. Every new connector today requires **Python code + per-user auth wiring + Flutter UI** — three separate systems with no shared infrastructure.

---

## 4. The Proposal — Agent Connect

### What it is

A standalone open-source Python library that bridges agents and SaaS:

```python
pip install agent-connect

from agent_connect import ConnectorHub

hub = ConnectorHub(user_id="alice", vault_path="./vault.db")
hub.register_from_yaml("connectors/*.yaml")

# User connects once
hub.authorize("google-workspace")  # Opens browser OAuth flow

# Agent gets tools
tools = hub.get_tools("alice")  # All connected services' tools, namespaced
```

### One YAML file per service

No Python code per connector. Three backends handle execution:

```yaml
# connectors/google-workspace.yaml
name: google-workspace
display: "Google Workspace"
icon: google
category: productivity
auth:
  type: oauth2
  authorize_url: https://accounts.google.com/o/oauth2/v2/auth
  token_url: https://oauth2.googleapis.com/token
  scopes:
    - https://www.googleapis.com/auth/gmail.readonly
    - https://www.googleapis.com/auth/calendar.readonly
    - https://www.googleapis.com/auth/drive.readonly
  extra_params:
    access_type: offline
    prompt: consent
tool_source:
  type: cli                    # Backend type: cli | mcp | http
  command: gws
  install: npm install -g @googleworkspace/cli
  env_mapping:
    access_token: GWS_ACCESS_TOKEN
```

```yaml
# connectors/github.yaml
name: github
display: "GitHub"
icon: github
category: dev-tools
auth:
  type: oauth2
  authorize_url: https://github.com/login/oauth/authorize
  token_url: https://github.com/login/oauth/access_token
  scopes: [repo, read:org]
tool_source:
  type: cli
  command: gh
  install: brew install gh
```

```yaml
# connectors/dropbox.yaml
name: dropbox
display: "Dropbox"
icon: dropbox
category: storage
auth:
  type: oauth2
  ...
tool_source:
  type: mcp                    # Has an MCP server — use the bridge
  server: dropbox
  command: npx @anthropic/dropbox-mcp
```

```yaml
# connectors/notion.yaml
name: notion
display: "Notion"
icon: notion
category: productivity
auth:
  type: oauth2
  ...
tool_source:
  type: http                   # No CLI or MCP — thin REST wrapper
  base_url: https://api.notion.com/v1
  tools:
    - name: search
      path: /search
      method: POST
      parameters:
        query: string
    - name: list_databases
      path: /databases
      headers:
        Notion-Version: "2022-06-28"
```

### Runtime resolution

```python
class ConnectorRuntime:
    """Loads a YAML spec → resolves auth → picks backend → returns tools."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.vault = CredentialVault(user_id)

    def load(self, spec: ConnectorSpec) -> list[ToolDefinition]:
        token = self.vault.get_token(spec.name)
        if not token:
            return []   # Service not connected — no tools exposed

        if spec.tool_source.type == "cli":
            backend = CLIAdapter(spec.tool_source, token, self.user_id)
        elif spec.tool_source.type == "mcp":
            backend = MCPAdapter(spec.tool_source, token, self.user_id)
        elif spec.tool_source.type == "http":
            backend = HTTPAdapter(spec.tool_source, token)

        return backend.discover_tools(namespace=spec.name)
        # Tools are namespaced: google-workspace__gmail_list, github__issue_create
```

### Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Agent Connect                        │
│                                                          │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Spec Parser │  │ Credential   │  │ OAuth Callback   │ │
│  │ (YAML→config)│  │ Vault        │  │ Endpoint         │ │
│  │             │  │ (encrypted   │  │ (spec-driven,    │ │
│  │             │  │  SQLite)     │  │  universal)      │ │
│  └────────────┘  └──────────────┘  └──────────────────┘ │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Adapter Backends                     │   │
│  │                                                  │   │
│  │  CLIAdapter   │  MCPAdapter   │  HTTPAdapter     │   │
│  │  (user-aware  │  (token → env │  (declarative    │   │
│  │   subprocess) │   for bridge) │   REST client)   │   │
│  └──────────────────────────────────────────────────┘   │
│                         ↓                                │
│  ┌──────────────────────────────────────────────────┐   │
│  │  ConnectorRuntime → returns ToolDefinition[]     │   │
│  │  with namespace: `{service}__{tool}`             │   │
│  └──────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
         ↓                          ↓
┌────────────────┐    ┌────────────────────────┐
│   Assistant SDK       │    │  Any Agent Framework   │
│  (AgentLoop)   │    │  (LangChain, CrewAI,   │
│                │    │   AutoGen, etc.)       │
│  Consumes as   │    │                        │
│  ToolDefinition│    │  Same interface        │
└────────────────┘    └────────────────────────┘
```

### What changes in EA

| Old Way | New Way |
|---------|---------|
| `_fc = FirecrawlCLI()` (module singleton) | Firecrawl is a YAML spec, loaded per-user with per-user API key from vault |
| `_ab = AgentBrowserCLI()` (module singleton) | Same — YAML spec, per-user |
| MCP servers configured manually in `.mcp.json` | `ConnectorRuntime` manages MCP lifecycle, vault injects tokens |
| No OAuth flow | Universal callback endpoint, spec-driven |
| No credential storage | Encrypted SQLite vault per user |

### What stays unchanged

- `@tool` decorator — built-in tools (filesystem, shell, time, subagents) stay as-is
- `ToolDefinition` — unchanged, connector tools are `ToolDefinition` objects
- `AgentLoop` — still gets a list of `ToolDefinition`, executes them. The difference is *how* they're discovered
- `MCPToolBridge` — still works, now called from `MCPAdapter` with vault-injected tokens

---

## 5. Scope & Positioning

### In scope

| Component | Description |
|-----------|-------------|
| ConnectorSpec (Pydantic model) | Parses YAML → typed config |
| CredentialVault | Per-user encrypted SQLite: API keys, OAuth tokens, refresh tokens |
| OAuth callback endpoint | Universal, spec-driven FastAPI route |
| CLIAdapter (per-user) | Refactored from global singleton, env-passes per-user tokens |
| HTTPAdapter | Declarative REST client from spec |
| MCPAdapter | Thin wrapper around existing MCPToolBridge, vault token injection |
| ConnectorRuntime | Spec → auth → backend → ToolDefinition[] |
| Connector catalog API | Lists available connectors, their connection status |
| Flutter ConnectorButton widget | Reusable "Connect {service}" UI, reads catalog + triggers OAuth |

### Out of scope

| Component | Belongs to |
|-----------|-----------|
| AgentLoop / ReAct reasoning | Assistant SDK |
| LLM providers | Assistant SDK |
| Conversation memory / summarization | Assistant SDK |
| HITL / interrupt handling | Assistant SDK |
| Subagent orchestration | Assistant SDK |
| Tool annotations / guardrails | Assistant SDK |
| Skills system | Assistant SDK |
| Chat UI (beyond the Connect button) | EA Flutter app |

### Positioning vs. existing tools

| Tool | What it does | How Agent Connect differs |
|------|-------------|--------------------------|
| **Nango** | OAuth broker, 250+ pre-built integrations | Nango handles auth only — no agent tools. Agent Connect generates agent tools from spec. |
| **Composio** | Agent tool marketplace, 250+ integrations | Closed source, hosted, pricing. Agent Connect is open source, self-hosted. |
| **MCP ecosystem** | Protocol for tool calling | MCP defines how tools are called. Agent Connect defines how users authorize them, where tokens are stored, how services are discovered. It's the auth layer MCP forgot. |

**Tagline:** *"The auth layer that MCP forgot."*

### Why it's a separate open-source project

| Belongs in EA? | Belongs in Agent Connect? |
|----------------|--------------------------|
| AgentLoop, providers, ReAct | OAuth callback + token vault |
| ToolDefinition, @tool decorator | YAML connector spec format |
| Conversation memory | CLI/MCP/HTTP adapter backends |
| Subagent coordination | Per-user credential encryption |
| Skills system | "Connect" Flutter widgets |
| Flutter app (full) | Connector catalog API |

Agent Connect is consumed by EA. Other agent projects (LangChain, CrewAI, AutoGen) consume it too. Separating it means:

1. EA stays focused on its SDK and agent loop
2. Agent Connect benefits from a wider community
3. The YAML connector format becomes a shared standard, not EA-proprietary

---

## 6. Implementation Plan

### Phase 1: Core Library (8 days)

| Day | Component | Description |
|-----|-----------|-------------|
| 1 | `ConnectorSpec` model | Pydantic model + YAML parser. Validates auth config, tool_source type, env mappings |
| 2-3 | `CredentialVault` | Encrypted SQLite per user. Stores tokens, handles refresh token rotation, expiry tracking |
| 4-5 | OAuth callback endpoint | Universal FastAPI route: `/auth/callback?service=google-workspace&code=...`. Reads spec for token_url, client_id, client_secret |
| 6-7 | Adapter backends | `CLIAdapter` (user-aware subprocess), `HTTPAdapter` (declarative REST), `MCPAdapter` (vault token → bridge) |
| 8 | `ConnectorRuntime` | Orchestrates: spec → auth check → backend selection → ToolDefinition[] |

### Phase 2: First 10 Connectors (2 days)

| Day | Task |
|-----|------|
| 9 | Write 10 YAML files: Google Workspace, Microsoft 365, Slack, GitHub, Linear, Notion, Dropbox, Stripe, Twilio, Airtable |
| 10 | Test end-to-end: OAuth login → token stored → agent can call tools → agent can call tools on behalf of user |

### Phase 3: Flutter + Catalog (2 days)

| Day | Component |
|-----|-----------|
| 11 | `ConnectorButton` widget — reusable, reads catalog, shows connection status, triggers OAuth |
| 12 | Connector catalog API — lists available connectors, their connection status per user |

### Total: ~12 days to v0.1.0

### After v0.1.0

Adding a new connector is 10 minutes:

```
1. Write connectors/{saas}.yaml
2. Register OAuth app on their developer console (one-time, 15 min)
3. Drop CLIENT_ID + CLIENT_SECRET into deployment config
```

Zero Python code. Zero Flutter changes. The Connect UI auto-discovers it from the catalog. The agent loop picks up new tools on the next conversation.

---

## 7. Tradeoffs & Risks

### What we trade

| Trade | Why it's worth it |
|-------|-------------------|
| 12 days upfront investment | Pays back at connector #6. By connector #20, it's clearly the right call |
| New open-source project to maintain | Wider audience = more contributors. Connector spec format attracts ecosystem |
| Refactoring FirecrawlCLI/AgentBrowserCLI singletons | They were already broken for multi-user. This fixes them |
| Learning YAML-based tool configuration | Same approach that powers n8n's 400+ nodes. Proven |
| Another repo in the EA ecosystem | Loose coupling. EA consumes Agent Connect as a dependency |

### Risks

| Risk | Mitigation |
|------|-----------|
| Services change their APIs → YAML specs go stale | Spec has version field. Community can PR updated specs. HTTP adapter backend uses spec to generate API calls — if spec is right, call is right |
| MCP servers may become the universal standard | Agent Connect is MCP-compatible. The `MCPAdapter` backend means every MCP server is an Agent Connect connector. If MCP wins, Agent Connect is the auth+discovery layer on top |
| OAuth token refresh is error-prone | CredentialVault handles refresh transparently. Same pattern as every production OAuth client |
| User doesn't want OAuth for every service | `auth.type: api_key` is supported. User pastes key once into vault, agent uses it thereafter |
| Enterprise needs SSO, not per-service OAuth | Admin provisions API keys centrally. CredentialVault seeded at deploy time. Same vault, different population method |
| Scope creep ("add RAG, add vector search...") | Spec format is opinionated: auth + tools only. No search, no memory, no RAG. Those belong to Assistant SDK |

---

## 8. What Success Looks Like

### v0.1.0 (2 weeks)

```python
# Solo desktop user
hub = ConnectorHub(user_id="default_user")
hub.register_from_yaml("connectors/*.yaml")
hub.authorize("google-workspace")     # Opens browser → OAuth → token vaulted
tools = hub.get_tools("default_user") # [google-workspace__gmail_list, ...]
```

### v1.0 (2 months)

- 50+ connector YAML specs shipped
- Community PRs adding connectors
- Connector catalog live at `registry.agentconnect.dev/connectors.json`
- EA ships with Agent Connect bundled
- Flutter app renders "Connect" marketplace
- Enterprise self-hosted deployment works with admin-provisioned vault

### v2.0 (6 months)

- Connector health: uptime monitoring, rate limit detection, degraded mode
- Scoped tools: user can limit what the agent can do (read-only Gmail, no Drive delete)
- Connector analytics: which services are most connected, most used
- Third-party agent frameworks adopt the spec format

---

## 9. Decision Points

### Questions for peer review

1. **Naming:** "Agent Connect" vs alternatives? "Connector Kit"? "Agent Auth"?

2. **Spec format:** YAML is proposed because it's human-friendly and n8n uses it. JSON alternative? TOML?

3. **CLI-first bias:** The proposal favors CLI-based connectors (GWS, M365, gh, slack). Should we prioritize HTTP adapter backends instead for services without CLIs?

4. **Namespace separator:** `__` (double underscore) like `google-workspace__gmail_list` vs `.` vs `:`? Existing MCP bridge uses `mcp__{server}__{tool}`.

5. **Open source license:** MIT (permissive, wide adoption) vs Apache 2.0 (patent grant, enterprise-friendlier)?

6. **Relationship to MCP:** Should Agent Connect be "an MCP auth layer" (narrow positioning) or "a universal connector framework" (broader, competes with Composio)?

7. **Scope boundary:** Auth + token vault + tool discovery? Or also include rate limiting, webhook listening, data sync?

---

## Appendix A: Full Connector Spec Schema

```python
from pydantic import BaseModel
from enum import Enum
from typing import Literal

class AuthType(str, Enum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BASIC = "basic"
    NONE = "none"

class ToolSourceType(str, Enum):
    CLI = "cli"
    MCP = "mcp"
    HTTP = "http"

class OAuth2Config(BaseModel):
    authorize_url: str
    token_url: str
    scopes: list[str]
    extra_params: dict = {}
    pkce: bool = False

class ApiKeyConfig(BaseModel):
    header_name: str = "Authorization"
    header_prefix: str = "Bearer"
    env_var: str

class AuthConfig(BaseModel):
    type: AuthType
    oauth2: OAuth2Config | None = None
    api_key: ApiKeyConfig | None = None

class CLIToolSource(BaseModel):
    type: Literal["cli"]
    command: str
    install: str
    env_mapping: dict[str, str] = {}

class MCPToolSource(BaseModel):
    type: Literal["mcp"]
    server: str
    command: str

class HTTPToolEndpoint(BaseModel):
    name: str
    path: str
    method: str = "GET"
    parameters: dict[str, str] = {}
    headers: dict[str, str] = {}

class HTTPToolSource(BaseModel):
    type: Literal["http"]
    base_url: str
    tools: list[HTTPToolEndpoint]

class ConnectorSpec(BaseModel):
    name: str
    display: str
    icon: str
    category: str
    version: str = "1.0"
    description: str = ""
    auth: AuthConfig
    tool_source: CLIToolSource | MCPToolSource | HTTPToolSource
```

## Appendix B: CredentialVault Schema

```sql
CREATE TABLE credentials (
    service_name TEXT PRIMARY KEY,
    auth_type TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    token_expires_at TEXT,
    api_key TEXT,
    extra_data TEXT,       -- JSON: any service-specific fields
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE oauth_states (
    state TEXT PRIMARY KEY,
    service_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    redirect_uri TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
```

Encryption: SQLite `sqlcipher` or application-layer `cryptography.fernet` with a master key stored as an env var (`AGENT_CONNECT_VAULT_KEY`).

## Appendix C: What About GWS Already?

EA has:

- `src/storage/gmail_cache.py` — a low-level `_run_gws()` subprocess helper for Gmail API calls. Not a CLIToolAdapter. Not per-user tool definitions. Just a storage helper.
- `src/sdk/tools_core/email.py` — 8 email tools (`email_connect`, `email_disconnect`, `email_accounts`, `email_list`, `email_get`, `email_search`, `email_send`, `email_sync`). Disabled in `__init__.py:1`.
- `scripts/sync_gmail.py` — batch sync script, not real-time agent tools.

EA also has contacts tools (`contacts_list`, `contacts_get`, `contacts_add`, `contacts_update`, `contacts_delete`, `contacts_search`) in `src/sdk/tools_core/contacts.py:6` — these are flat-file SQLite operations, not Google Contacts sync.

Under Agent Connect, GWS becomes:

```yaml
# connectors/google-workspace.yaml
name: google-workspace
display: "Google Workspace"
tool_source:
  type: cli
  command: gws
```

And the EA email/contacts tools become thin wrappers around `hub.get_tools("alice")` with GWS scoped to the user's authorized services. The old `email_connect` tool (which took plaintext credentials) is deprecated in favor of OAuth.

## Appendix D: What About MCP Bridge Already?

Assistant's existing `MCPToolBridge` (`src/sdk/mcp_bridge.py`) is already per-user and works well. Under Agent Connect, this code doesn't change — it just gets a token from the vault instead of depending on whatever auth the MCP server handles natively:

```python
# Before (current): MCP server handles auth itself
mcp_tools = MCPToolBridge(user_id="alice").discover()

# After: vault injects token, MCP bridge uses it
mcp_tools = MCPAdapter(spec, vault_token).discover()
```

The MCP bridge is still the transport layer. Agent Connect is the auth + credential layer above it.

## Appendix E: CLI Singleton — Not Actually a Problem

The proposal mentions that `FirecrawlCLI` and `AgentBrowserCLI` are "global singletons" and need refactoring. In practice, this concern is overblown:

**Single-User Desktop:**

One process. One user. One CLI session. Module-level singleton is perfectly fine.

```
firecrawl CLI → OAuth'd as Eddy → works ✅
gws CLI       → OAuth'd as Eddy → works ✅
```

**Multi-Tenant Docker (per-user containers):**

Each user gets their own container. Module-level singleton per container = per user = no conflict.

```
Container A (Alice)          Container B (Bob)
┌──────────────────────┐    ┌──────────────────────┐
│ firecrawl CLI        │    │ firecrawl CLI        │
│  → OAuth'd as Alice  │    │  → OAuth'd as Bob    │
│ gws CLI              │    │ gws CLI              │
│  → OAuth'd as Alice  │    │  → OAuth'd as Bob    │
└──────────────────────┘    └──────────────────────┘
```

EA never runs two users in the same process. The "singleton" is scoped to one user by virtue of per-container deployment.

**What Agent Connect actually does for CLIs:**

1. Reads the user's OAuth token from the vault
2. Sets it as an environment variable (`GWS_ACCESS_TOKEN=eyJ...`)
3. Spawns the CLI as a subprocess
4. The CLI picks up the token from the env var

The `env_mapping` in the YAML spec handles this. No singleton refactoring needed.

```yaml
tool_source:
  type: cli
  command: gws
  env_mapping:
    access_token: GWS_ACCESS_TOKEN
```

---

## Appendix F: Peer Review Notes (April 30, 2026)

### Verdict: ✅ Ship it.

The architecture is sound, scope is disciplined, and open-source positioning is strategic. Timeline needs adjustment and HTTPAdapter should be deferred, but the core idea — one YAML per service, vault for tokens, tools auto-discovered — is exactly right.

### Strengths

| Area | Assessment |
|------|-----------|
| **Problem definition** | Clear. The 3-path gauntlet (Python code, CLI wrapper, MCP config) is real. No shared auth infrastructure is the exact pain point. |
| **Competitive research** | Good triangulation. n8n = declarative, Zapier = ecosystem, Perplexity = MCP. The "invest in platform, not individual connectors" insight is right. |
| **Architecture** | Clean. YAML → auth vault → 3 adapter backends → ToolDefinition[]. The separation from Assistant SDK is correct — loose coupling. |
| **Positioning** | "The auth layer that MCP forgot" is a sharp tagline. Differentiates from Nango (auth-only) and Composio (closed-source). |
| **Scope discipline** | Agent Loop, memory, HITL, subagents, skills — all correctly placed out of scope. |

### Concerns

| # | Concern | Recommendation |
|---|---------|---------------|
| 1 | **Timeline optimistic.** 12 days for OAuth + vault + 3 adapters + 10 connectors + Flutter UI is 3-4 weeks in practice. OAuth has edge cases: token refresh races, revocation, scope changes, state replay attacks. | Double the estimate. Call it 3-4 weeks for v0.1.0. Under-promise, over-deliver. |
| 2 | **CLI adapter was overcomplicated.** The singleton concern in the proposal is not actually a problem — EA never runs two users per process. Per-container deployment handles multi-tenancy. (See Appendix E.) | Remove the singleton refactoring from scope. CLI adapter just needs vault → env var → subprocess. |
| 3 | **HTTPAdapter will explode on real APIs.** Declarative REST from YAML works for `GET /search?q={query}` but breaks on pagination, nested resources, rate limiting, multipart uploads. | Defer HTTPAdapter to Phase 2. Ship CLI + MCP only in v0.1. |
| 4 | **Missing: tool description quality.** The YAML generates ToolDefinition[] but descriptions determine whether the agent uses tools correctly. A bad description = agent calls tool wrong. | Add a required `tool_descriptions` section to the spec: per-tool descriptions written for LLM consumption, not API docs. |
| 5 | **Missing: connector health.** What happens when OAuth refresh fails mid-conversation? CLI install fails? Service rate-limits the agent? | Add `connector.health()` that reports status to the agent loop. Minimum: connected / disconnected / error. |
| 6 | **Enterprise admin vault is underspecified.** "Admin provisions API keys centrally" is 3 words for a complex admin UX. | Be explicit that v0.1 is solo/self-serve only. Enterprise admin vault is v2.0. |

### Recommendations

| # | Recommendation |
|---|---------------|
| 1 | **Start smaller.** Build CredentialVault + MCPAdapter first. Let existing MCP bridge handle tools. Add CLI next. Skip HTTP for v0.1. |
| 2 | **Get to working code before writing 10 YAMLs.** Build GWS + GitHub end-to-end. If those work, the architecture works. |
| 3 | **Ship Assistant's Firecrawl/AgentBrowser as the first connectors.** Replace the global singletons with YAML specs. Dogfood immediately. Validate the approach on your own stack. |
| 4 | **Add `session_strategy` to CLIAdapter spec** if needed: `env_var` | `profile` | `per_process`. Currently only `env_var` needs to be implemented since per-container deployment handles process isolation. |
| 5 | **Add required `tool_descriptions` per tool** in the YAML spec — LLM-optimized descriptions. |
| 6 | **Timeline:** Call it 3-4 weeks for v0.1.0. |
| 7 | **Naming:** "Agent Connect" is good. Don't change it. |
| 8 | **Namespace separator:** Keep `__` — consistent with existing `mcp__{server}__{tool}`. |

---

*End of proposal.*
