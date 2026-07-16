# Path Restructuring Spec

## Design Context

This spec was developed during an autoresearch implementation session.
The conversation that shaped it:

### Triggers

1. **Autoresearch needed git** — the `ResearchLoop` used in-memory backups but the workspace is git-versioned. Git branch isolation (branch → eval → merge/discard) is crash-safe and supports multi-file experiments, but targets (prompts, skills, subagent configs) were spread across `~/Assistant/` and `data/` — no single repo.
2. **Path split** — user data lived under `data/users/{id}/` (configurable) while workspaces, global memory, and solo skills lived under `~/Assistant/` (hardcoded). Two roots, no git on either.
3. **Multi-tenant mode** — Docker containers per user with team shared volumes. The existing `data/users/{id}/` structure doesn't map naturally to per-container `~/`.

### Key Decisions Made

| Decision | Why |
|---|---|
| **Single root at `~/Assistant/`** | Consolidates all user data under one tree, one `.git` |
| **Configurable via `ea_root`** | Multi-tenant containers can set a different root per tenant |
| **UUID workspaces** | Eliminates name collision between user and team workspaces; rename doesn't change directory path |
| **Lowercase → uppercase subdirs** | Skills, Subagents, Files, Memory for consistency across scopes |
| **Team volume at `{ea_team_root}/{id}/` or `~/EA/Teams/{id}/`** | Configurable for server-side, client-side fallback |
| **AGENTS.md replaces `settings.agent.system_prompt`** | Same seed pattern as skills; editable by user; removes hardcoded string from config |
| **Prompt order is resequencable** | `PROMPT_ORDER` setting controls injection sequence of user/workspace/team sections |
| **User → workspace → team priority** | User prompt → user skills → ws prompt → ws skills → team prompt → team skills → team ws prompt → team ws skills |
| **Seeding from `src/prompt_seed/AGENTS.md` and `src/skills_seed/`** | Identical mechanism — first-run copy, marker file prevents re-seed |

### What Stays in `data/`

Project-level data only: model cache, logs, traces, templates, job scheduler DBs.

Everything user-level moves to `~/Assistant/`.

---

## Goals

1. Consolidate all user data under `~/Assistant/` — single root, single `.git`
2. Cleanly separate user, team, and workspace scopes
3. Support per-container `.git` for autoresearch versioning
4. Replace `data/users/{id}/`, `data/teams/{id}/`, and split `~/...`/`data/...` with a unified tree
5. Keep all existing tool names and APIs identical — pure infrastructure change

---

## Stage Scope

This design spans two implementation stages. The spec documents the full
target architecture; Stage 1 builds the solo subset only.

### Stage 1 — Solo Mode

*What gets built now.* Everything a single-user desktop app needs.

- Single root `~/Assistant/` with one `.git`
- `DataPaths` with `root` property + `ea_root` config
- User-scoped and workspace-scoped methods only (no team methods)
- Workspace subdirs renamed lowercase → uppercase
- Migration script: `data/users/{id}/` → `~/Assistant/`
- Git init + `.gitignore` at root
- Prompt seeding from `src/prompt_seed/AGENTS.md`
- AGENTS.md replaces `settings.agent.system_prompt`
- Consolidate 3 hardcoded `Workspaces/` path references into `DataPaths`

**Skipped in Stage 1** (spec-only, implemented when multi-tenant is built):
- Team root, team-scoped methods, team path resolution
- `PROMPT_ORDER` / resequencable injection
- Team scope in context injection
- UUID workspaces (slug IDs stay)
- `ea_team_root` setting

### Stage 2 — Multi-Tenant

*What the spec describes but implements later.* Docker containers per user
with shared team volumes.

- `ea_team_root` setting + `DataPaths.team_root` property
- Team-scoped DataPaths methods
- Full scope resolution (user → workspace → team)
- `PROMPT_ORDER` for resequencable injection
- UUID workspaces (ships with Stage 2, safe to defer since no team/user
  workspace name collisions exist yet)

---

## 1. Directory Layout

### Solo mode (desktop / single-user container)

