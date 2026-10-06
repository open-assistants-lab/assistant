# Native client ↔ assistant instance contract

Date: 2026-10-06
Status: Proposed written design for review; not an implementation/deployment plan.
Baseline: `d7f4d97e` (v0.6.33).
Evidence: `docs/audits/2026-10-06-post-merge-client-readiness-scan.md`.

## 1. Agreed intent and boundary

The macOS app is primarily a native client. In future it should connect to
customer-owned Jen, eddyave and admi instances, each enforcing its own identity
and authority. The public product may ship a preconfigured assistant connection;
advanced endpoint entry is not a required consumer onboarding experience.

The client owns connection/session UX, secure server-credential storage, chat,
progress, approval presentation and eventually explicit file transfer. The
server owns assistant profile/tools/ontology/workflows, authentication decisions,
authorization, execution, history and artifacts. Deployment teams choose and
operate identity providers, account provisioning, access and TLS.

We supply an integration boundary, **not** an identity provider, account portal,
central organisation account, fleet control plane or universal assistant manifest.
One shared engine remains the platform foundation. A local runtime is another
optional connection target, not a prerequisite for every client installation.

This updates the local-only/mandatory-sidecar product assumption in
`docs/architecture/macos-dmg-v0.1.0-backend-impact-review.md` for this future client
increment. It does not remove the implemented desktop launch-token, loopback,
identity, storage/migration or helper-ownership safeguards when local mode is used.
Signed distribution and bundled local helpers remain separate release work.

## 2. Approaches and selected first slice

1. **Existing API + explicit bearer connection + small identity read — selected.**
   Reuse Native SDK UI, REST/SSE, server identity seam and per-user key auth.
   This proves the boundary without inventing login infrastructure.
2. Browser/OIDC native handoff first. Existing OIDC browser cookies do not
   automatically authenticate native requests. Valuable later; a separate
   reviewed standards-based sign-in flow, not copied cookies, is needed.
3. Universal discovery/auth-provider/plugin system first. Premature for three
   instances; no evidence it is necessary. Deferred.

First slice: one active remote connection using a deployment-issued **user-scoped
bearer credential**, plus preservation of the existing optional local mode.
The first remote acceptance uses synthetic servers/actors and scripted providers,
not production customer credentials, live models, Telegram, Zii or ClickHouse.
Saved multiple targets, native SSO and file transfer can follow independently.
No generic setup wizard, deployment manager or domain-specific client is required.

## 3. Existing contracts and the one additive server seam

Use existing routes rather than wrapping the engine in another service:
- POST /v1/message and /v1/message/stream.
- GET /v1/conversation/sessions and /v1/conversation (or typed turns when needed).
- POST /v1/message/cancel.
- Existing message-interrupt and durable-governance routes, separately (§6).

`/v1/bootstrap` remains the **desktop sidecar** contract. Its static default_user,
local migration metadata and filesystem paths must not be used as remote actor
discovery or exposed as generic connection information.

Propose one authenticated, read-only **GET /v1/identity**, using the existing
middleware IdentityResolver result. It has no caller-selected user_id and does
not initialize a different user's storage or choose identity from request data.
Minimal successful payload:

```json
{
  "api_version": "1",
  "server_version": "0.6.33",
  "identity": {"user_id": "alice", "authenticated": true, "scoped": true},
  "default_workspace_id": "personal"
}
```

This is effective server identity, not a new organisation/assistant/deployment ID.
No prompts, provider configuration/keys, root paths, membership graph or arbitrary
capability manifest are returned. The local display label belongs to the client;
it is not an authenticated claim that a URL is the customer's production Jen.

Invalid/missing credentials follow the current 401 auth boundary. Where existing
solo auth intentionally permits a request, the payload must report authenticated
false/scoped false; a known default_user alone is not proof of authentication.
A shared-secret identity with no scoped user likewise reports scoped false and
user_id null. It must not be silently upgraded into a personal identity.

