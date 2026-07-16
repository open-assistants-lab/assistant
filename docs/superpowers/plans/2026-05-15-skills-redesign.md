# Skills System Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace-aware skill resolution (workspace > user), a `skill_delete` tool, new REST endpoints (GET detail, PUT update), Pydantic response models, and a Flutter skills panel in the RHS sidebar.

**Architecture:** Registry gains workspace_id parameter and merges user + workspace skills. REST API adds scope fields and two new endpoints. Flutter adds a Skills tab sibling to Files in the RHS panel with merged list, create/edit/delete flows.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, Flutter/Dart, sqlite3 (existing HybridDB not needed for v1)

---

### Task 1: Registry — Workspace-Aware Resolution

**Files:**
- Modify: `src/skills/registry.py`
- Modify: `src/skills/models.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/sdk/test_skills_registry.py`:

```python
"""Tests for workspace-aware skill registry."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.skills.models import parse_skill_file
from src.skills.registry import SkillRegistry, get_skill_registry, reset_skill_registries


def test_registry_workspace_override_user_by_name():
    """Workspace skill with same name overrides user skill."""
    with tempfile.TemporaryDirectory() as user_d, tempfile.TemporaryDirectory() as ws_d:
        # Write user skill
        usp = Path(user_d) / "greeting" / "SKILL.md"
        usp.parent.mkdir(parents=True)
        usp.write_text("""---
name: greeting
description: User-level greeting skill
---
# User greeting
Say hello from user scope.""")

        # Write workspace skill (same name, different content)
        wsp = Path(ws_d) / "greeting" / "SKILL.md"
        wsp.parent.mkdir(parents=True)
        wsp.write_text("""---
name: greeting
description: Workspace-level greeting skill
---
# Workspace greeting
Say hello from workspace scope.""")

        registry = SkillRegistry(skills_dir=user_d, workspace_skills_dir=ws_d)
        skills = registry.get_all_skills()

        names = [s["name"] for s in skills]
        assert "greeting" in names
        assert len([s for s in skills if s["name"] == "greeting"]) == 1

        greeting = next(s for s in skills if s["name"] == "greeting")
        assert greeting["content"] == "Say hello from workspace scope."
        assert greeting.get("metadata", {}).get("scope") == "workspace"


def test_registry_scope_fields_populated():
    """User skills get scope=user, workspace skills get scope=workspace."""
    with tempfile.TemporaryDirectory() as user_d, tempfile.TemporaryDirectory() as ws_d:
        (Path(user_d) / "alpha").mkdir(parents=True)
        (Path(user_d) / "alpha" / "SKILL.md").write_text("""---
name: alpha
description: User only
---
Content A""")

        (Path(ws_d) / "beta").mkdir(parents=True)
        (Path(ws_d) / "beta" / "SKILL.md").write_text("""---
name: beta
description: Workspace only
---
Content B""")

        registry = SkillRegistry(skills_dir=user_d, workspace_skills_dir=ws_d)
        skills = registry.get_all_skills()

        alpha = next(s for s in skills if s["name"] == "alpha")
        beta = next(s for s in skills if s["name"] == "beta")

        assert alpha.get("metadata", {}).get("scope") == "user"
        assert beta.get("metadata", {}).get("scope") == "workspace"


def test_get_skill_registry_cached_by_user_and_workspace():
    """Factory caches per (user_id, workspace_id)."""
    reset_skill_registries()
    r1 = get_skill_registry(user_id="alice", workspace_id="personal")
    r2 = get_skill_registry(user_id="alice", workspace_id="personal")
    r3 = get_skill_registry(user_id="alice", workspace_id="project-x")
    r4 = get_skill_registry(user_id="bob", workspace_id="personal")

    assert r1 is r2
    assert r1 is not r3
    assert r1 is not r4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sdk/test_skills_registry.py -v`
Expected: FAIL — `SkillRegistry` doesn't accept `workspace_skills_dir`

- [ ] **Step 3: Update registry to accept workspace_skills_dir**

Edit `src/skills/registry.py`:

