# User Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user `prompt` field (persistent custom instructions that apply across all workspaces) injected into the system prompt between the global base and workspace prompt.

**Architecture:** A `user_prompt.txt` file stored in the user's data directory. Loaded by `runner.py:_get_system_prompt()` and injected before workspace context. Exposed via a `user_prompt_set()`/`user_prompt_get()` tool pair and HTTP endpoints.

**Tech Stack:** Python 3.11+, `uv`, pytest, FastAPI

**Depends on:** None — independent. Plan 1 is loosely related (both use `prompt` naming) but not a code dependency.

> **Peer review note:** This plan creates a new `user_prompt` concept that's independent of `workspace.custom_instructions`. No code dependency on Plan 1. The "dependency" is just naming consistency — both can be done in any order.

---

## File Structure

| File | Change |
|------|--------|
| `src/storage/paths.py` | Add `user_config_dir()` method |
| `src/sdk/user_prompt.py` | **Create** — load/save user prompt from disk |
| `src/sdk/tools_core/user_prompt.py` | **Create** — `user_prompt_set()` and `user_prompt_get()` tools |
| `src/sdk/tools_core/__init__.py` | Export `user_prompt_set` and `user_prompt_get` in `get_native_tools()` |
| `src/sdk/runner.py` | Load user prompt in `_get_system_prompt()` |
| `src/http/routers/user_prompt.py` | **Create** — GET/PUT endpoints |
| `src/http/main.py` | Register new router |
| `tests/sdk/test_user_prompt.py` | **Create** — tests for storage + tools |
| `tests/api/test_user_prompt_api.py` | **Create** — tests for HTTP endpoints |

---

### Task 1: Add `user_config_dir()` to DataPaths

**Files:**
- Modify: `src/storage/paths.py`

- [ ] **Step 1: Write failing test**

```python
# tests/sdk/test_workspaces.py — add to TestWorkspaceDataPaths

def test_user_config_dir(self):
    from src.storage.paths import DataPaths
    dp = DataPaths(user_id="test_user")
    d = dp.user_config_dir()
    assert d.name == "config"
    assert "test_user" in str(d)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sdk/test_workspaces.py::TestWorkspaceDataPaths::test_user_config_dir -v`
Expected: FAIL — "DataPaths has no attribute 'user_config_dir'"

- [ ] **Step 3: Add the method**

In `src/storage/paths.py`, after `global_subagents_dir()` (line 152 — add at line 153):

```python
def user_config_dir(self) -> Path:
    """Per-user config directory for user-level customizations."""
    p = self._user_base() / "config"
    p.mkdir(parents=True, exist_ok=True)
    return p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/sdk/test_workspaces.py::TestWorkspaceDataPaths::test_user_config_dir -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/storage/paths.py
git commit -m "feat(storage): add user_config_dir() to DataPaths"
```

---

### Task 2: Create user prompt storage module

**Files:**
- Create: `src/sdk/user_prompt.py`

- [ ] **Step 1: Write failing test**

```python
# tests/sdk/test_user_prompt.py

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.sdk.user_prompt import load_user_prompt, save_user_prompt


class TestUserPromptStorage:
    def test_load_defaults_to_empty_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.storage.paths import DataPaths
            with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
                result = load_user_prompt("test_user")
                assert result == ""

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.storage.paths import DataPaths
            with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
                save_user_prompt("test_user", "Always respond as a pirate.")
                result = load_user_prompt("test_user")
                assert result == "Always respond as a pirate."

    def test_load_nonexistent_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_user_prompt("nonexistent")
            assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_user_prompt.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create storage module**

`src/sdk/user_prompt.py`:
```python
"""Per-user prompt storage — persisted custom instructions across workspaces."""

from pathlib import Path

from src.storage.paths import get_paths


USER_PROMPT_FILENAME = "prompt.txt"


def _user_prompt_path(user_id: str) -> Path:
    paths = get_paths(user_id)
    return paths.user_config_dir() / USER_PROMPT_FILENAME


