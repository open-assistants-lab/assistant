# Governed Durable Operations Design (#21)

## Goal
Separate approval from execution for explicitly async governance-required tools. Approval returns a durable operation identifier promptly; execution progress and terminal outcome survive client disconnects and restart reconciliation.

## Ownership and storage

`GovernanceService` remains the approval authority. A new `GovernanceOperationStore` is a focused state-machine abstraction over the **same per-user `governance.db`** as proposals. Sharing the physical SQLite database is required for one transaction that consumes an approved proposal and creates exactly one operation.

Do not use HybridDB for the authoritative operation state machine. HybridDB may later index completed operation/audit history for search; it is not the transaction source of truth.

`WorkQueueDB` is renamed to `SubagentWorkQueueDB` (and its module accordingly) because it remains subagent-specific. Its WAL, claim, heartbeat, and conditional-transition patterns inform this design but are not reused as the operation schema.

## Operation model

`operations` has an immutable proposal binding and idempotency key:

```text
operation_id, proposal_id UNIQUE, user_id, tool_name, arguments_json,
arguments_hash, executor_kind, manifest_hash nullable, external_operation_id nullable,
status, cancel_requested, created_at, started_at, updated_at, completed_at,
result_json nullable, error_code nullable, error_detail_safe nullable
```

`operation_events` is append-only:

```text
operation_id, sequence UNIQUE per operation, timestamp, kind, message_safe,
structured_data_safe nullable
```

States: `queued → running → succeeded|failed|cancelled|uncertain`. `cancel_requested` is non-terminal until executor confirmation or reconciliation.

## Approval and dispatch

For a tool annotation `execution_mode: async` (default `sync`):

1. atomically transition `proposal.approved → proposal.consumed` and create/read its one operation;
2. return `{status: "accepted", proposal_id, operation_id, operation_status: "queued"}`;
3. asynchronously dispatch an immutable envelope with proposal binding, arguments hash, expiry, and idempotency key;
4. never blindly replay an external side effect after unknown outcome.

Initial executor contract is external/opaque. Assistant does not parse domain manifests or steps. Executor callbacks authenticate to the assigned operation capability, may append ordered checkpoints, and must use idempotent terminal completion/failure. A restart reconciles operations through executor status or marks them `uncertain` with evidence.

## API and access control

- `GET /operations/{operation_id}`
- `GET /operations?status=...`
- executor-only progress/terminal callback endpoints
- durable cancel request endpoint

All reads and updates use existing governance user/tenant authorization rules. Polling is MVP; SSE/webhook delivery is deferred.

## Non-goals

No LLM workflow engine, domain-step model, automatic retry of side effects, generic subagent execution reuse, or client-dependent persistence.