```
~/Assistant/
├── AGENTS.md              # User prompt (was data/users/{id}/config/prompt.txt)
├── Skills/                # User-level skills (was data/users/{id}/skills/)
├── Subagents/             # User-level subagents (was data/users/{id}/subagents/)
├── Teams/                 # (empty in solo mode)
├── Workspaces/
│   └── {workspace_id}/
│       ├── Skills/        # ⬆ upper (was lowercase)
│       ├── Subagents/     # ⬆ upper (was lowercase)
│       ├── Files/         # ⬆ upper (was lowercase)
│       ├── Memory/        # ⬆ upper (was lowercase)
│       ├── conversation.app.db
│       └── .mcp.json
├── Memory/
│   └── global/            # Cross-workspace memory
├── Email/
│   ├── emails.db
│   └── gmail_cache/
├── Contacts/
│   └── contacts.db
├── Todos/
│   └── todos.db
├── Conversation/
│   ├── messages.db
│   └── vectors/
├── Research/              # Experiment results (was data/private/research/)
├── Companion/
│   ├── notifications.db
│   └── memory.db
├── config.yaml            # User EA config
└── .mcp.json
```

### Team mode (Docker multi-user)

```
User container (~/Assistant/):
├── AGENTS.md              # User's personal prompt
├── Skills/                # User's personal skills
├── Subagents/             # User's personal subagents
├── Workspaces/            # User's personal workspaces
├── Memory/global/
├── Email/
├── Contacts/
├── Todos/
└── ...                    # Same structure as solo

Shared team volume (configurable: /mnt/ea-teams/{team_id}/ or ~/Assistant/Teams/{team_id}/):
├── AGENTS.md              # Team prompt (overrides user in team context)
├── Skills/                # Team shared skills
├── Subagents/             # Team shared subagents
├── Files/                 # Team file storage
├── Memory/
├── Workspaces/
│   └── {workspace_id}/
│       ├── Skills/        # ⬆ upper
│       ├── Subagents/     # ⬆ upper
│       ├── Files/         # ⬆ upper
│       ├── Memory/        # ⬆ upper
│       ├── conversation.app.db
│       └── .mcp.json
├── config.yaml
└── .mcp.json
```

- **Client-side (solo)**: team root = `~/Assistant/Teams/{team_id}/`
- **Server-side (multi-tenant)**: team root = `settings.ea_team_root / {team_id}` (e.g., `/mnt/ea-teams/{team_id}/`)

### Multi-tenancy

Same as solo mode — each container gets its own `~/Assistant/`. Teams use a shared volume.

---

## 2. Scope Definitions

**User scope**: data private to one user, at `~/Assistant/`.

**Team scope**: data shared across team members, at `{team_root}/{team_id}/`.

**Workspace scope**: data scoped to one workspace, at either user or team root depending on whether the workspace belongs to a team.

Resolution priority for all scoped lookups: **user → workspace → team** (workspace overrides user by name, team overrides workspace by name).

### Decision: UUID vs Slug Workspace IDs

**Slug ID** (current): `from_name("My Project")` → `id="my-project"`,
directory at `Workspaces/my-project/`.

**UUID ID** (proposed): `from_name("My Project")` → `id="a1b2c3d4-..."`,
directory at `Workspaces/a1b2c3d4-.../`.

| Concern | Slug | UUID |
|---------|------|------|
| Rename stability | Rename moves directory path | Path never changes |
| Collision across scopes | Team could have `my-project` same as user (same dir) | UUIDs globally unique |
| Human readability | `my-project` is obvious | Opaque hex string |
| Debuggability | Logs show `ws=my-project`, clear | Logs show `ws=a1b2c3d4-...`, need lookup |
| LLM UX | LLM uses `id` for tools, `name` for display | Same pattern needed either way |
| Implementation cost | Already done | New ID generation, migration of existing IDs |

**Decision:** Defer UUIDs to Stage 2 (multi-tenant). Reasons:

1. **Stage 1 has no teams** — the collision problem doesn't exist yet.
2. **Existing workspaces have slug IDs** — migrating them to UUIDs adds
   rename indirection with no current benefit. They can keep their slug IDs;
   UUID generation kicks in for new workspaces created after Stage 2 ships.
3. **Slug IDs are already stable on rename** for the solo case — the slug
   `my-project` doesn't change when the user renames to "My Big Project"
   (slug is derived from the original name at creation). Only the display
   name changes.
4. **When Stage 2 ships**, UUIDs prevent team/user collision. New workspaces
   get UUIDs; existing slug-ID workspaces keep theirs (no collision if team
   workspaces all use UUIDs).

```python
@dataclass
class Workspace:
    id: str            # UUID in Stage 2, slug in Stage 1
    name: str
    ...

    @classmethod
    def from_name(cls, name: str) -> Workspace:
        # Slug in Stage 1; UUID in Stage 2
        ws_id = _generate_workspace_id(name)
        return cls(id=ws_id, name=name.strip(), ...)
```

