# Deployment Native Tool Trimming Plan

**Goal:** Add deployment-wide exact/glob exclusion of built-in native tools without affecting custom or MCP tools.

**Spec:** `docs/superpowers/specs/2026-09-14-deployment-native-tool-trimming-design.md`

## Task 1: Settings and native filter

- [ ] Write red config tests for `tools.disabled` default, YAML patterns, and env parsing.
- [ ] Add `ToolsConfig.disabled: list[str]` and a small, pure native-tool matcher using `fnmatchcase`.
- [ ] Write red runner tests proving exact/glob native exclusions and that custom/MCP tools are retained.
- [ ] Apply the filter both during loop creation and live catalog refresh; disabled native tools cannot be restored by capabilities.
- [ ] Document `tools.disabled` in `config.yaml` and `DEPLOYMENT.md`.
- [ ] Run focused tests, ruff, mypy as appropriate; commit.

## Task 2: Review and release gate

- [ ] Scoped review: settings precedence, matching semantics, custom/MCP preservation, loop-refresh behavior.
- [ ] Full `timeout 900 uv run pytest tests/ -q` with dev+analytics extras.
- [ ] Final review, owner approval, merge/release.
