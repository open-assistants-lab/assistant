# Execution Kernel Contract and SQLite Receipt Store Implementation Plan

> **Architecture decision:** The Build/Use profile split was cancelled on 2026-09-23. This plan now applies to one capable agent mode and one shared execution runtime; the `profile` field is intentionally absent from the execution contract.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first tested vertical slice of the shared execution kernel: normalized lifecycle contracts plus a durable SQLite receipt/event store that can later back desktop and Jen adapters.

**Architecture:** Keep execution semantics independent from persistence. Pydantic models define the stable request, receipt, observation, and event vocabulary. A `ReceiptStore` protocol defines the persistence boundary; `SQLiteReceiptStore` implements it asynchronously with `aiosqlite`, WAL mode, append-only events, idempotent request creation, and rebuildable receipt projections. This slice does not yet execute shell commands or migrate governance/Jen code.

**Tech Stack:** Python 3.11+, Pydantic 2, `aiosqlite`, pytest, pytest-asyncio, SQLite WAL.

**Spec:** `docs/superpowers/specs/2026-09-22-dual-agent-profiles-execution-kernel-design.md`

## Global Constraints

- Use explicit `outcome`, `executor_state`, `effect_state`, and `verification_state`; legacy `executed`/`verified` booleans are projections only.
- Keep `timed_out` distinct from `uncertain`.
- Every execution request has an idempotency key (`request_id`); retries return the existing receipt.
- `execution_events` is append-only; the current receipt is rebuildable from events.
- Use `aiosqlite`; do not add a new database dependency.
- Preserve the existing one-writer-per-user storage model and use SQLite WAL mode.
- Do not modify `native-sdk-experiment/src/main.zig` in this slice.
- Do not migrate `src/sdk/governance.py`, Jen adapters, or tool execution paths until the contract tests pass.
- All new public function signatures require type hints and remain compatible with the project's Python 3.11 floor.

## Review Focus

- Duplicate `request_id` must never create a second receipt or second execution event.
- A timeout must not be normalized to `uncertain`, and an unknown external effect must not be normalized to `succeeded`.
- Rebuilding a receipt from events must preserve terminal state, observations, and artifact references.
- Concurrent initialization must be harmless and must not lose WAL/schema setup.
- JSON payloads must remain serializable and must not accidentally persist secrets in the common envelope.

---

### Task 1: Define lifecycle contract models

**Files:**
- Create: `src/sdk/execution_models.py`
- Create: `tests/sdk/test_execution_models.py`

**Interfaces:**
- Produces `Outcome`, `ExecutorState`, `EffectState`, and `VerificationState` string enums.
- Produces `ExecutionRequest`, `Observation`, `ExecutionEvent`, and `Receipt` Pydantic models.
- Produces `Receipt.legacy_executed` and `Receipt.legacy_verified` read-only projections returning `bool | None`.

- [ ] **Step 1: Write failing model tests**

Add tests covering the public vocabulary and projections:

```python
from src.sdk.execution_models import (
    EffectState,
    ExecutionRequest,
    ExecutorState,
    Outcome,
    Receipt,
    VerificationState,
)


def test_receipt_derives_legacy_execution_projection():
    receipt = Receipt(
        receipt_id="r1",
        request_id="req1",
        outcome=Outcome.TIMED_OUT,
        executor_state=ExecutorState.TERMINAL,
        effect_state=EffectState.UNKNOWN,
        verification_state=VerificationState.UNKNOWN,
    )

    assert receipt.legacy_executed is True
    assert receipt.legacy_verified is None


def test_rejected_receipt_projects_not_executed():
    receipt = Receipt(
        receipt_id="r1",
        request_id="req1",
        outcome=Outcome.REJECTED,
        executor_state=ExecutorState.NOT_STARTED,
        effect_state=EffectState.NOT_APPLICABLE,
        verification_state=VerificationState.NOT_REQUESTED,
    )

    assert receipt.legacy_executed is False
    assert receipt.legacy_verified is None
```

Also test that `Outcome.TIMED_OUT` and `Outcome.UNCERTAIN` are distinct values, and that arbitrary state strings are rejected by Pydantic.

- [ ] **Step 2: Run the model tests and verify the expected failure**

Run:

```bash
uv run pytest tests/sdk/test_execution_models.py -q
```

Expected: collection fails because `src/sdk/execution_models.py` does not exist.

- [ ] **Step 3: Implement the minimal models**

Implement string enums and Pydantic models with these fields:

```python
class ExecutionRequest(BaseModel):
    request_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

class Observation(BaseModel):
    observation_id: str
    kind: str
    source: str
    authoritative: bool = False
    correlation_id: str | None = None
    observed_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

class Receipt(BaseModel):
    receipt_id: str
    request_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str
    outcome: Outcome | None = None
    executor_state: ExecutorState
    effect_state: EffectState
    verification_state: VerificationState
    termination_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    observations: list[Observation] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
```