```python
"""Skill registry — unified storage for all skills.

Bundled seed skills (src/skills_seed/) are seeded to the user's skills directory on first
run. After seeding, all skills live in user or workspace directories. Workspace skills
override user skills by name.
"""

import threading
from pathlib import Path

from src.skills.models import Skill
from src.skills.storage import SkillStorage

_registries: dict[str, "SkillRegistry"] = {}
_lock = threading.Lock()


def get_skill_registry(
    user_id: str = "default_user", workspace_id: str = "personal"
) -> "SkillRegistry":
    """Get or create a cached SkillRegistry for a user+workspace pair.

    All code should use this factory instead of constructing SkillRegistry
    directly, to ensure a single cached instance per (user_id, workspace_id).
    """
    uid = user_id or "default_user"
    wid = workspace_id or "personal"
    cache_key = f"{uid}:{wid}"
    with _lock:
        if cache_key not in _registries:
            _registries[cache_key] = SkillRegistry(
                user_id=uid, workspace_id=wid
            )
        return _registries[cache_key]


def reset_skill_registries() -> None:
    """Clear all cached registries (useful for testing)."""
    with _lock:
        _registries.clear()


class SkillRegistry:
    """Registry for skills across user and workspace scopes.

    Workspace skills override user skills by name.
    On first run, bundled seed skills are seeded from src/skills_seed/ to
    the user's skills directory.
    """

    def __init__(
        self,
        skills_dir: str | Path | None = None,
        workspace_skills_dir: str | Path | None = None,
        user_id: str | None = None,
        workspace_id: str = "personal",
    ):
        from src.storage.paths import DataPaths

        paths = DataPaths(user_id=user_id, workspace_id=workspace_id)
        self.workspace_id = workspace_id

        self.skills_dir = Path(skills_dir) if skills_dir else paths.skills_dir()
        self.storage = SkillStorage(self.skills_dir)

        if workspace_skills_dir:
            self.workspace_skills_dir = Path(workspace_skills_dir)
        else:
            self.workspace_skills_dir = paths.workspace_skills_dir()

        self.ws_storage = SkillStorage(self.workspace_skills_dir)
        self._loaded_skills: set[str] = set()
        self._seeded = False

    def _seed_system_skills(self) -> None:
        """Copy bundled seed skills to user skills directory on first run."""
        if self._seeded:
            return
        self._seeded = True

        import shutil

        system_src = Path("src/skills_seed")
        if not system_src.exists():
            return

        self.skills_dir.mkdir(parents=True, exist_ok=True)

        for item in system_src.iterdir():
            if not item.is_dir():
                continue
            dest = self.skills_dir / item.name
            if not dest.exists():
                shutil.copytree(item, dest)

    def reload(self) -> None:
        """Reload all skills (clear cache, re-seed system skills)."""
        self._seeded = False

    def mark_skill_loaded(self, skill_name: str) -> None:
        """Track that a skill has been loaded into context."""
        self._loaded_skills.add(skill_name)

    def get_loaded_skills(self) -> list[str]:
        """Get list of skills loaded in current session."""
        return list(self._loaded_skills)

    def get_all_skills(self) -> list[Skill]:
        """Get all available skills, merged (workspace overrides user by name)."""
        self._seed_system_skills()
        user_skills = {s["name"]: s for s in self.storage.load_skills()}

        for s in user_skills.values():
            if "metadata" not in s:
                s["metadata"] = {}
            s["metadata"]["scope"] = "user"
            s["metadata"]["workspace_id"] = ""

        ws_skills_raw = self.ws_storage.load_skills()
        ws_skills = {}
        for s in ws_skills_raw:
            if "metadata" not in s:
                s["metadata"] = {}
            s["metadata"]["scope"] = "workspace"
            s["metadata"]["workspace_id"] = self.workspace_id
            ws_skills[s["name"]] = s

        merged = {**user_skills, **ws_skills}
        return list(merged.values())

    def get_skill(self, skill_name: str) -> Skill | None:
        """Get a specific skill by name (workspace overrides user)."""
        self._seed_system_skills()

        ws_skill = self.ws_storage.load_skill(skill_name)
        if ws_skill:
            if "metadata" not in ws_skill:
                ws_skill["metadata"] = {}
            ws_skill["metadata"]["scope"] = "workspace"
            ws_skill["metadata"]["workspace_id"] = self.workspace_id
            return ws_skill

        user_skill = self.storage.load_skill(skill_name)
        if user_skill:
            if "metadata" not in user_skill:
                user_skill["metadata"] = {}
            user_skill["metadata"]["scope"] = "user"
            user_skill["metadata"]["workspace_id"] = ""
            return user_skill

        return None

    def list_skills(self) -> list[str]:
        """List all available skill names."""
        skills = self.get_all_skills()
        return [s["name"] for s in skills]

    def search_skills(self, query: str) -> list[Skill]:
        """Search for skills matching a query string."""
        query_lower = query.lower()
        all_skills = self.get_all_skills()
        return [
            s
            for s in all_skills
            if query_lower in s["name"].lower()
            or query_lower in s.get("description", "").lower()
            or query_lower in s.get("content", "").lower()
        ]

    def get_skill_descriptions(self, include_disabled: bool = False) -> list[str]:
        """Get formatted skill descriptions for system prompt.

        Args:
            include_disabled: If True, include skills with disable_model_invocation.
                              If False, exclude them from the agent's discovery list.
        """
        from src.skills.models import skill_to_system_prompt_entry

        skills = self.get_all_skills()
        if not include_disabled:
            skills = [
                s for s in skills
                if not s.get("metadata", {}).get("disable_model_invocation", "").lower() in ("true", "1", "yes")
            ]
        return [skill_to_system_prompt_entry(s) for s in skills]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_skills_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run existing skill tests**

Run: `pytest tests/sdk/test_tool_contracts.py -v -k skills`
Expected: Existing skills tests still PASS (may need small adjustments)

- [ ] **Step 6: Commit**

```bash
git add src/skills/registry.py tests/sdk/test_skills_registry.py
git commit -m "feat: add workspace-aware skill registry with scope resolution"
```

---

### Task 2: SDK Tools — workspace_id + skill_delete

**Files:**
- Modify: `src/sdk/tools_core/skills.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/sdk/test_skills_tools.py`:

```python
"""Tests for workspace-aware skill tools."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.sdk.tools_core.skills import (
    _get_registry,
    skills_list,
    skills_search,
    skills_load,
    skill_create,
    skill_delete,
    skills_cache,
)


def test_skills_list_includes_scope_fields():
    """skills_list returns scope and workspace_id per skill."""
    with tempfile.TemporaryDirectory() as user_d, tempfile.TemporaryDirectory() as ws_d:
        (Path(user_d) / "demo" / "SKILL.md").mkdir(parents=True)
        (Path(user_d) / "demo" / "SKILL.md").write_text("""---
name: demo
description: A demo skill
---
# Demo""")

        registry = _get_registry(user_id="test", workspace_id="ws1")
        # Override registry with temp dirs
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(skills_dir=user_d, workspace_skills_dir=ws_d, workspace_id="ws1")
        _patch_key = "test:ws1"
        import src.sdk.tools_core.skills as sk
        sk.skills_cache[_patch_key] = reg

        result = skills_list.invoke({"user_id": "test", "workspace_id": "ws1"})

        assert "demo" in result
        assert "scope" in result.lower() or "user" in result.lower()


