# MT Execution Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status (2026-10-09): paused while SL/SR are delivered.** Read the
[MT checkpoint and exact resume sequence](../../audits/2026-10-09-mt-paused-checkpoint.md)
before restarting. No-op seccomp attribution passed; namespace/bwrap feasibility
and hardened MT remain unqualified. No current Docker/backend execution authorized.

**Goal:** Qualify and implement Linux Docker tenant/user execution and protected credential boundaries for MT.

**Architecture:** One engine package/release with trusted tenant coordination and isolated user execution; protected integration adapters remain outside arbitrary-code environments. Reuse current sandbox/governance components through a small authenticated dispatch interface. A feasibility stop gate precedes permanent backend implementation; failure never selects the soft/null backend.

**Tech Stack:** Existing Python/pytest, Linux Docker, Linux OS isolation and the qualified existing sandbox component; no microVM/fleet/identity platform.

**Spec:** [#40](../specs/2026-10-07-execution-authority-boundaries-design.md) and [#41](../specs/2026-10-07-execution-receipt-contract-design.md). Contracts consumed from [core plan](2026-10-07-engine-contracts-and-receipts.md).

## Global Constraints

- The first hardened multi-tenant target is Linux Docker MT.
- No arbitrary user code is imported or executed in privileged coordination.
- Execution identity is provisioned and persisted with tenant+user binding. It is not a modulo hash of user names.
- Private-resource protection is symmetric: seniority, billing ownership, company membership and membership administration do not confer private-content access.
- No general sudo or shared-system modification; no Docker socket or deployment secrets in user execution.
- Retain existing execution timeouts/output bounds and necessary observation/transport bounds; no new availability or noisy-neighbor guarantee.
- Failure blocks hardened MT; do not fall back to the soft/null sandbox.
- Automatic replay of uncertain writes is prohibited by default.
- There is no new mandatory fairness, per-user CPU/memory/storage quota or model-spend control in the first scope.
- Fresh-store breaking cutover; old/customer data remains untouched. No automatic migration.
- MU microVM provisioning, fleet UI, interactive CLI and customer deployment modifications are out of scope.
- Docker startup/build/pulls and dependency acquisition require explicit execution authorization. Plan approval alone does not start them.
- Use synthetic sentinels and local service doubles only; scrub inherited credentials and deny external destinations. Do not load `.env` or stop unrelated processes/containers.

## Review Focus

1. A bare shell/client ignoring proxy variables must still fail to reach protected/internal services — Tasks 1/3.
2. A symlink/rename swap must not expose sibling/private/control storage — Tasks 1/2.
3. A MCP startup/install/discovery hook must not inherit server credentials — Tasks 1/4.
4. Two fresh/restarted companies with the same user names must retain distinct provisioned identities — Task 2.
5. An allowed package/shared definition must not import its creator's credentials or execution privileges — Tasks 4/5.

---

## Files and shared boundary interfaces

Create:

- `src/sdk/worker_models.py`: `WorkerIdentity`, `WorkerDispatch`, `WorkerResult`, `BoundaryQualification`.
- `src/sdk/worker_supervisor.py`: narrow provision/launch/terminate ownership, not a scheduler.
- `src/sdk/tenant_worker.py`: trusted per-tenant coordination process with fixed instance storage/configuration and authenticated ingress; cannot select another tenant at runtime.
- `src/sdk/worker_entry.py`: unprivileged execution process entry, no API-admin/provider environment.
- `src/sdk/worker_transport.py`: authenticated bounded dispatch over a per-worker Unix socket; no public arbitrary-code RPC.
- `src/sdk/protected_integrations.py`: registered trusted integration adapters and credential resolution.
- `src/sdk/secret_references.py`: owner-scoped secret references to operator-controlled storage, never a general vault.
- `src/sdk/execution_egress.py`: policy enforcement binding qualified worker networking to allowed destinations.
- `tests/refactor/linux/`: actual backend and Docker tests.
- `docker/mt/compose.yaml`, `docker/mt/README.md`: qualified recipe only; one shared container, explicit tenant/user boundaries.

Core plan supplies `Actor`, `RunSnapshot`, `ExecutionOwner`, `ExecutionRequest`, `InstanceState`, dispatch authorization and receipt/evidence methods. New worker types:

- `WorkerIdentity`: opaque instance/user IDs, immutable assigned UID/GID, generation, permitted mount references and qualified backend ID.
- `WorkerDispatch`: request/run IDs, actor binding, owner generation, immutable definition/arguments/resource fingerprints, expiry/nonce and MAC; no secret values.
- `WorkerResult`: explicit `ExecutionCompletion`, termination evidence and observations. Worker assertions are not automatically authoritative business evidence.
- `BoundaryQualification`: platform/backend ID, qualification revision and passed/failed required mechanisms. Caller-supplied flags cannot mark a backend qualified.

`worker_supervisor.provision(actor: Actor) -> WorkerIdentity`; `launch(identity: WorkerIdentity) -> None`; `terminate(identity: WorkerIdentity) -> ExecutionCompletion`.
`worker_transport.execute(dispatch: WorkerDispatch) -> ExecutionCompletion` is asynchronous; transport authenticates fixed worker identity and checks expiry/generation/nonce before dispatch.
`protected_integrations.execute(actor: Actor, request: ExecutionRequest, owner: ExecutionOwner) -> ExecutionCompletion` is asynchronous and accepts only registered trusted adapters with bounded input. It does not accept a script plus injected secrets.

### Task 1: Qualified Linux feasibility probe — STOP GATE

**Files:** Create `tests/refactor/linux/test_boundary_probe.py`, `tests/refactor/linux/probe_helpers.py`, `docs/audits/2026-10-07-linux-boundary-probe.md`; use current `src/sdk/sandbox.py` read-only initially. No permanent runtime scaffolding before this gate.

**Interfaces:** Produce an evidence-backed `BoundaryQualification` description with backend/launch/network/mount mechanics. Temporary probe code is explicitly throwaway. Candidate is existing bubblewrap with provisioned non-root identities; current root-service refusal and unshared network are constraints, not bugs to disable.

- [ ] Obtain explicit permission for isolated Docker execution and any needed image/dependency acquisition; inventory existing containers/ports without changing them. Create uniquely labelled probe resources with cleanup restricted to their recorded IDs. No permission/no usable Linux target means report BLOCKED, not pass.
- [ ] Write negative probe cases: `test_user_cannot_read_sibling_or_engine_sentinel`, `test_same_names_different_tenants`, `test_pid_environment_not_visible`, `test_symlink_rename_cannot_escape`, `test_private_install_survives_restart`, `test_bare_socket_cannot_bypass_egress`, `test_internal_loopback_and_dns_alias_blocked`, `test_terminate_stops_only_owned_process_tree`.
  Assertions: forbidden sentinel reads/network connections fail; authorized private write/read succeeds; opaque identities differ; installed synthetic executable remains after restart; sibling process survives termination. Include stdout/stderr and syscall failure evidence without secrets.
  ```python
  assert uncontained_control.can_read_sibling is True
  assert qualified_worker.can_read_sibling is False
  assert qualified_worker.can_reach_internal_service is False
  assert sibling_process_survived is True
  ```
- [ ] Run tests on an intentionally uncontained throwaway fixture and confirm RED for the forbidden operations. Do not use production as the negative control.
- [ ] Configure candidate mount/process/user/network isolation without disabling security checks; run actual Linux tests. The allowed network fixture must work while direct/raw-IP/redirect/private-DNS bypasses fail.
- [ ] Record exit-clean results and exact host/kernel/Docker/backend requirements, including namespace/seccomp/privileged-helper restrictions. Current backend root refusal stays enforced. **If candidate fails, stop for backend/design review; do not quietly invent another backend or enable MT.** If an alternative is proposed, user review precedes new implementation assumptions.
- [ ] Commit probe evidence and retained tests only: `test: qualify Linux MT boundary feasibility`. Runtime tasks below depend on this gate plus core interfaces. No qualification claim from skipped/mocked tests.

### Task 2: Persistent identities, tenant coordination and narrow worker launch

**Files:** Create worker models/supervisor/tenant worker/entry/transport; modify `src/storage/instance_state.py`, `src/storage/paths.py`, `src/sdk/sandbox.py`, `src/http/main.py`, `src/sdk/runner.py`, `src/sdk/kit.py`, `src/sdk/providers/factory.py`, `src/config/settings.py`; create `tests/refactor/test_worker_provisioning.py`, `tests/refactor/linux/test_worker_filesystem.py`.

**Interfaces:** Implement the worker identities/provision/launch/terminate/execute methods above. `InstanceState` records identity assignments and generation without reuse across live principals. `launch_tenant(instance_id: str) -> None` in worker supervisor starts the trusted `tenant_worker` with fixed instance root, reduced tenant-owned configuration/credentials and authenticated ingress. It routes the existing runner/coordinator/kit, not a second loop. A tenant coordinator cannot default to another tenant's root; payload/tool/provider caches and CoreMem namespace bindings include the instance.

- [ ] **RED tests:** `test_uid_assignment_is_persistent_and_collision_checked`, `test_forged_worker_actor_rejected`, `test_expired_or_replayed_dispatch_rejected`, `test_tenant_coordinator_rejects_cross_tenant_request`, `test_launch_failure_disables_mt`, `test_unrelated_process_not_killed`.
  Assertions: reopen preserves UID/GID; two principals never share assignments; nonce/generation substitution raises refusal; no ambient API token in worker environment; root/helper access cannot be requested by user code.
  ```python
  assert reopened_identity.uid == original_identity.uid
  assert red_identity.uid != blue_identity.uid
  assert forged_dispatch_accepted is False
  assert engine_secret_sentinel not in worker_environment.values()
  ```
- [ ] Run unit tests through the core plan's offline runner and actual Linux tests through the qualified uniquely labelled recipe; confirm RED.
- [ ] Implement using only the gate-qualified primitives. Any privileged launcher exposes fixed create/chown/launch operations on verified roots and approved identities, never arbitrary commands/paths. Backend health is mandatory at MT startup. User data/sockets and protected engine-state files have distinct ownership and mount visibility. Reject inaccessible/granted resource mounts rather than broadening the filesystem.
- [ ] Run provisioning, restart, symlink/rename/process visibility and termination tests; require exit 0. Confirm native/HTTP client credentials are absent from worker argv/environment/files.
- [ ] Commit: `feat: provision authenticated tenant and user workers`.

### Task 3: Enforced egress and dispatch authority

**Files:** Create execution egress; modify worker transport/supervisor, `src/sdk/execution_dispatch.py`, `src/sdk/authority_policy.py`; create `tests/refactor/test_egress_policy.py`, `tests/refactor/linux/test_worker_egress.py`.

**Interfaces:** `bind_egress(identity: WorkerIdentity, policy: InstancePolicy) -> None`; `authorize_destination(actor: Actor, host: str, port: int, protocol: str, state: InstanceState) -> None`. OS routing/filtering is supplied by qualified backend; policy checks do not rely on a cooperative client using environment proxy variables. Explicit infrastructure deny cannot be overridden by a user approval.

- [ ] **RED tests:** direct socket despite empty proxy env, IP literals, DNS resolving private addresses, redirect into internal service, localhost/Unix-socket daemon bridge, destination denied after approval, and allowed local fixture through controlled egress.
  Assertions: every denied connection fails at the boundary; protected integration cannot be reached through a parallel credentialed route; only host/protocol/port and safe reason appear in operational records.
  ```python
  assert direct_socket_connected is False
  assert redirected_internal_request_succeeded is False
  assert permitted_fixture_response == expected_fixture_response
  assert private_url_query not in operational_record
  ```
- [ ] Run unit and Linux egress tests; confirm RED.
- [ ] Implement the qualified egress binding and current-policy checks, including model/external-tool/tracing destinations separately. Reject unsupported protocols rather than silently allowing them. Preserve policy revision in dispatch evidence; no HTTP-method-only claim of database read-only authority.
- [ ] Run all egress probes with clients that ignore proxies, and recheck revocation while work is active; require exit 0.
- [ ] Commit: `feat: enforce worker egress and current dispatch authority`.

### Task 4: MCP, private installations and protected credentials

**Files:** Create protected integrations/secret references; modify `src/sdk/tools_core/mcp_manager.py`, `src/sdk/tools_core/mcp_config.py`, `src/sdk/tools_core/mcp_bridge.py`, `src/sdk/tools_custom.py`, `src/sdk/tool_index.py`, worker entry; create `tests/refactor/test_mcp_boundaries.py`, `tests/refactor/linux/test_mcp_secret_isolation.py`.

**Interfaces:** `resolve_secret(actor: Actor, reference_id: str, request: ExecutionRequest) -> str` is callable only by protected adapters after authority validation; returned value never reaches receipt/admin projection. `start_user_mcp(identity: WorkerIdentity, config: MCPServerConfig) -> None` runs discovery/stdio processes through worker transport, with minimal environment and only policy-consistent scoped user credentials. Protected stdio/HTTP MCP runs separately with registered credential-bearing adapter code, not user-supplied arbitrary commands.

- [ ] **RED tests:** `test_stdio_mcp_does_not_inherit_engine_env`, `test_discovery_and_install_hooks_are_unprivileged`, `test_protected_secret_unreadable_by_user_code`, `test_user_mcp_db_grant_is_actual_scope`, `test_arbitrary_script_cannot_request_secret_injection`, `test_private_install_cannot_modify_shared_system`, `test_suspension_blocks_new_credentialed_calls`.
  Use a synthetic secret sentinel and local SQL/service double with enforced read/write identities. Assert denied mutation even if a tool says read-only; protected adapter requires approval and binding; unauthorized shell/direct MCP calls fail; no sentinel in logs/evidence.
  ```python
  assert protected_secret_sentinel not in user_worker_output
  assert unauthorized_database_write_count == 0
  assert discovered_mcp_uid == provisioned_identity.uid
  assert shared_system_modified is False
  ```
- [ ] Run unit/Linux cases; confirm RED.
- [ ] Replace MCP inherited `os.environ` startup with explicit boundary dispatch. Represent protected secrets by references and version; separately own private/team/company credentials. Installation environments persist under user-owned qualified paths; package source policy is checked without reviewing every script edit. Process callbacks use bound operation authentication, not unrestricted caller identity.
- [ ] Run MCP transport/concurrency/cache/reconnect plus new secret/isolation tests; require exit 0. Interrupt stdio/HTTP work and confirm #41 executor/effect uncertainty rather than fabricated termination.
- [ ] Commit: `refactor: isolate MCP and protected integration credentials`.

### Task 5: Granted team resources and immutable execution definitions

**Files:** Modify `src/sdk/tools_custom.py`, `src/sdk/tool_index.py`, `src/sdk/tools_core/skills.py`, `src/sdk/profile_loader.py`, `src/sdk/coordinator.py`, `src/http/routers/tools.py`, `src/http/routers/skills.py`, `src/http/routers/subagents.py`, `src/storage/paths.py`; create `src/sdk/resource_resolution.py`, `tests/refactor/test_shared_resources.py`, `tests/refactor/linux/test_shared_definition_execution.py`.

**Interfaces:** `resolve_resources(actor: Actor, state: InstanceState) -> tuple[ResourceGrant, ...]`; `snapshot_definition(actor: Actor, resource_id: str, state: InstanceState) -> str` returns immutable revision reference used by RunSnapshot and dispatch. This is grant-aware use of existing loaders, not a universal manifest compiler/tool registry replacement.

- [ ] **RED tests:** `test_list_does_not_discover_private_names`, `test_shared_definition_has_no_creator_credentials`, `test_shared_subagent_executes_callers_authority`, `test_maintainer_edit_applies_only_to_future_runs`, `test_definition_file_changed_after_approval_refused`, `test_offboarding_does_not_transfer_private_files`, `test_team_definition_is_not_writable_by_consumer`.
  Assertions: CEO names/source/history absent for staff; shared subagent uses staff grants and resolved launch model; old immutable revision remains stable; changed arguments/code invalidate prior approval; no creator environment mounted.
  ```python
  assert private_resource_name not in staff_listing
  assert delegated_actor.user_id == caller.user_id
  assert delegated_actor.scopes == bounded_caller_scopes
  assert running_definition_revision == original_revision
  assert changed_definition_approval_accepted is False
  ```
- [ ] Run new files and qualified Linux shared-resource tests; confirm RED.
- [ ] Bind existing tool/skill/profile discovery to explicit grants and immutable assets. Do not restore legacy `team_root` helpers wholesale or deployment-global Tools as implicit cross-tenant sharing. Team/group membership is many-to-many inside one company. Resource maintenance remains separate from use and secret ownership.
- [ ] Run source-loader, registry-order, skills, profile/subagent launch and all shared-resource negatives; require exit 0.
- [ ] Commit: `feat: resolve granted team resources without borrowed authority`.

### Task 6: Qualified Docker recipe, operations and full MT gate

**Files:** Create `docker/mt/compose.yaml`, `docker/mt/README.md`, `tests/refactor/linux/test_mt_acceptance.py`, `docs/audits/2026-10-07-mt-qualification.md`; modify `docker/Dockerfile`, `docker/DEPLOYMENT.md`, `DEPLOYMENT.md`, `AGENTS.md`, `CHANGELOG.md` only for qualified changes.

**Interfaces:** Deliver qualified backend/recipe consumed by native MT acceptance. Startup refuses unqualified identity/worker/egress setup. Persistent state and protected secrets are separate from immutable runtime; manual stop/update/restore does not enable schedules or replay uncertain writes. No per-user quotas/fleet controller.

- [ ] **RED tests:** complete red/blue companies, equal-name users, two teams, CEO/staff privacy, sanctioned personal and protected MCP, shared subagent, same-session observer, stale worker/callback, stop-copy-restart and external-effect-before-receipt failure. Assert all #40 negatives and #41 unknown/late evidence behavior under actual Linux processes.
  ```python
  assert cross_tenant_private_access_count == 0
  assert stale_owner_accepted_transitions == 0
  assert external_write_calls_after_restore == 1
  assert restored_schedules_enabled is False
  ```
- [ ] Run actual uniquely labelled Docker acceptance; confirm named missing cases fail rather than skip.
- [ ] Implement missing recipe/lifecycle wiring; use the approved worker prerequisites, not broad privileged Docker mode or exposed socket. Retain weak legacy recipes with accurate warnings, not as fallback. Record compatibility version and persistent-state requirements; refuse old/incompatible stores without touching them.
- [ ] Require Linux tests, core Python suite, relevant static checks and native MT acceptance from the native plan to exit 0 before public readiness. Record exact commands, versions, skips and overhead. A Compose-only check is not acceptance.
- [ ] Commit: `docs: qualify hardened MT deployment and boundaries`.

## Completion boundary

Hardened MT is enabled only after core/native acceptance and this real Linux gate.
This plan does not provide MU hostile-tenant containment, resource fairness or
availability guarantees. If the feasibility backend cannot meet the requirements,
return to architecture review rather than weaken the boundary.