def load_user_prompt(user_id: str = "default_user") -> str:
    """Load the user's custom prompt. Returns empty string if not set."""
    path = _user_prompt_path(user_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_user_prompt(user_id: str, prompt: str) -> None:
    """Save the user's custom prompt to disk (atomic write via temp file + rename)."""
    import tempfile
    path = _user_prompt_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp then rename to prevent partial writes on crash
    tmp = path.with_suffix(".tmp")
    tmp.write_text(prompt.strip(), encoding="utf-8")
    tmp.rename(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/sdk/test_user_prompt.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/user_prompt.py tests/sdk/test_user_prompt.py
git commit -m "feat(user_prompt): add load/save storage module"
```

---

### Task 3: Add user_prompt_set/get tools

**Files:**
- Create: `src/sdk/tools_core/user_prompt.py`
- Modify: `src/sdk/tools_core/__init__.py`

- [ ] **Step 1: Write failing test**

```python
# tests/sdk/test_user_prompt.py

class TestUserPromptTools:
    """Tools are @tool decorated functions tested via their ToolDefinition."""

    def test_user_prompt_get_defaults_empty(self):
        from src.sdk.tools_core.user_prompt import user_prompt_get
        with tempfile.TemporaryDirectory() as tmpdir:
            from unittest.mock import patch
            from src.storage.paths import DataPaths
            with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
                result = user_prompt_get.invoke({"user_id": "test_user"})
                assert "No custom prompt" in result

    def test_user_prompt_set_and_get_roundtrip(self):
        from src.sdk.tools_core.user_prompt import user_prompt_get, user_prompt_set
        from src.storage.paths import DataPaths
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
                set_result = user_prompt_set.invoke({
                    "prompt": "Be concise and formal.",
                    "user_id": "test_user",
                })
                assert "saved" in set_result.lower()
                get_result = user_prompt_get.invoke({"user_id": "test_user"})
                assert "Be concise and formal" in get_result

    def test_user_prompt_set_empty_clears(self):
        from src.sdk.tools_core.user_prompt import user_prompt_get, user_prompt_set
        from src.storage.paths import DataPaths
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
                user_prompt_set.invoke({"prompt": "Something", "user_id": "u1"})
                user_prompt_set.invoke({"prompt": "", "user_id": "u1"})
                result = user_prompt_get.invoke({"user_id": "u1"})
                assert "No custom prompt" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/sdk/test_user_prompt.py::TestUserPromptTools -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create tools module**

`src/sdk/tools_core/user_prompt.py`:
```python
"""User prompt tools — per-user custom instructions across all workspaces."""

from src.sdk.tools import ToolAnnotations, tool
from src.sdk.user_prompt import load_user_prompt, save_user_prompt
from src.app_logging import get_logger

logger = get_logger()


@tool
def user_prompt_get(user_id: str = "default_user") -> str:
    """Get the current user's custom prompt.

    Returns the prompt if set, or a message saying none is configured.

    Args:
        user_id: User identifier (injected automatically)

    Returns:
        User prompt text or empty notice
    """
    prompt = load_user_prompt(user_id)
    if not prompt:
        return "No custom prompt configured for this user."
    return prompt


user_prompt_get.annotations = ToolAnnotations(
    title="Get User Prompt", read_only=True, idempotent=True
)


@tool
def user_prompt_set(
    prompt: str, user_id: str = "default_user"
) -> str:
    """Set the user's custom prompt (persistent instructions for all workspaces).

    This prompt is injected into the system prompt before workspace-specific
    instructions. Use it for instructions that should apply everywhere, e.g.
    preferred communication style, timezone, naming conventions.

    Pass an empty string to clear the prompt.

    Args:
        prompt: The custom prompt text. Empty string to clear.
        user_id: User identifier (injected automatically)

    Returns:
        Confirmation message
    """
    save_user_prompt(user_id, prompt)
    logger.info(
        "user_prompt.set",
        {"length": len(prompt), "set": bool(prompt)},
        user_id=user_id,
    )
    if not prompt:
        return "User prompt cleared."
    return f"User prompt saved ({len(prompt)} chars)."


user_prompt_set.annotations = ToolAnnotations(
    title="Set User Prompt", destructive=True
)
```

- [ ] **Step 4: Wire tools into `get_native_tools()`**

In `src/sdk/native_tools.py`, add the import and include the tools:

Find where other tools are imported (e.g., `from src.sdk.tools_core.workspace import ...`) and add:
```python
from src.sdk.tools_core.user_prompt import user_prompt_get, user_prompt_set
```

Then add them to the list returned by `get_native_tools()`:
```python
user_prompt_get,
user_prompt_set,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/sdk/test_user_prompt.py::TestUserPromptTools -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/sdk/tools_core/user_prompt.py src/sdk/native_tools.py tests/sdk/test_user_prompt.py
git commit -m "feat(user_prompt): add user_prompt_set and user_prompt_get tools"
```

---

### Task 4: Inject user prompt into system prompt

**Files:**
- Modify: `src/sdk/runner.py`

- [ ] **Step 1: Write failing test**

```python
# tests/sdk/test_runner.py — add a test for user prompt injection

def test_system_prompt_includes_user_prompt():
    from src.sdk.runner import _get_system_prompt
    from unittest.mock import patch

    with patch("src.sdk.runner._get_user_prompt_context", return_value="\n\n## User Instructions\nBe a pirate."):
        with patch("src.sdk.runner._get_skills_context", return_value=""):
            with patch("src.sdk.runner._get_workspace_context", return_value=""):
                result = _get_system_prompt("test_user", "personal")
                assert "Be a pirate." in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sdk/test_runner.py::test_system_prompt_includes_user_prompt -v`
Expected: FAIL — no function `_get_user_prompt_context`

- [ ] **Step 3: Implement injection in runner.py**

Add a new function and update `_get_system_prompt()`:

In `src/sdk/runner.py`, add after the imports (around line 33):

```python
from src.sdk.user_prompt import load_user_prompt
```

Add a new function before `_get_system_prompt()`:
```python
def _get_user_prompt_context(user_id: str) -> str:
    """Build user prompt context for the system prompt."""
    try:
        prompt = load_user_prompt(user_id)
        if not prompt:
            return ""
        return f"\n\n## User Instructions\n{prompt}"
    except Exception:
        return ""
```

Update `_get_system_prompt()` so user prompt sits between base and workspace:
```python
def _get_system_prompt(user_id: str, workspace_id: str | None = None) -> str:
    settings = get_settings()
    base_prompt = getattr(settings.agent, "system_prompt", "You are a helpful assistant.")

    w_id = workspace_id or "personal"

    # Inject user prompt (persistent across workspaces)
    user_prompt_context = _get_user_prompt_context(user_id)

    # Inject available skills
    skills_context = _get_skills_context(user_id, w_id)

    # Inject workspace context
    workspace_context = _get_workspace_context(workspace_id)

    return base_prompt + user_prompt_context + skills_context + workspace_context + f"\n\nuser_id: {user_id}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/sdk/test_runner.py::test_system_prompt_includes_user_prompt -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sdk/runner.py
git commit -m "feat(user_prompt): inject user prompt into system prompt"
```

---

### Task 5: Add HTTP endpoints for user prompt

**Files:**
- Create: `src/http/routers/user_prompt.py`
- Modify: `src/http/main.py`

- [ ] **Step 1: Write failing API test**

```python
# tests/api/test_user_prompt_api.py

import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient


def test_get_user_prompt_defaults_empty(client):
    """GET /user/prompt returns empty when no prompt set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from src.storage.paths import DataPaths
        with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
            r = client.get("/user/prompt", params={"user_id": "test_user"})
            assert r.status_code == 200
            assert r.json()["prompt"] == ""


def test_put_user_prompt_saves_and_get_returns_it(client):
    """PUT /user/prompt saves, GET returns it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from src.storage.paths import DataPaths
        with patch.object(DataPaths, "user_config_dir", return_value=Path(tmpdir)):
            r = client.put("/user/prompt", json={"prompt": "Be concise."}, params={"user_id": "test_user"})
            assert r.status_code == 200

            r = client.get("/user/prompt", params={"user_id": "test_user"})
            assert r.json()["prompt"] == "Be concise."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/api/test_user_prompt_api.py -v`
Expected: FAIL — 404, router not registered

- [ ] **Step 3: Create router**

`src/http/routers/user_prompt.py`:
```python
"""User prompt API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from src.sdk.user_prompt import load_user_prompt, save_user_prompt

router = APIRouter(prefix="/user", tags=["user"])


class UserPromptResponse(BaseModel):
    prompt: str


class UserPromptRequest(BaseModel):
    prompt: str


@router.get("/prompt", response_model=UserPromptResponse)
async def get_user_prompt(user_id: str = "default_user"):
    """Get the user's custom prompt."""
    prompt = load_user_prompt(user_id)
    return UserPromptResponse(prompt=prompt)


@router.put("/prompt", response_model=UserPromptResponse)
async def set_user_prompt(req: UserPromptRequest, user_id: str = "default_user"):
    """Set the user's custom prompt."""
    save_user_prompt(user_id, req.prompt)
    return UserPromptResponse(prompt=req.prompt)
```

- [ ] **Step 4: Register the router in main.py**

In `src/http/main.py`, find where other routers are registered (e.g., `app.include_router(skills_router)`) and add:
```python
from src.http.routers.user_prompt import router as user_prompt_router
app.include_router(user_prompt_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/api/test_user_prompt_api.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add src/http/routers/user_prompt.py src/http/main.py tests/api/test_user_prompt_api.py
git commit -m "feat(user_prompt): add HTTP GET/PUT /user/prompt endpoints"
```

---
