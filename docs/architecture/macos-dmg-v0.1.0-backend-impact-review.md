# macOS DMG v0.1.0 — Backend Impact Review Memo

> **Status:** Draft for peer review (backend/security lane verified 2026-09-05 — see §3.1 amendment). This records confirmed product direction and the backend changes it implies. It is not an implementation plan and does not authorize code changes.
>
> **Audience:** Backend, security, release-engineering, and product reviewers.

## 1. Confirmed v0.1.0 product scope

Assistant v0.1.0 is a macOS, single-user, local-first product distributed as a signed and notarized DMG. Opening the native app must start and manage its bundled local backend; users must not install Python, `uv`, Docker, or run a terminal command.

### Confirmed decisions

- Canonical CLI/product command and Python distribution metadata: `assistant`. The current `assistant-sdk` name must be removed rather than retained as a deprecated alias. This does not restore a PyPI distribution plan.
- The app owns exactly one local backend instance and connects only over loopback. Desktop mode resolves the effective identity server-side as `default_user`; native clients do not send a configurable `user_id` or workspace identity.
- Top-level desktop navigation keeps **Tools**, **Skills**, and **Subagents** directly above **Settings**. Settings is reserved for application, model connection, appearance, and privacy preferences.
- Durable assistant data lives at `/Users/<macOS-login>/Assistant/` (`~/Assistant/`), not under `~/Library/Application Support`.
- The durable layout is:

  ```text
  ~/Assistant/
    Messages/
    Files/
    Memory/
    Skills/
    Subagents/
    .versions/
    .system/
    Logs/
  ```

- `Messages/` replaces the current `Conversation/` storage root.
- Email, contacts, and todos are out of scope. All `email_*`, `contacts_*`, and `todos_*` tools must be disabled in the DMG's local capability profile, hidden from the client catalog, and must not create their data stores.
- Browser automation is in scope. The DMG bundles and manages a version-pinned `agent-browser` CLI and compatible browser runtime as signed internal helpers. Users access browser automation through Assistant; they are never asked to install, name, or version-match its implementation.
- The first-run flow supports API-key providers and local model servers.
- First-run API-key flow: paste one key, identify a high-confidence provider locally, validate against that provider, select a model, then start chatting.
- When a key is unknown or ambiguous, users can select a provider/custom endpoint. An explicit opt-in **Check likely providers** action may validate a user-reviewed candidate list; it must never happen silently.
- Local-model choices: Ollama, LM Studio, a generic OpenAI-compatible server, and a custom endpoint. Generic OpenAI-compatible covers products such as llama.cpp, LocalAI, vLLM, and similar compatible servers.
- A successful context-compression/summarization run appears as a compact persisted transcript event with its before/after token estimate. There is no pre-threshold context warning or permanent composer context meter.
- No model is bundled in the DMG.

### Explicit non-goals for v0.1.0

- Remote/multi-device deployment, public hosting, or multi-user tenancy.
- Email, contacts, and todos.
- Arbitrary provider plugins.
- Automatic application updates.
- Shipping Docker or asking a consumer to run Docker.

## 2. Current-state gaps

The current repository does not yet meet the desktop-product contract:

| Current state | v0.1.0 requirement | Impact |
|---|---|---|
| `pyproject.toml` publishes `assistant-sdk` as the project/console name. | `assistant` is the sole product and distribution name. | Rename entry point and distribution metadata; update all Docker, compose, docs, tests, release scripts, and generated artifacts atomically. PyPI publication remains out of scope. |
| `native-sdk-experiment/src/main.zig` hard-codes `http://127.0.0.1:8080`, `native_sdk_chat`, and `personal` in many requests. | App discovers a bundled backend endpoint and backend-resolved `default_user` identity. | Create a runtime bootstrap/rendezvous contract; remove client-owned endpoint, user, and workspace constants. |
| `config.yaml` local default uses port 8080; current docs contain contradictory executable and port references. | Bundled backend must not collide with local servers such as llama.cpp or LocalAI. | Use a dynamically allocated loopback port and make one set of docs/configuration canonical. **Bind-host half (review P2): `config.yaml` also sets `api.host: 0.0.0.0` (all interfaces) — the loopback-only requirement covers both host and port.** |
| Bulk data (`data_root`) and settings/vault/capabilities (`data_path`) are split. | All durable app state is rooted in `~/Assistant`. | Map both roots under `~/Assistant`; do not leave `.app` bundle/CWD-relative writable state. |
| Conversation data currently lives under `Conversation/`. | New installs use `Messages/`. | Add a safe one-time migration/compatibility path. |
| `config.yaml` enables email sync. | Email is unavailable in v0.1.0. | The desktop profile must disable background email sync as well as all email/contact/todo tool registrations. **Current-state correction (review P2): `start_interval_sync` (`email_sync.py:468`) is never invoked in the repo — sync is an on-demand tool. The actual unconditional background task in the lifespan is the ConnectKit token-refresh loop (`_refresh_loop`, 300s, no enable flag); desktop mode must not start it.** |
| `native package --archive` packages the current native app only. **(External toolchain — unverified in this repo; only `native build/test/automate` are documented. Source or verify against the native toolchain.)** | DMG contains the native GUI, a production backend helper, and a managed browser-automation runtime. | Add a desktop release assembly process; a bare native-app DMG is insufficient. |

## 3. Backend changes required for local desktop operation

### 3.1 Desktop sidecar lifecycle

The native app needs an intentionally supported backend mode, not a shell invocation of development commands.

Required properties:

- Bind only to `127.0.0.1`.
- Allocate a dynamic port; do not reserve 8080 because local model servers commonly use it.
- Use a single-instance lock under `~/Assistant/.system/` so a second app launch attaches to, rather than kills, the owned sidecar.
- Publish a runtime rendezvous document only after the server is ready. It should contain the port, PID, launch nonce, server version, API version, and protocol version. It must never contain API-provider keys.
- The native app starts the helper, waits for authenticated readiness, and performs graceful shutdown/restart only for a sidecar matching its nonce.
- The local API should require a per-launch runtime bearer token for all non-health endpoints. Loopback alone is not a sufficient process-level boundary against other software running as the same macOS user.
  - **⚠️ Amendment (verified review, P1): today this requirement is DEFEATED BY DEFAULTS.** `SOLO_BYPASS` defaults to `True` and `SharedSecretResolver.resolve` (`src/http/auth/shared_secret.py:27-32`) short-circuits localhost **before** checking the Bearer token — a loopback sidecar with `API_KEY` set still accepts unauthenticated local calls today. **Desktop mode MUST disable `SOLO_BYPASS`** (or the desktop resolver bypasses the localhost shortcut entirely and requires the launch token). The §7 test "rejects requests without its launch token" is only effective with that change — as written, it would fail today.
- The sidecar must keep `~/Assistant` writable and treat the application bundle as read-only.

A likely shape is a dedicated internal mode such as `assistant desktop-server`, while `assistant http` remains the explicit developer/server command. The public product command remains `assistant`. **(Review P2): an existing switch point is `DeploymentConfig.mode` (`src/config/settings.py:28-32`, default `"solo"`, docstring already references `.dmg/.exe`) — the desktop-server mode should extend this existing flag rather than imply a greenfield mode concept.**

### 3.2 Data roots and migration

The desktop launcher must set both deployment roots before the server imports/initializes stores:

```text
DEPLOYMENT_DATA_ROOT=~/Assistant
DEPLOYMENT_DATA_PATH=~/Assistant/.system
```

This preserves a single backup root while retaining the application's existing distinction between bulk user data and private configuration/state.

Migration requirements:

