# Dual Agent Profiles and Shared Execution Kernel

> **Status:** Proposed design; no implementation implied.
>
> **Related issues:** GitHub #35, #36, #37, #38, #39, #40, and #41.
>
> **Related direction:** Pi-like local coding execution, governed personal-assistant automation, and the existing SDK governance/operation lifecycle.

## 1. Summary

Assistant should provide two agent profiles on top of one execution platform:

- **Build mode** is a Pi-like engineering agent. It can inspect a repository, edit files, run commands, test changes, use git, and author or modify tools and skills.
- **Use mode** is a capable personal-automation agent. It can use the approved tool, skill, and subagent catalog, operate on user data, compose workflows, use connectors, and run existing automation. It does not receive Build authoring affordances by default, but trusted shell access is intentionally not treated as a hard security boundary. Users or administrators may require HITL for selected tools, skills, or subagents.

The profiles are not separate agent implementations. They share:

- the agent loop;
- context and compaction;
- tool-call streaming;
- the execution kernel;
- durable run/operation lifecycle;
- artifacts;
- receipts and evidence.

The profile selects capabilities, protected roots, credential policy, network policy, HITL policy, evidence policy, and the visible tool catalog.

The central architectural change is to move from tool-specific execution paths to one execution kernel and one shared execution lifecycle contract. The contract covers requests, lifecycle events, observations, artifacts, and the current terminal receipt projection. Native tools, custom `TOOL.md` tools, MCP tools, connector actions, shell commands, and subagent work should either execute through that kernel or be explicit adapters to it.

## 2. Goals

1. Make Build mode as effective and low-friction as Pi for local repository work.
2. Keep Use mode broad and useful rather than reducing it to a narrow whitelist.
3. Prevent Use mode from creating, modifying, registering, or reloading tools.
4. Make HITL apply to consequential capabilities and actions, not every ordinary tool call.
5. Make execution results truthful across native, custom, MCP, connector, shell, and subagent paths.
6. Represent unknown side-effect state explicitly instead of forcing every outcome into success/failure.
7. Recover from dropped streams, cancellation, timeouts, and provider failures without wedging a session.
8. Preserve large command output as a durable artifact instead of silently losing it.
9. Make iteration exhaustion and other terminal reasons visible.
10. Provide evidence for governed external actions.
11. Migrate incrementally without replacing the existing SDK or desktop client in one release.

## 3. Non-goals

- Reimplement Pi or copy its source code.
- Add approval dialogs to every Build-mode shell or file action.
- Make Use mode incapable of filesystem, browser, connector, or workflow operations.
- Treat a UI-hidden tool as secure authorization.
- Use tool names alone as a security boundary.
- Guarantee that an external write took effect when the external system cannot verify it.
- Make arbitrary shell safe merely by filtering executable names.

## 4. Design principles

### 4.1 One runtime, two policy profiles

Build and Use use the same runtime. Differences are policy and capability configuration, not duplicated loops or tool registries.

### 4.2 Tool authoring and tool use are separate affordance planes

The platform distinguishes:

```text
Authoring affordances
  tool source, TOOL.md, schemas, registration, reload, policy editing

Use affordances
  approved tools, skills, subagents, user data, workflows, and results
```

Build mode exposes both affordance sets. Use mode exposes the Use affordances and does not advertise authoring APIs by default. In the trusted local profile, shell access follows the process permissions and may bypass this distinction; therefore the distinction is an interaction/policy default, not a hostile security boundary. Administrators that need an enforceable authoring boundary must provide OS/container isolation or deny shell access.

### 4.3 Broad capability, assignable governance

Use mode should be capable by default. In the trusted desktop profile, shell access and shell network access are allowed according to the process boundary. HITL is assigned per item rather than imposed on the whole mode:

- tools, including `shell_execute`;
- skills when they are loaded or invoked;
- subagents when they are started or delegated.

A user may make an item stricter for their own session. An administrator may require HITL, deny, or allow an item for the deployment; administrator policy takes precedence over user policy. If shell is not assigned HITL, a shell command is trusted and may bypass typed-tool governance by design.

### 4.4 Pi-like Build mode

Pi's local model is intentionally permissive: it runs with the permissions of the process and does not provide built-in per-command approval popups or a built-in sandbox. Build mode follows that interaction model. Stronger isolation is supplied by the desktop/container/VM boundary when required.

