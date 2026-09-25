# MCP Lifecycle Reliability Design

**Date:** 2026-09-25  
**Status:** Self-reviewed; ready for implementation planning  
**Scope:** MCP manager/bridge lifecycle reliability after v0.6.20

## Goal

Make MCP startup, tool discovery, reload, reconnect, retry, and shutdown
deterministic and safe for multiple active conversation loops without
reintroducing the deferred MCP transport-security redesign.

## Context

The focused MCP suite currently passes, and the manager already has useful
foundations:

- lazy per-user startup;
- `/mcp/health` and `/v1/mcp/health`;
- bounded reconnect attempts;
- single-flight reconnect protection;
- live `tools/list` rediscovery;
- bridge detach support;
- stale/degraded/absent health states.

The audit confirmed lifecycle gaps that can appear intermittent:

1. A connection is stored before `list_tools()` succeeds. If discovery fails,
   the half-open connection remains published and its `AsyncExitStack` is not
   closed.
2. Deleting or invalidating `.mcp.json` is not detected as a configuration
   change, so old connections can remain active.
3. `MCPManager.reload()` does not reliably notify every registered bridge;
   current-loop `mcp_reload` handling is not a complete fan-out mechanism.
4. The bridge retries every failed `call_tool()`, including non-idempotent
   tools that may already have produced an external side effect.
5. `cleanup()` does not cancel and await reconnect tasks, and the global MCP
   manager cache has no explicit destruction lifecycle.
6. Configuration checks are scattered across read, health, connection, and
   reload paths, allowing stale configurations to remain active.

## Scope

### In scope

- Candidate connection lifecycle and cleanup.
- Explicit manager destruction and reconnect-task cancellation.
- Centralized configuration reconciliation.
- Reload fan-out to all live user bridges/loops.
- Generation-safe reconnect/reload/cleanup races.
- Retry policy based on MCP tool annotations.
- Backward-compatible health/error reporting.
- Real local-stdio lifecycle tests.

### Explicitly out of scope

- MCP transport-security host allowlists.
- Replacing or redesigning MCP discovery/configuration.
- The audit-response portion of issue #7.
- Provider-specific MCP server behavior.
- Schema changes to MCP tools.

Issue #7 remains deferred for a separate MCP transport/security design.

## Architecture

### 1. Manager ownership and lifecycle generation

`MCPManager` owns a monotonically increasing lifecycle generation. Every full
reload, manager cleanup, or configuration replacement increments the generation
before closing connections.

A reconnect captures the generation at task creation. Before publishing a
candidate, it checks that the captured generation is still current. A late task
from an older generation closes its candidate and cannot repopulate the
manager.

The manager uses one lock for connection and bookkeeping mutations. Potentially
blocking operations (`initialize`, `list_tools`, `call_tool`, and `aclose`) run
outside that lock.

A candidate is published only after:

1. connection creation;
2. MCP session initialization;
3. successful `tools/list`;
4. generation validation.

The existing health shape remains compatible. Add fields only when needed, such
as `retrying`, `generation`, or a stable `last_error`; preserve `status`,
`connected`, `degraded`, `last_refresh`, `tool_count`, and `last_error`.

### 2. Explicit manager destruction

Add an explicit cache-removal operation:

```python
async def close_mcp_manager(user_id: str) -> None:
    manager = _MCP_MANAGERS.pop(user_id, None)
    if manager is not None:
        await manager.cleanup()
```

`cleanup()` must:

1. increment the lifecycle generation;
2. cancel and await the idle monitor;
3. cancel and await every reconnect task;
4. clear reconnect bookkeeping;
5. close published connections outside the state lock;
6. clear refresh listeners when the manager is permanently removed.

A reconnect that finishes after cleanup must observe a stale generation and
close its candidate instead of publishing it.

The cache-removal operation is used only after all bridges for that user have
been detached. The manager also carries a closed-generation guard so a stale
bridge cannot publish work after destruction. The normal `detach()` path remains
available when only a particular loop/bridge is evicted.

### 3. Centralized configuration reconciliation

Add one internal reconciliation method, conceptually:

```python
async def _ensure_current_config(self) -> None:
    ...
```

It distinguishes:

- valid configuration;
- missing configuration;
- invalid/unreadable configuration.

The method compares mtime and a normalized configuration hash. A changed mtime
with no valid config is a real change:

- missing file: stop all connections and report `absent`;
- invalid/unreadable file: stop all connections and report `degraded` with a
  sanitized `last_error`;
- changed valid file: stop and restart using the new configuration.

This reconciliation is called by:

- `get_tools()`;
- `ensure_connection()`;
- `list_servers()`;
- `health()`;
- `reload()`.

No MCP operation may use a connection from an obsolete configuration
generation.

### 4. Initial connection publication

`_start_server()` follows this sequence:

1. Create a candidate connection without publishing it.
2. Call `list_tools()`.
3. Set candidate tools and refresh timestamps.
4. Acquire the lifecycle lock and publish only if the generation is current.
5. Notify refresh listeners.
6. On any failure, close the candidate exactly once and record the sanitized
   error.