1. Stop/lock the old server before migrating.
2. If legacy root data has `~/Assistant/Conversation/` and no `Messages/`, atomically rename it where possible.
3. If the pre-DMG native client tree `~/Assistant/Users/native_sdk_chat/` is the only user-data tree, promote its complete user data into the `default_user` root and rename `Conversation/` to `Messages/`.
4. If root-level and `native_sdk_chat` data both exist, do not merge automatically; keep both intact, record a clear recovery/error state, and require an explicit recovery/export path.
5. Write a durable migration marker only after the new layout passes basic storage checks.
6. Fresh installations create `Messages/`, never `Conversation/`.
7. Existing data is never copied into the app bundle or a CWD-relative `data/` directory.
8. Do not discover or import legacy source-checkout `data/users/...` configuration by scanning the filesystem. Desktop `.system/` settings start fresh; credentials are restored through first-run/Keychain. A later release may offer an explicit user-selected import flow.

The local release profile must also disable email synchronization before startup, otherwise background jobs can recreate excluded email state.

### 3.3 Capability policy

The desktop build needs a fixed v0.1.0 baseline capability profile:

- Force `email_*`, `contacts_*`, and `todos_*` to `none` before tool registration.
- Ensure the three excluded families are absent from model-visible tools and `GET /tools` responses for the desktop identity.
- Ensure email/contact/todo store and scheduler modules are lazy enough not to create `Email/`, `Contacts/`, or `Todos/` just because the server starts.
- Do not expose a client control that can re-enable these product-excluded tools.

This is deliberately stronger than hiding UI. It prevents agent invocation and creates a reproducible support surface.

### 3.4 Managed browser automation

Browser automation is a supported v0.1.0 capability, not an external prerequisite. The desktop release bundles a version-pinned `agent-browser` executable and its compatible browser runtime inside `Assistant.app`; the launcher owns its lifecycle and compatibility with the backend browser tools.

Required properties:

- Expose browser capability through the ordinary `browser_*` tools and the `web-automation` skill without showing `agent-browser` as a user setup step.
- Package, sign, notarize, and version the browser helper/runtime with the app; do not download an executable or browser after install.
- Keep browser profile/cache state under `~/Assistant/.system/browser/`. Browser-session secret material must use the platform secure store where supported and must not leak into application logs, tool results, rendezvous files, or exported diagnostics.
- Preserve existing approval/interrupt rules for consequential browser actions; a bundled runtime does not make external side effects implicitly approved.
- If the managed browser cannot launch, report browser automation as unavailable with a recovery action—never a terminal installation instruction.

### 3.5 Top-level utility navigation

The native app will restore **Tools**, **Skills**, and **Subagents** as persistent sidebar destinations above Settings. The backend already has unversioned routers for these resources (`/tools`, `/skills`, and `/subagents`), so this does not require a new backend subsystem. It does make their list/detail/scope/job contracts release-critical for the desktop client.

Required backend work:

- Expose the desktop-consumed operations through the documented versioned API surface and cover them with contract fixtures/tests. **(Review P2: the `/v1` versioned surface already exists for tools/skills/subagents/capabilities/conversation/ws — `src/http/routers/v1.py`. Reframe: this is "desktop identity filtering + contract fixtures on the existing `/v1` surface", not new API work.)**
- Return only the desktop identity's enabled tools/skills/subagents; the fixed exclusions in §3.3 remain enforced server-side.
- Preserve scope/capability enforcement on every detail, mutation, and subagent-job route rather than trusting a hidden/disabled UI control.
- Make unavailable features explicit and stable in responses so the client can render an empty state rather than infer absence from a transport error.

### 3.6 Context-compression transparency

The backend already emits the typed streamed `context_compressed` event with a `status` plus `before` and `after` `ContextSnapshot`s. Today the native client discards those values and adds the generic local sentence `Conversation summarized to fit context window.`

Desktop v0.1.0 must retain the event's exact before/after estimates in the versioned run-event/session-history projection so the client can render a durable transcript disclosure such as `Context updated · 46k → 9k tokens` after a successful compression. This is a transparency event, not an assistant message and not a pre-threshold warning.

Requirements:

- Project successful compression records into replayable conversation history with status, estimates when known, and event ordering.
- Do not render a success event for failed compression. Preserve its actionable failure path separately.
- Keep the summarization text itself model-internal in v0.1.0; the user sees the completed outcome and token reduction, not hidden prompt material.
- Add contract fixtures covering known/unknown token estimates, failed compression, and a reload of a conversation containing the event.

