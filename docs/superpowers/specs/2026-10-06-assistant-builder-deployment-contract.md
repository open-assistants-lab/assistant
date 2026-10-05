# Assistant Builder and Deployment Contract

Date: 2026-10-06
Status: Proposed written design for user review. No implementation, installation, production access, or deployment is authorised by this document.
Basis: `docs/strategy/2026-10-06-platform-and-native-product-roadmap.md` and `docs/audits/2026-10-06-reference-deployment-inventory.md`.

## 1. Intent

Make it possible for a company to create and operate its own assistant without rebuilding agent infrastructure, while retaining a finished native product on the same engine.

The first deliverable is a reproducible reference deployment, not a new SaaS dashboard, package ecosystem, ontology editor, or orchestration engine.

Success: a builder independently installs a separate assistant instance, runs safe readiness checks and representative tasks, and performs documented restart/backup/recovery without editing the engine or relying on undocumented help.

## 2. Product concepts

- **Package:** versioned reusable code, profiles, tools/skills, domain bindings, process rules, service composition and tests. Contains no customer secrets or runtime records.
- **Instance:** a customer's configured installation of a package, with its own state, credentials, endpoint, access policy and operational history.
- **Organisation:** owns the instance and determines membership/administrative authority.
- **Actor:** authenticated person or service requesting work or approving an action.
- **Assistant:** the configured agent experience, which can serve one actor or a vetted team.
- **Deployment:** execution location/topology and its enforced isolation boundary.

These concepts must not be collapsed into a user_id or container name. This design documents ownership mappings; it does not add a multi-organisation database or refactor current stores. A dedicated customer runtime is the initial cross-customer boundary. Within that runtime, conversation/knowledge/credential sharing must be declared, not implied by organisation membership.

## 3. Approaches considered

1. **Convention-first reference deployments — selected.** Reuse existing PROFILE.md, TOOL.md, skills, Compose/systemd and domain files. Define common lifecycle and ownership rules. Minimal platform changes are identified through acceptance evidence.
2. **New universal manifest/compiler immediately.** Centralised validation is attractive but would guess abstractions before the three reference cases establish them. Deferred.
3. **Complete managed multi-tenant control plane first.** Eventually useful for provisioning/billing but delays the core builder proof and compounds unfinished identity/isolation work. Deferred.

## 4. Responsibilities

| Builder supplies | Platform supplies | Deployer supplies |
|---|---|---|
| Domain meaning, identities/source mappings, process rules, tools, skills, profile, tests | Runtime, provider integration, governed dispatch, persistence/API/events and documented extension/lifecycle surfaces | Runtime placement, authorised model endpoints, secret values, storage, users/access, budgets, observability consent, operations |

The same company may fill builder and deployer roles. The distinction is responsibility, not a requirement for different people.

Existing runtime guarantees are the starting point. Primary-run recovery, new policy migration, state ownership changes or other unbuilt features are not implied by this contract.

## 5. Package contents

Logical requirements, not a new file-format mandate:

1. Package identity/version and tested engine version/image digest.
2. Profile, tools, skills and service definitions.
3. Required configuration names, types, defaults and validation expectations.
4. Required secret references and the components authorised to consume each.
5. Domain entities/relationships, source bindings, metrics/aliases and process rules where applicable.
6. Permissions, approved action paths and safe readiness/workflow checks.
7. Mutable storage inventory, backup/restore, upgrade and recovery procedure.
8. Known limitations, data flows, external sharing/retention and diagnostic policy.

Use current directories first. A later manifest may index these artifacts; it must not become a second policy authority or per-tool metadata taxonomy.

Immutable package content, instance configuration, secrets and mutable state are separate logical classes. Read-only code/semantic mounts are preferred where supported; mutable paths are enumerated. Customer source data and dated evidence are not bundled into reference packages.

## 6. Configuration and compatibility