**LLM never shows UUIDs to users** regardless — `workspace_list` returns
`{name, id}`, LLM uses `id` internally.

### Context Injection Order (Configurable)

Current assembly: `base + user_prompt + skills + workspace`

New assembly (default order):
1. **User Prompt** — `## User Instructions\n{~/EA/AGENTS.md}` (seeded from `src/prompt_seed/AGENTS.md` on first run)
2. **User Skills** — skill descriptions from `~/EA/Skills/`
3. **Workspace Prompt** — `## Workspace Instructions\n{ws.prompt}`
 4. **Workspace Skills** — skill descriptions from `~/EA/Workspaces/{workspace_id}/Skills/`
5. **Team Prompt** — `## Team Instructions\n{team_root/AGENTS.md}`
6. **Team Skills** — skill descriptions from `{team_root}/Skills/`
7. **Team Workspace Prompt** — `## Team Workspace Instructions\n{team_ws.prompt}`
8. **Team Workspace Skills** — skill descriptions from `{team_root}/Workspaces/{uuid}/Skills/`

**Resequencable** via a `settings.prompt_order` list:

```python
PROMPT_ORDER: list[str] = [
    "user_prompt", "user_skills",
    "workspace_prompt", "workspace_skills",
    "team_prompt", "team_skills",
    "team_workspace_prompt", "team_workspace_skills",
]
```

Changing the list reorders injection. Any omitted section is skipped.

### Prompt Seeding

`settings.agent.system_prompt` is removed. Instead:

```
src/prompt_seed/AGENTS.md  ──first run──▶  ~/Assistant/AGENTS.md
```

Same pattern as `src/skills_seed/` — a `.prompt_seeded` marker prevents overwrite. The seed file is the new canonical default, editable by users.

---

## 3. Path Resolution (DataPaths)

### Constructor

```python
class DataPaths:
    def __init__(
        self,
        deployment=None,
        user_id=None,
        team_id=None,
        workspace_id=None,
        data_path=None,        # kept for backward compat (cache, logs)
        ea_root=None,          # ~/Assistant/ by default
        ea_team_root=None,     # team volume root, None for solo
    ):
```

### Root

```python
@property
def root(self) -> Path:
    """Primary user data root."""
    return Path(
        self._ea_root
        or settings.ea_root
        or Path.home() / "Assistant"
    )

@property
def team_root(self) -> Path | None:
    """Team volume root. None in solo mode."""
    if not self.team_id:
        return None
    root = self._ea_team_root or settings.ea_team_root
    if root:
        return Path(root) / self.team_id
    # Client-side fallback: ~/Assistant/Teams/{team_id}/
    return self.root / "Teams" / self.team_id
```

### User-scoped

| New Method | Path | Replaces |
|---|---|---|
| `user_prompt_path()` | `root / "AGENTS.md"` | `_user_base() / config/prompt.txt` |
| `user_skills_dir()` | `root / "Skills"` | `skills_dir()` (solo path) |
| `user_subagents_dir()` | `root / "Subagents"` | `subagents_dir()` |
| `user_memory_dir()` | `root / "Memory" / "global"` | `global_memory_dir()` |
| `email_dir()` | `root / "Email"` | `_user_base() / email` |
| `email_db()` | `root / "Email" / "emails.db"` | `_user_base() / email / emails.db` |
| `gmail_cache_dir()` | `root / "Email" / "gmail_cache"` | `_user_base() / gmail_cache` |
| `contacts_dir()` | `root / "Contacts"` | `_user_base() / contacts` |
| `contacts_db()` | `root / "Contacts" / "contacts.db"` | `_user_base() / contacts / contacts.db` |
| `todos_dir()` | `root / "Todos"` | `_user_base() / todos` |
| `todos_db()` | `root / "Todos" / "todos.db"` | `_user_base() / todos / todos.db` |
| `conversation_dir()` | `root / "Conversation"` | `_user_base() / conversation` |
| `conversation_db()` | `root / "Conversation" / "messages.db"` | `_user_base() / conversation / messages.db` |
| `user_apps_dir()` | `root / "Apps"` | `_user_base() / apps` |
| `user_mcp_config()` | `root / ".mcp.json"` | `_user_base() / .mcp.json` |
| `research_dir()` | `root / "Research"` | `data/private/research/` |
| `companion_dir()` | `root / "Companion"` | `_user_base() / companion` |

