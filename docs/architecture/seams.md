# Seam inventory + decision record (R-PL1)

> **Status:** decision record per enterprise-roadmap phase-2-plan §6.
> **Decision date:** 2026-08-26 (reaffirmed 2026-09-01).

## Decision record

1. **The agent loop stays concrete.** No plugin-everything refactor, no
   middleware-everything abstraction of the loop's internals. Seams are
   in-repo design boundaries, not plugin points. Full plugin-everything is
   deferred until a partner demands it or a Python-native composability
   framework (e.g. Ouroboros) matures enough to be worth adopting.
2. **PyPI dropped (2026-08-26).** Distribution = Docker images (GHCR,
   build-on-tag) + npm TS-client preview; development = clone. The
   `assistant-sdk` name in `pyproject.toml` remains local-only.
3. Seams are **in-repo design boundaries**: single implementation, clean
   interface, one owner. Swapping an implementation is a code change in one
   file, not a plugin system.

## Seam inventory

| Seam | Interface (entry point) | Owner | Status |
|---|---|---|---|
| LLM provider | `src/sdk/providers/factory.py` — `create_provider`, `create_model_from_config`, `provider_key_requirement` | SDK core | exists |
| Tool registry | `src/sdk/tools.py` (`ToolDefinition`, `ToolRegistry`), `src/sdk/native_tools.py` | SDK core | exists; in-place mutators (LC-1) |
| Capabilities / scoping | `src/sdk/capabilities.py` (`resource_enabled`, `tool_enabled`) + `item_scopes` | SDK core | exists |
| Skills | `src/skills/registry.py` (discovery, drafts review queue, `skills_load`) | SDK skills | exists |
| MCP bridge | `src/sdk/tools_core/mcp_bridge.py`, `mcp_manager.py` (namespaced `mcp__{server}__{tool}`, reconnect LC-3) | SDK MCP | exists |
| Sandbox | `src/sdk/sandbox.py` (planned — SB1-1..SB1-4) | SDK security | planned (Phase 2) |
| Session log | `src/sdk/session_events.py` (P0-T9 schema; P1-T10 derivation pending) | SDK runtime | partial |
| Session persistence | `src/storage/messages.py` (MessageStore on CoreMem) | storage | exists |
| Metering | `src/storage/metering.py` (CaptureBus sink) + `src/storage/analytics.py` (sidecar) | Phase 2 | exists |
| Identity | `src/http/auth/` IdentityResolver protocol + `src/auth/resolver.py` | Phase 0/2 | exists |

## Explicitly OUT of scope

- Plugin-everything refactor of the loop (until partner demand or Ouroboros
  maturity).
- PyPI/pip packaging of the SDK (dropped 2026-08-26).
- Per-seam hot-swapping at runtime beyond what a config change already buys
  (e.g. SB1 backend swap is a config change by construction — see SB1-3).
