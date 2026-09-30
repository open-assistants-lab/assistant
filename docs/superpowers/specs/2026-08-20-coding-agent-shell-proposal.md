# Coding-Agent Shell Access — Proposal

Date: 2026-08-20
Status: Proposal (no implementation)

## Context

The browser-tools migration plan (`2026-08-20-browser-tools-skills-migration.md`) surfaced a
blocker: the plan assumes the agent can run `agent-browser` via `shell_execute`, but the shell
tool is a **sandbox, not a coding-agent shell**:

| Constraint | Today |
|-----------|-------|
| Allowlist | `python3, node, echo, date, whoami, pwd` (config.yaml `shell_tool.allowed_commands`) |
| Metacharacters | `; & \| $ \` \n` rejected — no pipes, chaining, redirection |
| Paths | Command base names with `/` rejected |
| cwd | Workspace files dir (not the project) |
| Annotations | `destructive=True` → every call interrupts (HITL) |
| Timeout / output | 30s / 100KB |

Meanwhile the product is becoming a coding agent: native app, `files_*` tools, subagents,
skills, MCP bridge. Every serious coding agent (Claude Code, Cursor, Codex, Gemini CLI) gives
the model a general shell with **pattern-based approval** — and the models are trained on that
pattern. The question: should we move toward that model?

## The question

> Should the agent get coding-agent-style CLI access, and if so, how far?

## Options

### Option A — Status quo (sandbox only)

Keep the allowlist. The browser plan changes: the 16 long-tail tools stay native (or only the
rarely-used ones move to the skill, where they'd be blocked anyway).

- **Security**: unchanged. Zero risk.
- **UX**: unchanged. Read-only browser tools keep auto-approval.
- **Tokens**: no win — ~950 tokens stay in every session.
- **Effort**: none.
- **Cost**: the browser plan's token win dies; the agent stays unable to run `git`, `rg`,
  `curl`, or any CLI the product doesn't pre-wrap. Every new capability = a new wrapper tool.

### Option B — Allowlist `agent-browser` (minimal, unblocks browser plan)

One line in `config.yaml`. Sandbox stays; only browser commands become reachable.

- **Security**: low risk — one known binary, no metacharacters, no paths.
- **UX**: every browser command via shell interrupts (shell is `destructive=True`).
- **Tokens**: ~950 → ~260 (6 core tools stay native).
- **Effort**: ~1 line + skill.
- **Cost**: interrupts per browser command; no general CLI capability.

### Option C — Curated coding shell (recommended)

Expand the allowlist to a curated coding set (`git`, `ls`, `cat`, `rg`, `find`, `curl`,
`agent-browser`, `python3`, `node`, ...) **and** make approval pattern-aware via a
`ToolGuardrail` (the SDK already has guardrails — no loop changes):

- Read-only patterns (`ls`, `cat`, `git status`, `agent-browser get *`) → **auto-approved**
- Write/destructive patterns (`rm`, `mv`, `git push`, `curl | sh`, `sudo`) → **interrupt (HITL)**
- Session-level "always allow" per pattern (Claude Code permission model, simplified)

- **Security**: medium — the guardrail is the control point; metacharacter ban can stay or be
  relaxed per pattern. In container deployments, network egress is the real boundary (the
  agent already has full file access via `files_*`).
- **UX**: read-only commands flow; destructive ones ask. Matches what models expect.
- **Tokens**: browser family → ~260; future families can follow the same skill pattern.
- **Effort**: medium — guardrail (~100 lines) + config schema + tests.
- **Cost**: a real security review for multi-tenant mode; guardrail bypass risk (models can
  obfuscate commands — mitigated by keeping the metacharacter ban for now).

### Option D — Full Claude Code model

Arbitrary bash, per-user permission rules (allow/deny/ask patterns), trust levels, `yolo`
mode. This is the end-state if the product wants to be a general coding agent.

- **Security**: high risk in multi-tenant/container mode without per-user auth (the app has
  none today — `user_id` is client-supplied). Needs OIDC/per-user tokens first.
- **Effort**: high — permission engine, settings UI, security review.
- **Recommendation**: defer until per-user auth exists. Design the guardrail (Option C) so it
  can grow into this.

## Recommendation

**Phase 1 (now): Option B** — allowlist `agent-browser`, land the browser-tools skill, keep
the 6 core browser tools native. Unblocks the plan with one line.

**Phase 2 (next): Option C** — curated allowlist + `ToolGuardrail`-based pattern approval.
This is the "like all other coding agents" answer, scoped safely:

```
shell_execute (destructive annotation stays)
    → ToolGuardrail inspects command
        → read-only pattern  → auto-approve (no interrupt)
        → destructive pattern → interrupt (HITL approve/reject)
        → unknown            → interrupt (fail closed)
```

**Phase 3 (future): Option D** — only after per-user auth. The guardrail's pattern rules
become per-user permission rules.

## Related: tool-family consolidation (distinct from the shell question)

Measured 2026-08-20: all 75 native tool schemas ≈ **5,376 tokens** in every session. Browser is only ~17% of that. The shell decision (this proposal) and tool-family shrinkage are related but distinct:

- **CLI-wrapper families → shell + skill stub** (the browser pattern): audit of all 32 `tools_core/` modules found `AgentBrowserCLI` is the *only* `CLIToolAdapter` subclass. `web.py` is pure httpx (no CLI to point at). **No other family qualifies.**
- **Internal families → consolidation** (fewer, fatter tools): candidates ranked by tokens:

| Family | Tools | Tokens | Move | Saves |
|--------|-------|--------|------|-------|
| `subagent_*` | 10 | 1,374 | consolidate to ~3 (`subagent_manage`/`delegate`/`tasks`) | ~900 |
| `app_*` | 12 | 834 | consolidate to ~3 (`app_manage`/`query`/`schema`) | ~600 |
| `files_versions_*` | 4 | ~300 | 1 tool with a verb | ~200 |

  Not candidates: `files_*` (core, structured args matter), `message_*`/`memory_*`/`mcp_*`/`web_*` (small).

This is a separate workstream from the shell decision; the shell proposal only gates the browser migration.

## Open questions

1. Should the metacharacter ban relax for allowlisted commands (e.g., allow `git log --oneline | head`)? Proposal: keep the ban in Phase 2; revisit in Phase 3.
2. Where do pattern rules live — `config.yaml` (global), per-user capabilities, or both?
3. Does the native app need a "trust this command pattern for this session" affordance, or is approve/reject per call enough?
4. Should `files_*` tools also get pattern-aware treatment, or is shell the only surface?

## Non-goals

- No arbitrary bash in multi-tenant mode until per-user auth exists.
- No replacement of the `files_*` tool family with shell.
- No changes to the agent loop itself — guardrails are the extension point.
