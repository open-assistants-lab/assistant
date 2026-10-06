# Post-merge repository scan: native client readiness

Date: 2026-10-06
Baseline: `d7f4d97e3206edb41b04171b9f10a1e2345900f2` (v0.6.33).
Scope: merged changes, native/SDK/API/auth seams, and the next client contract.
Read-only source review plus isolated targeted tests; no production access.

## 1. Merged state and ownership

Since the last verified reference baseline `8e232228`, committed changes affect
CHANGELOG/pyproject and subagent lifecycle code/tests: instruction delivery,
stop-on-delete, last-answer extraction, schema PATCH, callback isolation,
context release and claimed delegate runs. v0.6.33 explicitly re-lands a batch
lost during earlier concurrent history manipulation. Do not infer inclusion
from issue status alone; the lifecycle regression file is present and exercised.

The changelog reports 3,857 passed / 27 skipped for v0.6.33. That is the release
owner's evidence, **not a fresh full-suite run in this scan**.

At inspection main had dirty hygiene/docs/task changes and an unrelated uv.lock
edit; a later concurrent main commit `2769f3df` updated AGENTS.md only, without
changing this scan's runtime baseline; these were not reset, stashed, committed or changed. Prior Jen worktrees
were retained. New documentation is isolated on `docs/native-client-contract`
in `.worktrees/native-client-contract`, based on the inspected release commit.
Docker smoke is skipped for this round at the user's direction. Other agents'
processes/deployments are outside this lane's ownership.

## 2. Existing foundations — reuse, do not rebuild

- `src/http/auth/resolver.py`: IdentityResolver/UserIdentity seam.
- `src/http/auth/__init__.py`: middleware resolver chain, identity binding,
  desktop launch-token handling and router effective-user enforcement.
- `src/auth/resolver.py`: optional per-user bearer keys already exist.
- `src/auth/oidc.py` and `src/http/routers/auth_oidc.py`: optional OIDC browser
  sessions already exist. The latest auth batch durably binds subjects and
  uses Secure cookies. Sessions are in-process; restart requires re-login.
- `src/http/routers/v1.py`: existing API aliases cover chat, history,
  governance, tools, skills, subagents, settings and WebSockets.
- `clients/typescript`: preview REST/SSE/WS SDK, not a frozen client contract.
- `native-sdk-experiment/src/main.zig`: configurable base URL/bearer header,
  streaming/history/approval UI and Native SDK credential effects already exist.
- Existing server persistence/governance and deployment-team identity providers
  remain authoritative. The native app is a client, not their replacement.

## 3. Concrete gaps and implications

### A. Bootstrap is a desktop-sidecar contract, not remote identity discovery

`src/http/routers/bootstrap.py` returns static default_user/personal and local
migration/data-root/system-dir information. It does not describe the actual
remote actor. Reusing that payload as generic remote discovery would both
misidentify users and unnecessarily expose server filesystem details.

Per-user REST routers still require request user_id to match authenticated
identity. Omitting it often becomes default_user, which is not necessarily the
key owner. A small authenticated identity read is justified; a universal
manifest/discovery framework is not.

### B. Authentication exists, but the login transports are not interchangeable

Shared-secret mode deliberately cannot bind individual users; it may accept a
caller-selected namespace. It is a trusted deployment mode, not public-client
multi-user authorization. Solo/localhost bypass must not become a remote client
fallback. Deployer per-user keys fit an initial native bearer connection.

Some older AGENTS.md deployment/pitfall text still says there is no per-user
authentication/OIDC, despite the implemented resolvers and the newer identity
invariants in that same file. Treat current source/tests as evidence and reconcile
those statements in a targeted documentation change; auth availability does not
imply hostile-user tool containment or resolve #40.

OIDC's existing browser session cookie is not automatically available inside a
native HTTP client. No browser-to-native handoff was proved. A future standards-
based native sign-in integration needs its own small reviewed flow, not copied
browser cookies or a new identity provider. Proxy assertions need a trusted,
non-bypassable resolver boundary; arbitrary client headers are never identity.

### C. Native connection state is sidecar/provider-centric

The app reads NATIVE_ASSISTANT_BASE_URL/NATIVE_ASSISTANT_LAUNCH_TOKEN and stores
one active provider credential under `active-provider-credential`. A server
access token is **not** a provider API key. Remote operation needs per-connection
credentials and separate connection/account-scoped UI state. Remote chat must
not inject the user's local model-provider key into customer servers.

The native README's fixed localhost/native_sdk_chat description is stale against
current configurable endpoint/token source and D3 tests. Update it as part of
implementation; do not treat it as the wire contract. The older DMG memo assumes
mandatory loopback-only sidecar ownership; that product assumption is superseded
by the agreed client-first direction, while its local-mode safety rules remain.