def test_skill_delete_removes_directory():
    """skill_delete removes the skill directory and reloads registry."""
    with tempfile.TemporaryDirectory() as user_d:
        sd = Path(user_d) / "tmp-skill"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("""---
name: tmp-skill
description: Temporary skill
---
# Temp""")

        registry = _get_registry(user_id="test_del", workspace_id="")
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(skills_dir=user_d, workspace_id="")
        _patch_key = "test_del:personal"
        import src.sdk.tools_core.skills as sk
        sk.skills_cache[_patch_key] = reg

        # Verify it exists
        assert len(reg.get_all_skills()) == 1

        # Delete it
        result = skill_delete.invoke(
            {"skill_name": "tmp-skill", "scope": "user", "user_id": "test_del"}
        )

        assert "deleted" in result.lower() or "success" in result.lower()
        assert not sd.exists()


@pytest.fixture(autouse=True)
def _clear_cache():
    import src.sdk.tools_core.skills as sk
    sk.skills_cache.clear()
    yield
    sk.skills_cache.clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sdk/test_skills_tools.py -v`
Expected: FAIL — `skill_delete` doesn't exist, `workspace_id` not wired

- [ ] **Step 3: Update `src/sdk/tools_core/skills.py`**

```python
"""Skills tools -- SDK-native implementation.

Skills are on-demand knowledge modules (SKILL.md files) that agents can
load when handling specific task types.

Design:
  1. Skill descriptions are injected into the system prompt at startup.
     The agent always knows what skills are available -- no discovery step needed.
  2. When a task matches a skill's description, call skills_load(name) directly.
  3. skills_list() and skills_search() are available for explicit queries
     (e.g., "what skills do you have?" or finding recently added user skills).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.skills.models import _is_valid_skill_name
from src.skills.registry import SkillRegistry, get_skill_registry

logger = get_logger()

skills_cache: dict[str, SkillRegistry] = {}


def _get_registry(user_id: str = "default_user", workspace_id: str = "personal") -> SkillRegistry:
    """Get or create cached registry for (user_id, workspace_id)."""
    cache_key = f"{user_id}:{workspace_id}"
    if cache_key not in skills_cache:
        skills_cache[cache_key] = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    return skills_cache[cache_key]


@tool
def skills_list(user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """List all available skills (user + workspace, merged); use skills_load for full instructions.

    Skill descriptions are always available in your system prompt context.
    Call this explicitly when the user asks "what skills do you have?" or
    when you want to see user-created skills that may have been added recently.

    Args:
        user_id: User identifier
        workspace_id: Current workspace identifier

    Returns:
        List of available skills with names, descriptions, and scopes
    """
    registry = _get_registry(user_id, workspace_id)
    skills = registry.get_all_skills()

    if not skills:
        return "No skills available."

    lines = ["Available skills:\n"]
    for skill in skills:
        scope = skill.get("metadata", {}).get("scope", "user")
        lines.append(f"  - {skill['name']} [{scope}]: {skill['description']}")

    lines.append("\nUse skills_load(skill_name) to get detailed instructions.")
    return "\n".join(lines)


skills_list.annotations = ToolAnnotations(title="List Skills", read_only=True, idempotent=True)


@tool
def skills_search(query: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Search for skills matching a query across both user and workspace scopes.

    Use this when you're looking for skills related to a specific task or topic
    but don't know the exact skill name.

    Args:
        query: Search terms (e.g., 'research', 'sql', 'browser')
        user_id: User identifier
        workspace_id: Current workspace identifier

    Returns:
        Matching skills with names and descriptions
    """
    registry = _get_registry(user_id, workspace_id)
    skills = registry.search_skills(query)

    if not skills:
        all_names = ", ".join(registry.list_skills())
        return f"No skills matching '{query}'. Available skills: {all_names}"

    lines = [f"Skills matching '{query}':\n"]
    for skill in skills:
        scope = skill.get("metadata", {}).get("scope", "user")
        lines.append(f"  - {skill['name']} [{scope}]: {skill['description']}")

    lines.append("\nUse skills_load(skill_name) to get detailed instructions.")
    return "\n".join(lines)


skills_search.annotations = ToolAnnotations(title="Search Skills", read_only=True, idempotent=True)


