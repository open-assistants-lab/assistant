# Rename: "Assistant" → "Assistant" + CLI `ea` → `assistant`

> **Date:** 2026-07-16
> **Status:** Spec
> **Scope:** Backend, Native SDK app, Flutter app, docs, CLI

## Goal

Remove all references to "Executive" and "EA" across the codebase. Rename the CLI command from `ea` to `assistant`. The project and repo are now just "Assistant".

## What Changes

### 0. Replacement patterns

| Pattern | Replace with | Scope |
|---------|-------------|-------|
| `Assistant` | `Assistant` | Display strings, docs, config |
| `Assistant` | `Assistant` | Code identifiers (PascalCase) |
| `executive_assistant` | `assistant` | Code identifiers (snake_case) |
| `assistant` | `assistant` | URLs, hyphenated names |
| `Assistant` / `Assistant backend` / `Assistant client` | `Assistant` (drop "EA" prefix) | Display strings, docs |
| `uv run assistant` | `uv run assistant` | CLI invocations in scripts/docs |
| Standalone `ea ` as CLI command | `assistant` | Makefile, shell scripts |

### 1. CLI command: `ea` → `assistant`

| File | Change |
|------|--------|
| `pyproject.toml` | `[project.scripts] ea = "src.__main__:main"` → `assistant = "src.__main__:main"` |
| `Makefile` | All `ea` invocations → `assistant` |
| `uv.lock` | Regenerated after pyproject change |
| All docs/scripts using `uv run assistant` | → `uv run assistant` |
| Native app `main.zig` | Shell title, bundle_id |

### 2. Display name: "Assistant" → "Assistant"

| Location | Change |
|----------|--------|
| `src/config/settings.py` | `name = "Assistant"` → `name = "Assistant"` |
| `src/__main__.py` | CLI help text, app name |
| `src/__init__.py` | `__version__` string |
| `src/app_logging.py` | Logger name |
| `src/storage/paths.py` | App data directory name |
| `src/storage/user_storage.py` | User data references |
| `src/storage/__init__.py` | Module references |
| `src/config/__init__.py` | Config references |
| `src/http/main.py` | FastAPI app title |
| `src/sdk/companion_scheduler.py` | Companion name |
| `src/sdk/registry.py` | Registry references |
| `src/sdk/registry_update.py` | Registry update references |
| `src/sdk/tools_core/workspace.py` | Workspace tool references |
| `scripts/generate_connectors.py` | Script references |
| `scripts/migrate-paths.py` | Migration script references |
| `src/prompt_seed/AGENTS.md` | Seed prompt references |
| `src/skills_seed/*/SKILL.md` | Skill seed references |
| `tests/evaluation/evaluate.py` | Persona evaluation references |
| `tests/evaluation/test_25_personas.py` | Test persona references |
| Native `main.zig` | Window title "Assistant" → "Assistant" |
| Native `main.zig` | Bundle id `dev.native_sdk.native-sdk-experiment` → `dev.assistant.app` |
| Native `main.zig` | App name `native-sdk-experiment` → `assistant` |
| Native `app.zon` | App name field |
| Flutter `pubspec.yaml` | App name/description |
| Flutter Xcode project | Display name |
| `README.md` | Title and description |
| `AGENTS.md` | References |

### 3. What does NOT change

- Git repo directory name (can't rename from inside)
- Python package name `src` (internal, not user-facing)
- `data/users/` directory structure
- API endpoints (`/message`, `/conversation`, etc.)
- Model/provider names (`deepseek:deepseek-v4-flash`)
- Database schemas
- Environment variable names (`DEEPSEEK_API_KEY`, etc.)
- The substring "ea" in other words (e.g., "each", "area", "search", "reason") — only replace `ea` as a standalone CLI command or `uv run assistant` pattern

## Execution Order

1. `pyproject.toml` — rename CLI entry point
2. `uv sync` — regenerate lock file
3. Python source — replace all "Assistant" → "Assistant"
4. Native SDK app — update window title, bundle id, app name
5. Flutter app — update display name
6. Makefile — replace `ea` → `assistant`
7. Docs — mechanical find-replace across `.md` files
8. Test scripts — `frontend_suite.sh`, `frontend_smoke.sh`, and any test using `uv run assistant`
9. Run `uv run assistant http` + `native test` + `ruff check` to verify

## Risk

| Risk | Mitigation |
|------|-----------|
| CLI rename breaks existing scripts | Acceptable — user requested |
| `uv.lock` needs regeneration | `uv sync` handles it |
| Flutter Xcode project has binary refs | Update display name only |
| Docs have many references | Mechanical replace, no logic risk |