Build mode may optionally load an extension or policy that adds confirmation, but confirmation is not part of the normal local edit/test loop.

### 4.5 Truthful receipts over optimistic UX

No layer may claim `succeeded` or a definitively applied effect without an observation supporting that claim. A timeout, dropped response, partial write, or missing observation must remain visible as `uncertain`, `timed_out`, `failed`, or `incomplete`.

## 5. Agent profiles

### 5.1 Build profile

Build mode is intended for repository and tool engineering.

Default capabilities:

```text
filesystem.read        workspace and repository roots
filesystem.write       workspace and repository roots
process.execute        local development commands
search                 repository search and inspection
git.read               status, diff, log, history
git.write              branch/worktree/commit operations as configured
network.read           provider/package/documentation access as configured
network.write          trusted external network access as explicitly enabled
subagent.use           isolated read/write worktrees
tool.author            create and modify tool source and TOOL.md
tool.register          register/reload development tools
```

Build mode may use existing external-service tools when enabled. Build mode is trusted and does not require HITL by default; deployments may still deny capabilities. Typed external actions carry their own capability policy.

### 5.2 Use profile

Use mode is intended for everyday personal assistant workflows and existing-tool composition.

Default capabilities:

```text
filesystem.read        user data and configured workspace roots
filesystem.write       user data and configured workspace roots
search                 approved data and workspace search
process.execute        trusted local shell; no ambient credentials
browser.use            approved browser capability
network.write          trusted shell/agent network access when explicitly enabled
connector.read         approved connected services
connector.write        approval/policy controlled
subagent.use           approved existing profiles
tool.use               approved catalog only
```

Use mode does not receive these authoring affordances by default:

```text
tool.author
tool.register
tool.reload
tool.policy.write
tool.source.write
```

Use mode can request a missing capability or ask the user to switch to Build mode, but it cannot grant an application-level capability to itself. Trusted shell execution remains subject to the operating-system process boundary rather than this affordance list.

### 5.3 Trust levels

Use mode may offer deployment/user trust levels without creating separate implementations:

```yaml
agent:
  profile: use
  trust_level: standard  # trusted | standard | strict
```

- `trusted`: local actions, shell access, and explicitly enabled shell network access are automatic unless an item-level HITL assignment overrides them.
- `standard`: the same item-level model applies, with safer deployment defaults and administrator-controlled shell/network capabilities.
- `strict`: network, credentials, destructive actions, and selected tools/skills/subagents require explicit policy or HITL.

The default should be `trusted` for the local single-user desktop profile. Remote, shared, and unattended deployments should select `standard` or `strict`.

## 6. Shared execution architecture

```text
AgentSession
  └── AgentLoop
        └── CapabilityBroker
              └── ExecutionKernel
                    ├── filesystem executor
                    ├── process executor
                    ├── git executor
                    ├── browser executor
                    ├── connector executor
                    ├── MCP adapter
                    ├── custom-tool adapter
                    └── subagent executor
                          ├── Receipt
                          ├── Evidence
                          └── Artifacts
```

### 6.1 AgentSession

Owns conversation state, active run identity, steering/follow-up queues, session persistence, compaction, and event subscription.

The session must distinguish:

```text
turn       one model response plus resulting tool calls
run        the complete agent attempt, including retries/compaction
operation  durable work that may outlive a turn
```

A dropped transport stream must not leave the session permanently locked. Session state is reconciled from the durable run lifecycle.

### 6.2 CapabilityBroker

The broker resolves whether an execution request may proceed. It evaluates:

- profile;
- capability;
- resource/path/destination;
- arguments and annotations;
- current trust level;
- approval state;
- credential requirements;
- whether the action is reversible;
- whether verification is required.

The broker returns one of:

```text
allow
require_hitl
start_async
reject
```

The broker must run before the executor and must not be implemented separately by each tool family.

### 6.3 ExecutionKernel

The kernel receives a normalized request and owns:

- command/tool invocation;
- timeout and cancellation;
- process and signal status;
- stdout/stderr streaming;
- output spill and artifact creation;
- capability enforcement at execution time;
- terminal result creation;
- receipt persistence;
- evidence attachment;
- operation lifecycle transitions.

