# Jen Reference Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, synthetic Jen-shaped deployment that proves package installation, authored domain bindings, approval-gated fixture changes, readiness and offline recovery without accessing Gong Cha production.

**Architecture:** Add a self-contained reference under `examples/jen_reference/`, using existing PROFILE.md, TOOL.md and Compose formats. A small SQLite-backed fixture replaces Zii; scripted model responses exercise the real custom-tool loader, AgentLoop, HITL middleware and governance execution. Optional container smoke proves installation/startup only; it does not establish live-model or real-Zii parity.

**Tech Stack:** Python 3.11+, standard-library SQLite/filesystem/subprocess, existing PyYAML/pytest/AgentProfile, existing Assistant runtime, optional Docker Compose. No new dependencies or lockfile changes.

**Spec:** `docs/superpowers/specs/2026-10-06-assistant-builder-deployment-contract.md`, especially sections 5-10 and the selected first slice in section 12.

## Global Constraints

- "No developer checkout, named Docker container, iCloud availability, static port or production account is an implicit prerequisite."
- "Required missing secrets refuse readiness; output names the missing reference, never its value."
- "Validation does not print resolved secret-bearing Compose configuration or environment dumps."
- "A mutable `latest` tag is not sufficient for a reproducibility gate."
- "Do not start two runtime replicas against the same mutable store."
- "Soft sandbox/file permissions are not advertised as hostile-user or secret-exfiltration containment."
- Automated acceptance uses synthetic stores and an in-process scripted provider; no live LLM, Zii, ClickHouse, Telegram or customer credentials.
- No production/staging mutation is authorised. No changes under `src/`, existing `scripts/`, existing `kits/`, existing tests, pyproject.toml or uv.lock.
- Work in a new isolated worktree from a reviewed commit; never reuse or clean an existing agent's worktree. Spec/plan are currently uncommitted: carry their exact reviewed versions into the execution worktree explicitly.
- This plan implements only the first reference slice. eddyave, admi, native UX, fleet administration, organisation identity/storage and primary-run durability require separate plans.

## Review Focus

1. Unsafe instance paths/project names or existing nonempty destinations must refuse before any write (Task 2).
2. Installed main profile/tool paths must match DataPaths, not the legacy partner script's unused profile mount (Tasks 2 and 5).
3. Unknown/ambiguous targets, no-op changes and stale revisions must never mutate another store (Task 3).
4. Rejection, duplicate approval and changed tool definitions must not execute unintended actions (Task 4).
5. Secret-bearing diagnostics, health-only false readiness and restored state accidentally starting services must be prevented (Tasks 5 and 6).

---

## Execution preflight

- [ ] Read the spec and reference inventory, then inspect current main/worktree status. Record the selected commit and active owners; do not assume bug work has ended.
- [ ] Create a fresh branch/worktree using using-git-worktrees. This lane owns only the new example/tests and targeted docs.
- [ ] Inspect `src/storage/paths.py`, `src/sdk/tools_custom.py`, `src/sdk/middleware_hitl.py`, `src/sdk/governance.py`, `src/sdk/profile_loader.py` and tests of those interfaces. If reviewed bug fixes changed them, update the plan's integration seam before coding; do not patch engine issues here.
- [ ] Never invoke `scripts/partner_deploy.sh`, `scripts/enterprise_smoke.sh`, Jen installers or customer test suites. They can start deployments or contact production.

## File structure

New example:

