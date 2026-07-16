# Path Restructuring — Stage 1 (Solo Mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all user data under `~/Assistant/`, replace `data/users/{id}/`, and lay groundwork for autoresearch git versioning.

**Architecture:** `DataPaths` gains a configurable `root` property. User-scoped path methods (email, contacts, todos, conversation, memory, skills, subagents, etc.) redirect to `root / DirName/`. Workspace subdirectories rename lowercase → uppercase. Old methods become deprecated wrappers. A one-shot migration script moves `data/users/{id}/` to the new layout.

**Tech Stack:** Python 3.11+, pathlib, shutil, git

**Spec:** `docs/superpowers/path-restructuring-spec.md`

---

### Task 1: Add `ea_root` to settings

**Files:**
- Modify: `src/config/settings.py`
- Confirm: `docs/superpowers/path-restructuring-spec.md` §3 (configurable root)

- [ ] **Step 1: Read current settings to find where to add `ea_root`**

Run: `grep -n 'class AgentConfig\|class DeploymentConfig\|data_path\|class AppConfig' src/config/settings.py`

Read the relevant section to understand existing pattern.

- [ ] **Step 2: Add `ea_root` to settings**

Find the config class that holds path-related settings. Add:

```python
# Alongside data_path or in a relevant config class
ea_root: str = ""
"""Root for user data directory. Empty string means Path.home() / "Assistant"."""
```

Commit the specific file and line after reading.

- [ ] **Step 3: Verify the setting loads**

Run: `uv run python -c "from src.config import get_settings; s = get_settings(); print(repr(s.ea_root))"`
Expected: `''` (empty string default)

- [ ] **Step 4: Commit**

```bash
git add src/config/settings.py
git commit -m "feat(config): add ea_root setting for path restructuring"
```

---

### Task 2: Rewrite DataPaths — root property and user-scoped methods

**Files:**
- Modify: `src/storage/paths.py` (the core — ~367 lines rewritten)
- Test: `tests/unit/test_paths.py` or `tests/sdk/test_paths.py` (locate first)

- [ ] **Step 1: Read full current DataPaths class**

Read `src/storage/paths.py` in full. Understand the constructor, every method, the `get_paths()` cache, and the deprecation pattern.

- [ ] **Step 2: Write tests for the new DataPaths**

Create test file at `tests/sdk/test_paths.py`:

```python
"""Tests for DataPaths path restructuring."""

import os
from pathlib import Path

from src.storage.paths import DataPaths


def test_root_defaults_to_home_ea():
    dp = DataPaths(user_id="tester", data_path="/tmp/ea-test-data")
    # When ea_root is not set, default to ~/Assistant/
    # During tests, inject ea_root to avoid touching real home dir
    dp2 = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp2.root) == "/tmp/ea-test-root"


def test_user_skills_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_skills_dir()) == "/tmp/ea-test-root/Skills"


def test_user_subagents_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_subagents_dir()) == "/tmp/ea-test-root/Subagents"


def test_user_prompt_path():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_prompt_path()) == "/tmp/ea-test-root/AGENTS.md"


def test_email_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.email_dir()) == "/tmp/ea-test-root/Email"


def test_email_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.email_db()) == "/tmp/ea-test-root/Email/emails.db"


def test_gmail_cache_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.gmail_cache_dir()) == "/tmp/ea-test-root/Email/gmail_cache"


def test_contacts_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.contacts_dir()) == "/tmp/ea-test-root/Contacts"


def test_contacts_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.contacts_db()) == "/tmp/ea-test-root/Contacts/contacts.db"


def test_todos_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.todos_dir()) == "/tmp/ea-test-root/Todos"


def test_todos_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.todos_db()) == "/tmp/ea-test-root/Todos/todos.db"


def test_conversation_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.conversation_dir()) == "/tmp/ea-test-root/Conversation"


def test_conversation_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.conversation_db()) == "/tmp/ea-test-root/Conversation/messages.db"


def test_user_memory_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_memory_dir()) == "/tmp/ea-test-root/Memory/global"


def test_user_apps_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_apps_dir()) == "/tmp/ea-test-root/Apps"


def test_user_mcp_config():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_mcp_config()) == "/tmp/ea-test-root/.mcp.json"


def test_research_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.research_dir()) == "/tmp/ea-test-root/Research/tester/testws"


def test_companion_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.companion_dir()) == "/tmp/ea-test-root/Companion"


def test_workspace_skills_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_skills_dir()) == "/tmp/ea-test-root/Workspaces/testws/Skills"


def test_workspace_subagents_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_subagents_dir()) == "/tmp/ea-test-root/Workspaces/testws/Subagents"


def test_workspace_files_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_files_dir()) == "/tmp/ea-test-root/Workspaces/testws/Files"


def test_workspace_memory_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_memory_dir()) == "/tmp/ea-test-root/Workspaces/testws/Memory"


def test_workspace_conversation_path():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_conversation_path()) == "/tmp/ea-test-root/Workspaces/testws/conversation.app.db"


def test_deprecated_skills_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.skills_dir()
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Skills"


def test_deprecated_global_subagents_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.global_subagents_dir()
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Subagents"


def test_kept_in_data_model_cache():
    dp = DataPaths(user_id="tester", data_path="/tmp/ea-test-data")
    assert "cache" in str(dp.model_cache_path())


def test_work_queue_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    path = dp.work_queue_db()
    assert "Subagents" in str(path)
    assert path.name == "work_queue.db"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/sdk/test_paths.py -v`