Native tools and custom tools may retain their domain-specific schemas, but execution semantics belong to the kernel.

### 6.4 Adapters

Adapters translate existing tool surfaces into normalized execution requests:

- native typed tools;
- `TOOL.md` custom commands;
- MCP tools;
- connector actions;
- subagent tasks.

Adapters must not independently decide success, timeout, credential scope, or proposal status. They provide intent and domain metadata; the kernel creates the receipt.

## 7. Execution request

A normalized request should contain the following logical fields:

```json
{
  "request_id": "req_123",
  "run_id": "run_123",
  "tool_call_id": "call_123",
  "tool_name": "shell_execute",
  "capability": "process.execute",
  "profile": "use",
  "arguments": {"command": "pytest -q"},
  "workspace_root": "/workspace/project",
  "resource_targets": ["/workspace/project"],
  "annotations": {
    "read_only": false,
    "destructive": false,
    "requires_network": false,
    "requires_credentials": false,
    "long_running": false
  },
  "approval": {
    "required": false,
    "decision_id": null
  },
  "verification": {
    "required": false,
    "plan": null
  }
}
```

The exact wire format may differ. The semantic fields must remain available to policy and audit code.

## 8. Receipt contract

Every execution path must produce one terminal receipt.

```json
{
  "receipt_id": "rcpt_123",
  "request_id": "req_123",
  "run_id": "run_123",
  "tool_call_id": "call_123",
  "tool_name": "connector_write",
  "outcome": "uncertain",
  "executor_state": "unknown",
  "effect_state": "unknown",
  "verification_state": "unknown",
  "termination_reason": "stream_disconnected",
  "is_error": true,
  "content": "The connection closed before the external write could be verified.",
  "structured_content": {
    "error_code": "delivery_lost"
  },
  "artifacts": [],
  "evidence": [],
  "started_at": "...",
  "finished_at": "..."
}
```

### 8.1 Outcome values

```text
succeeded   execution completed and the requested result was observed
failed      execution completed unsuccessfully with a known failure
cancelled   execution was deliberately cancelled before completion
timed_out   execution exceeded its configured deadline
uncertain   execution or side effect may have occurred but cannot be established
incomplete  the agent plan/turn ended before the requested work was complete
rejected    execution never began because policy denied it
```

The canonical receipt does not use `executed` or `verified` as booleans because those fields conflate process state, external effect, and observation.

Use these explicit states:

```text
executor_state:
  not_started | running | terminal | unknown

effect_state:
  not_applicable | applied | not_applied | unknown

verification_state:
  not_requested | pending | verified | not_verified | unknown
```

A timeout of a read-only local command may be `timed_out` with `executor_state=terminal` and `effect_state=not_applicable`. A timeout during an external write is `timed_out` with `effect_state=unknown` and `verification_state=unknown` unless the external system provides authoritative evidence.

For compatibility, APIs may expose derived fields:

```text
executed=true/false/null
verified=true/false/null
```

These are projections only and must not be used as the lifecycle source of truth.

User-facing wording is intentionally distinct:

- `timed_out`: **Timed out** — the executor reached its deadline; the requested effect may require reconciliation.
- `uncertain`: **Outcome uncertain** — the system cannot establish whether execution or the requested effect occurred.

Neither status should be silently rewritten as generic failure.

### 8.2 Persistence and projection

The receipt is the source of truth. The following are projections and must not independently invent lifecycle status:

- model-visible tool result;
- conversation timeline;
- proposal row;
- audit record;
- UI status;
- API response.

For governed proposals, `proposals.status=executed` is legal only when the receipt outcome is `succeeded`, `effect_state=applied`, and the available evidence supports that conclusion.

### 8.3 Receipt persistence

Use SQLite with the existing async Python stack (`aiosqlite`) rather than introducing a second database library. Each user store remains single-writer and uses WAL mode.

The minimum logical schema is:

```text
execution_events
  event_id, receipt_id, request_id, run_id, sequence, event_type,
  payload_json, created_at

execution_receipts
  receipt_id, request_id, run_id, tool_call_id, tool_name, profile,
  outcome, executor_state, effect_state, verification_state,
  termination_reason, started_at, finished_at, content_json

execution_observations
  observation_id, receipt_id, kind, source, authoritative,
  payload_json, observed_at

execution_artifacts
  artifact_id, receipt_id, kind, path, sha256, bytes, characters,
  truncated, created_at, expires_at
```

