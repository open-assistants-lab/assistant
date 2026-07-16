# Skills System Redesign — Design Spec

## Summary

Redesign the Assistant's skills system to support user-scoped and workspace-scoped skills with a management UI in Flutter and the web dashboard. Align with the [Agent Skills specification](https://agentskills.io/specification) (progressive disclosure, `SKILL.md` format). Remove the unused "system" tier — system skills seed into user skills on first run and become fully editable.

---

## 1. Architecture & Storage

Skills follow the agentskills.io format: each skill is a directory containing `SKILL.md` with YAML frontmatter.

### Scopes

| Scope | Solo desktop path | Multi-user/server path | Priority |
|-------|-------------------|------------------------|----------|
| **User** | `~/Assistant/Skills/` | `data/users/{user_id}/skills/` | Lower |
| **Workspace** | `~/Assistant/Workspaces/{id}/skills/` | `data/users/{user_id}/workspaces/{id}/skills/` | Higher |

Resolution: workspace > user by name. A workspace skill named `code-review` replaces the user-level skill of the same name.

Path resolution must go through `DataPaths` so solo users get normal files under `~/Assistant/...`, while multi-user/container deployments keep every user's skills isolated under `data/users/{user_id}/...`.

There is no "system" scope. On first run, bundled seed skills from `src/skills_seed/` are copied into the user skills directory and become normal user skills — editable, deletable, no special treatment. The initial seed set contains only `skill-creator`.

### Directory Structure

```
skill-name/
├── SKILL.md          # Required: YAML frontmatter + markdown body
├── scripts/          # Optional: executable code
├── references/       # Optional: docs loaded on demand
└── assets/           # Optional: templates, images
```

### SKILL.md Format

YAML frontmatter followed by markdown body. Fields:

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | 1-64 chars, `[a-z0-9]+(-[a-z0-9]+)*`, matches directory name |
| `description` | Yes | 1-1024 chars. Describes WHEN to use the skill |
| `license` | No | License name or reference |
| `compatibility` | No | Environment requirements (1-500 chars) |
| `metadata` | No | String-to-string map. Stored in frontmatter `metadata:` |
| `allowed-tools` | No | Space-separated tool names (experimental) |

EA-specific conventions:
- `metadata.version` — semantic version
- `metadata.scope` — populated by registry on load, not stored in file
- `disable-model-invocation: true` — set in `metadata` map, respected by system prompt builder

---

## 2. Progressive Disclosure

Three stages, matching the agentskills.io spec:

**Stage 1 — Metadata only** (~100 tokens/skill): At AgentLoop creation, `_get_skills_context()` resolves user + workspace scopes, merges (workspace overrides user), and injects only `name` and `description` into the system prompt:

```
## Available Skills
When a task matches a skill description below, call skills_load(name) first...

- **skill-name**: Description of what it does and when to use it.
```

Skills with `disable-model-invocation: true` are excluded from this section.

**Stage 2 — Full instructions on activation**: Agent calls `skills_load("name")`. Registry resolves through merged scopes (workspace > user), reads `SKILL.md`, returns full markdown body into conversation context.

**Stage 3 — Resources on demand**: Agent loads `scripts/`, `references/`, `assets/` as needed via existing file tools (`files_read`, `shell_execute`). SKILL.md body should be under 500 lines. Resources loaded via relative paths from the skill root.

---

## 3. Registry & Resolution

### Factory

```python
def get_skill_registry(user_id: str = "default_user", workspace_id: str = "personal") -> SkillRegistry
```

### Resolution Algorithm (on `get_all_skills()`)

1. Load user skills from the deployment-aware user skills directory (read-write)
2. Load workspace skills from the deployment-aware current workspace skills directory, including `workspace_id="personal"`
3. Merge: user → workspace (workspace overrides user by name)
4. Populate `metadata.scope` (`"user"` or `"workspace"`) and `metadata.workspace_id` on each skill

### Seeding