## 4. First-run provider and credential contract

### 4.1 Credential handling

The native app is responsible for durable secret storage in the macOS Keychain. The backend may hold active provider credentials only in memory for its current sidecar lifetime.

Implications:

- Pasted provider keys must not be written to `~/Assistant`, logs, diagnostic payloads, tool results, or the runtime rendezvous file.
- On each app launch, the native app retrieves an approved credential from Keychain and securely injects it into its authenticated local sidecar.
- Model selection, non-secret provider metadata, and local endpoint configuration may persist under `~/Assistant/.system/`.
- Connector OAuth is out of current scope. If it is later enabled, its vault root/key handling must be reviewed separately; `CONNECTKIT_VAULT_KEY` must not silently become an ephemeral value that loses refresh tokens.

The current settings/API-key persistence path needs review because it may store provider credentials in backend-managed settings. Desktop mode must provide an in-memory credential path rather than duplicate plaintext/persistent secret storage.

### 4.2 Key recognition and validation

Provider recognition must have three separate operations:

1. **Local classification:** Match only high-confidence key signatures locally (for example provider-specific prefixes). This sends no secret anywhere.
2. **Single-provider validation:** With a high-confidence result or an explicit user choice, validate against exactly one provider endpoint.
3. **Opt-in likely-provider check:** When classification is ambiguous, present the candidate provider names and require user confirmation before validating each listed endpoint. Copy should state that the key will be sent only to the reviewed candidates.

The backend validation API must accept explicit candidate provider IDs, never choose additional targets internally, redact credentials from all logs/errors, provide per-candidate results without echoing a key, and restrict retries/timeouts.

Examples of inherently ambiguous credentials include generic `sk-...` formats. They must not trigger automatic multi-provider probing.

### 4.3 Local-model server discovery

The first-run UI should offer an explicit local-model scan. It may query documented loopback endpoints without an API key, for example an Ollama tags endpoint and OpenAI-compatible models endpoints.

Requirements:

- Scan only an allowlisted set of loopback host/port combinations.
- Never scan non-loopback networks automatically.
- Exclude the Assistant sidecar's dynamically allocated port.
- Report detected endpoint/model metadata to the user for confirmation.
- Always offer manual URL + model-ID entry.
- Distinguish Ollama's native API from generic OpenAI-compatible APIs.

A generic OpenAI-compatible configuration is the extensibility boundary for v0.1.0; arbitrary runtime provider plugins are not.

## 5. Client/backend compatibility contract

The app and sidecar ship together in a DMG, but explicit compatibility is still required for recovery, development, and future updates.

Add a bootstrap endpoint or authenticated readiness payload with at least:

```json
{
  "server_version": "0.1.0",
  "api_version": 1,
  "stream_protocol_version": 1,
  "identity": {"mode": "single_user"},
  "capability_profile": "desktop-v0.1.0",
  "data_root_state": "ready"
}
```

The native app must fail clearly if the expected API/protocol range is not available. The backend must not leave the UI to discover a protocol mismatch midway through streamed output.

The v0.1.0 native client should use a documented versioned API surface (`/v1/...`) for the endpoints it consumes. Existing unversioned routes can remain compatible during migration, but the product client should not continue to expand the unversioned contract.

## 6. DMG/backend assembly and release engineering

`native package --archive` can make the drag-to-Applications DMG shell, but production distribution also needs a backend helper embedded within `Assistant.app`.

Release work must answer and automate:

- Build a self-contained macOS backend runtime; it cannot depend on a developer's Python, editable install, `uv`, or Docker.
- Start with Apple Silicon (`arm64`) artifacts; add Intel/universal only after the release pipeline works end-to-end.
- Decide the frozen/runtime mechanism after measuring compatibility with native Python extensions and the memory/vector dependency set. This is a release-engineering spike, not a UI change.
- Sign the app, backend helper, browser-automation executable/runtime, Python extensions/native libraries, and any nested frameworks in the correct order using a Developer ID Application certificate.
- Enable hardened runtime as needed by the packaged components.
- Notarize the final artifact with Apple, staple the ticket, and verify it on a clean macOS account with `spctl`.
- Add a CI release job that produces an unsigned internal DMG and a signed/notarized release DMG from version tags.

