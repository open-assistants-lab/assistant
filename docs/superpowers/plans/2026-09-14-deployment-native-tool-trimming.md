# Deployment Native Tool Policy Plan

**Goal:** Replace the unmerged denylist proposal with an allowlist-first native policy; custom `TOOL.md` and MCP tools remain independent.

**Spec:** `docs/superpowers/specs/2026-09-14-deployment-native-tool-trimming-design.md`

## Task 1: Policy model and hard ceiling

- [ ] Write red tests for default `all`, `none`, and `selected` exact/glob matching plus nested environment settings.
- [ ] Add `tools.native.mode` and `tools.native.enabled`; remove the unmerged `tools.disabled` proposal entirely.
- [ ] Centralize native-policy/provenance checks.
- [ ] Enforce the policy at native catalog creation, refresh, persisted search, lazy load, direct registered execution, and prompt guidance.
- [ ] Preserve matching custom `TOOL.md` and MCP definitions at every boundary.
- [ ] Document `TOOL.md` as per-custom-tool and policy configuration in deployment/config docs.

## Task 2: Review and release

- [ ] Scoped review of policy precedence and all enforcement boundaries.
- [ ] Full `timeout 900 uv run pytest tests/ -q` with development and analytics extras.
- [ ] Final review and owner merge approval.