### Team-scoped

| New Method | Path |
|---|---|
| `team_prompt_path()` | `team_root / "AGENTS.md"` |
| `team_skills_dir()` | `team_root / "Skills"` |
| `team_subagents_dir()` | `team_root / "Subagents"` |
| `team_files_dir()` | `team_root / "Files"` |
| `team_memory_dir()` | `team_root / "Memory"` |
| `team_apps_dir()` | `team_root / "Apps"` |
| `team_contacts_dir()` | `team_root / "Contacts"` |
| `team_todos_dir()` | `team_root / "Todos"` |
| `team_mcp_config()` | `team_root / ".mcp.json"` |

All return `None` when `team_id` is not set.

### Workspace-scoped

```python
def workspace_root(self) -> Path:
    team = self.team_root
    base = team / "Workspaces" if team else self.root / "Workspaces"
    return base / self.workspace_id

def workspace_skills_dir(self) -> Path:    # ⬆ upper S
    return self.workspace_root() / "Skills"

def workspace_subagents_dir(self) -> Path: # ⬆ upper S
    return self.workspace_root() / "Subagents"

def workspace_files_dir(self) -> Path:     # ⬆ upper F
    return self.workspace_root() / "Files"

def workspace_memory_dir(self) -> Path:    # ⬆ upper M
    return self.workspace_root() / "Memory"

def workspace_conversation_path(self) -> Path:
    return self.workspace_root() / "conversation.app.db"

def workspace_cache(self) -> Path:
    return self.workspace_root() / ".file_cache.json"

def versions_dir(self) -> Path:            # stays dotted
    return self.workspace_root() / ".versions"
```

### Kept in `data/` (project-level, not user-level)

| Method | Path | Purpose |
|---|---|---|
| `model_cache_path()` | `{data_path}/cache/models.json` | models.dev registry cache |
| `templates` | `{data_path}/templates` | App templates |
| `logs_dir()` | `{data_path}/logs` | JSONL logs (though actually uses LoggingConfig) |
| `traces_path()` | `{data_path}/traces/traces.jsonl` | Traces |
| `jobs_db_path()` | `{data_path}/jobs.db` | Job scheduler DB |
| `jobs_results_db_path()` | `{data_path}/jobs_results.db` | Job results DB |

`data_path` defaults to `"data"` (project-relative) — unchanged.

### Deprecated / removed

| Method | Removal Reason |
|---|---|
| `_user_base()` | `data/users/{id}/` goes away |
| `user_dir` | Replaced by `root` |
| `skills_dir()` | Replaced by `user_skills_dir()` (no mode conditional — always `root / "Skills"`) |
| `global_skills_dir()` | Same as skills_dir |
| `global_subagents_dir()` | Removed. Coordinator uses `user_subagents_dir()` directly — no `global/` subdirectory |
| `global_memory_dir()` | Replaced by `user_memory_dir()` / `team_memory_dir()` |
| `subagents_dir()` | Replaced by `user_subagents_dir()` |
| `agent_defs_dir()` | Dead code (never called) |
| `user_config_dir()` | Replaced by `user_prompt_path()` |
| `private` (property) | `data/private/` goes away |
| `shared` (property) | `data/shared/` goes away |

Old methods delegate to new ones with a deprecation warning for one release.

### Consolidation target

Currently the workspace base path `~/Assistant/Workspaces/` is hardcoded in **3 places**:
- `paths.py:108` — `DataPaths._workspaces_base()`
- `workspace_models.py:126` — `_default_workspaces_dir()`
- `memory.py:166` — `_list_workspace_ids()`

All three become `DataPaths.root / "Workspaces"`.

---

## 4. Git Strategy

```gitignore
*.db
Memory/
Files/
Email/
Contacts/
Todos/
Conversation/
gmail_cache/
.versions/
.env
*.log
```

`git init` happens once at `~/Assistant/` on first `DataPaths` access in solo mode.

`ResearchLoop` detects git at `DataPaths.root / ".git"`:
- `.git` exists → use branch isolation for experiments
- No `.git` (team volume, server-side) → fall back to in-memory backup

The hardcoded `Path("data") / "private" / "research"` in `research.py` is replaced with `DataPaths.research_dir()` (`~/Assistant/Research/`).

---

## 5. Migration

One-shot script, gated by `~/.ea_migrated` marker.