- `examples/jen_reference/__init__.py`: import marker only.
- `examples/jen_reference/PROFILE.md`: synthetic operational persona, no real stores/accounts.
- `examples/jen_reference/Tools/fixture_store_read/TOOL.md`: read fixture state.
- `examples/jen_reference/Tools/fixture_store_pause/TOOL.md`: explicit approval-gated fixture change.
- `examples/jen_reference/domain.json`: reviewed synthetic store IDs/aliases and revision semantics.
- `examples/jen_reference/fixture.py`: deterministic fixture CLI/domain checks only; imports no runtime/customer modules and performs no network I/O.
- `examples/jen_reference/instance.py`: preparation and static instance validation.
- `examples/jen_reference/readiness.py`: safe readiness aggregation, no writes or model requests.
- `examples/jen_reference/compose.yaml`: one isolated engine service, no model/provider service or real Zii components.
- `examples/jen_reference/config.yaml`: local synthetic defaults and approval policy using current supported keys.
- `examples/jen_reference/.env.example`: names/placeholders only, no usable key or mutable image default.
- `examples/jen_reference/README.md`: setup, checks, limitations, stopped backup/restore and upgrade runbook.

New tests: `tests/reference_deployments/conftest.py`, `test_jen_content.py`, `test_jen_instance.py`, `test_jen_fixture.py`, `test_jen_governance.py`, `test_jen_readiness.py`, `test_jen_restore.py`. Do not place them under the active storage owner's test files.

## Task 1: Author the minimum fixture package

**Files:** PROFILE.md, two TOOL.md files, domain.json, config.yaml, .env.example; `tests/reference_deployments/test_jen_content.py`.

**Interfaces:** Produces the package paths above; `domain.json` holds exactly two synthetic stores (`fixture-alpha`, `fixture-beta`) with stable IDs and initial revision 1. Defines tools `fixture_store_read(store_id: str)` and `fixture_store_pause(store_id: str, paused: str, expected_revision: int)`, where the tool schema restricts paused to `true|false`; the CLI normalises this to the Task 3 boolean API.

- [ ] Write tests `test_profiles_and_tools_parse`, `test_write_requires_explicit_approval`, `test_no_customer_endpoints_or_data`, `test_no_default_usable_secrets` using existing AgentProfile parsing and TOOL.md frontmatter parsing. Assert tool names/parameter schemas, `read_only: true` on the read and `read_only: false`, `requires_approval: true` on the write; reject command installation hooks and arbitrary script/URL parameters.
- [ ] Run `uv run pytest tests/reference_deployments/test_jen_content.py -q`; expected failure because package files are absent.
- [ ] Add the content. Profile clearly says synthetic environment. Tool commands call a single installed `fixture.py` relative to `{{tool_dir}}` (two parents to the user root), with an explicit fixture-state path; no network commands. Boolean passed as an enum string `true|false` at the CLI boundary and normalised once. Do not put a made-up provider in the profile: omit the model and use instance settings for optional smoke. Native tools are off; synthetic write is `ask`; scheduling and content tracing are off.
- [ ] Rerun the tests; expected all pass. Inspect configuration keys against current settings before committing.
- [ ] Commit only these example files and their test: `feat: add synthetic Jen reference content`.

## Task 2: Prepare independent instances without starting anything

**Files:** `examples/jen_reference/instance.py`, `__init__.py`; `tests/reference_deployments/test_jen_instance.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class InstancePaths:
    root: Path
    data: Path
    env_file: Path
    metadata: Path

def prepare_instance(package_dir: Path, destination: Path, *,
                     instance_id: str, owner_label: str,
                     engine_image: str) -> InstancePaths: ...

def validate_instance(paths: InstancePaths) -> list[str]: ...
```

Metadata records schema version 1, package version 0.1.0, selected engine image, instance ID and owner label; this is inventory, NOT runtime organisation authorization. Require instance ID `[a-z][a-z0-9_-]{0,31}` and a fully digest-pinned `repository@sha256:<64 lowercase hex>` image. Owner label is nonempty metadata, never a credential.

