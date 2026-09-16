# Phase 3b Dispatcher Allowlist Fixture Fix

## Change

Added the explicit deployment allowlist fixture for `executor.internal` to the dispatcher external-operation tests. It matches the external-operation test configuration and satisfies the fail-closed executor-host validation introduced in the Phase 3b safety repair.

## Validation

- `timeout 240 uv run pytest tests/sdk/test_governance_dispatcher.py tests/sdk/test_governance_external_operations.py -q`
  - 22 passed in 1.05s
- `timeout 180 uv run ruff check tests/sdk/test_governance_dispatcher.py`
  - All checks passed

No production behavior changed.
