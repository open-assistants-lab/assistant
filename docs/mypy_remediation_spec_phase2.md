# Mypy Remediation Spec — Phase 2

**Date:** 2026-06-14  
**Scope:** Resolve the remaining ~444 mypy errors after Phase 1 fixes  
**Status:** In progress

---

## 1. Phase 1 Summary (Completed)

| Area | Result |
|---|---|
| `types-PyYAML` | Installed as dev dependency |
| `src/config/settings.py` | `ConfigDict` -> `SettingsConfigDict` |
| `src/http/ws_protocol.py` | Fixed `Any` returns and generic `dict` params |
| `src/sdk/validation.py` | Cast returns; added `dict[Any, Any]` |
| `src/sdk/handoffs.py` | Parameterized `Callable`; fixed `filter_input()` |
| `src/sdk/tools_core/browser_agent.py` | Return type `str \| ToolResult` — *superseded: file deleted in `85bf4a8e`; kept as a historical record* |
| `src/app_logging.py` | Added return types and generic dict annotations |

**Current state:** `uv run mypy src/` reports **444 errors in 67 files**.

---

## 2. Remaining Error Breakdown

| Error | Count | Strategy |
|---|---|---|
| `Missing type parameters for generic type "dict"` | 167 | Add `dict[str, Any]` or `dict[Any, Any]` |
| `Function is missing a return type annotation` | 108 | Add `-> None` / `-> Any` / concrete type |
| `Missing type parameters for generic type "list"` | 14 | Add concrete element type |
| `Missing type parameters for generic type "Callable"` | 10 | Add `[..., Any]` or concrete signature |
| `Function is missing a type annotation for one or more arguments` | 10 | Add argument types |
| `Returning Any from function declared to return ...` | ~40 | Cast or assert returns |
| `Incompatible types in assignment` (str -> int) | 7 | Fix annotation or cast |
| `Call to untyped function "_get_shell_config"` | 4 | Add return/argument types |
| `"ToolRegistry" has no attribute "get_native_tools"` | 3 | Update API or fix call sites |
| Other miscellaneous | ~50 | Triage per file |

---

## 3. Execution Plan

### 3.1 Top-impact files (batch 1)
Fix the files with the highest error counts first. These are mostly generic-parameter and missing-return-type noise.

1. `src/storage/agents_db.py` (41 errors)
2. `src/storage/gmail_cache.py` (21 errors)
3. `src/http/routers/conversation.py` (20 errors)
4. `src/sdk/messages.py` (17 errors)
5. `src/http/routers/workspace.py` (15 errors)
6. `src/sdk/tools_core/contacts_storage.py` (14 errors)
7. `src/sdk/tools_core/email_sync.py` (13 errors)

### 3.2 Router and SDK core files (batch 2)
8. `src/http/routers/subagents.py` (12)
9. `src/http/routers/settings.py` (12)
10. `src/sdk/registry.py` (12)
11. `src/subagent/manager.py` (11)
12. `src/sdk/runner.py` (10)
13. `src/http/routers/skills.py` (10)
14. `src/http/routers/email.py` (10)

### 3.3 Remaining files (batch 3)
All other files with <= 9 errors.

---

## 4. Tactics Per Error Type

### 4.1 Bare `dict` / `list` / `Callable`
- `dict` -> `dict[str, Any]` when content is unknown, or a concrete key/value type when known.
- `list` -> `list[Any]` or `list[dict[str, Any]]` etc.
- `Callable` -> `Callable[..., Any]` when signature unknown, or `Callable[[ArgType], ReturnType]` when known.

### 4.2 Missing return types
- Add `-> None` for procedures.
- Add `-> Any` for functions that truly return mixed types (temporary, can be refined later).
- Add concrete return types where obvious from the code.

### 4.3 Missing argument types
- Add `str`, `int`, `dict[str, Any]`, etc. based on usage.
- For `**kwargs` and complex args, use `Any` or `dict[str, Any]` temporarily.

### 4.4 `Returning Any`
- Use `cast(ExpectedType, value)`.
- Or add `assert isinstance(value, ExpectedType)`.
- For `Path`, `str`, `int` returns from dynamic sources, prefer `cast`.

### 4.5 `Incompatible types in assignment` (str -> int)
- Usually happens in HybridDB rows where SQLite returns strings for integer columns. Fix by annotating the row dict as `dict[str, Any]` or casting.

### 4.6 `ToolRegistry.get_native_tools`
- Investigate whether the method exists in `src/sdk/native_tools.py`. If it does not, update call sites or add the method.

---

## 5. Validation Steps

After each batch:
1. `uv run ruff check src/ tests/`
2. `uv run mypy src/` (monitor count)
3. `uv run pytest tests/sdk/ tests/api/ tests/unit/ tests/storage/`

---

## 6. Acceptance Criteria

- `uv run mypy src/` reports fewer than **50 errors** (or zero if practical).
- `uv run ruff check src/ tests/` passes.
- `uv run pytest` passes.

---

## 7. Exit Strategy

If generic-type cleanup becomes too noisy across 67 files, consider adding to `pyproject.toml`:
```toml
[tool.mypy]
disallow_any_generics = false
```
This suppresses the bulk of the remaining errors. Only do this if the team decides strict generic parameters are not worth the churn.