- [ ] Write tests for traversal/unsafe IDs, symbolic-link destinations, missing image digest, nonempty destination, absent files, spaces in parent paths, and two instances with disjoint state/keys. Assert validation occurs before directory creation; invalid input leaves no partial installation. Patch subprocess/socket to raise if preparation attempts execution/network.
- [ ] Run the new file; expected import/missing-function failures.
- [ ] Implement preparation by staging in a sibling temporary directory and publishing without overwriting an existing destination. Reject any package symlink; copy only the enumerated package files. Use existing `DataPaths(user_id='default_user', data_root=...)` in tests to verify actual profile/Tools install paths; installer itself copies into those demonstrated paths without importing/initialising MessageStore. Install the single fixture script/domain beside the main profile. Generate a synthetic API key with `secrets.token_urlsafe(32)`, write it in `.env` with mode 0600, and never return/log its value. Each instance has independent keys and mutable roots. CLI is `python -m examples.jen_reference.instance prepare ...` and `validate --instance ...`; no Docker invocation.
- [ ] Verify `uv run pytest tests/reference_deployments/test_jen_instance.py -q` passes; unknown/extra configuration is refused rather than forwarded arbitrarily.
- [ ] Commit targeted files: `feat: prepare isolated reference instances`.

## Task 3: Implement a network-free domain fixture

**Files:** `examples/jen_reference/fixture.py`; `tests/reference_deployments/test_jen_fixture.py`.

**Interfaces:**

```python
def initialise_state(db_path: Path, domain_path: Path) -> None: ...
def read_store(db_path: Path, store_id: str) -> dict[str, object]: ...
def set_pause(db_path: Path, store_id: str, paused: bool,
              expected_revision: int) -> dict[str, object]: ...
```

State table contains stable store ID, slug, paused and revision. SQLite is fixture state only—not a replacement for Jen's operational database or the run journal. Successful change increments revision once; a no-op leaves it unchanged; unknown target/stale revision raises a bounded error with no mutation.

- [ ] Write tests for read, unknown ID, ambiguous aliases refused (tools accept stable IDs only), stale revision, no-op, concurrent same-revision writers, repeated initialisation preserving changes, and read-back. Seed the two synthetic stores. Assert only one concurrent writer succeeds, and the other store never changes.
- [ ] Run the tests; expected missing implementation.
- [ ] Implement transactionally with SQLite `BEGIN IMMEDIATE` and a revision-guarded update. CLI has explicit `init`, `read`, `pause` subcommands, `--state` and fixed domain path; `pause` requires `--expected-revision` and validated true/false. JSON result has `ok`, `outcome`, `store_id`, `paused`, `revision`; errors never dump arbitrary filesystem/environment values. No URLs, arbitrary SQL, shell execution or customer imports.
- [ ] Run fixture and content tests; expected all pass. Test subprocess invocation with spaced paths using argv, not shell interpolation in test helpers.
- [ ] Commit: `feat: add revision-checked synthetic store operations`.

## Task 4: Prove real governance over the installed tools

**Files:** `tests/reference_deployments/conftest.py`, `test_jen_governance.py`.

**Interfaces:** Consumes Tasks 1-3, existing `get_custom_tools`, `AgentLoop`, `HITLMiddleware`, governance service and scripted provider from `tests/integration/fake_provider.py`. Produces acceptance evidence—not a new approval engine.

