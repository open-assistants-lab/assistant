# MCP Proxy-First Architecture Design

**Date:** 2026-09-25  
**Status:** Self-reviewed; ready for implementation planning  
**Scope:** Follow-up to the MCP lifecycle reliability design

## Goal

Reduce MCP tool-schema context cost without weakening capability governance,
tool discoverability, receipts, or the existing lifecycle guarantees.

The design separates:

1. MCP connection and metadata lifecycle;
2. MCP tool discovery and invocation surfaces;
3. optional promotion of selected tools into the direct model schema.

## Current state

The current bridge discovers MCP tools and registers namespaced
`mcp__<server>__<tool>` definitions directly into the live agent registry.
This makes every discovered tool part of the model-facing schema and increases
reload, stale-tool, and context-window pressure as MCP servers grow.

The lifecycle reliability work remains authoritative for:

- connection generations;
- configuration reconciliation;
- health/status;
- cleanup;
- retry safety;
- completion and failure reporting.

## Goals

- Keep the default MCP tool surface small and stable.
- Discover and describe tools without requiring a live connection.
- Start servers only when a tool is actually used.
- Preserve explicit opt-in for direct MCP tools.
- Preserve administrator/user capability and approval policy.
- Keep metadata cache invalidation and reload observable.
- Bound oversized MCP results before they consume the model context.
- Avoid a second independent MCP connection manager.

## Non-goals

- MCP transport-security host allowlists.
- Full OAuth implementation.
- Semantic search through an external service.
- MCP UI, tasks, sampling, or plugin-package discovery.
- Removing the existing namespaced MCP tool identifiers.
- Replacing the existing execution kernel or receipt model.

## Model-facing surfaces

### Proxy tool

Introduce one compact proxy tool, conceptually:

```text
mcp(
  action: "search" | "describe" | "call" | "status",
  server?: string,
  query?: string,
  tool?: string,
  args?: object,
  limit?: integer,
  offset?: integer
)
```

The proxy tool is the target default MCP surface. During migration, current
direct registration remains available behind a configuration flag until the
rollout metrics justify changing the default. The proxy supports:

- metadata-only search;
- describe without connecting;
- explicit call;
- status inspection;
- bounded pagination;
- structured errors.

The proxy tool is itself governed as a normal tool. A proxy call resolves the
underlying MCP tool and applies the same permission policy before dispatch.

### Direct tools

Selected tools may be promoted into the live registry through configuration:

```yaml
mcp:
  exposure: hybrid
  direct_tools:
    - "github__create_issue"
    - "chrome-devtools__take_screenshot"
```

Rules:

- `proxy`: no MCP tools are directly registered by default.
- `hybrid`: only explicitly selected direct tools are registered.
- `direct`: retain current broad direct-registration behavior for compatibility.

Direct promotion is a performance/compatibility choice, not a governance
bypass. A promoted tool still passes through the same capability, approval,
sandbox, execution-kernel, and receipt paths.

## Metadata cache

The existing manager may cache server metadata, but the proxy architecture
requires a durable, versioned cache independent of live sessions.

Each cached server record contains:

```text
server_name
source_path
config_hash
connected_at
last_refresh
tools: names, descriptions, input schemas, annotations
resources: names and metadata when supported
cache_status
```

Cache rules:

- Search and describe use cached metadata when available.
- A cache hit never starts a server.
- A cache miss may start the server only when the caller explicitly invokes a
  tool or requests a live refresh.
- Cache records never contain credentials, headers, or raw environment values.
- Config changes invalidate only affected server records.
- A disconnected server with valid cached metadata reports `cached`, not
  `unknown`.
- Cache invalidation and refresh are observable in status snapshots.

## Discovery and refresh

### Initial session behavior

1. Load configuration and provenance.
2. Load cached metadata if valid.
3. Register the proxy tool.
4. Register only configured direct tools.
5. Do not connect lazy servers.
6. Emit a status snapshot after the model-facing surface is synchronized.

### On proxy search/describe

- Use the cache first.
- Return source and freshness metadata.
- Mark results as `cached` when no live connection was opened.

### On proxy call

1. Resolve the server/tool from the cache or live catalog.
2. Revalidate capability and approval policy.
3. Ensure the server connection.
4. Revalidate the current tool definition before dispatch.
5. Execute through the existing MCP session.
6. Return a bounded structured result and receipt.

### Refresh triggers

Refresh metadata when:

- configuration changes;
- a connection is established;
- an MCP `list_changed` notification arrives;
- an explicit `mcp({ action: "refresh" })` occurs;
- a cache entry expires;
- a live call reports a tool/schema mismatch.

Refresh is bounded by a per-server request timeout. A failed refresh preserves
the last known cache and reports `stale` rather than deleting the cache.

## Configuration and provenance

Support explicit layers with later layers taking precedence:

1. user-global shared config;
2. project config;
3. runtime/session config;
4. adapter-owned overrides.