`legacy_executed` returns `False` for `NOT_STARTED`, `True` for `RUNNING` or `TERMINAL`, and `None` for `UNKNOWN`. `legacy_verified` returns `True` only for `VERIFIED`, `False` for `NOT_VERIFIED`, and `None` otherwise.

- [ ] **Step 4: Run model tests and verify they pass**

Run:

```bash
uv run pytest tests/sdk/test_execution_models.py -q
```

Expected: all model tests pass.

- [ ] **Step 5: Run Ruff for the new files**

Run:

```bash
uv run ruff check src/sdk/execution_models.py tests/sdk/test_execution_models.py
```

Expected: no violations.

---

### Task 2: Define the receipt-store boundary and SQLite schema

**Files:**
- Create: `src/sdk/execution_store.py`
- Create: `tests/sdk/test_execution_store.py`

**Interfaces:**
- Produces `ReceiptStore` as a `typing.Protocol`.
- Produces `SQLiteReceiptStore(path: Path | str)`.
- Public async methods:
  - `initialize() -> None`
  - `create_or_get(request: ExecutionRequest) -> Receipt`
  - `get(receipt_id: str) -> Receipt | None`
  - `get_by_request(request_id: str) -> Receipt | None`
  - `append_event(receipt_id: str, event_type: str, payload: dict[str, Any]) -> ExecutionEvent`
  - `record_observation(receipt_id: str, observation: Observation) -> Receipt`
  - `finalize(receipt_id: str, *, outcome: Outcome, executor_state: ExecutorState, effect_state: EffectState, verification_state: VerificationState, termination_reason: str | None = None, content: dict[str, Any] | None = None) -> Receipt`
  - `list_events(receipt_id: str) -> list[ExecutionEvent]`
  - `rebuild(receipt_id: str) -> Receipt | None`
  - `close() -> None`

The schema must include the four logical tables from the spec:

```sql
CREATE TABLE IF NOT EXISTS execution_events (...);
CREATE TABLE IF NOT EXISTS execution_receipts (...);
CREATE TABLE IF NOT EXISTS execution_observations (...);
CREATE TABLE IF NOT EXISTS execution_artifacts (...);
```

`execution_receipts.request_id` is unique. `execution_events(receipt_id, sequence)` is unique. JSON is stored with `json.dumps(..., sort_keys=True)` and parsed on read.

- [ ] **Step 1: Write failing store tests for initialization and idempotent creation**

Add:

```python
@pytest.mark.asyncio
async def test_create_or_get_is_idempotent(tmp_path):
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    request = make_request("req-1")

    first = await store.create_or_get(request)
    second = await store.create_or_get(request)

    assert second.receipt_id == first.receipt_id
    assert await store.get_by_request("req-1") == first
    assert await store.list_events(first.receipt_id) == []
    await store.close()
```

Add a restart test that closes one store, opens another against the same path, and retrieves the receipt.

- [ ] **Step 2: Run the focused store tests and verify the expected failure**

Run:

```bash
uv run pytest tests/sdk/test_execution_store.py -q
```

Expected: collection fails because `src/sdk/execution_store.py` does not exist.

- [ ] **Step 3: Implement initialization and `create_or_get`**

Use one `aiosqlite.Connection` per store instance. `initialize()` must execute `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, and the schema DDL. Protect initialization with an `asyncio.Lock`.

Use `INSERT ... ON CONFLICT(request_id) DO NOTHING`, then select the existing row. The initial receipt has `outcome=NULL`, `executor_state=NOT_STARTED`, `effect_state=NOT_APPLICABLE`, and `verification_state=NOT_REQUESTED`.

- [ ] **Step 4: Run the focused store tests and verify they pass**

Run:

```bash
uv run pytest tests/sdk/test_execution_store.py -q
```

Expected: initialization, restart, and idempotency tests pass.

---

### Task 3: Add event append, observations, finalization, and replay

**Files:**
- Modify: `src/sdk/execution_store.py`
- Modify: `tests/sdk/test_execution_store.py`

**Interfaces:**
- `append_event` returns an `ExecutionEvent` with a monotonic per-receipt sequence.
- `record_observation` persists the common observation envelope and includes it in the returned receipt.
- `finalize` writes one terminal `execution.completed` event and rejects a second incompatible finalization.
- `rebuild` reconstructs the receipt from the request snapshot plus ordered events and observations.

- [ ] **Step 1: Write failing event and lifecycle tests**

Add tests for:

```python
@pytest.mark.asyncio
async def test_timeout_preserves_unknown_external_effect(tmp_path):
    store = SQLiteReceiptStore(tmp_path / "receipts.db")
    await store.initialize()
    receipt = await store.create_or_get(make_request("req-timeout"))

    finished = await store.finalize(
        receipt.receipt_id,
        outcome=Outcome.TIMED_OUT,
        executor_state=ExecutorState.TERMINAL,
        effect_state=EffectState.UNKNOWN,
        verification_state=VerificationState.UNKNOWN,
        termination_reason="deadline_exceeded",
    )

    assert finished.outcome is Outcome.TIMED_OUT
    assert finished.effect_state is EffectState.UNKNOWN
    assert finished.outcome is not Outcome.UNCERTAIN
