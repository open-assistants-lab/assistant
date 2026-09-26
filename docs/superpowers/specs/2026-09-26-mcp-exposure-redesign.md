# MCP Exposure Redesign

**Date:** 2026-09-26
**Status:** Draft for review
**Supersedes:** the `mcp.exposure` model introduced in v0.6.21
**Related:** #46 (scheduling), `2026-09-25-mcp-proxy-architecture-design.md`, `2026-09-25-mcp-lifecycle-reliability-design.md`

---

## Goal

Replace a three-value operator-chosen exposure enum with a model that:

1. costs ~nothing when no MCP server is configured,
2. measures context pressure instead of asking the operator to guess it,
3. keeps *which tools the model can see* strictly separate from *which tools the
   model is permitted to call*, and
4. borrows each idea from the reference implementation that actually owns it.

## Measured baseline (v0.6.21)

| Measure | Value |
|---|---|
| All native tools (73) | ~11,591 tokens |
| Without the 4 MCP meta tools (69) | ~10,866 tokens |
| **MCP meta surface** | **~725 tokens every turn** |
| `mcp_proxy` alone | ~255 tokens |
| `mcp_list` + `mcp_tools` + `mcp_reload` | ~470 tokens |

The ~725 tokens are paid on every turn regardless of whether any MCP server
exists. That is the number this design exists to remove.

## Reference implementations

Three coding-agent harnesses, used strictly for the axis each one owns.

### Claude Code — *when* to defer (Axis A)

Tool search is on by default. When active, tool definitions are withheld from
context; the agent receives a summary and searches for relevant tools, loading
up to **five** matches. `auto` counts the tokens of deferrable definitions and
activates at **10% of the model's context window**; below that, loading
everything upfront is genuinely cheaper, so it does. Core built-ins always load
and are not counted. Per-tool `alwaysLoad` exempts a definition from deferral.
Tools loaded by search stay loaded until the SDK compacts the messages that
introduced them, after which the agent re-searches.

