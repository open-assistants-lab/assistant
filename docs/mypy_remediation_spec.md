# Mypy Remediation Spec

**Date:** 2026-06-14  
**Scope:** Resolve mypy errors surfaced by `uv run mypy src/`  
**Status:** Partially complete — skill-scope fix applied; remaining items documented below.

---

## 1. Already Fixed

### 1.1 `src/sdk/item_scopes.py` — `set` type shadowed by method name
- **Problem:** `ItemScopeDB` defines a method named `set`. Because the module uses `from __future__ import annotations`, forward references in annotations are resolved lazily; `get_available_names() -> set[str]` and `get_excluded_names() -> set[str]` were being resolved against the class method `set` instead of the built-in `set` type.
- **Fix:** Imported built-in `set` under the alias `_set` (`from builtins import set as _set`) and updated the two return-type annotations to `_set[str]`.
- **Also fixed:** Added the type parameter `str` to the `wids` variable annotation in `remove_workspace_from_selected()`.

---

## 2. Remaining Mypy Issues

### 2.1 Missing type stubs for `yaml`
- **Files:** `src/skills/models.py:6`, `src/sdk/capabilities.py:8`, `src/config/settings.py:5`
- **Error:** `Library stubs not installed for "yaml"`
- **Fix:** Add `types-PyYAML` as a dev dependency (`uv add --dev types-PyYAML`) and re-lock.
- **Risk:** Low; adding stubs only affects type checking, not runtime behavior.

### 2.2 `src/config/settings.py` — Pydantic `ConfigDict` mismatch
- **Error:**
  - `Incompatible types in assignment (expression has type "ConfigDict", base class "BaseSettings" defined the type as "SettingsConfigDict")`
  - `Extra key "env_prefix" for TypedDict "ConfigDict"`
- **Fix:**
  1. Change `from pydantic import ConfigDict, Field` to `from pydantic import Field` and add `from pydantic_settings import BaseSettings, SettingsConfigDict` (if not already present).
  2. In every `model_config = ConfigDict(...)` declaration, change to `model_config = SettingsConfigDict(...)`.
  3. Keep `env_prefix` inside each `SettingsConfigDict(...)` call. For example:
     ```python
     from pydantic_settings import SettingsConfigDict
     
     model_config = SettingsConfigDict(env_prefix="AGENT_")
     ```
- **Risk:** Medium — this is a real Pydantic V2 / `pydantic-settings` compatibility issue. If `pydantic-settings` removes support for `ConfigDict` in future versions, runtime will break.
- **Validation:** After the change, run `uv run pytest tests/` to confirm env-based settings still load correctly.

### 2.3 `src/http/ws_protocol.py` and `src/sdk/tools_core/browser_agent.py` — returning `Any` / wrong declared type

> Superseded: `src/sdk/tools_core/browser_agent.py` was deleted in `85bf4a8e`
> (unreachable dead code superseded by `src/sdk/tools_core/browser.py`). The
> `browser_agent` items below are kept only as a historical record.
- **Functions/lines:**
  - `src/http/ws_protocol.py:336` — `parse_client_message()`
  - `src/http/ws_protocol.py:376` — `parse_server_message()`
  - `src/sdk/tools_core/browser_agent.py` — decorated browser tools returning `str` but actually returning `ToolResult`
- **Errors:**
  - `Returning Any from function declared to return Union[...]` (ws_protocol)
  - `Incompatible return value type (got "ToolResult", expected "str")` (browser_agent)
- **Fix:**
  - **ws_protocol:** Cast the result to the declared union, e.g.:
    ```python
    return cast(_ServerMessage | None, msg_cls(**data))
    ```
  - **browser_agent:** The `@tool` decorator likely wraps the function to return `ToolResult`, but the function annotations say `-> str`. Either change the annotations to `-> ToolResult` or let the decorator produce the wrapper and annotate the wrapper correctly.
- **Validation:** Add a unit test for malformed WS messages and run browser agent tests.