- [ ] Build function-scoped fixtures that force fresh temporary DEPLOYMENT_DATA_ROOT/PATH and reload/restore settings/cache state, synthetic API/config values, no cloud keys, and blocked Python HTTP/socket clients. Subprocess execution is allowed only for the installed fixed fixture CLI; do not monkeypatch governance decisions or the tool body to return success.
- [ ] Write `test_read_runs_without_approval`, `test_write_creates_real_pending_without_mutation`, `test_rejection_leaves_state_unchanged`, `test_approval_executes_once`, `test_changed_definition_refuses_execution`, `test_state_changed_after_proposal_refuses_write`, and `test_other_instance_cannot_use_pending`. Load installed tools via the real loader. Script the provider to emit exact calls. Assert pending/outcome records AND fixture DB state, not just response strings.
- [ ] Run the tests before completing the harness; failures must expose missing wiring. If the engine itself violates an assertion, report it to its owner with a reproducer; do not relax the assertion or fix src/ here.
- [ ] Complete the harness using real service calls: pending originates from HITLMiddleware, cancel through `service.cancel`, approved state through current `service.approve`, execution through `service.execute_approved(..., registry=...)`. Do not manufacture a pending directly for the main proposal test. Changed-definition and policy-drift cases reload the actual registry before execution. This proves governance-service/custom-tool behaviour; authenticated HTTP/channel-role enforcement remains a separate gate.
- [ ] Run `uv run pytest tests/reference_deployments/test_jen_governance.py -q`; expected all pass and zero network attempts. No actual Telegram approval is claimed.
- [ ] Commit: `test: prove synthetic deployment approval boundaries`.

## Task 5: Add safe readiness and optional container composition

**Files:** `examples/jen_reference/readiness.py`, `compose.yaml`; `tests/reference_deployments/test_jen_readiness.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    checks: dict[str, str]

def check_readiness(paths: InstancePaths, *,
                    runtime_url: str | None = None) -> ReadinessResult: ...
```

Checks are `package`, `configuration`, `profile`, `tools`, `fixture_state`, `runtime`. Without runtime_url report `runtime: not_checked`, `ready: false`; static validation can succeed separately. HTTP runtime_url accepts only literal loopback addresses, disables redirects, and uses a 5-second timeout. Never performs model/tool requests.

- [ ] Write tests for missing config/profile/tool/state, health-only false readiness, invalid/remote URLs, redirects, timeout, engine-image/version mismatch and secret redaction. A deliberately supplied key appears nowhere in readiness output/errors. Assert HTTP checks use the prepared instance key and inspect the installed tool catalog via the current `/tools` contract, not only `/health`; verify names match the two reference tools. Static file checks plus server catalog are necessary but not proof of LLM quality.
- [ ] Run tests; expected missing readiness implementation/Compose file.
- [ ] Implement bounded checks returning stable reason codes, never raw underlying HTTP/environment messages. Compose uses a unique `--project-name`, no container_name, one app with image from required ASSISTANT_IMAGE, `127.0.0.1:${INSTANCE_PORT}:8080`, internal network, no host/docker socket, required env file, SOLO_BYPASS=false, separate instance data root, and read-only specific profile/tool/fixture-code mounts over the instance's installed paths. Fixture DB alone remains writable alongside normal engine state. Do not mount real Jen source/secrets. No auto-run writer, scheduler or provider service. Runtime model setting is an explicit harmless local configuration; no model request is part of startup acceptance. All downloaded assets must already be in the chosen image/cache; offline startup failures are reported, not solved by enabling egress.
- [ ] Validate Compose structurally in unit tests, then optionally use `docker compose ... config --quiet` (never print resolved config). Tests that need Docker skip with an explicit reason when unavailable; no skip counts as a smoke pass.
- [ ] Verify new tests; commit: `feat: add reference readiness and isolated compose template`.

## Task 6: Prove stopped restore and write the operator runbook

**Files:** `examples/jen_reference/README.md`; `tests/reference_deployments/test_jen_restore.py`.

**Interfaces:** Uses instance preparation/validation and existing filesystem/Docker commands. No new backup daemon, scheduler or fleet CLI. Snapshot is an offline copy of the instance's mutable data plus non-secret version metadata; keys are separately recoverable instance secrets, not part of the reusable example.

