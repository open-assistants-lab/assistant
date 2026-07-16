# Middleware vs Hooks — Architecture Decision

Date: 2026-06-09

## Context

EA needs in-process lifecycle hooks for summarization, progress tracking, instruction
injection, and doom loop detection. Claude Code uses external hooks (shell/HTTP/MCP) for
similar purposes. We evaluated whether to replace Assistant's middleware with a hooks system.

## Decision

**Keep middleware as the primary pipeline. Do not adopt hooks as a replacement.**

## Rationale

### 1. Latency

Middleware fires on every LLM call + every tool call — 10–50 events per conversation turn.
An in-process Python method call takes ~1µs. An external hook (subprocess or HTTP) takes
10–100ms. Replacing middleware with hooks would add 2–5 seconds of overhead per turn.

### 2. State

Middleware tracks live state across turns (e.g., progress middleware's doom loop counter,
summarization middleware's running token count). In-process middleware stores this in a
Python dict — updated in microseconds. Hooks would need to serialize state to a file or DB
between every event, adding complexity and latency.

### 3. Data model mismatch

| Dimension | EA Middleware | Claude Code Hooks |
|-----------|--------------|-------------------|
| Transport | Python method call | Shell stdio / HTTP / MCP |
| Data format | Python objects (AgentState, Message) | JSON via env vars + stdin/stdout |
| Lifecycle events | 6 (before/after agent/model, tool wrap) | 30+ (SessionStart, PreToolUse, etc.) |
| Configuration | runner.py (Python code) | settings.local.json (JSON file) |
| Data model | Messages in SQLite | Conversations as files in working directory |

EA is a chat-assistant with SQLite message storage. Claude Code is a CLI TUI over a filesystem.
A plugin written for one cannot meaningfully run in the other.

### 4. Plugin compatibility

Claude Code plugins expect a filesystem-CLI world (working directory, git, file edits,
shell commands). EA has none of these. **For cross-product plugins, use MCP** — both
platforms support it natively.

## Future: User-Customizable Middleware

If the multi-tenant admin use case materializes, add a configurable hook layer **on top of**
middleware, not as a replacement:

```
Middleware pipeline (Python, always on)
    → User hook layer (from ~/.ea/hooks.yaml)
        → Each hook = optional shell/HTTP call at a lifecycle point
        → Input: JSON, Output: JSON decision
```

This gives:
- Core logic stays in fast, stateful middleware (summarization, progress, instructions)
- Extensibility for admins (audit logging, notifications, custom approval flows)
- No complexity for non-tech users (nothing to configure)

Estimated effort: 2–3 days when the admin use case arrives.

## Checkpointing — Deferred

### Decision

**Do not adopt checkpointing (Claude Code `/rewind` or LangGraph `thread.get_state`).**

### Rationale

| Product | What's checkpointed | Why it's needed |
|---------|-------------------|----------------|
| Claude Code | File state before each edit | Rollback broken code changes |
| LangGraph | Graph execution state per step | Resume paused graph mid-execution |
| EA | Nothing today | Neither use case applies |

EA is a **chat assistant** — it reads email, manages tasks, searches the web. Unlike
Claude Code, there are no file edits to roll back. Unlike LangGraph, there is no graph
execution to pause and resume.

The two things checkpointing would enable:

1. **"Undo that last turn"** — rewinding the conversation to before a bad response.
   The summarization middleware already compresses context. If a user wants to ignore
   the last exchange, they can just type a new message. The old messages remain in
   SQLite history but don't affect ongoing context.

2. **Rolling back tool effects** — unsending an email or restoring a deleted contact.
   This is fundamentally impossible without service-side support (Google doesn't have
   "undo email via API for free").

### When to revisit

If users explicitly ask for "undo conversation turn" or if EA starts making automated
filesystem changes (like Claude Code's file edits), checkpointing becomes relevant.
Estimated effort at that point: 3–5 days.

## Related