On first invocation, copy skill directories from `src/skills_seed/` to the deployment-aware user skills directory. Only copy if destination doesn't exist. After seeding, skills are user-scoped and fully editable. Current bundled seed: `skill-creator`.

### Caching

- Per `(user_id, workspace_id)` cache in registry factory
- Invalidated on `skill_create`, `skill_delete`, `reload()`
- Workspace switch triggers loop rebuild → fresh registry

### Tool Changes

All four tools currently call `_get_registry(user_id)` which is user-scope only. The shared helper becomes:

```python
def _get_registry(user_id: str = "default_user", workspace_id: str = "personal") -> SkillRegistry:
    return get_skill_registry(user_id=user_id, workspace_id=workspace_id)
```

Each tool adds `workspace_id` to its parameter schema. `AgentLoop` auto-injects `workspace_id` into tool calls that declare it, so the wiring from the current conversation context is automatic once the param exists.

| Tool | Change |
|------|--------|
| `skills_list` | Adds `workspace_id` param. Calls `registry.get_all_skills()` on merged (workspace > user) registry. Response includes `scope` and `workspace_id` per skill. |
| `skills_search` | Adds `workspace_id` param. Searches name, description, and content across both scopes using merged registry. |
| `skills_load` | Adds `workspace_id` param. Resolves skill through merged registry (workspace > user by name). |
| `skill_create` | Adds `scope` (`"user"`/`"workspace"`) and `workspace_id` params. When scope=workspace, writes to `DataPaths.workspace_skills_dir()`. Calls `registry.reload()` after write. |
| `skill_delete` | **New tool.** Accepts `name`, `scope`, `workspace_id`. Deletes skill directory via `shutil.rmtree`, calls `registry.reload()`. Destructive (requires approval). |

`sql_write_query` is unchanged — it's a skill-gate demo tool, not a skill management tool.

---

## 4. Flutter UX

### Desktop Layout

```
┌─ LHS ───────┐  ┌─ Chat (Center) ──────────┐  ┌─ RHS Panel ──────────────┐
│              │  │                          │  │                          │
│  Personal    │  │  User: save this to      │  │  skill-creator      user │
│  Q2 Planning │  │  the wiki                │  │  Custom workflows        │
│  Settings    │  │                          │  │  [⟳] [✎] [✕]           │
│              │  │  Agent: ingested into    │  │                          │
│              │  │  wiki/stripe.md          │  │  stripe-research     ws │
│              │  │                          │  │  Stripe integration      │
│              │  │                          │  │  [⟳] [✎] [✕]           │
│              │  │                          │  │                          │
│              │  │                          │  │  [+ Create New]          │
│              │  │                          │  │                          │
│              │  │                          │  │  ─────────────────────   │
│              │  │                          │  │  📁  ⚡            ← icons│
└──────────────┘  └──────────────────────────┘  └──────────────────────────┘
```

### Key Design Decisions

1. **RHS panel** — Skills tab is a sibling of Files tab. Switched via icon buttons (📁 Files / ⚡ Skills) at the bottom of the panel, following the Zed editor pattern.

2. **Merged list** — User and workspace skills appear in a single flat list. Each row shows a scope badge (`user` / `ws`). No filter tabs — everything visible at once.

3. **Scope badge** — Each skill row displays its scope. When in Personal workspace, all skills show `user`. When in a named workspace, workspace-scoped skills show `ws` badge.

4. **Actions per skill** — View (load full content), Edit (open markdown editor), Delete (confirm dialog). All skills are editable/deletable (no locked system skills).

5. **Create flow** — Modal or inline form: name, description, body (markdown editor). Scope defaults to current workspace if in a named workspace, otherwise user scope. User can toggle scope.

6. **Skill row** — Shows name, truncated description (1-2 lines), scope badge. Clicking the name opens the detail view.

### API Client

Add to `flutter_app/lib/services/api_client.dart`:
- `getSkillDetail(name, workspaceId)` → `SkillDetailResponse`
- `updateSkill(name, body, workspaceId)` → void
- Update `listSkills()` to accept `workspaceId`

