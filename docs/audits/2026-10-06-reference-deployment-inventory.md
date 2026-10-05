# Reference Deployment Inventory: Jen, eddyave, admi

Date: 2026-10-06
Method: read-only local source/document inspection. No live infrastructure probes, production calls, deployment actions, credential reads, or test execution.
Assistant baseline: 93fe49dd. Eddy Ave baseline: de0e486. Analytics baseline: ed000d0. These are checkout HEADs, not proofs of clean trees or deployed versions.

## 1. Sources of truth

| Reference | Application source | Other source |
|---|---|---|
| Jen | `~/Library/Mobile Documents/com~apple~CloudDocs/Agents/zii_portal_gongchaaus/Jen/` plus the parent `scripts/` | Parent AGENTS.md, Jen deployment docs and dated execution evidence |
| eddyave | `~/Developer/Python/eddy-ave/` | iCloud `Agents/eddyave/` is a research evidence archive, not the running application |
| admi | iCloud `Agents/analytics_gongchaaus/agents/admi/` | `deploy/admi/` and `runtime/admi/`; legacy `ontology_gongchaaus/Admi/` is explicitly retired in current deployment docs |

Do not copy customer records, audit logs, tokens, or runtime databases into a reusable package. Source locations are inventory evidence, not prescribed future install paths.

## 2. Jen: operational assistant

### Observed shape

- Telegram frontend with vetted-user and role checks.
- Assistant runtime with custom TOOL.md tools and platform approvals.
- Zii HTTP/API scripts, service-token authentication, shared resource locks.
- Mixed execution: direct in-container runner and host-side HTTP bridge.
- Postgres-backed domain control plane for delivery sync: operations, frozen targets, approval bindings, workers, scheduler health, notification outbox.
- Other workflows include snooze, ordering hours, availability, service charges, menu assignment/migration, jobs and team access.

Local tool files: **33 total**, **16** referencing `scripts/zii_tool.py`, **16** referencing port 8809, **1** other (`ch_query`). This classifies local command text, not deployed execution coverage. CR-J12 reports a different historical migration count.

### Domain understanding already implemented

`Jen/control_plane/models.py` includes Store, PortalBranch, StorePortalBinding, StoreAlias, IdentityReconciliation, Operation, TargetManifest, ApprovalBinding, TargetAction, ActionAttempt and EvidenceEvent.

Protected menu migrations implement fresh comparison/preservation checks and reviewed carry lists; availability is captured/restored. The parent runbook explicitly says approval alone does not establish preservation safety.

### Evidence and limitations

- `sync_execute/TOOL.md` describes prepare -> approve exact frozen manifest -> queue -> asynchronous control-plane execution.
- `snooze_execute/TOOL.md` invokes the direct runner, requires approval, and writes a separate tool ledger.
- `menu_change_execute/TOOL.md` invokes the bridge, is approval-gated upstream and specifies preservation/partial-outcome rules.
- `Jen/DEPLOYMENT.md` section 9 explicitly states direct production-write removal is only partial: several legacy exec routes depend on upstream approvals/trusted callers rather than equivalent handler-local approval binding.
- CR-J12 documents service-token copies and the honest limit that code running under the user's sandbox authority can read that user's token. File permissions do not establish model-inaccessible secrets.
- The profile records wrong-store proposals and confusion between hour systems. Prompt discipline is useful but not an enforced target identity guarantee.
- README contains early phase/TODO material; CR-J12 interleaves historical and later updates. Neither is a reliable standalone current-state manifest.

### Packaging requirements

Explicit services, script mounts, dependency requirements, token rotation/refresh procedure, locks, separate ledgers, runtime state, role mapping and approval paths. Preserve the domain control plane; a fleet manager must not replace it.

### Safe readiness checks to define

Validate mounted code/tool paths, service readiness, credential configuration without printing values, database migration state, worker/scheduler heartbeat, notification ownership, and approval rejection against fixtures/staging. Do not trigger a production sync or modify a store to demonstrate startup.

## 3. eddyave: property-research application

### Observed shape

- Separate Python FastAPI relay (`server.py`) and web UI over Assistant SSE.
- Root PROFILE.md is deployed to the Assistant user directory by startup code.
- Persistent browser session identifier and follow-up research conversation.
- Report-structure checks and one recompilation attempt after unusable output.
- Local binding at port 8787; Assistant endpoint defaults to loopback.
- Current defaults depend on a named `assistant` Docker container and a developer-checkout data directory. API key fallback reads that container's environment.

### Domain/process content

Profile and property-research skill define address/buyer context, source routing, evidence labels, comparable-selection criteria, live tax-rule lookup, report sections and disclaimers. These are authored rules, not a formal universal ontology.

iCloud report files are dated evidence and must not be edited. Two agreeing sources are the current archive/profile convention for a verified figure; evaluation must also consider source independence, not mechanically count syndicated copies.

### Discrepancies and gaps

- README refers to `property-poc`; startup code defaults to `eddy-ave`. Deployment docs must agree with code and validated configuration.
- Documentation disagrees on model thinking values (`max` versus accepted values described in README); confirm the provider contract during later validation rather than treating either doc as proof.
- Runtime profile auto-copy couples frontend startup to an external mutable data tree. Reproducibility needs an explicit install step or clearly bounded lifecycle action.
- No customer-independent deployment package was found in the inspected paths. Existing POC documentation disclaims accounts, rate limiting and other product features.
- Report recovery is application-specific behaviour, not proof of durable primary-run recovery.