@tool
def skills_load(skill_name: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Load the full content of a skill into the agent's context.

    Resolves through merged registry (workspace overrides user by name).

    Args:
        skill_name: The name of the skill to load (e.g., 'skill-creator')
        user_id: User identifier
        workspace_id: Current workspace identifier

    Returns:
        Full skill content, or error message if not found
    """
    registry = _get_registry(user_id, workspace_id)

    skill = registry.get_skill(skill_name)

    if not skill:
        available = ", ".join(registry.list_skills())
        return f"Skill '{skill_name}' not found. Available skills: {available}"

    registry.mark_skill_loaded(skill_name)

    scope = skill.get("metadata", {}).get("scope", "user")
    header = f"# {skill['name']} [{scope}]\n\n"
    return header + skill["content"]


skills_load.annotations = ToolAnnotations(title="Load Skill", read_only=True, idempotent=True)


@tool
def skill_create(
    name: str,
    content: str,
    scope: str = "user",
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Create a new skill in user or workspace scope.

    This tool automatically saves to the correct directory from config.
    Use this instead of files_write for skill creation.

    Args:
        name: Skill name (e.g., 'my-skill')
        content: Full SKILL.md content including YAML frontmatter
        scope: Where to create -- 'user' (available everywhere) or 'workspace' (current workspace only)
        user_id: User identifier
        workspace_id: Current workspace identifier

    Returns:
        Success or error message
    """
    if not _is_valid_skill_name(name):
        return (
            f"Invalid skill name: '{name}'. "
            "Must be 1-64 chars, lowercase letters/digits/hyphens only, "
            "no leading/trailing hyphens, no consecutive hyphens."
        )

    from src.storage.paths import get_paths

    paths = get_paths(user_id, workspace_id=workspace_id)

    if scope == "workspace":
        target_dir = str(paths.workspace_skills_dir())
    else:
        target_dir = str(paths.skills_dir())

    skill_path = Path(target_dir) / name / "SKILL.md"

    resolved = skill_path.resolve()
    skills_root = Path(target_dir).resolve()
    if not resolved.is_relative_to(skills_root):
        return f"Invalid skill name: '{name}' resolves outside skills directory."

    try:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")

        registry = _get_registry(user_id, workspace_id)
        registry.reload()

        logger.info(
            "skill.created",
            {"name": name, "scope": scope, "path": str(skill_path), "size": len(content)},
            user_id=user_id,
        )

        return f"Successfully created skill '{name}' in {scope} scope at {skill_path}"
    except Exception as e:
        logger.error("skill.create.error", {"name": name, "error": str(e)}, user_id=user_id)
        return f"Error creating skill: {e}"


skill_create.annotations = ToolAnnotations(title="Create Skill", destructive=True)


@tool
def skill_delete(
    skill_name: str,
    scope: str = "user",
    user_id: str = "default_user",
    workspace_id: str = "personal",
) -> str:
    """Delete a skill from user or workspace scope.

    Args:
        skill_name: Name of the skill to delete
        scope: Which scope to delete from -- 'user' or 'workspace'
        user_id: User identifier
        workspace_id: Current workspace identifier

    Returns:
        Success or error message
    """
    from src.storage.paths import get_paths

    paths = get_paths(user_id, workspace_id=workspace_id)

    if scope == "workspace":
        target_dir = paths.workspace_skills_dir()
    else:
        target_dir = paths.skills_dir()

    skill_d = Path(target_dir) / skill_name

    if not skill_d.exists():
        return f"Skill '{skill_name}' not found in {scope} scope."

    resolved = skill_d.resolve()
    root = Path(target_dir).resolve()
    if not resolved.is_relative_to(root):
        return f"Path traversal blocked for '{skill_name}'."

    try:
        shutil.rmtree(skill_d)
        registry = _get_registry(user_id, workspace_id)
        registry.reload()
        logger.info("skill.deleted", {"name": skill_name, "scope": scope}, user_id=user_id)
        return f"Successfully deleted skill '{skill_name}' from {scope} scope."
    except Exception as e:
        logger.error("skill.delete.error", {"name": skill_name, "error": str(e)}, user_id=user_id)
        return f"Error deleting skill: {e}"


skill_delete.annotations = ToolAnnotations(title="Delete Skill", destructive=True)


@tool
def sql_write_query(query: str, database: str, user_id: str = "default_user") -> str:
    """Write and validate a SQL query for a specific database.

    The required skill must be loaded first using skills_load.

    Args:
        query: The SQL query to validate
        database: Database name (e.g., 'sql-analytics', 'inventory')
        user_id: User identifier

    Returns:
        Validated query or error
    """
    registry = _get_registry(user_id)
    skills_loaded = list(registry._loaded_skills) if hasattr(registry, "_loaded_skills") else []

    if database not in skills_loaded:
        return (
            f"Error: You must load the '{database}' skill first. "
            f"Use skills_load('{database}') to load the database schema."
        )

    return f"SQL Query for {database}:\n\n```sql\n{query}\n```\n\nQuery validated against {database} schema"


sql_write_query.annotations = ToolAnnotations(title="Write SQL Query", open_world=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_skills_tools.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run all existing SDK tests**

Run: `pytest tests/sdk/ -v`
Expected: Existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/tools_core/skills.py tests/sdk/test_skills_tools.py
git commit -m "feat: add workspace_id to skill tools, add skill_delete tool"
```

---

### Task 3: Runner — Wire workspace_id to skills context

**Files:**
- Modify: `src/sdk/runner.py`

- [ ] **Step 1: Update `_get_skills_context` to accept workspace_id**

Edit `src/sdk/runner.py` lines 86-106:

```python
def _get_skills_context(user_id: str, workspace_id: str = "personal") -> str:
    """Build a concise skills reference for the system prompt."""
    try:
        from src.skills.registry import get_skill_registry

        registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
        skills = registry.get_all_skills()
        if not skills:
            return ""

        # Exclude skills with disable_model_invocation
        visible_skills = [
            s for s in skills
            if not s.get("metadata", {}).get("disable_model_invocation", "").lower()
            in ("true", "1", "yes")
        ]

        if not visible_skills:
            return ""

        lines = ["\n\n## Available Skills"]
        lines.append(
            "When a task matches a skill description below, call skills_load(name) "
            "first to get detailed instructions before proceeding. Do NOT call "
            "skills_list — descriptions are already here."
        )
        lines.append("")
        for s in visible_skills:
            name = s.get("name", "")
            desc = s.get("description", "")
            scope = s.get("metadata", {}).get("scope", "user")
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)
    except Exception:
        return ""
```

Edit `_get_system_prompt` (line 56-66):

```python
def _get_system_prompt(user_id: str, workspace_id: str | None = None) -> str:
    settings = get_settings()
    base_prompt = getattr(settings.agent, "system_prompt", "You are a helpful assistant.")

    w_id = workspace_id or "personal"

    # Inject available skills
    skills_context = _get_skills_context(user_id, w_id)

    # Inject workspace context
    workspace_context = _get_workspace_context(workspace_id)

    return base_prompt + skills_context + workspace_context + f"\n\nuser_id: {user_id}"
```

- [ ] **Step 2: Run existing tests**

Run: `pytest tests/sdk/ -v`
Expected: All SDK tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/sdk/runner.py
git commit -m "feat: wire workspace_id through skills context in runner"
```

---

### Task 4: REST API — New endpoints + Pydantic models

**Files:**
- Modify: `src/http/routers/skills.py` (full rewrite)
- Create: `src/http/models/skill_models.py`

- [ ] **Step 1: Write the failing API tests**

Create `tests/api/test_skills_api.py`:

```python
"""Tests for skills REST API endpoints."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def skills_client():
    """Create test client with mocked skills dirs."""
    from src.http.main import app

    with tempfile.TemporaryDirectory() as user_d, tempfile.TemporaryDirectory() as ws_d:
        # Write a user skill
        usp = Path(user_d) / "user-skill" / "SKILL.md"
        usp.parent.mkdir(parents=True)
        usp.write_text("""---
name: user-skill
description: A user-scoped skill
---
# User Skill
User content.""")

        # Write a workspace skill
        wsp = Path(ws_d) / "ws-skill" / "SKILL.md"
        wsp.parent.mkdir(parents=True)
        wsp.write_text("""---
name: ws-skill
description: A workspace-scoped skill
---
# WS Skill
Workspace content.""")

        # Mock get_skill_registry to use temp dirs
        with patch("src.http.routers.skills.get_skill_registry") as mock_factory:
            from src.skills.registry import SkillRegistry
            def _make_registry(user_id="default_user", workspace_id="personal"):
                return SkillRegistry(
                    skills_dir=user_d,
                    workspace_skills_dir=ws_d,
                    workspace_id=workspace_id,
                )
            mock_factory.side_effect = _make_registry

            client = TestClient(app)
            yield client


def test_list_skills_returns_all(skills_client):
    r = skills_client.get("/skills?user_id=test&workspace_id=t1")
    assert r.status_code == 200
    data = r.json()
    assert "skills" in data
    names = [s["name"] for s in data["skills"]]
    assert "user-skill" in names
    assert "ws-skill" in names


def test_list_skills_has_scope_fields(skills_client):
    r = skills_client.get("/skills?user_id=test&workspace_id=t1")
    data = r.json()
    user_skill = next(s for s in data["skills"] if s["name"] == "user-skill")
    ws_skill = next(s for s in data["skills"] if s["name"] == "ws-skill")
    assert user_skill.get("scope") == "user"
    assert ws_skill.get("scope") == "workspace"


def test_get_skill_detail(skills_client):
    r = skills_client.get("/skills/user-skill?user_id=test&workspace_id=t1")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "user-skill"
    assert "User content" in data["content"]
    assert data["scope"] == "user"


def test_get_skill_not_found(skills_client):
    r = skills_client.get("/skills/nonexistent?user_id=test&workspace_id=t1")
    assert r.status_code == 404


def test_put_skill_updates_content(skills_client):
    r = skills_client.put(
        "/skills/user-skill?user_id=test&workspace_id=t1",
        json={"description": "Updated description", "content": "---\nname: user-skill\ndescription: Updated\n---\n# Updated\nNew body."},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["description"] == "Updated"


def test_delete_skill(skills_client):
    r = skills_client.delete("/skills/user-skill?user_id=test&workspace_id=t1&scope=user")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "deleted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_skills_api.py -v`
Expected: FAIL — endpoints don't exist

- [ ] **Step 3: Create Pydantic models**

Create `src/http/models/skill_models.py`:

```python
"""Pydantic models for skill API responses."""

from pydantic import BaseModel, Field


class SkillResponse(BaseModel):
    """Summary of a skill (used in list responses)."""

    name: str
    description: str
    scope: str = Field(default="user", description="user | workspace")
    workspace_id: str | None = Field(default=None)
    is_loaded: bool = Field(default=False)
    disable_model_invocation: bool = Field(default=False)

    class Config:
        from_attributes = True


class SkillDetailResponse(SkillResponse):
    """Full skill including markdown body."""

    content: str = Field(description="Full markdown body of the skill")
    metadata: dict[str, str] = Field(default_factory=dict)


class SkillCreateRequest(BaseModel):
    """Request body for creating a skill."""

    name: str
    description: str
    content: str
    scope: str = Field(default="user", pattern=r"^(user|workspace)$")
    workspace_id: str | None = Field(default=None)


class SkillUpdateRequest(BaseModel):
    """Request body for updating a skill."""

    description: str | None = None
    content: str | None = None
    disable_model_invocation: bool | None = None
```

- [ ] **Step 4: Rewrite skills router**

Edit `src/http/routers/skills.py`:

```python
"""Skills REST API — CRUD with workspace scoping."""

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreateBody(BaseModel):
    name: str
    description: str
    content: str
    scope: str = "user"


class SkillUpdateBody(BaseModel):
    description: str | None = None
    content: str | None = None
    disable_model_invocation: bool | None = None


def _resolve_skill_path(
    name: str, scope: str, user_id: str, workspace_id: str = "personal"
) -> Path:
    """Get the path to a skill's SKILL.md file."""
    from src.storage.paths import get_paths

    paths = get_paths(user_id, workspace_id=workspace_id)
    if scope == "workspace":
        return paths.workspace_skills_dir() / name / "SKILL.md"
    return paths.skills_dir() / name / "SKILL.md"


@router.get("")
async def list_skills(
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
):
    """List all available skills with scope metadata."""
    from src.skills.registry import get_skill_registry

    registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    all_skills = registry.get_all_skills()
    loaded_names = set(registry.get_loaded_skills())

    skills = []
    for s in all_skills:
        md = s.get("metadata", {}) or {}
        skills.append(
            {
                "name": s["name"],
                "description": s["description"],
                "scope": md.get("scope", "user"),
                "workspace_id": md.get("workspace_id", None),
                "is_loaded": s["name"] in loaded_names,
                "disable_model_invocation": md.get("disable_model_invocation", "").lower()
                in ("true", "1", "yes"),
            }
        )

    return {"skills": skills}


@router.get("/{skill_name}")
async def get_skill(
    skill_name: str,
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
):
    """Get full detail for a single skill."""
    from src.skills.registry import get_skill_registry

    registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    skill = registry.get_skill(skill_name)

    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    md = skill.get("metadata", {}) or {}
    return {
        "name": skill["name"],
        "description": skill["description"],
        "content": skill["content"],
        "scope": md.get("scope", "user"),
        "workspace_id": md.get("workspace_id", None),
        "is_loaded": skill_name in registry.get_loaded_skills(),
        "disable_model_invocation": md.get("disable_model_invocation", "").lower()
        in ("true", "1", "yes"),
        "metadata": md,
    }


@router.post("")
async def create_skill(body: SkillCreateBody, user_id: str = Query("default_user"), workspace_id: str = Query("personal")):
    """Create a new skill in user or workspace scope."""
    from src.skills.models import _is_valid_skill_name
    from src.skills.registry import get_skill_registry

    if not _is_valid_skill_name(body.name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid skill name '{body.name}'. Must be [a-z0-9]+(-[a-z0-9]+)*",
        )

    skill_path = _resolve_skill_path(body.name, body.scope, user_id, workspace_id)
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(body.content, encoding="utf-8")

    registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    registry.reload()

    return {"status": "created", "name": body.name, "scope": body.scope}


@router.put("/{skill_name}")
async def update_skill(
    skill_name: str,
    body: SkillUpdateBody,
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
    scope: str = Query("user"),
):
    """Update a skill's description, content, or disable_model_invocation flag."""
    skill_path = _resolve_skill_path(skill_name, scope, user_id, workspace_id)

    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found in {scope} scope")

    current = skill_path.read_text(encoding="utf-8")

    if body.content is not None:
        # Replace entire file
        skill_path.write_text(body.content, encoding="utf-8")
    elif body.description is not None:
        # Update just description in frontmatter
        import re
        new_desc = body.description.replace("\\", "\\\\").replace(
            "description:", "description\\:"
        )
        current = re.sub(
            r"(description:\s*).+",
            rf"\1{new_desc}",
            current,
            count=1,
        )
        skill_path.write_text(current, encoding="utf-8")

    if body.disable_model_invocation is not None:
        # Toggle in metadata section
        current = skill_path.read_text(encoding="utf-8")
        val = "true" if body.disable_model_invocation else "false"
        if "disable_model_invocation" in current:
            import re
            current = re.sub(
                r"disable_model_invocation:\s*(true|false)",
                f"disable_model_invocation: {val}",
                current,
            )
        else:
            # Add to metadata or top-level
            parts = current.split("---", 2)
            if len(parts) >= 3:
                front = parts[1]
                if "metadata:" in front:
                    parts[1] = front.rstrip() + f"\n    disable_model_invocation: {val}"
                else:
                    parts[1] = front.rstrip() + f"\ndisable-model-invocation: {val}"
                current = "---".join(parts)
        skill_path.write_text(current, encoding="utf-8")

    from src.skills.registry import get_skill_registry
    registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    registry.reload()

    return {"status": "updated", "name": skill_name}


@router.delete("/{skill_name}")
async def delete_skill(
    skill_name: str,
    user_id: str = Query("default_user"),
    workspace_id: str = Query("personal"),
    scope: str = Query("user"),
):
    """Delete a skill from user or workspace scope."""
    skill_dir = _resolve_skill_path(skill_name, scope, user_id, workspace_id).parent

    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found in {scope} scope")

    shutil.rmtree(skill_dir)

    from src.skills.registry import get_skill_registry
    registry = get_skill_registry(user_id=user_id, workspace_id=workspace_id)
    registry.reload()

    return {"status": "deleted", "name": skill_name, "scope": scope}
```

- [ ] **Step 5: Run API tests to verify they pass**

Run: `pytest tests/api/test_skills_api.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add src/http/routers/skills.py src/http/models/skill_models.py tests/api/test_skills_api.py
git commit -m "feat: add skill detail, update, and delete API endpoints with Pydantic models"
```

---

### Task 5: Flutter — Skills Panel in RHS

**Files:**
- Create: `flutter_app/lib/widgets/skills_panel.dart`
- Modify: `flutter_app/lib/screens/desktop_layout.dart` (or the desktop layout widget)
- Modify: `flutter_app/lib/services/api_client.dart`

- [ ] **Step 1: Extend API client**

Edit `flutter_app/lib/services/api_client.dart` — add to the class:

```dart
  Future<Map<String, dynamic>> getSkillDetail(
    String name, {
    String? workspaceId,
  }) async {
    final params = <String, String>{};
    if (workspaceId != null) params['workspace_id'] = workspaceId;
    final uri = Uri.parse('$baseUrl/skills/$name')
        .replace(queryParameters: params);
    final response = await http.get(uri, headers: _headers);
    if (response.statusCode == 200) {
      return json.decode(response.body) as Map<String, dynamic>;
    }
    throw HttpException('Failed to load skill detail: ${response.statusCode}');
  }

  Future<void> updateSkill(
    String name,
    Map<String, dynamic> body, {
    String? workspaceId,
    String scope = 'user',
  }) async {
    final params = <String, String>{'scope': scope};
    if (workspaceId != null) params['workspace_id'] = workspaceId;
    final uri = Uri.parse('$baseUrl/skills/$name')
        .replace(queryParameters: params);
    final response = await http.put(uri, headers: _headers, body: json.encode(body));
    if (response.statusCode != 200) {
      throw HttpException('Failed to update skill: ${response.statusCode}');
    }
  }

  Future<void> deleteSkill(
    String name, {
    String? workspaceId,
    String scope = 'user',
  }) async {
    final params = <String, String>{'scope': scope};
    if (workspaceId != null) params['workspace_id'] = workspaceId;
    final uri = Uri.parse('$baseUrl/skills/$name')
        .replace(queryParameters: params);
    final response = await http.delete(uri, headers: _headers);
    if (response.statusCode != 200) {
      throw HttpException('Failed to delete skill: ${response.statusCode}');
    }
  }
```

Update `listSkills` to accept `workspaceId`:

```dart
  Future<List<dynamic>> listSkills({String? workspaceId}) async {
    final params = <String, String>{};
    if (workspaceId != null) params['workspace_id'] = workspaceId;
    final uri = Uri.parse('$baseUrl/skills')
        .replace(queryParameters: params);
    final response = await http.get(uri, headers: _headers);
    if (response.statusCode == 200) {
      final data = json.decode(response.body) as Map<String, dynamic>;
      return (data['skills'] as List<dynamic>?) ?? [];
    }
    return [];
  }
```

- [ ] **Step 2: Create SkillsPanel widget**

Create `flutter_app/lib/widgets/skills_panel.dart`:

```dart
import 'package:flutter/material.dart';
import '../services/api_client.dart';

class SkillsPanel extends StatefulWidget {
  final String? workspaceId;
  final ApiClient apiClient;

  const SkillsPanel({
    super.key,
    this.workspaceId,
    required this.apiClient,
  });

  @override
  State<SkillsPanel> createState() => _SkillsPanelState();
}

class _SkillsPanelState extends State<SkillsPanel> {
  List<dynamic> _skills = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadSkills();
  }

  @override
  void didUpdateWidget(covariant SkillsPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.workspaceId != widget.workspaceId) {
      _loadSkills();
    }
  }

  Future<void> _loadSkills() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final skills = await widget.apiClient.listSkills(
        workspaceId: widget.workspaceId,
      );
      if (mounted) {
        setState(() {
          _skills = skills;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _loading = false;
        });
      }
    }
  }

  Future<void> _deleteSkill(String name, String scope) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete Skill'),
        content: Text('Delete "$name" from $scope scope?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
        ],
      ),
    );

    if (confirm == true) {
      try {
        await widget.apiClient.deleteSkill(
          name,
          workspaceId: widget.workspaceId,
          scope: scope,
        );
        _loadSkills();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Failed to delete: $e')),
          );
        }
      }
    }
  }

  void _showCreateDialog() {
    final nameCtl = TextEditingController();
    final descCtl = TextEditingController();
    final bodyCtl = TextEditingController();
    String scope = 'user';

    showDialog(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setDialogState) {
            return AlertDialog(
              title: const Text('Create Skill'),
              content: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(controller: nameCtl, decoration: const InputDecoration(labelText: 'Name')),
                    const SizedBox(height: 8),
                    TextField(controller: descCtl, decoration: const InputDecoration(labelText: 'Description')),
                    const SizedBox(height: 8),
                    DropdownButtonFormField<String>(
                      value: scope,
                      items: const [
                        DropdownMenuItem(value: 'user', child: Text('User (available everywhere)')),
                        DropdownMenuItem(value: 'workspace', child: Text('Workspace (this project only)')),
                      ],
                      onChanged: (v) => setDialogState(() => scope = v!),
                      decoration: const InputDecoration(labelText: 'Scope'),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: bodyCtl,
                      maxLines: 6,
                      decoration: const InputDecoration(labelText: 'Body (markdown)', hintText: '---\nname: my-skill\ndescription: ...\n---\n\n# My Skill\n...'),
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
                ElevatedButton(
                  onPressed: () async {
                    final name = nameCtl.text.trim();
                    final content = bodyCtl.text.trim();
                    if (name.isEmpty || content.isEmpty) {
                      ScaffoldMessenger.of(ctx).showSnackBar(
                        const SnackBar(content: Text('Name and content are required')),
                      );
                      return;
                    }
                    try {
                      await widget.apiClient.createSkill(name, content, scope: scope, workspaceId: widget.workspaceId);
                      Navigator.pop(ctx);
                      _loadSkills();
                    } catch (e) {
                      if (mounted) {
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(content: Text('Failed to create: $e')),
                        );
                      }
                    }
                  },
                  child: const Text('Create'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_error != null) {
      return Center(child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text('Error: $_error', style: const TextStyle(color: Colors.red)),
          const SizedBox(height: 8),
          ElevatedButton(onPressed: _loadSkills, child: const Text('Retry')),
        ],
      ));
    }

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(8.0),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Skills (${_skills.length})', style: Theme.of(context).textTheme.titleSmall),
              IconButton(
                icon: const Icon(Icons.add, size: 20),
                onPressed: _showCreateDialog,
                tooltip: 'Create New Skill',
              ),
            ],
          ),
        ),
        Expanded(
          child: _skills.isEmpty
              ? const Center(child: Text('No skills available'))
              : ListView.builder(
                  itemCount: _skills.length,
                  itemBuilder: (ctx, i) {
                    final skill = _skills[i] as Map<String, dynamic>;
                    final name = skill['name'] ?? '';
                    final desc = skill['description'] ?? '';
                    final scope = skill['scope'] ?? 'user';

                    return ListTile(
                      dense: true,
                      title: Row(
                        children: [
                          Expanded(child: Text(name, style: const TextStyle(fontWeight: FontWeight.w500))),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: scope == 'workspace' ? Colors.blue.shade100 : Colors.grey.shade200,
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              scope == 'workspace' ? 'ws' : 'user',
                              style: TextStyle(fontSize: 10, color: scope == 'workspace' ? Colors.blue.shade800 : Colors.grey.shade700),
                            ),
                          ),
                        ],
                      ),
                      subtitle: Text(desc, maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12)),
                      trailing: PopupMenuButton<String>(
                        icon: const Icon(Icons.more_vert, size: 18),
                        itemBuilder: (ctx) => [
                          const PopupMenuItem(value: 'view', child: Text('View Detail')),
                          const PopupMenuItem(value: 'delete', child: Text('Delete')),
                        ],
                        onSelected: (action) async {
                          if (action == 'view') {
                            try {
                              final detail = await widget.apiClient.getSkillDetail(name, workspaceId: widget.workspaceId);
                              if (mounted) {
                                showDialog(
                                  context: context,
                                  builder: (ctx) => AlertDialog(
                                    title: Text('$name [${detail['scope']}]'),
                                    content: SingleChildScrollView(
                                      child: Text(detail['content'] ?? '', style: const TextStyle(fontSize: 13)),
                                    ),
                                    actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Close'))],
                                  ),
                                );
                              }
                            } catch (e) {
                              if (mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Failed to load: $e')));
                              }
                            }
                          } else if (action == 'delete') {
                            _deleteSkill(name, scope);
                          }
                        },
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }
}
```

Note: The `createSkill` method signature in api_client needs a matching update. Update its signature:

```dart
  Future<void> createSkill(
    String name,
    String content, {
    String scope = 'user',
    String? workspaceId,
  }) async {
    final body = {
      'name': name,
      'content': content,
      'scope': scope,
    };
    final params = <String, String>{};
    if (workspaceId != null) params['workspace_id'] = workspaceId;
    final uri = Uri.parse('$baseUrl/skills')
        .replace(queryParameters: params);
    final response = await http.post(
      uri,
      headers: _headers,
      body: json.encode(body),
    );
    if (response.statusCode != 200) {
      throw HttpException('Failed to create skill: ${response.statusCode}');
    }
  }
```

- [ ] **Step 3: Integrate skills panel into desktop layout**

In `flutter_app/lib/screens/desktop_layout.dart` (or whichever file has the three-column desktop layout), add the Skills tab to the RHS panel.

Locate where the RHS panel renders (the Files tab). Add a bottom tab bar with icons:

```dart
// Inside the RHS panel's build method, replace the single content with:

DefaultTabController(
  length: 2,
  child: Column(
    children: [
      Expanded(
        child: TabBarView(
          children: [
            // Existing file explorer
            _buildFileExplorer(),
            // Skills panel
            SkillsPanel(
              workspaceId: _currentWorkspaceId,
              apiClient: _apiClient,
            ),
          ],
        ),
      ),
      Container(
        decoration: BoxDecoration(
          border: Border(top: BorderSide(color: Colors.grey.shade300)),
        ),
        child: TabBar(
          labelColor: Theme.of(context).primaryColor,
          unselectedLabelColor: Colors.grey,
          indicatorWeight: 2,
          tabs: const [
            Tab(icon: Icon(Icons.folder_outlined, size: 20), text: ''),
            Tab(icon: Icon(Icons.bolt_outlined, size: 20), text: ''),
          ],
        ),
      ),
    ],
  ),
)
```

Replace `_buildFileExplorer()` with your existing RHS file list.

- [ ] **Step 4: Check Flutter compilation**

Run: `cd flutter_app && flutter analyze lib/widgets/skills_panel.dart`
Expected: No errors. Fix any import issues.

- [ ] **Step 5: Commit**

```bash
git add flutter_app/lib/widgets/skills_panel.dart flutter_app/lib/screens/desktop_layout.dart flutter_app/lib/services/api_client.dart
git commit -m "feat: add skills panel to Flutter RHS sidebar with CRUD flows"
```

---

### Task 6: Web Dashboard — Skills management UI

**Files:**
- Modify: `src/http/static/` (or wherever the web dashboard lives)

The web dashboard shares the same REST API. Implementation depends on the existing dashboard framework (React/Vue/Svelte/etc.). Key requirements:

- Split-pane: skill list on left, markdown editor on right
- Merged list with scope badges (same as Flutter)
- Create/edit form with frontmatter fields
- Calls the same `/skills` endpoints

Since the web dashboard tech stack varies, this task provides the API contract only. The Flutter panel (Task 5) is the primary UX deliverable.

- [ ] **Step 1: Verify API contract is sufficient for web dashboard**

Confirm these endpoints cover all web dashboard needs:
- `GET /skills?workspace_id=` — list with scope info
- `GET /skills/{name}?workspace_id=` — detail with content
- `POST /skills` — create (scope in body)
- `PUT /skills/{name}?scope=&workspace_id=` — update
- `DELETE /skills/{name}?scope=&workspace_id=` — delete

- [ ] **Step 2: Commit if web changes needed** (skip if dashboard uses existing endpoints)

```bash
git commit -m "feat: confirm API contract supports web dashboard skills panel"
```

---

### Task 7: Final — Lint, Type Check, Full Test Suite

**Files:**
- All modified files

- [ ] **Step 1: Run ruff linter**

Run: `uv run ruff check src/`
Expected: No errors (fix any issues with: `uv run ruff check src/ --fix`)

- [ ] **Step 2: Run mypy type checker**

Run: `uv run mypy src/`
Expected: No errors

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/sdk/ tests/api/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: final lint and type check pass"
```