A failed `list_tools()` must never leave a connection in `_connections`.

### 5. Reload fan-out

`MCPManager.reload()` is the authoritative operation:

1. Increment the lifecycle generation.
2. Stop all current connections.
3. Load and validate the new configuration.
4. Start each configured server using candidate publication.
5. Notify every registered bridge for each refreshed server.

Bridges update their own registries and call the existing
`refresh_user_tool_registries()` path. Removed tools are removed from every live
loop before new tools are registered.

The current-loop special case in `mcp_reload` remains only as a compatibility
fallback after the manager fan-out. A partial reload reports partial/failure
state; it never claims that every tool is current when some servers failed.

### 6. Safe retry policy

The bridge receives MCP annotations through `_convert_tool_annotations()`.
When `call_tool()` raises:

- **Read-only or explicitly idempotent tool:** one reconnect and one retry are
  allowed.
- **Missing idempotency metadata, non-idempotent, or unknown-effect tool:** do
  not replay. Return a structured `ToolResult` with:
  - `outcome=uncertain`;
  - effect state `unknown`;
  - a clear message that the remote side effect may have occurred;
  - `retryable=false`.
- **Connection unavailable before dispatch:** reconnect/retry is allowed
  because no call was sent.
- **MCP `isError` response:** return the server's error; do not reconnect
  merely because the tool reported a normal MCP error result.

This prevents duplicate writes while retaining recovery for safe operations.

### 7. Reload/reconnect serialization

A per-manager reload lock serializes full reloads. Reconnect single-flight remains
per server. Stop/publish operations use lifecycle-generation checks.

The implementation must not await network or process operations while holding
the state lock.

## Error and health semantics

- `connected`: published connection with successful recent tool discovery.
- `stale`: connection exists but exceeded the idle threshold.
- `reconnecting`: a reconnect task is active and no current connection exists.
- `degraded`: configured but unavailable, including discovery/configuration
  failure.
- `absent`: server is no longer present in valid configuration.

`last_error` must be stable and safe for logs/frontends. HTTP headers, environment
values, and tool secrets must never appear in health responses.

## Testing strategy

### Unit tests

Add tests for:

1. `list_tools()` failure closes and does not publish the candidate.
2. Configuration deletion invalidates connections.
3. Invalid configuration transitions to degraded/error state.
4. Reload notifies every registered bridge.
5. Reload removes stale tools and adds new tools in all listeners.
6. Read-only/idempotent tools retry once after reconnect.
7. Non-idempotent tools do not replay after an ambiguous exception.
8. MCP `isError` responses do not trigger reconnect.
9. Cleanup cancels and awaits reconnect tasks.
10. A reconnect finishing after cleanup cannot repopulate connections.
11. Manager cache removal calls cleanup exactly once.
12. Concurrent reload/reconnect calls do not publish stale generations.
13. Existing health response compatibility remains intact.

### Lifecycle integration tests

Use a bounded local fixture server, not only mocks, to cover:

- stdio startup and tool discovery;
- server stop/restart;
- config edit and reload;
- config deletion;
- tool addition/removal;
- bridge detach and reattach;
- two live loops receiving the same reload;
- cleanup during an in-flight reconnect.

The fixture must use test-owned processes, bounded timeouts, and deterministic
tool results.

## Rollout and observability

Add structured events for:

- `mcp.connection_published`;
- `mcp.connection_start_failed`;
- `mcp.config_reconciled`;
- `mcp.reload_started`;
- `mcp.reload_completed`;
- `mcp.reconnect_cancelled`;
- `mcp.manager_closed`;
- `mcp.retry_suppressed_non_idempotent`.

Do not log MCP headers, environment values, or tool secrets.

## Acceptance criteria

- No failed initial discovery leaves a published connection.
- No deleted or invalid config leaves old connections active.
- Reload reaches every registered live loop.
- Non-idempotent MCP calls are never automatically replayed after an ambiguous
  transport failure.
- Cleanup cannot be followed by a late reconnect repopulating state.
- Explicit manager destruction releases resources and cache ownership.
- Existing health and MCP meta-tool contracts remain backward compatible.
- MCP unit, integration, and focused API suites pass.
- Transport-security allowlisting remains explicitly deferred from #7.

## Resolved review decisions

1. Missing MCP idempotency metadata fails closed for automatic replay.
2. Invalid configuration is represented as `degraded` with a sanitized
   `last_error`; missing configuration remains `absent`.
3. The first integration gate uses a real local stdio fixture. Existing HTTP
   bridge/config tests remain required; a full external HTTP MCP fixture can
   follow when a deployment-specific endpoint is available.
4. The manager cache has explicit destruction rather than relying on process
   shutdown.
5. Configuration reconciliation is centralized and used by every MCP operation.
6. `v0.6.20` already provides typed `ToolResult.outcome`; the MCP bridge uses
   that contract rather than introducing a second outcome model.