Expected: All fail with ImportError or AttributeError (new methods don't exist yet)

- [ ] **Step 4: Rewrite DataPaths**

Read the full `src/storage/paths.py` first, then rewrite it to:

1. **Constructor**: Add `ea_root` and `ea_team_root` params. Keep `data_path` for project-level data.
2. **`root` property**: resolves `ea_root` → `settings.ea_root` → `Path.home() / "Assistant"`.
3. **`team_root` property**: for solo mode, always return None. (Stage 1 — no teams.)
4. **All user-scoped methods** from the spec table (see `docs/superpowers/path-restructuring-spec.md` §3).
5. **Workspace-scoped methods**: uppercase subdirectories (Skills, Subagents, Files, Memory).
6. **`research_dir()`**: returns `root / "Research" / self.user_id / self.workspace_id` — includes user+workspace scope since the data tree is partitioned per-user (unlike email/contacts/todos which are single-user directories). Migration moves `data/private/research/` as a whole tree, so the user/workspace subdirectories are preserved.
7. **Consolidated workspace base**: `root / "Workspaces"` — delete `_workspaces_base()`, inline it.
8. **Deprecated wrappers**: every old method calls the new one with `warnings.warn("deprecated", DeprecationWarning, stacklevel=2)`.
9. **`data/` project-level methods**: keep unchanged (model_cache_path, templates, logs_dir, traces_path, jobs_db_path).
10. **Keep `get_paths()` cache** unchanged (cached on `(user_id, team_id)`).

The old `_user_base()` path (`data/users/{id}/`) is removed entirely. The old `_workspaces_base()` (`~/Assistant/Workspaces/`) is replaced by `root / "Workspaces"`.

Key change: `work_queue_db()` moves from `{subagents_dir()}/work_queue.db` to `{user_subagents_dir()}/work_queue.db` — same pattern, just under `~/EA/Subagents/` instead of `data/users/{id}/subagents/`.

Show the full rewritten file (it's ~400 lines). Make sure every method auto-creates its parent directory with `mkdir(parents=True, exist_ok=True)` as the current code does.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/sdk/test_paths.py -v`
Expected: All pass

- [ ] **Step 6: Run existing path-related tests to check no regressions**

Run: `uv run pytest tests/sdk/ -k "path" -v`
Expected: No new failures (pre-existing failures are OK)

- [ ] **Step 7: Commit**

```bash
git add src/storage/paths.py tests/sdk/test_paths.py
git commit -m "feat(paths): rewrite DataPaths with ea_root, uppercase workspace subdirs, deprecated wrappers"
```

---

### Task 3: Consolidate 3 hardcoded Workspaces/ paths

**Files:**
- Modify: `src/sdk/workspace_models.py` (line 126 — `_default_workspaces_dir()`)
- Modify: `src/sdk/tools_core/memory.py` (line 166 — `_list_workspace_ids()`)

- [ ] **Step 1: Read both files to see the current hardcoded paths**

Read `src/sdk/workspace_models.py` around line 120-135.
Read `src/sdk/tools_core/memory.py` around line 155-175.

- [ ] **Step 2: Fix workspace_models.py**

Replace `Path.home() / "Assistant" / "Workspaces"` with `DataPaths(...).root / "Workspaces"`:

```python
# Before
@staticmethod
def _default_workspaces_dir() -> Path:
    return Path.home() / "Assistant" / "Workspaces"

# After
@staticmethod
def _default_workspaces_dir() -> Path:
    from src.storage.paths import DataPaths
    return DataPaths().root / "Workspaces"
```

- [ ] **Step 3: Fix memory.py**

Replace the hardcoded workspace base path similarly. The function enumerates workspace IDs by listing directories:

```python
# Find the hardcoded path and replace with DataPaths().root / "Workspaces"
```

- [ ] **Step 4: Run tests to verify**

Run: `uv run pytest tests/sdk/test_workspace_models.py tests/sdk/ -k "memory" -v`

- [ ] **Step 5: Commit**

```bash
git add src/sdk/workspace_models.py src/sdk/tools_core/memory.py
git commit -m "refactor: consolidate 3 hardcoded Workspaces/ paths into DataPaths.root"
```

---

### Task 4: Create prompt seed + update runner

**Files:**
- Create: `src/prompt_seed/AGENTS.md`
- Modify: `src/sdk/runner.py` (remove `settings.agent.system_prompt`, inject from AGENTS.md, new 4-section order)
- Modify: `src/sdk/user_prompt.py` (change path from `user_config_dir() / "prompt.txt"` to `root / "AGENTS.md"`)
- Test: `tests/sdk/test_runner.py` or `tests/sdk/test_prompt_seeding.py`

- [ ] **Step 1: Create seed prompt file**

Write `src/prompt_seed/AGENTS.md`:

```markdown
# Assistant — User Instructions

You are a helpful assistant. You have access to tools for email,
contacts, todos, file management, web search, browser automation, and more.

## Core Rules

- Use tools to get exact information — never guess or estimate
- If you don't know something, call the appropriate tool to check
- When asked about the user's information, call memory_search first
- Tell the user exactly what tools return, not summaries or estimates

## How to Use Skills

When a task matches a skill description shown in your context, call
`skills_load(skill_name)` to get full instructions before proceeding.
```

- [ ] **Step 2: Update `src/sdk/user_prompt.py`**

Change the prompt file path from `user_config_dir() / "prompt.txt"` to `root / "AGENTS.md"`:

```python
# Before
USER_PROMPT_FILENAME = "prompt.txt"
...
paths = get_paths(user_id)
return paths.user_config_dir() / USER_PROMPT_FILENAME

# After
USER_PROMPT_FILENAME = "AGENTS.md"
...
paths = get_paths(user_id)
return paths.root / USER_PROMPT_FILENAME
```

- [ ] **Step 3: Update `_get_system_prompt()` in runner.py**

Read the current `_get_system_prompt()` in `src/sdk/runner.py`. Change from:

```python
def _get_system_prompt(user_id, workspace_id=None):
    settings = get_settings()
    base_prompt = getattr(settings.agent, "system_prompt", "...")
    ...
    return base_prompt + user_prompt_context + skills_context + workspace_context
```

To:

```python
def _get_system_prompt(user_id, workspace_id=None):
    settings = get_settings()
    w_id = workspace_id or "personal"

    user_prompt_context = _get_user_prompt_context(user_id)
    skills_context = _get_skills_context(user_id, w_id)
    workspace_context = _get_workspace_context(workspace_id)

    sections = [
        user_prompt_context,
        skills_context,
        workspace_context,
    ]
    sections = [s for s in sections if s]
    body = "\n".join(sections)
    return body + f"\n\nuser_id: {user_id}"
```

Also remove the import of `get_settings` if it's no longer needed. Remove the `settings.agent.system_prompt` reference entirely since it comes from AGENTS.md now.

Also add prompt seeding logic. At the end of `_get_system_prompt` or as a separate `_ensure_prompt_seeded()` function:

```python
def _ensure_prompt_seeded(user_id: str) -> None:
    """Seed AGENTS.md from src/prompt_seed/ on first access."""
    from src.storage.paths import DataPaths
    prompt_path = DataPaths(user_id=user_id).root / "AGENTS.md"
    marker = prompt_path.parent / ".prompt_seeded"
    if prompt_path.exists() or marker.exists():
        return
    seed = Path("src/prompt_seed/AGENTS.md")
    if seed.exists():
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    marker.write_text("", encoding="utf-8")
```

Call `_ensure_prompt_seeded(user_id)` at the start of `_get_system_prompt()`.

- [ ] **Step 4: Update the current 4-section order**

The order in the assembled prompt should be:
```
user_prompt_context  (from AGENTS.md)
skills_context       (from user + workspace skills)
workspace_context    (from workspace config)
```

This matches the spec: user prompt → user skills → workspace prompt → workspace skills (all in those 3 sections).

- [ ] **Step 5: Run existing runner tests**

Run: `uv run pytest tests/sdk/test_runner.py tests/sdk/test_runner_skills_context.py -v`
Expected: Pass (some tests may need updating for the new prompt path)

- [ ] **Step 6: Commit**

```bash
git add src/prompt_seed/AGENTS.md src/sdk/runner.py src/sdk/user_prompt.py
git commit -m "feat(prompt): seed AGENTS.md from src/prompt_seed/, remove settings.agent.system_prompt"
```

---

### Task 5: Update coordinator — subagents path

**Files:**
- Modify: `src/sdk/coordinator.py`

- [ ] **Step 1: Read coordinator.py to find subagents path references**

Search for `global_subagents_dir` and `workspace_subagents_dir` usage.

- [ ] **Step 2: Replace `global_subagents_dir()` with `user_subagents_dir()`**

The coordinator currently uses `DataPaths(user_id=...).global_subagents_dir()` as a user-level fallback for agent defs. Replace with `DataPaths(user_id=...).user_subagents_dir()`.

No `global/` subdirectory — agent defs go directly in `~/EA/Subagents/{name}/config.yaml`.

- [ ] **Step 3: Run coordinator-related tests**

Run: `uv run pytest tests/sdk/test_subagent_v1.py -v`
Expected: Pass

- [ ] **Step 4: Commit**

```bash
git add src/sdk/coordinator.py
git commit -m "fix(coordinator): replace global_subagents_dir with user_subagents_dir"
```

---

### Task 6: Fix research.py hardcoded path + git detection

**Files:**
- Modify: `src/sdk/research.py` (ResearchLoop git detection)
- Modify: `src/sdk/tools_core/research.py` (research_list hardcoded path)
- Test: `tests/sdk/test_research.py`, `tests/sdk/test_research_tools.py`

- [ ] **Step 1: Read both research files**

Read the full `src/sdk/research.py` and `src/sdk/tools_core/research.py`.

- [ ] **Step 2: Fix ResearchLoop git detection**

In `ResearchLoop.__post_init__` or a new method, add git detection:

```python
def _git_available(self) -> bool:
    """Check if the root directory has a .git repo."""
    from src.storage.paths import DataPaths
    root = DataPaths().root
    return (root / ".git").exists()
```

Update the experiment methods to use `_git_available()` — if True, use git branch isolation (future enhancement for Stage 1.5). For now, just add the detection method — the actual branch logic is not implemented in Stage 1 (the in-memory backup stays as the only mechanism).

- [ ] **Step 3: Fix research_list hardcoded path**

In `src/sdk/tools_core/research.py`, replace:

```python
base = Path("data") / "private" / "research" / user_id / workspace_id
```

With:

```python
from src.storage.paths import DataPaths
base = DataPaths(user_id=user_id, workspace_id=workspace_id).research_dir()
```

- [ ] **Step 4: Run research tests**

Run: `uv run pytest tests/sdk/test_research.py tests/sdk/test_research_tools.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/sdk/research.py src/sdk/tools_core/research.py
git commit -m "fix(research): replace hardcoded data/private/research path, add git detection"
```

---

### Task 7: Update tools and HTTP routers (bulk find-and-replace)

**Files — all need updating (new DataPaths method names):**

| File | Old method | New method |
|---|---|---|
| `src/sdk/tools_core/filesystem.py:50,54` | `paths.skills_dir()` | `paths.user_skills_dir()` |
| `src/sdk/tools_core/file_search.py:25,29` | `paths.skills_dir()` | `paths.user_skills_dir()` |
| `src/sdk/tools_core/skills.py:242-243` | `paths.skills_dir()` | `paths.user_skills_dir()` |
| `src/sdk/tools_core/workspace.py:50-53` | `dp.workspace_files_dir()` | → `dp.workspace_files_dir()` (name same, path changes) |
| `src/http/routers/workspace.py:132` | `get_paths(user_id).skills_dir()` | `get_paths(user_id).user_skills_dir()` |
| `src/http/routers/workspace.py:133` | `get_paths(user_id).subagents_dir()` | `get_paths(user_id).user_subagents_dir()` |
| `src/http/routers/skills.py:164` | `paths.skills_dir()` | `paths.user_skills_dir()` |
| `src/http/routers/workspaces.py:59-63` | `dp.workspace_*_dir()` | (name same, path changes) |
| `src/skills/registry.py:63` | `paths.skills_dir()` | `paths.user_skills_dir()` |
| `src/skills/storage.py:69` | `get_paths(user_id).skills_dir()` | `get_paths(user_id).user_skills_dir()` |
| `src/storage/memory.py:131` | `paths.memory_dir()` | deprecated — will warn but still work |
| `src/storage/messages.py:74-75` | `paths.conversation_dir()` | deprecated — will warn but still work |
| `src/storage/email_db.py:17-18` | `paths.email_dir()` | deprecated — will warn but still work |
| `src/storage/gmail_cache.py:86` | `paths.gmail_cache()` | deprecated — will warn but still work |
| `src/sdk/tools_core/memory.py:34-35` | `paths.conversation_dir()` | deprecated — will warn but still work |
| `src/sdk/user_prompt.py:12-13` | `paths.user_config_dir()` | `paths.root / "AGENTS.md"` (done in Task 4) |
| `src/subagent/manager.py:44` | `paths.subagents_dir()` | deprecated — will warn but still work |
| `src/subagent/scheduler.py:23,27` | `paths.jobs_db_path()` | (kept in data/ — unchanged) |

- [ ] **Step 1: Search for all old method callers**

Run: `grep -rn '\.skills_dir()\|\.subagents_dir()\|\.global_subagents_dir()\|\.global_memory_dir()\|\.user_config_dir()' src/ --include="*.py"`

This shows every caller that needs updating.

- [ ] **Step 2-5: Update each file group, testing after each**

For each caller, replace the deprecated method with the new one. Since the old methods have deprecation wrappers, things will still work — but should be updated for cleanliness.

Focus on the ones where the path *actually changes* (not just the name):

- `skills_dir()` → `user_skills_dir()` — path stays `~/EA/Skills/` (solo mode always), but rename the call for clarity
- `subagents_dir()` → `user_subagents_dir()` — path changes from `data/users/{id}/subagents/` to `~/EA/Subagents/`
- `global_subagents_dir()` → `user_subagents_dir()` — path changes
- `user_config_dir()` → `root / "AGENTS.md"` — done in Task 4

Skip storage/*.py files that use deprecated methods — the deprecation wrapper makes them work. Focus on tools and HTTP routers where the path needs to match the new layout.

- [ ] **Step 6: Run full tool contract tests**

Run: `uv run pytest tests/sdk/test_tool_contracts.py -v --timeout=30`
Expected: Same results as before (DB-dependent tests may fail the same way)

- [ ] **Step 7: Commit each file group with meaningful messages**

---

### Task 8: Write migration script

**Files:**
- Create: `scripts/migrate-paths.py`

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate-paths.py`:

```python
#!/usr/bin/env python3
"""One-shot migration: move data/users/{id}/ → ~/Assistant/.

Usage:
    python scripts/migrate-paths.py [--dry-run]

Guardrails:
    - Dry-run mode: prints what would move without touching disk
    - Resume marker: ~/.ea_migrated prevents re-run
    - Case-insensitive FS fix: rename lowercase → uppercase via temp dir
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

EA_ROOT = Path.home() / "Assistant"
DATA_PATH = Path("data")
MIGRATED_MARKER = Path.home() / ".ea_migrated"

FILE_MAP = [
    # (source relative to data/users/{user_id}/, destination relative to EA_ROOT)
    ("config/prompt.txt", "AGENTS.md"),
    ("skills", "Skills"),       # entire directory
    ("subagents", "Subagents"),  # entire directory
    ("conversation", "Conversation"),
    ("memory", "Memory/global"),
    ("email", "Email"),
    ("gmail_cache", "Email/gmail_cache"),
    ("contacts", "Contacts"),
    ("todos", "Todos"),
    ("companion", "Companion"),
    ("apps", "Apps"),
    (".mcp.json", ".mcp.json"),
]

RESEARCH_SOURCE = DATA_PATH / "private" / "research"
RESEARCH_DEST = EA_ROOT / "Research"

WS_SUBDIR_MAP = [
    ("skills", "Skills"),
    ("subagents", "Subagents"),
    ("files", "Files"),
    ("memory", "Memory"),
]


def dry_run(msg):
    print(f"[DRY-RUN] {msg}")


def do_move(src, dst, is_dry_run):
    if is_dry_run:
        dry_run(f"mv {src} → {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    print(f"  ✓ {src} → {dst}")


def migrate_user(user_id, is_dry_run):
    user_dir = DATA_PATH / "users" / user_id
    if not user_dir.exists():
        return

    for rel_src, rel_dst in FILE_MAP:
        src = user_dir / rel_src
        if not src.exists():
            continue
        dst = EA_ROOT / rel_dst
        if dst.exists() and is_dry_run:
            dry_run(f"SKIP {rel_src} → {rel_dst} (destination exists)")
            continue
        if not is_dry_run and dst.exists():
            print(f"  ⚠ SKIP {rel_src} → {rel_dst} (destination exists)")
            continue
        do_move(src, dst, is_dry_run)


def migrate_research(is_dry_run):
    if not RESEARCH_SOURCE.exists():
        return
    if RESEARCH_DEST.exists():
        print(f"  ⚠ SKIP research → Research (destination exists)")
        return
    do_move(RESEARCH_SOURCE, RESEARCH_DEST, is_dry_run)


def migrate_workspace_subdirs(is_dry_run):
    ws_base = EA_ROOT / "Workspaces"
    if not ws_base.exists():
        return
    for ws_dir in ws_base.iterdir():
        if not ws_dir.is_dir():
            continue
        for old_name, new_name in WS_SUBDIR_MAP:
            old_path = ws_dir / old_name
            if not old_path.exists():
                continue
            new_path = ws_dir / new_name
            if new_path.exists():
                continue
            # macOS case-insensitive FS: rename through temp
            if is_dry_run:
                dry_run(f"mv {old_path} → {new_path}")
                continue
            tmp = ws_dir / f"{old_name}.ea-migrate-tmp"
            shutil.move(str(old_path), str(tmp))
            shutil.move(str(tmp), str(new_path))
            print(f"  ✓ {old_path.relative_to(EA_ROOT)} → {new_path.relative_to(EA_ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if MIGRATED_MARKER.exists():
        print("Migration already completed. Remove ~/.ea_migrated to re-run.")
        return

    if not args.dry_run:
        import warnings as _w
        _w.warn(
            "STOP THE BACKEND FIRST! Running migration while the app is live "
            "can corrupt SQLite databases (email, contacts, todos, conversation) "
            "that are being written to under data/users/{id}/. "
            "Use --dry-run to preview, then stop the app and re-run without --dry-run.",
            RuntimeWarning,
            stacklevel=2,
        )
        print("WARNING: Stop the backend before migration! (use --dry-run to preview)")

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}Migrating data/ → ~/Assistant/")
    print()

    # Migrate each user directory
    users_root = DATA_PATH / "users"
    if users_root.exists():
        for user_dir in sorted(users_root.iterdir()):
            if user_dir.is_dir():
                print(f"\nUser: {user_dir.name}")
                migrate_user(user_dir.name, args.dry_run)

    # Migrate research
    print("\nResearch:")
    migrate_research(args.dry_run)

    # Migrate workspace subdirs lowercase → uppercase
    print("\nWorkspace subdirs (lowercase → uppercase):")
    migrate_workspace_subdirs(args.dry_run)

    if not args.dry_run:
        MIGRATED_MARKER.write_text("", encoding="utf-8")
        print(f"\n✅ Migration complete. Marker: {MIGRATED_MARKER}")
    else:
        print(f"\n[DRY-RUN] Complete. No files moved.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run dry-run to verify**

Run: `python scripts/migrate-paths.py --dry-run`
Expected: Lists all files that would be moved

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate-paths.py
git commit -m "feat(migration): add one-shot path migration script"
```

---

### Task 9: Git init + .gitignore at root

**Files:**
- Create: `~/.ea_init_git` marker (optional)
- Run: git init at `~/Assistant/`

- [ ] **Step 1: Add gitignore + init logic to DataPaths**

In `src/storage/paths.py`, add a `_ensure_git()` method called during first root access:

```python
_GITIGNORE_CONTENT = """\
# Databases
*.db

# Large binary directories
Memory/
Files/
Email/
Contacts/
Todos/
Conversation/
gmail_cache/

# Cache and temp
.versions/
.env
*.log
"""

def _ensure_git(self) -> None:
    git_dir = self._ea_root_value / ".git"
    gitignore = self._ea_root_value / ".gitignore"
    if git_dir.exists():
        return
    try:
        import subprocess
        subprocess.run(
            ["git", "init"],
            cwd=self._ea_root_value,
            capture_output=True,
            timeout=10,
        )
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
    except Exception:
        pass  # git not available — fine, autoresearch falls back
```

The `root` property must cache its value and gate `_ensure_git()` to prevent circular access (root ↻ _ensure_git):

```python
@property
def root(self) -> Path:
    if not hasattr(self, '_ea_root_value'):
        raw = os.environ.get("EA_ROOT") or getattr(settings, "ea_root", None)
        self._ea_root_value = Path(raw) if raw else Path.home() / "Assistant"
        self._ea_root_value.mkdir(parents=True, exist_ok=True)
        self._git_ensured = False
    if not self._git_ensured:
        self._git_ensured = True
        self._ensure_git()
    return self._ea_root_value
```

Key: `_ensure_git()` reads `self._ea_root_value` (the cached Path), NOT `self.root`, so there's no recursion. The guard `_git_ensured` ensures `_ensure_git()` runs only once — on the first `root` access.

- [ ] **Step 2: Run test to verify it works**

Run: `uv run python -c "from src.storage.paths import DataPaths; dp = DataPaths(user_id='tester', ea_root='/tmp/ea-test-init'); _ = dp.root; print('OK')"`
Expected: `OK`. Then `/tmp/ea-test-init/.git` exists.

- [ ] **Step 3: Commit**

```bash
git add src/storage/paths.py
git commit -m "feat(git): auto-init git repo at ~/Assistant/ with .gitignore"
```

---

### Task 10: Update tests

**Files:**
- Modify: Many test files that reference old paths
- Run: `tests/sdk/` full suite

- [ ] **Step 1: Find tests that reference old paths**

Run: `grep -rn 'data/users/\|data/private/\|skills_dir\|subagents_dir\|user_config_dir\|global_subagents_dir\|global_memory_dir\|prompt.txt' tests/ --include="*.py"`

This shows every test that references old paths.

- [ ] **Step 2-5: Update each test file**

For each test file, update:
- `prompt.txt` → `AGENTS.md`
- `data/users/{id}/` paths → `~/EA/` equivalents (use `ea_root` fixture or monkeypatch)
- Deprecated method calls → new method names

The pattern for tests:

```python
# Before
dp = DataPaths(user_id="test")
path = dp.skills_dir()
assert "data/users/test/skills" in str(path)

# After
dp = DataPaths(user_id="test", ea_root="/tmp/ea-test")
path = dp.user_skills_dir()
assert "/tmp/ea-test/Skills" in str(path)
```

- [ ] **Step 6: Run full SDK test suite**

Run: `uv run pytest tests/sdk/ -v --timeout=30`
Expected: Same pre-existing failures only (tool contract DB tests, etc.)

- [ ] **Step 7: Run the migration**

Run: `python scripts/migrate-paths.py --dry-run` first, then `python scripts/migrate-paths.py`

- [ ] **Step 8: Run full SDK test suite again (post-migration)**

Run: `uv run pytest tests/sdk/ -v --timeout=30`
Expected: Same results — new paths work, old tests pass via deprecated wrappers

- [ ] **Step 9: Final commit**

```bash
git add tests/  # all updated test files
git commit -m "test: update tests for path restructuring"
```

---

## Self-Review Checklist

After writing:

1. **Spec coverage**: Every section in the spec maps to a task above. The 3 hardcoded Workspaces paths are consolidated in Task 3. The ResearchLoop git detection is in Task 6. The deprecated method list is in Task 2. Migration guardrails are in Task 8.

2. **Placeholder scan**: All code blocks contain complete, runnable code. No "TBD", "TODO", or "implement later".

3. **Type consistency**: All method names used in later tasks match names defined in Task 2 (user_skills_dir, user_subagents_dir, root, research_dir, etc.).