The initial remote personal-client flow accepts only authenticated **and** scoped
identity. Shared-key operator mode stays available to explicit trusted deployment
tooling; native support for that mode is deferred rather than mislabeled as
multi-user access. Desktop launch-token mode remains authenticated default_user.

The client uses the returned effective user_id in legacy API request fields where
needed. That field is routing context, never authority: existing server enforcement
still rejects mismatches. Defaults are limited to today's personal workspace; this
is not a design for workspace/organisation switching. Unsupported/missing identity
endpoint or incompatible API version yields a bounded compatibility error, not a
fallback to default_user, anonymous auth or a guessed namespace.

## 4. Deployer authentication and trust

- Reuse per-user keys and the IdentityResolver seam already present in OSS.
  Deployment teams issue/revoke credentials; the native app cannot mint itself
  an admin key, infer roles from names, or turn provisioning rights into approval.
- Existing OIDC/browser sessions remain supported server functionality. Native
  login/refresh/logout interoperability is not implied by their existence and
  is not implemented in this first slice. Do not extract browser cookies.
- A reverse proxy may enforce authentication if bypass is prevented and its
  assertions are consumed only from the trusted boundary. A custom resolver must
  map authenticated actors and least-privilege authorization consistently;
  arbitrary X-User-ID/forwarded headers from clients are not trusted identity.
  Do not label individual proxy users as owner-equivalent merely because the
  proxy is trusted. Header transport/validation is deployment-specific, not a
  blanket default enabled by this document.
- Remote personal deployments must disable localhost/solo bypass where an edge
  proxy would otherwise make all callers appear local. No client flag fixes that
  server configuration. This remains a deployer/server test requirement.
- Authorization remains on every server operation, including history, files and
  approvals. Hidden UI and identity response flags are not security controls.

## 5. Client connection state and secrets

A small connection value contains a locally generated connection ID, display
label, explicit base URL/API prefix and auth kind. Nonsecret configuration may
persist locally; the bearer credential is a separate Keychain item associated
with that connection and canonical server/account context. Never use the current
`active-provider-credential` slot for a server token.

Initial flow: user/deployer supplies endpoint and token → authenticated identity
read → compatible/scoped identity accepted → load this actor's sessions → chat.
Provider detection/validation and provider-key setup are **not** part of remote
sign-in. Remote messages omit provider_keys; the connected server supplies its
model setup. Existing local-provider Keychain behaviour remains local-mode only.

Endpoint rules: require HTTPS with ordinary certificate validation for remote
servers; allow HTTP only for explicit literal loopback/local test targets. Reject
URL userinfo, embedded credentials and query/fragment tokens. Keep a configured
reverse-proxy path prefix when constructing routes. Do not follow authentication
redirects or forward credentials to another origin. Endpoint changes require a
new credential binding/confirmation, not silent reuse of an old server token.

Keep credentials out of logs, argv, URLs, saved chat records and diagnostic
exports. Display bounded status/reason codes rather than unchecked raw server
error bodies. Keychain rejection does not pretend the connection was saved.
Explicit disconnect stops client transport and clears active account state;
credential deletion/remote revocation are separate actions and reported honestly.

State is scoped by connection + effective actor + session. A connection switch
or actor change clears/rebinds transcript, pending approvals and model-selection
state. Old streaming callbacks are rejected by connection/generation ownership.
Never display one server's cached conversation under another server's label.
Closing a stream does not assert that a remote business action was cancelled.

## 6. Chat, approval and reconnect semantics

Use REST/SSE first; no rewrite to WebSockets is needed for basic chat. Preserve
unknown event fields, canonical block frames, revision boundaries and terminal
outcomes. Do not collapse rejected/failed/cancelled/uncertain into success.

There are currently **two** approval paths:
- Transient stream interruption: call_id + session_id, matched to
  /v1/message/approve or /reject. Expiry/restart may make it unavailable.
- Durable proposal: proposal_id in an actor's store, matched to
  /v1/governance/pendings/{proposal_id}/approve or /cancel, with outcomes and
  optional external-operation IDs. It survives independently of the stream.