- [ ] Write tests closing all fixture/governance connections, snapshotting temp data, restoring into a new test directory, and validating store revisions/history and pending-state preservation without executing pending actions. Use this exact offline recipe in tests and the runbook: validate the stopped source; reject symlinks anywhere in source data; `shutil.copytree(source.data, snapshot / 'data')` into a nonexistent destination; save non-secret source metadata separately; reject package/checksum mismatch before restore; prepare a fresh destination to generate new metadata/key; rename its freshly prepared data to `data.initial`; copy snapshot data to destination/data; compare installed immutable content checksums with the current package; delete only the known fresh `data.initial` after successful validation. On failure, keep all copies and report manual recovery; never overwrite a preexisting restore destination. This recipe is not a generic archive extractor or a new backup API. Assert source/destination state is independent and restored services never start. This is stopped backup/restore only, not online consistency or crash recovery proof.
- [ ] Run restore tests; expected initial failures until the runbook exists and its sequence is exercised by the harness. Gate restored fixture writes with the instance configuration set to deny; assert attempting to consume a restored approval leaves state unchanged. Re-enabling writes is a separate review action, not automatic restore behaviour.
- [ ] Write the runbook with exact preparation, validation, optional startup/readiness, stop, snapshot, separate restore and teardown commands. Every destructive command is confined to the uniquely named test project/destination; never `docker system prune`, broad rm, or `down -v`. Task 2 preparation must record the supplied engine digest and SHA-256 checksums of enumerated immutable package content in instance metadata; extend its tests here if necessary. State that different digests require revalidation. Restore procedure keeps services stopped until access/approvals/configuration are reviewed. Document real Jen mappings and gaps: no actual Zii auth, Telegram, fleet sync, preservation migration, domain worker, organisation identity or full recovery is proven.
- [ ] Verify tests and command examples agree. Optional smoke requires operator-supplied cached digest-pinned image; no login/pull/build against production tooling is automatic. Run two instances on separately selected ports; record profile/tool catalog readiness and cross-instance storage separation; stop/restart one and recheck without invoking LLMs. Report each executed check and each skipped one separately.
- [ ] Commit: `docs: document and test reference recovery lifecycle`.

## Final verification and integration

- [ ] Run `uv run pytest tests/reference_deployments/ -q`; require actual test execution and no cloud/customer access. Record exact count/output, not a predicted count.
- [ ] Run `uv run ruff check examples/jen_reference tests/reference_deployments` and scoped mypy for new Python modules. Resolve issues locally; do not modify engine settings or mypy baselines to hide failures.
- [ ] Run relevant existing governance/custom-tool/profile tests, then the wider suite at integration against the reviewed bug-fix baseline. Use isolated roots; inspect fixtures before commands that could initialise networked services.
- [ ] Review the diff: only planned new files/targeted docs; no runtime/storage/lockfile/customer modifications or secret/runtime artifacts.
- [ ] Record whether optional Docker smoke was executed, the image digest and baseline commit. Passing Python integration without Docker is not a successful container-deployment claim.
- [ ] Rebase/integrate with the runtime owner after review. No assumption of compatibility with future storage changes.

## Plan self-review

- Coverage: package/configuration (Tasks 1-2), authored semantics/targets (3), approvals (4), readiness/versions/privacy (5), offline restore/upgrades/limitations (6). Broader contract obligations for real integrations, partners, eddyave/admi and native users are explicitly outside this slice.
- Interface consistency: Task 2 owns InstancePaths; Task 3 owns fixture-state APIs; Task 5 owns ReadinessResult. Later tasks consume those exact names.
- Five review-focus classes are assigned concrete tests, including unsafe paths, main-profile wiring, stale state, definition drift and false readiness.
- Dependency order: Tasks 1-3 establish the package; 4 and 5 can proceed in separate ownership scopes after their dependencies; 6 follows both. Do not parallelise changes to instance.py or shared test fixtures without an owner.
- Residual: actual digest/image startup compatibility and authenticated channel roles need execution evidence; this plan neither invents an image digest nor claims synthetic tests validate Zii production safety.

## Handoff

Review the plan before implementation. Recommend native execution for this small, tightly coupled example, with independent review before integration; subagent-driven execution remains available. No implementation or container startup occurs merely because this plan was written.