### D. SDK conformance needs narrow repairs, not a rewrite

In `clients/typescript/src/client.ts`, socket() does not propagate the client's
basePath into ConversationSocket; a configured unprefixed REST client therefore
still defaults its socket to /v1. deleteSession()/clearConversation() do not check
non-2xx responses. Their void completion can hide denied deletion. These are
source-proven client gaps, not findings from a live server trial. Add regression
cases in the implementation plan before changing either behaviour.

### E. Two approval identifier/lifecycle surfaces coexist

`/v1/message/approve` consumes a transient stream interrupt call_id/session_id.
`/v1/governance/pendings/{proposal_id}/approve` operates on durable proposals.
They are not interchangeable IDs or persistence guarantees. The native app/TS
SDK currently lean on message interrupts. A client should retain the source and
scope of each pending action, use its matching route, and let server outcomes
win; transport failure is not approval or cancellation success.

### F. File APIs are not a generic artifact-transfer contract

`src/http/routers/workspace.py` offers authenticated text/list/write/sync-style
operations and returns server paths. These routes are not among the current /v1
aliases. No reviewed binary/multipart/retention/export contract was established.
A remote filesystem path is not a local Mac path. Explicit file transfer and
rich artifacts remain a follow-on; the first connection/chat slice must not
pretend local file grants imply access on a remote server.

## 4. Fresh verification

- `ruff check src`: passed.
- TypeScript `npm test` with ASSISTANT_URL unset: 12 passed / 1 deliberately
  skipped live-server test; compilation passed. No install, publishing or live
  integration. Existing local dependencies were used.
- Isolated API/auth/desktop/WS/governance/lifecycle/reference selection: pytest
  reported **312 passed / 1 skipped / 7 warnings in 11.94s**. The containing
  Python process did not terminate within the tool's 180-second window and was
  timed out after the test summary. Consequently this is test-result evidence,
  **not an exit-0 harness claim**. Temporary .env was removed before the hang;
  no process from this lane remains running. Shutdown/lifetime needs a narrower
  reproducer before attributing it to a particular test or merge.
- The selection comprised test_identity_resolver, test_auth, test_auth_keys,
  test_auth_binding_b3, test_oidc, test_desktop_contracts, test_desktop_server,
  test_desktop_ws_identity, test_ws_auth_fail_closed, test_ws_protocol,
  test_ws_transport_b4, test_ws_approval, test_governance_api,
  test_subagent_lifecycle_b6 and tests/reference_deployments.
- Test warnings: existing Pydantic/websocket deprecations and dummy OIDC HMAC
  key-length warnings. No reported test assertion failures.
- External Python DNS/TCP blocked, synthetic data roots, inherited keys cleared
  and a temporary synthetic .env shadow used to prevent loading parent secrets.
- Native Zig tests, signed app packaging, remote customer instances, OIDC native
  handoff and full-suite rerun were **not** executed. Tests do not certify those.

## 5. Broader roadmap status

The release explicitly leaves #40 (per-tenant isolation), #41 (receipt contract)
and #74 (guardrail prevention policy) as design decisions, not completed patches.
Do not turn successful regression tests into a claim that those contracts are
settled. Dedicated instance boundaries remain the initial deployment posture.

Email/contacts/todos source and routers still exist; their earlier full-deletion
plan has not been executed merely because desktop filtering exists. CoreMem
unit-of-work/durable-document requests and the parked graph/fork/new-messaging
specs are not evidence of shipped capabilities. They remain separate tracks;
none justifies building another client-side assistant engine.

The merged Jen-shaped example is still synthetic packaging/governance evidence,
not a deployed Zii/Telegram conversion. eddyave/admi reuse and independent-builder
trials remain necessary for the platform proof. The native client contract can
advance separately, against safe compatible fixtures, without waiting for all
customer integrations or an operator dashboard.

## 6. Next slice

Draft the thin native-client/server contract: explicit connection URL + deployer
credential, authenticated effective identity, connection-scoped state, existing
REST/SSE history and correct approval/error semantics. Keep local sidecar mode
optional and unchanged. Start with a single active bearer connection; permit
saved targets later without building fleet management or an auth platform.

The proposed written design is
`docs/superpowers/specs/2026-10-06-native-client-server-contract.md`.
Implementation requires written-spec review, an implementation plan, and an
execution-method choice. The scan authorizes neither engine edits nor customer
access. eddyave packaging remains a separate builder lane, not a prerequisite
for this client's connection contract.
