# Service Adapter Pattern

## Context

EA's agent connects to external services — Gmail, Slack, GitHub, Jira, calendars, CRMs. Each service has a different API surface, auth model, and data format. Writing a custom SDK `@tool` per service doesn't scale past ~5 integrations.

The codebase already contains three independent integration mechanisms:
- `CLIToolAdapter` (`src/sdk/tools_core/cli_adapter.py`) — wraps any CLI as an SDK tool (used by browser, firecrawl)
- `MCPToolBridge` (`src/sdk/tools_core/mcp_bridge.py`) — converts MCP server tools to SDK tool definitions
- `ConnectKit` (`packages/connectkit/`) — YAML-defined connectors with CLI, MCP, and API backends (544 connectors)

These three mechanisms were built independently for different use cases. The pattern unifies them under one conceptual model.

## The Pattern

```
Skill (what)    ──►  Adapter (how)    ──►  Service
                        │
                        ├── CLI backend   (CLIToolAdapter)
                        ├── MCP backend   (MCPToolBridge)
                        └── API backend   (ConnectKit bridge / custom @tool)
```

| Layer | Role | Example |
|---|---|---|
| **Skill** | Tells the agent WHAT commands exist, how to use them | `gws-email/SKILL.md` — "Use `gws gmail +send --to ...`" |
| **Adapter** | Handles HOW the command executes — CLI, MCP, or API | `CLIToolAdapter` wraps `gws` CLI. `MCPToolBridge` wraps Slack MCP server |
| **Service** | The actual external system | Gmail, Slack, GitHub, Jira |

**One YAML per connector, one backend per execution layer. The agent sees only tools — it never knows which backend runs underneath.**

## Why

### Before (per-service SDK tools)

```python
# 8 files, ~1500 lines per service
@tool def email_list(): ...      # IMAP wrapper
@tool def email_send(): ...      # SMTP wrapper
@tool def slack_send(): ...      # Slack API wrapper
# ... repeat for every service
```

Each new service means new Python code, new tests, new maintenance burden. When the upstream API changes, we update our code.

### After (adapter pattern)

```yaml
# One YAML file, 20 lines
name: google-workspace
backend: cli
command: gws
tools:
  - name: gmail_list
    exec: gmail messages list
```

Zero Python code. When Google updates their API, `gws` updates itself (Discovery Service). We change nothing.

## Pros

| Pro | Detail |
|---|---|
| **Zero-code integrations** | One YAML per service. ConnectKit already has 544 connectors pre-built |
| **One auth system** | ConnectKit OAuth vault handles token storage, refresh, expiry for all services |
| **Agent-friendly by design** | CLI tools (gws, m365, gh, stripe) output structured JSON natively. MCP servers are built for LLM tool calling |
| **Service updates are free** | `gws` auto-updates its command surface from Google's Discovery Service. MCP servers update their own tools. Zero maintenance |
| **Degraded-mode resilient** | If one MCP server crashes, other tools still work. `MCPToolBridge` already supports partial failures |
| **Already built** | `CLIToolAdapter`, `MCPToolBridge`, and ConnectKit exist and work. No new infrastructure needed |
| **Skill-driven discovery** | Agent loads a skill → learns what tools are available → calls them. Progressive disclosure. No system prompt bloat |

## Cons

| Con | Detail |
|---|---|
| **CLI dependency** | User must install `gws`, `m365`, `gh`, etc. Adds setup friction. Mitigated by skill `install` blocks that auto-check and install |
| **Auth fragmentation** | Each service has its own OAuth flow (Google, Microsoft, GitHub). ConnectKit unifies the UX, but OAuth is inherently per-provider |
| **No offline-first** | CLI tools need network access. For services where offline is critical, a HybridDB-backed SDK tool with sync is better (e.g., email for Flutter browse mode) |
| **Adapter ≠ SDK-quality** | CLI text may change format between versions. MCP servers may go down. Robustness depends on CLI/MCP quality |
| **Not universal** | Some services have no CLI and no MCP server. Fallback: ConnectKit's API backend or a custom `@tool` |
| **Skill maintenance** | Each connector needs a SKILL.md. Can be auto-generated from YAML, but still needs human review for accuracy |