The client retains source kind, connection, actor, session and matching ID. Do
not substitute one ID into the other route. Refresh durable pending state after
runs/reconnect; do not infer durable approval from an arbitrary text message.
An approval click becomes submitting, not completed. Server refusal/expiry/drift,
uncertainty or an HTTP error remains visible and does not lose a live pending
merely because the UI button was clicked. Approved external work may remain
queued/running; approval acceptance is not proof of its business effect.

On 401, stop the authenticated flow and ask for valid credentials; on 403, show
access denied without retrying under another identity. Network loss means
reconnect/read history and pending state, **not resend the last message/action**.
Do not auto-retry consequential POSTs. No durable event-cursor/exactly-once
replay guarantee is added here. Midstream recovery may show an explicit interrupted
run until server history/status can settle it. Cancellation requires the matching
server request/result, not merely closing the client transport.

Customer-specific approvals (such as Jen control-plane/Telegram flows) remain
server/domain-owned. This slice proves existing engine surfaces with fixtures,
not that every current customer deployment exposes those surfaces today.

## 7. Files, domain UI and client reuse

The same client targets compatible Jen, eddyave and admi endpoints; it does not
bundle their tools, ontology or customer credentials. Custom relay deployments
must expose the agreed engine API or an explicitly reviewed compatible adapter.
No audit established that all three current live deployments already do so.

File access/upload/download, binary artifacts, export/retention and sharing need
a separate small contract after connection/chat proof. Existing workspace text
routes are not a complete artifact API, and a server path is not a Mac file path.
No automatic local-folder mirroring or local file grant is implied by remote
chat. Preserve explicit data-transfer consent in later work. Domain report UI,
arbitrary HTML/web surfaces, fleet dashboards and local model/browser packaging
are not prerequisites to the first client connection.

SDK conformance gaps (socket basePath propagation and delete/clear error handling)
are queued narrow fixes. The TypeScript SDK is a contract reference/test consumer,
not a runtime dependency to introduce into the Zig native client. Do not expand
this first slice into a replacement SDK or transport framework.

## 8. Acceptance and implementation boundary

Use two synthetic actor-scoped servers with distinct stores/tokens; start with
one active target and switch explicitly. First implementation plan must cover:
1. Valid identity, wrong/expired token, solo/shared-key refusal and forged user_id.
2. URL/TLS/prefix validation, redirect refusal and no cross-server credential use.
3. Native credential effects (test doubles only; no real personal Keychain),
   isolated connection state and stale callback rejection.
4. Scripted chat/SSE/history; remote requests contain no model-provider key.
5. Both approval identifier kinds, rejection/expiry/definition drift, lost
   connections and no automatic message/business-action replay.
6. Existing local launch-token/identity/first-run behaviour regression tests.
7. Explicit unsupported-version/auth-flow states, not namespace guessing.

These tests prove a client/server boundary, not hostile-tenant isolation, provider
quality, production Jen parity, native SSO, binary transfer or a signed release.
Investigate the scan's post-summary Python shutdown timeout separately before
claiming exit-clean API harness verification; do not hide it by weakening tests.

Likely owned seams: a small HTTP identity router/alias plus API tests; native
connection/auth state and tests; targeted documentation updates. No storage,
ontology, dependency, auth-provider or customer-deployment rewrite is authorised.

## 9. Self-review and handoff

Scope is one incremental connection/auth/chat boundary over existing machinery.
Identity comes from the server, but deployment teams keep authentication choices.
Local safeguards are preserved instead of reused as remote guarantees. Provider
credentials, server access tokens, call IDs and proposal IDs remain separate.
File transfer and native OIDC handoff are explicitly deferred, not silently
claimed. The one additive identity seam has a concrete existing routing gap to
solve and exposes no server filesystem/configuration details.

**Next gate:** user reviews this written spec. Then write the implementation plan
and select native/subagent-driven execution. Agreement to rescan and continue
permits this design artifact, not implementation of unreviewed server/client
changes. No code, identity configuration or deployment was changed in this lane.