**Context:** There is no production deployment yet — no solo users, no team
deployments. The migration is for the developer's own environment only.
If the script fails, clean up and re-run. No rollback plan needed.

### Guardrails

| Guard | Why |
|-------|-----|
| **Dry-run mode** (`--dry-run`) | Prints what would move without touching disk — verify before committing |
| **Resume marker** | If the script crashes mid-migration, the `.ea_migrated` marker is NOT written. Re-running skips already-moved items |
| **macOS case-insensitive FS rename** | `mv skills Skills` is a no-op. Script uses `mv skills Skills.tmp && mv Skills.tmp Skills` to force the rename through |

### File Map

```
data/users/{id}/config/prompt.txt         →  ~/Assistant/AGENTS.md
data/users/{id}/skills/{n}/               →  ~/Assistant/Skills/{n}/
data/users/{id}/subagents/                →  ~/Assistant/Subagents/
data/users/{id}/conversation/             →  ~/Assistant/Conversation/
data/users/{id}/memory/                   →  ~/Assistant/Memory/global/
data/users/{id}/email/                    →  ~/Assistant/Email/
data/users/{id}/gmail_cache/              →  ~/Assistant/Email/gmail_cache/
data/users/{id}/contacts/                 →  ~/Assistant/Contacts/
data/users/{id}/todos/                    →  ~/Assistant/Todos/
data/users/{id}/companion/               →  ~/Assistant/Companion/
data/users/{id}/apps/                     →  ~/Assistant/Apps/
data/users/{id}/.mcp.json                →  ~/Assistant/.mcp.json
data/private/research/                    →  ~/Assistant/Research/
~/EA/Workspaces/{id}/skills/ (lowercase)  →  ~/EA/Workspaces/{id}/Skills/
~/EA/Workspaces/{id}/subagents/            →  ~/EA/Workspaces/{id}/Subagents/
~/EA/Workspaces/{id}/files/                →  ~/EA/Workspaces/{id}/Files/
~/EA/Workspaces/{id}/memory/               →  ~/EA/Workspaces/{id}/Memory/
```

Left in place (not moved): `data/teams/{id}/` — these become `{team_root}/{id}/`
on the server side. Solo users don't have teams yet.

After migration, `data/users/{id}/` and `data/private/` are empty and safe to
remove.

---

## 6. Implementation Order

### Stage 1 (Solo Mode)

1. Add `ea_root` to settings (default: `~/Assistant/`)
2. Update `DataPaths` — add `root` property, new user-scoped methods, old-method deprecation wrappers
3. Consolidate 3 hardcoded `Workspaces/` path references into `DataPaths.root / "Workspaces"`
4. Write migration script with dry-run, manifest, resume marker guards
5. Run migration: `data/users/{id}/` → `~/Assistant/`
6. Rename workspace subdirs lowercase → uppercase via migration
7. Add `.gitignore` + `git init` at `~/Assistant/`
 8. Update `ResearchLoop` to detect `.git` and fall back to in-memory backup
 9. Remove hardcoded `Path("data") / "private" / "research"` in all research tools (`research.py`, `research_list`) — use `DataPaths.research_dir()`
10. Create `src/prompt_seed/AGENTS.md` with seed content
11. Replace `settings.agent.system_prompt` with AGENTS.md loading in runner
12. Update `_get_system_prompt()` to hardcode new 4-section order:
    `user_prompt + user_skills + workspace_prompt + workspace_skills`
    (PROMPT_ORDER setting is Stage 2 — in Stage 1 the order is hardcoded)
12. Update all callers (coordinator, runner, tools, HTTP routers)
13. Update tests
14. Run full test suite

### Stage 2 (Multi-Tenant)

1. Add `ea_team_root` to settings
2. Add `DataPaths.team_root` property + team-scoped methods
3. Add team scope resolution in context injection (prompt + skills)
4. Add `PROMPT_ORDER` setting with resequencable order list
5. Switch `Workspace.from_name()` to UUID generation (slug IDs for existing workspaces kept as-is)
6. Update tests
7. Run full test suite

---

## 7. No Visible Changes to Users or LLM

- All tool names stay the same
- All tool parameters stay the same (`scope="user"` still works)
- The migration is transparent — files move once, paths are re-pointed
- `AGENTS.md` is the new canonical name for user prompt (replaces `prompt.txt`)
- Stage 2 additions (team scope, PROMPT_ORDER) are invisible until configured