- Runtime endpoint, ports, storage locations, instance identifiers and model settings are explicit instance configuration.
- No developer checkout, named Docker container, iCloud availability, static port or production account is an implicit prerequisite.
- Required missing secrets refuse readiness; output names the missing reference, never its value.
- Validation does not print resolved secret-bearing Compose configuration or environment dumps.
- Pin package and runtime versions together. A mutable `latest` tag is not sufficient for a reproducibility gate; record the actual digest if a tag is used for acquisition.
- Preserve one-process-per-user-store limitations until a separate design changes them. Do not start two runtime replicas against the same mutable store.
- Instance configuration narrows the package's supported authority; a package cannot grant itself operator privileges.

## 7. Ontology and process rules

Support authored semantics as first-class package content. Jen's enforced identity/preservation models and admi's certified metrics/aliases are valid different implementations.

Derived catalogs, relationship views and prompt guidance should reuse authoritative definitions where feasible. Business meaning, alias resolution, approved calculations and source authority cannot be safely inferred merely from schema introspection.

Enforcement remains code/policy at execution boundaries. Examples:

- A frozen Zii target set is bound to the approved operation.
- A migration refuses unreviewed differences before mutation.
- PII permission derives from authenticated authority, not a model-supplied boolean.
- A metric's definition/version and source freshness are visible in its evidence.

No generic process compiler is required for the reference deployment. No reimplementation of Jen's domain control plane is proposed.

## 8. Customer and actor authority

- Unrelated customers use separate initial runtime/storage/credential boundaries.
- Membership does not grant all assistants' sources or all conversations.
- Channel user mappings are vetted by the deployer and checked against the active identity resolver. IDs supplied by a client or model are not sufficient authorization.
- Requester, approver, and execution service identity are retained distinctly for consequential actions.
- Technical deployment administration does not automatically grant business-action approval.
- A shared service credential is an explicit customer decision; per-user token copies do not prove per-user upstream access scoping.
- Soft sandbox/file permissions are not advertised as hostile-user or secret-exfiltration containment.

A pilot may use existing trusted-user mappings, with its limits disclosed. General shared-team state and multi-organisation API authorization need their own approved designs before exposure beyond those limits.

## 9. Lifecycle contract

### Install and readiness

Validate package/configuration/ownership, resolve authorised secret references, prepare explicit storage, start the required services, run migrations only under an approved procedure, and execute safe checks. Existing tools are sufficient initially; no new assistantctl command is claimed as shipped.

Readiness is distinct from liveness. A serving HTTP process is not ready if its profile, domain data, credentials, worker or required source is unavailable.

### Operation and monitoring

Expose engine/package versions, liveness, readiness reasons and domain health. Reuse available interfaces; identify missing endpoints as later implementation tasks.

Shared health: provider connectivity/configuration, active/stuck work, approval queue, usage/cost, storage/backup status.

Domain health: Jen worker and uncertain operations; eddyave report/source failures; admi source freshness, ontology drift and query/artifact failures.

Health/diagnostics default to allowlisted metadata without customer payloads or secrets. Full-content tracing requires separate explicit acceptance.

### Pause and upgrade

Define how to stop new work/writes without assuming this cancels an in-flight external action. Quiesce, capture versions/state, back up, migrate/replace under review, then validate. Never interrupt a consequential action and silently retry it.

Revalidate or invalidate approvals when the execution definition changes. Rollback documents database compatibility; restoring an old image is not an automatic data rollback or reversal of an external action.

### Backup and recovery

Enumerate runtime/domain databases, artifacts, configuration and secret recovery references. Prove restore into a separate test location. Keep restored schedules and writers disabled until reviewed. Do not copy one customer's records or keys into another instance.

## 10. Reference deployment acceptance

### Jen

- Independent fixture/staging deployment with explicit script/service/token/lock/ledger requirements.
- Correct target resolution, real pending approval, rejection/no execution and verified outcomes tested without production mutation.
- Distinguish delivery-sync control-plane guarantees from other existing write paths.
- Credential accessibility and authoritative audit boundaries are documented, not overstated.

### eddyave

- Explicit relay/runtime/auth/profile installation without developer-path or container-environment discovery.
- Fixture-backed report structure, evidence labels, source failure and one recompilation behaviour.
- Follow-up session continuity, cancellation and clear missing-source/model failures.
- Existing research archives remain immutable evidence, not mutable deployment content.