`execution_events` is append-only. `execution_receipts` is a rebuildable current projection. Observations and artifacts are separately addressable so large output is not embedded in lifecycle rows.

Default retention:

- receipt projections and observations: 180 days, unless referenced by an open operation, proposal, audit export, or conversation retention policy;
- raw lifecycle events: 180 days before archival/compaction;
- unpinned command artifacts: 30 days;
- pinned or audit-referenced artifacts: retain until explicitly released.

Receipt creation must be idempotent on `request_id`/`tool_call_id`; retries cannot create duplicate executions.

## 9. Evidence and artifacts

### 9.1 Artifacts

Large output must be streamed to a durable artifact rather than held only in memory. The receipt includes:

```text
artifact_id
path or opaque reference
content_type
bytes
characters
truncated
producer
```

A result must never claim that full output is recoverable unless the artifact exists and can be read.

### 9.2 Agent-observed evidence

Build and Use use the same verification strategy: the agent observes tool results and may perform follow-up reads, tests, diffs, or queries when the task requires them. The platform does not require a separate verifier for every tool call.

Typical observations include:

```text
attempted: request or command was issued
observed: exit status, response, or output was received
follow_up: agent reread, queried, tested, or compared state
verified: agent determined that the requested condition was met
```

The execution kernel records available observations and their timestamps in the receipt. Typed tools may provide useful structured details, such as a provider request ID, file hash, test report, or resource version, but they do not need to implement a mandatory `verify()` method.

HITL authorization is not verification. If a timeout, disconnect, partial response, or missing observation prevents the agent from establishing what happened, the receipt remains `uncertain` or `timed_out` with `effect_state=unknown` rather than claiming a successful side effect.

A provider acknowledgement is sufficient when it is authoritative. Provider-specific read-back is optional unless the action policy marks it as required. If an external write has neither authoritative acknowledgement nor verification evidence, it cannot be reported as `succeeded`.

Standard observations use a small common envelope:

```json
{
  "observation_id": "obs_123",
  "kind": "provider_ack",
  "source": "connector_action",
  "authoritative": true,
  "correlation_id": "provider-request-123",
  "observed_at": "...",
  "payload": {}
}
```

Standard observation kinds are:

```text
process_exit
signal
provider_ack
http_response
artifact_created
file_readback
test_report
agent_followup
transport_disconnect
user_approval
```

Payloads are redacted according to credential policy. Tools may add domain-specific details without changing the common envelope.

Verification/readback policy is risk-tiered:

- local reads, tests, and builds use their normal result and artifacts;
- ordinary external writes may rely on an authoritative provider acknowledgement;
- destructive, financial, permission-changing, deployment, migration, and sync actions require readback or reconciliation when the provider supports it;
- if a high-risk action has no authoritative acknowledgement or supported readback, it remains `effect_state=unknown` after interruption or ambiguity.

## 10. HITL policy

HITL is an item-level broker decision, not a property of the entire profile. The default is allow for trusted Build and Use workflows, but a user or administrator may assign `require_hitl` to a tool, skill, or subagent. Administrator policy is the upper-bound authority: it can allow, require HITL, or deny; user policy may only make an item stricter.

Default action classes:

| Action | Build | Use |
|---|---|---|
| Read local files | allow | allow |
| Search workspace | allow | allow |
| Run tests/builds | allow | allow |
| Write normal workspace files | allow | allow or policy-controlled |
| Create/modify tools | allow | reject |
| Register/reload tools | allow | reject |
| Read connected service | allow | allow/allowlist |
| Send or modify external data | allow if capability granted | allow unless item policy requires HITL |
| Delete external data | allow if capability granted | allow unless item policy requires HITL |
| Add/change credentials | allow if capability granted | allow unless item policy requires HITL |
| Change governance policy | allow if trusted Build capability granted | reject by default |
| Publish/deploy | allow if capability granted | allow unless item policy requires HITL |

HITL assignments are durable policy records with subject type and subject identifier:

```yaml
hitl:
  tools:
    shell_execute: required
    email_send: required
  skills:
    deployment: required
  subagents:
    production-ops: required
```

