# Native Engine Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the required native client against the refactored engine, including an SL bundled-helper DMG and authenticated SR/MT connections.

**Architecture:** Retain the existing Zig app and use the version-2 server contract from the core plan. Split connection/protocol/helper-lifecycle state out of `main.zig` only where it prevents authority/cache confusion; no native agent engine. One DMG includes a private Python engine helper for SL, while remote targets do not silently start or use it.

**Tech Stack:** Existing Zig/native SDK and automation CLI, macOS Keychain, existing Python desktop-server helper, PyInstaller onedir packaging as an isolated build dependency, pytest and scripted-provider fixtures. Do not rewrite the app in another UI stack.

**Spec:** [#40](../specs/2026-10-07-execution-authority-boundaries-design.md) and [#41](../specs/2026-10-07-execution-receipt-contract-design.md). Requires [core contract](2026-10-07-engine-contracts-and-receipts.md); MT acceptance requires [qualified Linux boundaries](2026-10-07-mt-execution-boundaries.md).

## Global Constraints

- Native required; interactive CLI can wait.
- Target packaging is one macOS DMG that can select SL, SR, MT or MU connections.
- For SL it contains a private engine helper and Python runtime; users should not need Docker or a separate Python installation.
- Switching a target must not transfer authority or silently run remote work locally.
- User model selection updates only the addressed conversation, persists across restart, and takes effect on its next run.
- Existing conversations retain their saved model when the instance default changes; new conversations use the new default.
- The later same-session request is not queued and its prompt is not added to accepted conversation history.
- A browser/socket disconnect is not execution failure or automatic cancellation.
- Fresh-store breaking cutover; no automatic import of existing native credentials, histories or old server settings. Existing stores/Keychain entries remain untouched.
- MU hosting/sign-in/containment is not implemented here; unsupported MU service/version fails explicitly. A mode selector is not MU qualification.
- No automatic updater, interactive CLI or fleet dashboard.
- Automation uses synthetic connection credentials, isolated test app ID/Keychain namespace, isolated state and owned ports/processes. No production backend URLs, conversation DELETEs, live inference or user Keychain access.
- Build dependency downloads, Docker execution, signing/notarization credentials and publication each require explicit execution authorization. No installation/download is performed during planning.

## Review Focus

1. Switching SR/MT while an old stream/key load resolves must not apply old credentials/content to the new target — Task 1.
2. Model save failure/revision conflict must not look persisted or overwrite another device — Task 2.
3. Busy second-device submission must distinguish its rejected prompt while showing the accepted run — Task 3.
4. A stale helper rendezvous/PID must not attach to or kill an unrelated process — Task 4.
5. A fresh Mac without Python/Docker must start SL from the packaged artifact, not the developer checkout — Tasks 5/6.

---

## Files and interfaces

Existing `native-sdk-experiment/src/main.zig`, `theme.zig` and `tests.zig` remain the UI entry/build path. New small files:

- `native-sdk-experiment/src/connection_state.zig`: `ConnectionTarget` (opaque ID, SL/SR/MT/MU code, base URL, Keychain reference, connection generation) and isolated target-owned state.
- `native-sdk-experiment/src/engine_protocol.zig`: version-2 identity/config/admission/receipt/snapshot decoding and request construction.
- `native-sdk-experiment/src/helper_lifecycle.zig`: local helper launch/rendezvous/ownership; no remote fallback.
- `native-sdk-experiment/tests/engine_contract_suite.sh`: new hermetic automation lane, not the existing potentially live/destructive frontend suite.
- `tests/native_contract/fixture_server.py`: existing real server with isolated store/scripted provider and explicit synthetic identity; not a production deployment mode.
- `scripts/build_native_dmg.py`, `packaging/desktop_engine.spec`, `tests/packaging/test_desktop_bundle.py`: isolated onedir helper and DMG assembly/artifact tests.

Core API assumptions: `GET /v1/identity` with contract version `2`; revisioned conversation configuration; POST run/message admission returning accepted/existing/busy plus run ID; authorized run snapshot/events; explicit run-ID cancellation. Remote identity does not use desktop-only bootstrap.

Connection target URLs/IDs are not authority: server-verified identity binds the configuration surface. Keychain references are separate from provider keys. Each callback includes target generation and is discarded if it belongs to a retired connection.

### Task 1: Authenticated target selection and connection isolation

**Files:** Create connection state/engine protocol; modify `native-sdk-experiment/src/main.zig`, `native-sdk-experiment/app.zon`, `native-sdk-experiment/README.md`; add Zig tests in `native-sdk-experiment/src/tests.zig` and new `tests/native_contract/test_identity_contract.py`.

**Interfaces:** `connection_state.activate(target: ConnectionTarget) -> u64` increments generation and returns it. `engine_protocol.decodeIdentity(body: []const u8) -> !IdentitySummary` returns instance/user/mode/contract_version/configuration abilities; `IdentitySummary` is defined in the same module. `acceptCallback(target_id: []const u8, generation: u64) -> bool` rejects retired callbacks. Existing native credential effects load only the active target's reference.

- [ ] **RED tests:** Zig `target_switch_discards_old_stream_and_credentials`, `unknown_contract_version_refused`, `remote_identity_not_desktop_bootstrap`; pytest wrong-key/wrong-instance and TLS target failures. Assert distinct target caches, no credential value in URL/argv/logs, and no local launch when SR/MT fails to connect.
  ```zig
  try std.testing.expect(!acceptCallback(old_target_id, old_generation));
  try std.testing.expect(!remote_failure_started_local_helper);
  try std.testing.expect(!credentials_crossed_targets);
  ```
- [ ] Run `native test` from the isolated native app directory and the core offline runner for API tests; confirm named RED assertions.
- [ ] Implement target-owned state, per-target Keychain references and authenticated version negotiation. Start with explicit server-issued individual bearer credentials, not a new native SSO/account system; deployer-managed sign-in remains separate. Show clear unsupported/unauthorized state. No inherited per-user/model caches across targets.
- [ ] Run native tests and API contract file, require exit 0; build existing app with `native build`. Update README only for verified connection methods.
- [ ] Commit: `feat: isolate authenticated native connection targets`.

### Task 2: Conversation configuration and authorized profile/admin controls

**Files:** Modify engine protocol/connection state/main; create `native-sdk-experiment/src/configuration_state.zig`; add Zig tests and `tests/native_contract/test_configuration_contract.py`.

**Interfaces:** Define `ConversationChoice` (session ID, canonical model, revision, save status) and `ConfigurationPermissions` (instance config edit, private/shared profile maintain, operational view). Produce `requestConversationModel(session_id: []const u8, revision: u64, model_id: []const u8) -> ![]const u8` and `applySavedChoice(response: []const u8) -> !ConversationChoice` in engine protocol. Permission flags are server responses; backend still enforces them.

- [ ] **RED tests:** picker/cycle both request conversation PATCH, failed save preserves old saved state, 409 rehydrates rather than retries blindly, reopening restores saved model, old/new conversations differ after admin default update, company admin cannot view/edit private profile, and team maintainer can edit authorized shared profile only.
  Assertions: no `/settings default_model` write from a conversation picker; saved state changes only after successful committed response; active run keeps old resolved model; read-only controls cannot send privileged mutations.
  ```python
  assert conversation_patch_url.endswith('/config')
  assert global_default_writes == 0
  assert failed_save_displayed_as_persisted is False
  assert stale_revision_response.status_code == 409
  assert private_admin_edit_response.status_code == 403
  ```
- [ ] Run native and API configuration tests, confirm RED.
- [ ] Implement profile/instance/conversation configuration surfaces in the existing settings UI using opaque resources and server-granted maintenance rights. Keep instance default distinct from conversation choice; display pending/error/conflict save status. Main/subagent model displays use actual server snapshot, not guessed inheritance. Admin operational view excludes private details by construction.
- [ ] Run native tests, authority/configuration API tests and `native build`; require exit 0.
- [ ] Commit: `feat: expose scoped native configuration and saved conversation models`.

### Task 3: Busy attach, reconnect, approvals and honest receipt rendering

**Files:** Modify engine protocol/connection state/main; add `native-sdk-experiment/src/run_state.zig`, native tests, `tests/native_contract/test_observer_contract.py` and hermetic `native-sdk-experiment/tests/engine_contract_suite.sh`.

**Interfaces:** `RunView` carries active run ID, accepted prompt, admitted/resolved model, lifecycle/approval state, original receipt and separate late-evidence assessment. `applySnapshot(body: []const u8) -> !RunView`; `applyEvent(view: *RunView, body: []const u8) -> !void`; all applications are target-generation bound. A busy result points to its active run; it is not another accepted turn.

- [ ] **RED tests:** two clients/one session, busy rejected text never in history, attach shows same run/model/content, disconnect does not cancel, replacement snapshot after overflow, explicit cancel targets correct run, timeout/unknown effect with later applied evidence, approval drift/refusal, reader revoked while connected.
  Assertions: executor count is one; second prompt is visibly not accepted; no optimistic duplicate bubble survives; timeout remains original outcome even when assessment becomes applied; UI says cancellation requested until termination evidence exists.
  ```python
  assert first_view.run_id == second_view.run_id
  assert rejected_prompt not in accepted_history
  assert execution_count == 1
  assert timeout_view.original_outcome == 'timed_out'
  assert timeout_view.assessed_effect == 'applied'
  ```
- [ ] Run native reducer/API tests, confirm RED.
- [ ] Implement version-2 snapshot/SSE handling and explicit busy->authorized attach flow. Resume from current server state, not blind replay of outgoing prompt. Preserve request IDs across genuine retries, including after default changes. Render existing approval/evidence controls; stale events cannot update another session/target.
- [ ] Run new unit/API tests; run UI automation only against the new hermetic harness with uniquely scoped app ID/data/Keychain entries and scripted server. Never run the old suite unmodified against an ordinary backend; require exit 0.
- [ ] Commit: `feat: render server-owned native runs and busy-session observation`.

### Task 4: Owned local helper lifecycle and SL platform qualification

**Files:** Create helper lifecycle; modify `src/http/desktop.py`, `src/http/routers/bootstrap.py`, `src/__main__.py`, `native-sdk-experiment/src/main.zig`; create `tests/native_contract/test_sl_helper.py`, helper lifecycle Zig tests and `docs/audits/2026-10-07-sl-helper-qualification.md`.

**Interfaces:** Define `HelperHandle` (owned process identity, launch nonce, rendezvous path, base URL, credential reference, helper contract version). `launchLocalHelper(executable_path: []const u8, state_root: []const u8) -> !HelperHandle`; `stopOwnedHelper(handle: HelperHandle) -> !void`; rendezvous validates nonce/ownership/version. Python keeps existing desktop-server entry with loopback-only prebind and synthetic per-launch identity, not unauthenticated localhost bypass.

- [ ] **RED tests:** stale rendezvous, occupied unrelated port, PID reuse, multiple app launches, app disconnect/reconnect versus explicit shutdown, update with running uncertain write, restored-disabled schedules, missing helper/backend qualification. Assert no unrelated PID killed and no remote target starts helper.
  ```python
  assert unrelated_process_survived is True
  assert remote_target_helper_launches == 0
  assert stale_nonce_accepted is False
  assert loopback_without_launch_credential.status_code == 401
  ```
- [ ] Run helper/Python/Zig tests and confirm RED.
- [ ] Implement owned launch/shutdown/rendezvous and persistent user-owned state. Qualify SL arbitrary-code/credential enforcement on actual macOS separately from Linux MT: use the narrow supported macOS execution boundary, require synthetic process/filesystem/credential/internal-endpoint negative tests, and stop for review if that boundary cannot meet the spec. Do not infer SL containment from Linux tests or downgrade silently. No separate company/team provisioning is required for solo ownership.
- [ ] Run actual isolated macOS helper/tool/MCP negative tests and record exact supported runtime/boundary. Confirm local model calls are scripted, helper shutdown reports unresolved effects honestly and no synthetic-secret sentinel leaks. Require exit 0.
- [ ] Commit: `feat: qualify owned native SL engine lifecycle`.

### Task 5: Bundled runtime and one DMG artifact

**Files:** Create packaging spec/build script/artifact tests above; modify `pyproject.toml` and owned build lock only for a separately approved pinned PyInstaller build extra, `native-sdk-experiment/app.zon`, `src/http/desktop.py` resource lookup if needed; create `docs/native-dmg-build.md`.

**Interfaces:** `build_native_dmg(output_dir: Path, helper_dist: Path, native_binary: Path, signing: Literal['none','adhoc','identity']) -> Path` in the build script. Onedir frozen helper includes Python/dependencies/engine resources/seeds; native .app includes helper assets without shared mutable data or credentials. Runtime version comes from actual built package metadata, not stale app manifest copy. The final frozen distribution is the input to artifact tests, not source checkout imports.

- [ ] **RED tests:** `test_bundle_starts_without_system_python_or_docker`, `test_runtime_resources_loaded_outside_checkout`, `test_no_env_keys_user_data_or_test_artifacts_packaged`, `test_app_helper_contract_versions_match`, `test_update_retains_state`, `test_remote_connection_does_not_start_bundled_helper`.
  Assertions: restricted PATH with no developer Python/Docker still starts packaged helper and scripted native chat; all required seeds/assets resolve from bundle; credential sentinels/.env absent; persistent data lives outside the .app.
  ```python
  assert packaged_helper_exit == 0
  assert helper_python_origin.is_relative_to(bundle_path)
  assert secret_sentinel not in packaged_bytes
  assert app_contract_version == helper_contract_version == 2
  assert user_state_after_update == user_state_before_update
  ```
- [ ] Run artifact tests against an intentionally incomplete throwaway bundle, confirm RED. Seek explicit build-dependency/download permission before acquiring anything unavailable; no system/global installation.
- [ ] Implement onedir resource/package inclusion and native packaging using existing native CLI capabilities. Do not build a universal installer or fetch providers/models at launch. Keep runtime/tool dependency needs explicit; an absent optional external CLI produces a clear capability error, not silent package installation.
- [ ] Test the actual copied/mounted artifact outside checkout and venv; run `native test`/`native build` and artifact tests with process exit. Local ad-hoc tests do not prove clean-machine distribution: a clean macOS VM without Python/Docker must repeat SL acceptance. Developer-ID signing/notarization require separately authorized publisher credentials/network; without them label distribution proof incomplete, never claim a production-ready DMG.
- [ ] Commit: `build: package native app with private SL engine runtime`.

### Task 6: Required native SL/SR/MT acceptance and release handoff

**Files:** Complete hermetic suite and `tests/native_contract/fixture_server.py`; add `docs/audits/2026-10-07-native-engine-qualification.md`; update native README, root README/DEPLOYMENT/AGENTS and CHANGELOG only for actual evidence.

**Interfaces:** Deliver one qualified app artifact against the version-2 core and MT recipe. MU connection remains unavailable until a qualified service exists; no MU hosting claims.

- [ ] **RED tests:** actual app local packaged helper, authenticated non-default SR endpoint, two app/client observers with same session, server restart/model choice/history recovery, private/shared profile controls, MT red/blue same-name isolation, approval/cancellation/unknown effect and connection switching with late callbacks.
  ```python
  assert packaged_sl_chat_completed is True
  assert remote_work_executed_locally is False
  assert model_after_server_restart == 'test:model-y'
  assert unauthorized_mt_content_received == []
  assert retired_target_callback_applied is False
  ```
- [ ] Run targeted new automation against isolated scripted services; confirm missing UI/packaging paths fail.
- [ ] Complete missing wiring in owning modules; qualify API and app/helper version mismatch messages, manual upgrade/state retention, no credential/history target bleed. Cleanup only processes/app IDs/Keychain entries created by this harness.
- [ ] Require native tests/build, actual hermetic UI suite, qualified MT/Linux suite and copied-artifact/clean-Mac tests to exit 0. Record signing/notarization proof separately. Preserve actual counts/warnings/skips; synthetic service proof is not customer parity. No release/push/website update without separate authorization.
- [ ] Commit: `docs: qualify native engine product and deployment modes`.

## Completion boundary

Native success requires actual client flows and packaged SL runtime, not backend tests or a successful Zig build alone. The production DMG gate includes a clean supported Mac and publisher signing/notarization evidence; if those resources are unavailable, report the exact blocked gate. CLI and MU hosting remain deferred.