### admi

- Reviewed ontology and version visible; concepts/metrics/patterns resolve to the intended sources.
- Read-only DB authority verified through safe grant/settings inspection, not destructive test writes.
- PII authority assessed across the complete path; model-provided allow_pii is not authorisation.
- Artifact export/sharing/retention is explicit; diagnostic errors cannot expose credentials.

### Cross-reference gate

At least two unlike workflows install under the same documented lifecycle conventions with no customer-specific engine edits. An independent builder then attempts the procedure; record setup time, failures and every manual intervention. Do not claim external reuse from copying our own installations alone.

## 11. Native acceptance boundary

The parallel native product proves: install -> configure model -> grant file access -> ask about a CSV/spreadsheet -> receive a checked answer and saved analysis artifact -> reopen without losing results.

Use a known-answer dataset and explicit checks. A nontechnical user completes it without terminal/developer assistance. This is an acceptance definition, not a UI implementation design or an iPhone/remote-connectivity commitment.

## 12. First implementation slice after review

1. A reproducible Jen fixture/staging deployment and readiness checks using existing formats.
2. A contrasting eddyave reference using the same conventions; admi's authored ontology requirements shape the contract even before its package conversion.
3. Only then a thin shared validator/operator layer for demonstrated repetition.

### Selected first-slice environment and proof boundary

The first plan targets a new isolated local worktree and temporary instance directories. Automated acceptance uses synthetic stores and an in-process scripted model provider; no live LLM, Zii, ClickHouse, Telegram or customer credentials. An optional Docker smoke uses an explicitly supplied digest-pinned engine image, a unique Compose project, loopback publishing and an internal network. It is never pointed at a current deployment. No production/staging mutation is authorised.

This is a Jen-shaped packaging/approval/readiness harness, not a port of all 33 tools or proof of real Zii parity. Domain-independent deployment conventions are proved first. Real Jen integration and deployed-version verification require a separate slice and explicit access approval. eddyave, admi and native acceptance remain separate plans.

### Existing mechanisms and reuse

`kits/factory.py` and `src/sdk/kit.py` already define content-kit conventions, but current kit installation targets subagents rather than the main assistant and does not deploy service compositions. `scripts/partner_deploy.sh` and `scripts/generate_enterprise_compose.sh` already generate deployments, but inspection finds fixed ports, mutable image defaults and configuration/secret practices that do not satisfy this contract. Do not invoke them unchanged against a customer or create a second platform-wide kit/policy authority. The first reference uses existing PROFILE.md, TOOL.md and Compose formats with narrowly scoped local preparation/validation helpers; modifications to the legacy tooling are a separately owned follow-on.

No production credentials need to enter the reference package.

## 13. Concurrency and exclusions

Active bug work at inspection: `.worktrees/b11-storage`, touching storage/history, memory/message surfaces, tests and uv.lock. Main also contains other staged/uncommitted work.

This lane writes new documentation only. Do not change storage, identity, dependencies, existing runtime code or customer deployments. Do not invoke reference modules/tests that may initialise services or contact production. Revalidate source contracts against reviewed merged fixes before implementation.

Excluded: generic ontology editor/compiler, new messaging channels, Rust relay, graph builder, complete fleet SaaS, billing automation, native remote client and clinical deployment.

## 14. Design self-review

- **Scope:** contract + first reference-deployment design; not a full platform rewrite.
- **Consistency:** organisation ownership is explicit without claiming current stores already implement it; authored ontology is preserved; derived views are optional.
- **Safety:** production mutation, secret copying and unsafe restored schedules are excluded; declarations never substitute for actual enforcement.
- **Evidence:** current inventory distinguishes local code, historical docs, proposed work and unverified deployed behaviour.
- **Self-review correction:** selected an offline synthetic first slice; separated harness proof from real integration; documented existing kit/deployment mechanisms and why they cannot be reused unchanged.
- **Next gate:** user authorised plan writing after this review; execution still requires review of the written plan and selection of an execution method. No implementation or deployment is authorised by plan creation.