### Packaging requirements

Relay/UI, profile/skills, configured runtime endpoint/authentication, supported model settings, storage/session ownership, source tooling and evaluation fixtures. Replace implicit container discovery with explicit configuration in the proposed reproducible deployment.

### Safe readiness checks to define

Frontend/relay launch, authenticated runtime reachability, profile installation verification, valid model configuration, fixture-backed source failure/recompile checks, cancellation and follow-up continuity. No live paid research is required for installation checks; optional live read tests need separate consent/budget.

## 4. admi: business information and analysis

### Observed shape

Current repository contains:

- Assistant service, Telegram bot, Caddy file-serving service and cleanup service.
- Whitelist/user mapping and per-user profiles.
- ClickHouse query tools and Outline search/read tools.
- Governed ontology mounted read-only.
- Runtime audit/session-generation/files directories.

Local tool definitions: ch_query, ontology_list, ontology_read, ontology_catalog, ontology_version, outline_search, outline_read and file_save. Two per-user PROFILE.md files exist in the inspected tree; documents describing one user/three tools are not a complete current inventory.

Compose uses `assistant:latest`, fixed container names and host ports, optional env files, and a process healthcheck. These are portability/readiness concerns, not proof that the live deployment is broken.

### Ontology is already substantial

`ontology/` contains concepts, relationships, certified metrics, product aliases and approved SQL patterns. Definitions bind business meaning to tables/keys, include sensitivity and data quirks, and require review/version disclosure.

Do not replace these definitions with automatic schema inference. Derived catalogs and relationship views can complement the authored semantic authority.

### Safety questions requiring verification

- README claims database-enforced read-only access; DEPLOYMENT.md still lists a dedicated read-only account as open. No live grants were inspected, so DB enforcement is **unverified**, not assumed present or absent.
- `admi_tools.py::ch_query` has SELECT/WITH and forbidden-statement checks plus a caller-supplied `allow_pii` boolean. The inspected function does not receive authenticated actor authority. Tool description/profile claims that only an owner may request PII are not proof of deterministic authorisation. Inspect the complete effective path before declaring either a bypass or safety.
- Query code puts credential parameters in a URL and returns some underlying errors. Safe observability/error handling must ensure credential-bearing URLs cannot escape into logs/results.
- Generated files are served by bearer-like unguessable links without login, with documented retention. This must be an explicit export/data-sharing policy, not silently treated as private in-app storage.
- Ontology host edits are live; version/session refresh is procedural. Reproducibility needs coherent package versioning and drift reporting.

### Packaging requirements

Code + ontology + reviewed query patterns, external database/Outline bindings, user mapping, artifact-serving policy, cleanup retention, engine pin, runtime storage and migration/update procedure.

### Safe readiness checks to define

Parse/configure services, ontology/version/schema checks, fixture-backed query/PII authorization tests, database grants inspected with a safe read-only probe, source freshness, audit/artifact paths and file-serving refusal behaviour. Read-only DB guarantees must come from actual grants/settings, not executing a destructive probe.

## 5. Shared versus domain-specific

Shared conventions:

- Versioned immutable package content separated from instance configuration and mutable state.
- Explicit endpoint/identity mapping, customer ownership and access.
- Secret references and rotation procedures; no value copying into manifests.
- Installation/config validation, readiness, health, backup and upgrade contracts.
- Cost/usage visibility, safe diagnostics and workflow acceptance tests.

Remain domain-specific:

- Zii identity reconciliation, preservation and mutation procedures.
- Research sources, evidence labels, country rules and report structures.
- Certified business metrics, aliases, PII rules and SQL patterns.

There is enough overlap to define conventions, but not evidence that one tool runtime, one database layout or one universal ontology compiler fits all three.

### Existing platform packaging mechanisms (planning follow-up)

- `kits/factory.py` and `src/sdk/kit.py` already validate/install content kits. Current kit installation targets subagent profiles and skills, not main-assistant service deployment.
- `scripts/partner_deploy.sh` already generates a Compose deployment, but inspected code defaults to a mutable image, a predictable synthetic API key, a fixed host port, and a profile mount that must be checked against the actual main-profile path. Reload failure is tolerated. Do not execute it unchanged as the new reproducibility gate.
- `scripts/generate_enterprise_compose.sh` already generates container-per-user compositions, but its port assignment has a finite collision space and its key placeholders are not production secrets. `enterprise_smoke.sh` starts containers and is not a read-only inventory command.

These observations guide reuse of formats and conventions; no installer was run or modified.

## 6. Worktree boundary

Assistant main at inspection: 93fe49dd. `.worktrees/b11-storage` has active changes to message storage/history, memory/message surfaces, storage tests and uv.lock. No files there were changed by this inventory.

Builder/deployment design may proceed, but storage implementation, lockfiles, runtime identity/state refactors and journal/document implementation remain outside this lane. Revalidate the contract against the reviewed merged baseline before implementation.

## 7. Immediate outcome

The first package contract must support authored domain semantics and existing service compositions. No fleet dashboard, universal ontology editor, production migration or engine rewrite is authorised by this inventory.

Next review questions: ownership/visibility rules, actual supported deployment topology, safe verification of deployed versions, admi DB/PII enforcement, and a clean reproducibility trial using staging/fixtures.