Each effective server definition includes:

```text
source_path
source_type
config_hash
disabled
```

Rules:

- Missing and invalid configuration are distinct.
- Conflicting definitions are surfaced in status and never silently guessed.
- Runtime configuration is isolated and does not mutate files.
- Disabled servers remain visible in status but are not callable.

The full host-config import/discovery system is outside this first phase.

## Status snapshots

Extend the current health response into a stable, read-only snapshot contract:

```json
{
  "servers": {
    "github": {
      "status": "connected",
      "connected": true,
      "cached": true,
      "tool_count": 42,
      "direct_tool_count": 1,
      "last_refresh": "2026-09-25T12:00:00Z",
      "last_error": null,
      "source_path": "/project/.mcp.json",
      "config_hash": "..."
    }
  },
  "totals": {
    "tools": 42,
    "connected": 1,
    "cached": 0,
    "disabled": 0
  }
}
```

Status reads never:

- start a server;
- start OAuth;
- expose transport credentials;
- expose raw headers or environment values;
- mutate the live tool registry.

Snapshots are emitted after initialization, metadata refresh, direct-tool
reconciliation, and shutdown.

## Output guard

MCP responses are bounded before entering model context.

Guard modes:

- text;
- structured content;
- resources;
- binary/resource materialization.

When a result exceeds the configured limit:

- preserve a concise preview;
- report `omitted=true`;
- include safe size/count metadata;
- spill the complete result to the existing protected result store when
  possible;
- return a result ID for bounded retrieval;
- never claim full delivery when the result was omitted.

Output guarding is independent of connection lifecycle and applies equally to
proxy and direct calls.

## Lifecycle integration

The proxy architecture consumes the lifecycle manager from the MCP reliability
design. It does not create a second client/session owner.

Required integration points:

- `MCPManager` owns live sessions and metadata cache.
- `MCPToolBridge` maps proxy actions to manager operations.
- `ToolResult`/execution-kernel outcomes remain authoritative.
- `refresh_user_tool_registries()` updates only explicitly promoted direct
  tools and removes stale promoted definitions.
- manager destruction invalidates cache handles and detaches bridges.

## Security boundaries

- Proxy search/describe is read-only but may reveal configured server/tool
  metadata; apply normal user identity and capability filtering.
- Proxy call resolves the final server/tool name before permission checks.
- Direct promotion never bypasses `allow`/`ask`/`deny`.
- MCP server headers and credentials remain outside tool arguments, search
  results, logs, and health snapshots.
- Cache records are user-scoped and follow existing data-root isolation.
- A stale metadata cache cannot authorize a tool that is not currently allowed;
  execution revalidates policy.

## Migration and rollout

### Phase 1 — observability and cache

- Add provenance and cache metadata.
- Add stable status snapshots.
- Add bounded refresh and stale-cache preservation.
- Keep current direct registration as the default.

### Phase 2 — proxy surface

- Add `mcp` proxy tool.
- Add search/describe/call/status actions.
- Add config flag to enable proxy mode.
- Keep direct tools available for compatibility.

### Phase 3 — hybrid direct tools

- Add `direct_tools` allowlist.
- Add stale direct-tool removal.
- Add schema/context-size advisory thresholds.
- Keep proxy fallback available.

### Phase 4 — default decision

Change the default only after measuring:

- prompt token reduction;
- successful MCP call rate;
- search/describe latency;
- stale-tool incidents;
- reconnect/reload recovery.

## Testing strategy

### Unit tests

- Proxy search/describe uses cache without connecting.
- Proxy call connects lazily and applies policy.
- Direct allowlist registers only selected tools.
- Stale direct tools are removed after refresh.
- Config precedence and provenance are deterministic.
- Invalid/missing config states are distinct.
- Output guard bounds text and structured results.
- Status reads have no connection side effects.
- Retry suppression for non-idempotent tools remains enforced.

### Integration tests

- Local stdio server with changing tool catalogs.
- Cache-only search after server shutdown.
- Live refresh after `list_changed`.
- Proxy call and direct call produce equivalent receipts/outcomes.
- Config edit updates only affected servers.
- Two sessions observe consistent metadata without duplicate server ownership.

## Acceptance criteria

- The target default MCP exposure does not inject every server tool into the model schema.
- Search/describe work from cache without starting servers.
- Calls are lazy, governed, receipted, and bounded.
- Direct promotion is explicit and reversible.
- Config provenance and precedence are visible.
- Health/status snapshots are read-only and secret-free.
- Existing lifecycle reliability guarantees remain intact.
- No MCP behavior is removed by this follow-up design.

## Review decisions

1. Proxy-first is the long-term default direction; current direct behavior remains
   available during migration.
2. Durable metadata caching is separate from live session ownership.
3. Transport-security allowlists and OAuth remain separate follow-ups.
4. Output guarding is part of the proxy/call boundary, not only a UI concern.
