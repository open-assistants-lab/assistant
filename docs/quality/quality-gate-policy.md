# Quality-Gate Policy

**Adopted:** 2026-09-21 · **Owner:** repo gate reviews · **Context:** the D1 exit
gate required an explicit policy for the pre-existing, unrelated baseline
failures before D1 could be recorded as passing.

## Rules

### 1. Ruff is a hard zero gate

`ruff check src/ tests/ scripts/` must report **zero** diagnostics. No
baselines, no per-directory exclusions. Where a late import is deliberate
(e.g. `src/http/routers/conversation.py` loads `.env` before importing
libraries that read it; `scripts/*gmail*.py` insert the repo root on
`sys.path` before importing `src.*`), suppress it per file with
`# ruff: noqa: E402` **and a comment explaining why** — do not move behaviour
to satisfy the linter.

### 2. mypy is a checked-in ratchet

mypy runs in `strict` mode and currently reports annotation debt that predates
this policy. The ratchet is enforced by `scripts/mypy_baseline.py` against
`docs/quality/mypy-baseline.txt` (per-file counts):

- **A file's count may never grow.** CI fails if it does.
- **A file absent from the baseline may not add an error.** New code must be
  clean; new files are not auto-baselined.
- **A file may always shrink** — reductions need no baseline update.
- **When a file's count is ≤ 3, fix it instead of re-baselining it.** The
  baseline is debt to burn, not a licence.
- Regenerate the baseline only when the added errors are deliberately deferred;
  say why in the commit message.

```
uv run ruff check src/ tests/ scripts/        # rule 1
uv run python scripts/mypy_baseline.py        # rule 2 (check)
uv run python scripts/mypy_baseline.py --write  # deliberate re-baseline
```

### 3. Gate reviews carry scoped evidence

Milestone reviews (D1-style) additionally run a scoped pass over the reviewed
surface:

```
uv run ruff check <reviewed files>
uv run mypy --follow-imports=skip --disable-error-code=untyped-decorator <reviewed source files>
```

The tree ratchet (rules 1–2) is the floor; the scoped pass is the gate's own
evidence.

## Baseline at adoption

| Gate | Result |
|---|---|
| `ruff check src/` | 0 |
| `ruff check tests/` | 0 |
| `ruff check scripts/` | 0 |
| `mypy src/` | 42 errors / 10 files — `src/http/routers/*` except `src/sdk/run_service.py` (1) |

The adoption slice burned the mypy baseline from **62 errors / 17 files** to
**42 / 10**, including every `src/sdk/` file except one deliberate hold-out:

- `src/sdk/profile_loader.py`, `src/sdk/observability.py`,
  `src/sdk/subagent_models.py`, `src/sdk/kit.py`,
  `src/sdk/governance_operations.py` — annotation debt fixed.
- `src/sdk/tools_core/tool_search.py`, `src/sdk/runner.py` — typing fixed, and
  the ratchet surfaced a **real runtime bug**: `runner.py` imported
  `get_message_store` from `src.sdk.messages` (it lives in
  `src.storage.messages`), so the Issue #18 context-pruning escape hatch raised
  `ImportError` when invoked. Fixed with the correct import.
- `src/sdk/run_service.py:572` — **deliberately left in the baseline.**
  `_load_history()` is declared `list[sdk.messages.Message]` but returns
  `src.storage.messages.Message` rows on the default (session-log-off) path.
  The two classes are structurally compatible today (tests pass), so a cast
  would hide the design question rather than answer it. Follow-up: decide
  whether the storage row should be converted at this seam or the two classes
  unified.

## Rationale

- A zero-baseline mypy rewrite (42 fixes across HTTP routers) would touch
  runtime code for annotation-only reasons in one slice — high review cost,
  low product value, and unrelated to any feature. The ratchet stops new debt
  immediately and lets the remaining files burn down when they are next edited.
- Zero-tolerance Ruff is cheap to keep and catches real import/name mistakes
  (the 56 test diagnostics included dead imports and unused setup variables).

## CI

`.github/workflows/quality.yml` runs both rules on every push to `main` and
every pull request. It installs all extras (`uv sync --all-extras`) so the mypy
counts match the environment the baseline was recorded in; the baseline was
verified stable with `--all-extras`.