---

## 5. Web Dashboard UX

Same capabilities as Flutter, richer layout:

- Split-pane: skill list on left, markdown editor on right
- Merged list with scope badges
- Create/edit form with frontmatter fields + markdown body
- Same API endpoints as Flutter

---

## 6. API Design

### Endpoints

| Method | Path | Changes |
|--------|------|---------|
| `GET` | `/skills` | Add `workspace_id` query param. Response adds `scope`, `workspace_id`. Rename `is_system` → `is_loaded`. |
| `GET` | `/skills/{skill_name}` | **New.** Returns full skill with body. |
| `POST` | `/skills` | Add `scope`, `workspace_id` params. Rename body param → `content`. |
| `PUT` | `/skills/{skill_name}` | **New.** Update description, content, metadata. Name cannot change. |
| `DELETE` | `/skills/{skill_name}` | Accept `workspace_id` to target correct scope. |

### Response Models

```python
class SkillResponse(BaseModel):
    name: str
    description: str
    scope: str               # "user" | "workspace"
    workspace_id: str | None
    is_loaded: bool
    disable_model_invocation: bool

class SkillDetailResponse(SkillResponse):
    content: str             # Full markdown body
    metadata: dict[str, str]
```

---

## 7. Error Handling

| Scenario | Code | Response |
|----------|------|----------|
| Load non-existent skill | 404 | `"Skill 'name' not found"` |
| Invalid skill name | 400 | `"Invalid skill name. Must match [a-z0-9]+(-[a-z0-9]+)*"` |
| Duplicate name (same scope) | 409 | `"Skill 'name' already exists in user scope"` |
| Corrupt SKILL.md | 500 | `"Skill file is corrupt: {error}"` |
| Disk full / permissions | 500 | `"Failed to write skill: {OS error}"` |

---

## 8. Testing Plan

| Layer | Tests | Estimate |
|-------|-------|----------|
| **Registry** | Workspace-aware resolution, override priority, seeding, cache invalidation | ~8 |
| **SDK Tools** | Scoped load/create/list, merged results | ~6 |
| **REST API** | New endpoints, scope params, error responses, Pydantic validation | ~8 |
| **Flutter** | Panel rendering, merged list, create/edit form | ~4 |
| **Integration** | Create → agent loads → wiki uses | ~2 |

---

## 9. Migration

No breaking changes. All existing consumers are unaffected:

1. Registry gains `workspace_id` parameter — defaults to `"personal"`, backward compat
2. REST API gains new endpoints and fields — old fields preserved
3. Flutter gains a new tab — existing tabs untouched
4. `is_system` → `is_loaded` rename in REST response — no consumer uses `is_system` today
5. Bundled seed skills live in `src/skills_seed/`; `src/skills/` is not the seed source. Removed system skills (`subagent-manager`, `planning-with-files`, `deep-research`) are not seeded.

---

## 10. Future: Skill Evaluation (Deferred)

A skill eval pipeline for measuring description triggering accuracy, running baseline comparisons, and optimizing descriptions via iterative testing. Based on the Anthropic `skill-creator` workflow:

- **Trigger eval queries** — should-trigger and should-not-trigger test cases
- **Description optimization loop** — measure trigger rates, propose improvements, re-test
- **Benchmark grading** — assertion-based output verification

This is a separate system from skill CRUD management. Deferred to a later implementation cycle.

---

## 11. Execution Order

| Step | Effort | What |
|------|--------|------|
| 1. Registry | 1 day | Workspace-aware resolution, seeding refactor, tool changes |
| 2. REST API | 0.5 day | New endpoints, Pydantic models, scope params |
| 3. Flutter UI | 1 day | Skills tab in RHS panel, merged list, create/edit/delete |
| 4. Web dashboard | 0.5 day | Same as Flutter, richer editor |
| 5. Testing | 0.5 day | All test layers |
| 6. Lint & cleanup | 0.25 day | Ruff + mypy |
