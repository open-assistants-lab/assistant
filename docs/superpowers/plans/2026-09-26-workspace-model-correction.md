# Workspace Model Correction (#47)

**Date:** 2026-09-26
**Status:** Approved for implementation
**Issue:** #47
**Decision owner:** product (user-global storage, now; team-scoped later)

## Decision

**Files, custom tools, skills, subagent definitions and memory are user-global.**
A workspace is *not* a storage boundary. It is a named scope that carries
`name`, `description`, `prompt`, `model_override` and subagent *execution*
context, and nothing else.

This is not a new decision — it is the decision the code already made, stated
correctly. `workspace_models.py:1-4` is the single source of the wrong promise.

## Why the code is the authority here

| Concern | Reality | Evidence |
|---|---|---|
| Skills | user-global | `skills.py:103` — `_skill_dir(user_id)` calls `get_paths(user_id)`, no workspace |
| Subagents | user-global | `coordinator.py:299` — `base_path = user_subagents_dir()` |
| Memory | user-global | `memory_profile` reads `get_message_store(user_id, workspace_id).core`; the store cache keys on `_store_key(user_id)` and `MessageStore.__init__` discards `workspace_id` |
| Conversation | user-global | same store as memory; the `workspace_id` generated column is never populated |
| Files / memory dirs | user-global | `workspace_files_dir()` → `files_dir()`; `workspace_memory_dir()` → `user_memory_dir()` |

Memory and conversation are **one substrate**: `memory_profile` is semantic
recall *over the message history*, not a separate store. So "memory is
user-global" forces "conversation is user-global", and the profile digest
deliberately spans all of a user's projects. That is a product decision, now
made explicitly.

## Why not per-workspace storage

The team boundary is the axis that matters, and **it already exists**:
`DataPaths.__init__` takes `team_id` and `data/teams/{team_id}/` is the team
layout. Going user-global → team-scoped is then a scope parameter on a seam
that already exists. Per-workspace storage would be an axis that has to be
unpicked when teams arrive, and it is the wrong sharing boundary anyway.

## What the six `workspace_*` accessors actually are

Not per-workspace paths. They are **user-global accessors that historically
grew a `workspace_` prefix**, kept for compatibility:

```python
workspace_files_dir()         -> files_dir()          # user-scoped
workspace_memory_dir()        -> user_memory_dir()
workspace_skills_dir()        -> user_skills_dir()
workspace_subagents_dir()     -> user_subagents_dir()
workspace_conversation_path() -> conversation_dir() / "app.db"
workspace_cache()             -> user_dir / ".file_cache.json"
```

There is even a fixture comment inverting the apparent direction:
*"Alias old user-level paths to workspace-scoped for backwards test compat"*.

## Plan

1. **`src/sdk/workspace_models.py`** — rewrite the module docstring to describe
   the actual model: what a workspace carries, and explicitly that storage is
   user-global. State the team direction.
2. **`src/storage/paths.py`** — add an honest docstring to each of the six
   accessors saying the path is user-global and `workspace_id` is not part of
   it. Do **not** rename them (§ Rejected).
3. **`CHANGELOG.md`** — add a correction note in the current (unreleased)
   section. Do **not** edit the published v0.6.18 entry (§ Rejected).
4. **`tests/sdk/test_workspace_model.py`** — pin the documented model: all six
   accessors return identical paths for two different `workspace_id`s of the
   same user. A future change to per-workspace storage must update this test and
   the docstrings deliberately, in the same commit.
5. **Comment on #47** with the decision, the evidence, and the team direction.

## Rejected, and why

- **Renaming the `workspace_*` accessors.** ~90 call sites
  (`workspace_files_dir` alone: 22 in `src/`, 19 in tests), and it is the
  *primary* name in `filesystem.py`, not an alias. Cosmetic churn with real
  regression risk for a problem a docstring solves.
- **Rewriting the v0.6.18 changelog entry.** That release is published and
  Docker-tagged. A correction belongs in the current section so the historical
  record stays accurate about what was claimed at the time.
- **Removing the `workspace_id` generated column on `messages`.** Harmless,
  populating it is a one-line future change, and dropping it would be a schema
  migration for no user-visible gain. Document it as reserved instead.

## Known false claim this corrects

**v0.6.18 states:** *"Child file access is confined to that workspace."*

That is **not true**. `filesystem.py:32` threads `workspace_id` correctly to
`get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()`, and that
accessor then discards it. A child's file access is confined to the *user's*
files directory.

Consequently `requested_workspace_id` in the subagent frozen manifest does
**not** confine file access. It scopes skills, prompt and tool-selection
context. Task 2 work described it as an authority boundary; that was imprecise
and is corrected here, because an operator could otherwise build on a stronger
guarantee than exists.

## Acceptance criteria

- `workspace_models.py` makes no claim that storage is per-workspace.
- Each of the six accessors documents that its path is user-global.
- A test pins user-global storage for two workspaces of one user, and names the
  docstrings as the thing to update if that ever changes.
- The changelog carries a correction note; v0.6.18 is untouched.
- #47 is commented with the decision and the team direction.