Catalog discovery does not require approval. A skill assignment is evaluated when the skill is loaded/invoked; a subagent assignment is evaluated before start/delegation; a tool assignment is evaluated before the tool body runs. Subagents inherit the caller's effective policy and cannot approve themselves.

HITL decisions are durable, correlated with the request and receipt, and terminal. A rejected or expired approval must not invoke the tool body.

## 11. Process execution policy

### 11.1 Build mode

Build mode may use direct local process execution in a trusted workspace. It should behave like Pi: no confirmation popup for ordinary local commands, and isolation is supplied by the surrounding process/container/VM when required.

### 11.2 Use mode

Use mode exposes the same brokered local shell capability as Build mode in the trusted profile:

- shell access follows the process boundary and is allowed by default;
- a user or administrator may assign HITL to `shell_execute` before the command runs;
- bridge credentials must not be injected into shell environments unless explicitly supplied by the user;
- shell-originated network activity is trusted when `shell_execute` is allowed without HITL;
- typed tools, skills, and subagents still evaluate their own item-level policy;
- shell output and termination are governed by the shared kernel.

An executable-name allowlist is not a sufficient security boundary when general-purpose interpreters are available. This trusted profile intentionally does not promise that application-level tool/skill/subagent restrictions survive an unrestricted shell.

## 12. Tool surface

### 12.1 Build core

The initial Build catalog should be small and Pi-like:

```text
files_read
files_write
files_edit
files_search
shell_execute
git/status/diff/history
```

Additional tools are enabled by capability rather than automatically exposed.

### 12.2 Use catalog

Use mode may expose typed domain adapters such as:

```text
email_send
contacts_update
todos_update
browser_click
connector_action
```

These remain useful because they express domain intent and evidence requirements. They are not separate execution systems.

### 12.3 Tool creation

Tool creation is a Build capability. A Build agent may:

1. create or edit tool source/`TOOL.md`;
2. run schema and policy validation;
3. run tests;
4. register the tool in its development profile;
5. produce a diff/artifact for review.

Use mode may submit a request for a new tool and does not receive tool-plane mutation/reload APIs. In the trusted profile, an unrestricted shell may still mutate files or invoke registration commands; this is intentionally outside the application affordance guarantee.

## 13. Run lifecycle and recovery

```text
created
  → running
  → waiting_for_hitl
  → executing
  → succeeded | failed | cancelled | timed_out | uncertain | incomplete
```

A stream transport is only a delivery channel. If it disconnects:

1. persist the transport failure;
2. continue or cancel the underlying run according to policy;
3. release the session lock after the bounded grace period;
4. reconcile any active operation/proposal;
5. deliver the terminal receipt on reconnect or the next user message.

A run cannot remain active indefinitely solely because a client stream disappeared.

### 13.1 Iteration exhaustion

When the loop reaches its iteration budget, it must emit:

```text
outcome: incomplete
termination_reason: max_iterations
```

This is distinct from a model-selected normal stop. `agent.max_iterations` becomes a deployment setting with an explicit default and accepted range.

## 14. Configuration

Illustrative configuration:

```yaml
agent:
  profile: use
  trust_level: standard
  max_iterations: 25

profiles:
  build:
    capabilities:
      - filesystem.read
      - filesystem.write
      - process.execute
      - search
      - git.read
      - git.write
      - tool.author
      - tool.register
      - skill.author
      - subagent.author

  use:
    capabilities:
      - filesystem.read
      - filesystem.write
      - process.execute
      - search
      - browser.use
      - connector.read
      - connector.write
      - tool.use
      - skill.use
      - subagent.use
    denied_capabilities:
      - tool.author
      - tool.register
      - tool.reload
      - tool.policy.write
      - credential.raw_read

execution:
  output:
    inline_chars: 5000
    spill_directory: .assistant/artifacts
  process:
    default_timeout_seconds: 120
  network:
    mode: allow_trusted_shell  # standard/strict profiles deny or mediate shell egress
  receipts:
    store: sqlite
    async_driver: aiosqlite
    wal: true
    append_only_events: true
  artifacts:
    directory: .assistant/artifacts

governance:
  default_external_write: allow
  default_destructive: allow
  record_agent_observed_evidence: true
  hitl:
    tools: {}
    skills: {}
    subagents: {}
```