**Mechanism caveat, and the reason we borrow only the policy:** Claude Code's
deferral rides on provider-side `tool_reference` blocks. The SDK silently
disables tool search when `ANTHROPIC_BASE_URL` is not first-party ("most proxies
don't forward `tool_reference` blocks") and on Vertex ("their serving stacks
reject the required beta header"). Most of our providers cannot do this. We take
the *policy*; we must build the *mechanism* on our own index and search tool.

### Pi — *which* tools may be direct (Axis B)

One proxy tool (~200 tokens) instead of hundreds of direct definitions, with a
graduated per-server ladder:

| `directTools` | Behaviour |
|---|---|
| `false` (default) | proxy only |
| `"search"` | registered as real tools **but inactive**; search activates matches additively |
| `true` | all tools always in context |
| `["a","b"]` | only these |

Plus `includeTools` / `excludeTools` globs (applied in that order) which filter
direct tools, proxy search/describe, and the status panel. A 75-tool advisory
warns that `directTools: true` costs context *and* accuracy on smaller models;
search-mode tools do not count toward it. `freezeDirectTools` keeps the
registered set stable so metadata refreshes don't rebuild the system prompt.

### OpenCode — cheap enable/disable and org governance

MCP tools load in full; the docs state plainly that servers "add to the context"
and that you should "be careful with which MCP servers you use." The
mitigations are cheap and worth copying:

- `enabled: false` per server.
- Glob tool-disable at the `tools` layer (`"my-mcp*": false`), with **per-agent
  re-enable over a global disable** — a tiering model we have no equivalent of.
- A 5-second default timeout for fetching tools.
- Organisation defaults delivered via `.well-known/opencode` that are
  **disabled by default, with local opt-in** — a governance pattern worth
  adopting for team deployments.

### Explicitly out of scope

Code execution (Anthropic, Cloudflare) is not a reference here. It fixes a
problem tool search does not — intermediate *results* flowing through context —
but requires a sandbox this platform does not have: #40 establishes that our
sandbox is process-level, not kernel-enforced. Revisit only with a hardened
sandbox, not on speculation.

---

## Root cause of the current design

`exposure: direct | hybrid | proxy` is **one axis carrying two concerns**:

- *when* to defer (context pressure) — only Claude Code solves this, and we
  solve it not at all;
- *which* tools may be direct — Pi and OpenCode both solve this richly, and we
  solve it with a single global list (`mcp.direct_tools`) and no globs.

So operators pick a value that answers neither question correctly, and the
three values do not compose. That, not the choice of proxy-over-code, is why
the current design is wrong.

## Principles

- **P1 — zero cost when unused.** No MCP surface is paid for unless an MCP
  server is configured.
- **P2 — context policy never widens permission.** Exposure and filtering are
  inputs to context assembly only. They must never influence allow/ask/deny.
- **P3 — measured, with an explicit override.** Default to measurement; keep the
  knob for operators who want determinism.
- **P4 — no oscillation.** The exposed surface must be stable within a context
  window, or the model thrashes and every turn is a different tool set.

---

## Design

### Layer 1 — One surface

`mcp_proxy` becomes the only always-present MCP tool. `mcp_list`, `mcp_tools`,
and `mcp_reload` are removed as tools; their behaviour becomes `mcp_proxy`
actions:

| Removed tool | Action |
|---|---|
| `mcp_list` | `status` (already exists) |
| `mcp_tools` | `describe` (already exists; add a list form) |
| `mcp_reload` | `refresh` (already exists) |

Saves ~470 tokens per turn permanently, permanently. `mcp_reload`'s loop
re-registration path moves to a non-tool entry point
(`await mcp_bridge.reload_for_loop(loop, user_id)`) called by the HTTP route and
by `mcp_proxy(action="refresh")`.

### Layer 2 — Axis B: which tools may be direct

Per-server, borrowed from Pi and OpenCode. This replaces the global
`direct_tools` list.

```yaml
mcp:
  exposure: never | search | always        # Pi's ladder
  include_tools: ["get_*"]                  # Pi globs, applied first
  exclude_tools: ["read_figjam"]            # Pi globs, applied second
  enabled: true                             # OpenCode server switch
  always_load: ["health"]                   # Claude Code per-tool exemption
```

Layered at the `tools` map, per OpenCode, for a coarse deployment-level gate:

```yaml
tools:
  "mcp__github*": false                     # disable a family
```

Resolution order (later wins), mirroring OpenCode's model:

1. server `enabled` (default true)
2. per-agent `tools` entry (glob) — a *narrowing* override
3. `include_tools` / `exclude_tools` globs
4. `exposure` decides whether the survivors are direct, search-activated, or
   proxy-only

`exposure` values:

| Value | Behaviour |
|---|---|
| `never` | proxy only; no MCP tool definitions in context |
| `search` | registered but **inactive**; `tool_search` activates additively |
| `always` | all surviving tools directly callable |

### Layer 3 — Axis A: when to defer

Add two values to the same ladder, so it stays one axis:

| Value | Behaviour |
|---|---|
| `auto` | equivalent to `auto:10` |
| `auto:N` | measured threshold, N percent of the context window |

`auto:N` is **not** a new enum axis — it selects the value of Axis B
dynamically. The algorithm, at loop build and on compaction only:

```
deferrable = surviving MCP tools whose names are not in always_load
cost       = estimate_prepared_tokens(messages=[], tools=deferrable)
window     = resolve_context_window(model)
if cost / window * 100 >= N:  treat as "search"
else:                          treat as "always"
```

**Why loop-build and compaction only** (P4): measuring per LLM call makes the
surface oscillate as the conversation grows — tools appear, appear in every
subsequent turn, then vanish at an arbitrary boundary. Re-evaluating at
`context_compressed` (the event we already emit, `src/sdk/session_events.py:365`)
matches Claude Code's "stay loaded until compaction, then re-search" rule and
bounds churn to once per compression.

**Why our mechanism, not theirs:** activation reuses `tool_search`, which already
loads a discovered `ToolDefinition` into the live loop (#45) and is already
backed by `tool_index.desired_index_rows()`, which already indexes `mcp_tools`
into ChromaDB. The new work is a policy decision, not new infrastructure.

**Per-model thresholds** (open question 2): `resolve_context_window` is
already per-model, so `auto:10` on an 8k local model and a 200k hosted model
already mean different things. That is *correct* — a percentage is the right unit
— but it means the same deployment exposes a different tool set per model. We
accept that, and surface the effective decision in `/mcp/health`.

### Layer 4 — the governance firewall

None of the three references need this; we do.

**Invariants, enforced in code and covered by tests:**

- **I1.** `exposure`, `include_tools`, `exclude_tools`, `enabled`, and the
  `tools` glob map may only **narrow** the set of tools the model can see.
  None of them is ever consulted when resolving a permission.
- **I2.** A tool activated by `tool_search` passes the **same**
  `resolve_permission_for_call(user_id, f"mcp__{server}__{tool}", args)` check
  on invocation that a `mcp_proxy` call passes today. A discovered tool is not
  an authorised tool.
- **I3.** A server excluded by `enabled: false` or `exclude_tools` never
  connects, never authenticates, and never appears as callable — but remains
  visible in `/mcp/health` with the reason. Silent disappearance is a
  diagnosis dead end.
- **I4.** `auto` changing the reachable surface is an auditable event: the
  transition (threshold value, model, resulting tool count) is written to the
  audit stream and reported in `/mcp/health` as `exposure_decision`.
- **I5.** Capability scope (`resource_enabled` / `tool_enabled`) is applied
  *before* exposure resolution, so a `scope=none` tool is not deferred — it is
  absent. Deferral is not a way to smuggle a disabled tool into context.

I5 is the failure mode of #44 in a new costume: a filter that removes a tool
from the *active* set can accidentally readmit it to *context*. Tests pin the
order.

### Layer 5 — independent fixes

Not part of the exposure model; do them regardless.

1. **Wire the dead `disabled` field.** `MCPServerConfig.disabled` shipped in
   v0.6.21, is `exclude=True` so it cannot be read from `.mcp.json`, and is
   never consulted. Either honour it properly as `enabled` (inverted) or delete
   it. Shipping a field that does nothing is worse than not having it.
2. **5s tool-fetch timeout** (OpenCode). We have `refresh_timeout_seconds`
   which is currently unused for this; wire it to `list_tools()`.
3. **Org defaults disabled by default** (OpenCode's `.well-known` model) for
   team deployments.
4. **Honour `_meta.alwaysLoad`** in the bridge, so a server can pin its critical
   tools out of deferral (Claude Code's exemption, at our end since we own the
   bridge).

---

## Interface changes

```python
# src/config/settings.py
class MCPConfig(_BaseSettings):
    enabled: bool = True
    idle_timeout_minutes: int = 30
    exposure: Literal["auto", "auto:N", "never", "search", "always"] = "auto"
    include_tools: list[str] = []
    exclude_tools: list[str] = []
    always_load: list[str] = []
    # direct_tools: removed — superseded by include/exclude + exposure
    cache_ttl_seconds: int = 86_400
    refresh_timeout_seconds: float = 5.0
    max_result_chars: int = 20_000
```

```python
# src/sdk/tools_core/mcp_config.py
class MCPServerConfig(BaseModel):
    ...
    enabled: bool = True            # replaces the dead `disabled` field
    include_tools: list[str] = []
    exclude_tools: list[str] = []
```

```python
# src/sdk/mcp_exposure.py  (new)
def resolve_exposure(
    *, setting: str, surviving: Sequence[ToolDefinition],
    model: str, always_load: frozenset[str],
) -> ExposureDecision:
    """Decide direct | search | never for one loop build, from measurement."""

def apply_governance_floor(
    decision: ExposureDecision, caps: dict, user_id: str,
) -> ExposureDecision:
    """I5: drop capability-disabled tools from the *active* set, never re-admit."""
```

## Migration

`exposure` is already shipped in v0.6.21, so this is a breaking rename and must
be explicit — this is exactly where #44 went wrong.

| v0.6.21 | v0.6.22 | Behaviour |
|---|---|---|
| `direct` | `always` | identical |
| `hybrid` | `search` | identical (registered, inactive) |
| `proxy` | `never` | identical |

- The three legacy values are **accepted for one release** with a deprecation
  warning in `/mcp/health`; they map as above.
- `mcp.direct_tools` maps to `include_tools` if it is a non-empty list.
- The new default is `auto`, which is a **behaviour change** for deployments
  that left `exposure` at `direct`. That is intentional and is the point of the
  redesign, but it must be called out in release notes and shown in
  `/mcp/health` as an `exposure_decision` entry.
- **No silent translation of an unknown value.** An unrecognised `exposure`
  fails closed to `never` with a config error, rather than defaulting to
  `always` and re-creating a governance hole.

## Testing strategy

- **Layer 1:** `get_native_tools()` contains exactly one `mcp_*` tool; the
  always-on token cost is asserted against a budget (extend
  `tests/sdk/test_tool_schema_budget.py`).
- **Layer 2:** table-driven tests for include/exclude precedence, glob
  matching, the `tools`-map gate, and per-agent override.
- **Layer 3:** `auto:N` at, below, and exactly at the threshold; per-model
  window differences; re-evaluation only on `context_compressed` (assert the
  decision is stable across many LLM calls within one window).
- **Layer 4:** one test per invariant I1–I5, including the negative case for I5
  (a `scope=none` MCP tool must not reappear in context under any exposure
  value) and for I2 (a `deny` on a search-activated tool blocks invocation).
- **Regression:** the `exposure` deprecation mapping table, and a fail-closed
  test for an unknown value.

## Rollout

1. Layer 5 fixes (independent, no behaviour risk).
2. Layer 1 (pure removal, immediate token win, no config change).
3. Layer 2 (new config, `auto` not yet the default).
4. Layer 4 firewall (must land **before** Layer 3 ships `auto`).
5. Layer 3, default flipped to `auto`, gated behind an explicit opt-in flag
   until measured on at least one deployment.

Layer 4 strictly precedes Layer 3: shipping a policy that changes the model's
reachable surface before the invariants that constrain it exist would be the
mistake this design exists to prevent.

## Acceptance criteria

- One always-present MCP tool, and a token budget test that fails if it grows.
- Context cost of MCP is ~zero when no server is configured.
- `auto:N` makes the exposure decision from measurement, is stable within a
  context window, and re-evaluates only on compaction.
- Every one of I1–I5 has a test, and a `scope=none` tool can never re-enter
  context.
- `exposure: direct|hybrid|proxy` warns, maps, and is removed after one release.
- An unknown `exposure` fails closed.

## Open questions for review

1. **Should `auto` be the default, or opt-in?** It makes the model's reachable
   surface change during a session. The firewall makes it *auditable and
   permission-checked*, but an operator who wants a fixed surface must be able
   to pin one. Recommendation: ship `auto` behind an opt-in flag, measure on a
   real deployment, then flip the default — the same staged approach the plan
   file for #46 uses.
2. **Is a per-model surface acceptable?** The same deployment will expose a
   different tool set on an 8k local model than on a 200k hosted one. A
   percentage is the correct unit, but it means "what can this model reach" is
   not a single answer. Recommendation: accept, and make the effective decision
   visible in `/mcp/health`.

## Non-goals

- Code execution / `mcpScript` (needs a hardened sandbox; see #40).
- Output spill-to-disk (separate change; today the guard truncates).
- Server-side MCP host validation (we are a client; the SDK's
  `TransportSecuritySettings.allowed_hosts` covers operators who run a server).