No automatic updater is required in v0.1.0. Users may install subsequent signed DMGs manually; the launcher must preserve `~/Assistant` across app replacement.

## 7. Required test coverage

Before shipping, tests must demonstrate:

### Backend

- `assistant` is the only advertised CLI command; Docker and docs use it consistently.
- Fresh desktop startup creates only the approved data layout.
- `Conversation/` → `Messages/` migration succeeds, is idempotent, and refuses unsafe dual-directory cases.
- The desktop capability profile removes all email/contact/todo tools before model-visible schema construction, while preserving browser tools and the `web-automation` skill.
- Email scheduling is disabled in desktop mode and excluded storage is not created at server startup.
- The packaged browser helper/runtime launches without a terminal dependency, keeps browser data beneath `.system/browser/`, and returns a recoverable unavailable state if it cannot start.
- Sidecar starts on loopback with a dynamic port, emits a valid rendezvous record only once ready, and rejects requests without its launch token.
- A second launch does not terminate or attach to an unrelated process.
- Bootstrap/API/protocol compatibility failures return a stable, user-actionable response.
- Successful `context_compressed` events retain before/after estimates in replayable history; failed events cannot appear as successful transcript notices.
- Credentials are absent from logs, stored settings, rendezvous documents, and API responses.
- Provider recognition never performs automatic multi-provider validation; opt-in validation targets exactly the user-approved candidates.
- Local-server discovery never sends requests off loopback.

### Native client and integration

- The UI first-run path accepts a paste, renders recognized/ambiguous/custom states, and recovers from failed validation.
- Keychain values are injected after sidecar launch and are available for model use without persisting under `~/Assistant`.
- Ollama, LM Studio/OpenAI-compatible, custom endpoint, and no-local-server states have deterministic automation coverage.
- The frontend suite can target an externally supplied backend URL as well as spawning a development backend; this supports release-candidate contract testing.
- A clean macOS user can install the signed DMG, complete first-run, restart the app, and retain only intended data under `~/Assistant`.

## 8. Review questions / unresolved decisions

1. Should the desktop backend require a runtime bearer token for loopback API calls in v0.1.0? This memo recommends yes.
2. Which high-confidence provider signatures are supported at launch, and who owns their ongoing maintenance?
3. Which exact localhost endpoints/ports are allowed for discovery? The list must be explicit and testable rather than an open port scan.
4. What browser-profile persistence policy balances convenient signed-in sessions with secret handling and supportability? The runtime must keep its profile under `.system/browser/` and use platform secure storage where supported.
5. Does the first arm64 release include the memory/vector extra, or does it ship a smaller runtime profile? This affects DMG size, signing, cold start, and feature parity.
6. What is the recovery/support policy if root-level data and `~/Assistant/Users/native_sdk_chat/` both exist after an interrupted/manual migration?
7. **Dev-route stripping (review P2) — DECIDED 2026-09-05:** desktop mode strips the dev router entirely (incl. `/dev/gmail-demo`).
8. **MCP enablement (review P2) — DECIDED 2026-09-05:** desktop mode keeps `mcp.enabled: true` (the shipped config stands).
9. **`shell_execute` stance (review P2) — DECIDED 2026-09-05:** keep-with-HITL in the consumer DMG (destructive=True interrupts on every use; the sandboxed execution leg applies).

## 9. Recommended implementation order

1. Define the desktop runtime/identity/compatibility contract and data migration policy.
2. Build and test the backend desktop mode, capability profile, credential boundary, and local server discovery contract.
3. Replace native hard-coded connection/identity assumptions and implement first-run against the stable backend contract.
4. Build the self-contained backend helper and perform an unsigned arm64 DMG smoke release.
5. Add Developer ID signing, notarization, clean-machine installation verification, and tag-driven release automation.

No production code has been changed by this memo.