### 2.4 `src/sdk/validation.py` — returning `Any` and missing generic parameters
- **Functions:** `normalize_tool_schema()` (line 34), `repair_tool_call()` (lines 98, 104, 110, 117)
- **Error:** `Returning Any from function declared to return "dict[Any, Any]"` and `Missing type parameters for generic type "dict"`
- **Fix:**
  1. Update function signatures to use `dict[Any, Any]` (e.g. `normalize_tool_schema(schema: dict[Any, Any]) -> dict[Any, Any]` and `repair_tool_call(...) -> dict[Any, Any]`).
  2. Cast the final return values: `normalize_tool_schema()` returns `_normalize_node(schema, defs)`; `repair_tool_call()` returns `json.loads(...)`.
  3. Use `cast(dict[Any, Any], result)` or `assert isinstance(result, dict)` because the rest of the code expects a dict.
- **Validation:** Ensure existing tests in `tests/sdk/test_validation.py` still pass.

### 2.5 `src/sdk/handoffs.py:91` — returning `Any`
- **Function:** `Handoff.filter_input()`
- **Error:** `Returning Any from function declared to return "list[Message]"`
- **Fix:** Cast the returned list to `list[Message]`, e.g.:
  ```python
  if self.input_filter is not None:
      return cast(list[Message], self.input_filter(handoff_input))
  ```
- **Validation:** Run `tests/sdk/test_handoffs.py` if it exists; otherwise run the SDK test suite.

### 2.6 `src/app_logging.py` — missing return types
- **Functions:** `Logger.__init__()` (line 36), `Logger._init_langfuse()` (line 55), plus additional logging helper functions in the same file (lines 107, 141, 145, 149, 153, 158, 209, 215)
- **Error:** `Function is missing a return type annotation`
- **Fix:** Add `-> None` to all untyped functions in `app_logging.py`.
- **Risk:** Low; purely annotation change.
- **Note:** `app_logging.py` is the largest contributor to "missing return type" errors; fixing it alone removes most of that category.

### 2.7 Widespread missing generic type parameters
- **Files:** `src/sdk/tools.py`, `src/sdk/messages.py`, `src/http/ws_protocol.py`, `src/sdk/validation.py`, `src/sdk/guardrails.py`, `src/http/models.py`, etc.
- **Error:** `Missing type parameters for generic type "dict"` / `"list"` / `"Callable"`
- **Fix:** Replace bare generics with parameterized forms:
  - `dict` → `dict[str, Any]`
  - `list` → `list[Any]` or a concrete element type
  - `Callable` → `Callable[..., Any]`
- **Scope note:** This accounts for the majority of the ~275 remaining errors. Fixing it is mechanical but touches many files. It can be done incrementally, but the acceptance criterion of zero mypy errors cannot be met until these are also resolved.

---

## 3. Suggested Execution Order

1. **Install stubs:** `uv add --dev types-PyYAML`
2. **Fix Pydantic config:** `src/config/settings.py` (highest runtime risk)
3. **Fix `Any` returns:** `ws_protocol.py`, `validation.py`, `handoffs.py`
4. **Add missing return types:** `src/app_logging.py`
5. **Generic type parameters:** Tackle file-by-file; `src/http/ws_protocol.py`, `src/sdk/messages.py`, and `src/sdk/tools.py` are good starting points because they are central.

After each step:
- Run `uv run ruff check src/ tests/`
- Run `uv run pytest tests/sdk/ tests/api/ tests/unit/` (fast subset)

---

## 4. Acceptance Criteria

- `uv run mypy src/` reports zero errors.
- `uv run ruff check src/ tests/` passes.
- `uv run pytest` passes (full suite).

**Caveat:** Reaching zero mypy errors requires fixing the generic-type-parameter noise in addition to the targeted issues above. If the team prefers to suppress that class of error temporarily, add `disallow_any_generics = False` to `mypy.ini` / `pyproject.toml` and note it in this spec.