Configuration names are illustrative and must be reconciled with existing settings conventions before implementation.

## 15. Migration strategy

### Phase 1: contracts and profile resolution

- Define profile and capability models.
- Define normalized execution request.
- Define receipt/outcome/evidence models.
- Add profile resolution without changing existing tool behavior.
- Add tests for Build/Use capability decisions.

### Phase 2: Build vertical slice

- Route filesystem, search, shell, and git through the execution kernel.
- Add artifact spill for command output.
- Add terminal reasons and iteration exhaustion.
- Preserve Pi-like no-popup local workflow.

### Phase 3: Use vertical slice

- Route one connector read and one connector write through the kernel.
- Add HITL broker decisions.
- Record agent-observed evidence and structured tool details.
- Remove raw credential access from the execution environment.
- Protect tool roots and registration capabilities.

### Phase 4: lifecycle and recovery

- Add run heartbeats and dropped-stream reconciliation.
- Connect existing durable operation lifecycle to tool receipts.
- Ensure proposals derive status from receipts.
- Add reconnect and user recovery APIs.

### Phase 5: adapter migration

Migrate remaining families incrementally:

1. custom `TOOL.md`;
2. MCP;
3. browser;
4. subagents;
5. remaining domain tools.

Each migration removes tool-specific timeout/output/status logic only after equivalent kernel tests pass.

### Phase 6: remove duplicate paths

- Retire parallel receipt/status implementations.
- Retire custom-tool subprocess wrappers that bypass the kernel.
- Retire tool-name-only governance decisions.
- Keep domain adapters, schemas, and user-facing names where they provide value.

## 16. Acceptance criteria

### Profiles

- Build can author, validate, register, and use a development tool.
- Use exposes approved tool, skill, and subagent APIs without authoring APIs by default.
- The kernel does not inject bridge credentials into shell environments; trusted shell access is not treated as a filesystem or environment security boundary.
- Build and Use use the same execution kernel.

### Receipts

- Every tool call has one terminal receipt.
- Timeout, cancellation, disconnect, rejection, and iteration exhaustion have distinct outcomes.
- A proposal cannot become `executed` unless the receipt has `outcome=succeeded` and `effect_state=applied`.
- Canonical executor/effect/verification states are persisted; legacy `executed`/`verified` booleans are projections only.
- Receipts, API responses, audit rows, and UI projections agree.

### Execution

- Shell, custom tools, MCP, connectors, and subagents share timeout/cancellation/output semantics.
- Output beyond the inline limit is recoverable from an artifact.
- Early pipeline failures follow the declared pipeline policy and are not silently reported as success.
- A dropped stream cannot wedge a session indefinitely.

### Evidence

- Build and Use record the same agent-observed execution evidence.
- Tool-specific structured details may improve evidence but no universal verifier is required.
- Missing or contradictory observations produce `verification_state=not_verified` or `unknown`, or an `uncertain`/`timed_out` outcome, never an unqualified success.
- Governed external writes require authoritative acknowledgement or verification evidence before `effect_state=applied`.
- HITL approval is recorded separately from execution evidence.

### Usability

- Build mode does not require approval for ordinary local read/edit/test loops.
- Use mode remains capable of multi-step personal workflows.
- HITL appears only for configured capabilities/actions.
- Normal local workflows do not require a separate tool implementation for each execution surface.

## 17. Open decisions

1. The exact compatibility bridge for existing `ToolResult`, `EffectResponse`, governance proposal, and work-queue models.
2. Whether the initial SQLite retention periods need deployment-level overrides.
3. Which connector-specific payload fields should be redacted beyond the common credential policy.

## 18. Decision summary

The proposed architecture is:

```text
one Pi-like execution kernel
+ Build profile with tool-authoring access
+ Use profile with approved-tool access
+ capability-level policy
+ trusted brokered shell with an OS-isolated helper
+ selective HITL for Use-mode typed actions
+ durable SQLite operations and artifacts
+ truthful executor/effect/verification receipts
```

This preserves Pi-like effectiveness for Build mode, keeps Use mode broad rather than crippled, and addresses Jen's issue family at the platform-contract level instead of adding another tool-specific fix.