```

Also add tests that:

- a provider acknowledgement observation is retained after restart;
- `rebuild()` preserves the terminal receipt and observations;
- a duplicate finalization with the same values is idempotent;
- a conflicting finalization raises a typed `ReceiptStateError`;
- event sequences are `1, 2, ...` per receipt;
- a transport disconnect can finalize as `UNCERTAIN` without changing `TIMED_OUT` into `UNCERTAIN`.

- [ ] **Step 2: Run the focused tests and verify they fail for missing behavior**

Run:

```bash
uv run pytest tests/sdk/test_execution_store.py -q
```

Expected: the tests fail because event, observation, finalization, and replay behavior is not implemented.

- [ ] **Step 3: Implement append-only events and observations**

Serialize event payloads and observation payloads as JSON. Allocate the next sequence inside the same transaction as the insert. Use the receipt row as the foreign-key parent and reject unknown receipt IDs.

- [ ] **Step 4: Implement finalization and state validation**

Allow exactly one terminal completion event. Treat an identical repeated finalization as an idempotent read. Raise `ReceiptStateError` when a finalized receipt is changed to a different outcome or state tuple.

Use `finished_at` for terminal states. Do not infer `effect_state=applied` from `outcome=succeeded`; callers must provide it explicitly.

- [ ] **Step 5: Implement replay and read methods**

Build a receipt from the stored request snapshot, ordered lifecycle events, and observation rows. `rebuild()` must return the same Pydantic value as `get()` for a healthy store. `list_events()` returns events ordered by sequence.

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/sdk/test_execution_store.py -q
```

Expected: all store lifecycle, idempotency, and replay tests pass.

- [ ] **Step 7: Run Ruff and mypy for the new implementation**

Run:

```bash
uv run ruff check src/sdk/execution_models.py src/sdk/execution_store.py tests/sdk/test_execution_models.py tests/sdk/test_execution_store.py
uv run mypy src/sdk/execution_models.py src/sdk/execution_store.py
```

Expected: Ruff is clean; mypy introduces no new errors.

---

### Task 4: Export the contract without wiring existing execution paths

**Files:**
- Modify: `src/sdk/__init__.py`
- Create: `tests/sdk/test_execution_public_api.py`

**Interfaces:**
- Public imports are available from `src.sdk` for the state enums, request, receipt, observation, and store protocol.
- Existing governance and tool result imports remain unchanged.

- [ ] **Step 1: Write the failing public API test**

Add a test importing the contract from `src.sdk` and asserting that the exported names are the same classes from `execution_models.py` and `execution_store.py`.

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/sdk/test_execution_public_api.py -q
```

Expected: import failure for the new public names.

- [ ] **Step 3: Add explicit exports**

Update `src/sdk/__init__.py` without changing existing exports or importing optional runtime integrations.

- [ ] **Step 4: Run the public API test**

Run:

```bash
uv run pytest tests/sdk/test_execution_public_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the full relevant SDK suite**

Run:

```bash
uv run pytest tests/sdk/test_execution_models.py tests/sdk/test_execution_store.py tests/sdk/test_execution_public_api.py tests/sdk/test_governance.py -q
```

Expected: all selected tests pass, with no changes to existing governance behavior.

---

### Task 5: Final verification and handoff boundary

**Files:**
- Modify: `docs/superpowers/specs/2026-09-22-dual-agent-profiles-execution-kernel-design.md` only if implementation details materially clarify the contract.
- No Jen or native UI files in this task.

- [ ] **Step 1: Run repository verification**

Run:

```bash
uv run ruff check src/
uv run pytest
```

Expected: Ruff passes and the full suite has no regressions. Record any pre-existing mypy baseline separately; this slice must not add errors.

- [ ] **Step 2: Inspect the schema and event replay behavior manually**

Use a temporary SQLite database to confirm WAL mode, the four tables, unique `request_id`, and event sequence ordering. Do not inspect or modify user data directories.

- [ ] **Step 3: Document the Jen adapter boundary**

Record that Jen should implement the same `ReceiptStore` protocol using PostgreSQL rather than duplicate lifecycle semantics. Do not add the PostgreSQL adapter in this plan.

- [ ] **Step 4: Commit the vertical slice**

```bash
git add src/sdk/execution_models.py src/sdk/execution_store.py src/sdk/__init__.py tests/sdk/test_execution_models.py tests/sdk/test_execution_store.py tests/sdk/test_execution_public_api.py docs/superpowers/plans/2026-09-22-execution-kernel-contract.md
git commit -m "feat: add execution receipt kernel contract"
```
