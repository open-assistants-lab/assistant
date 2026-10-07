# #40 — Execution and authority boundaries

Status: design draft for user review; not implementation authorization or shipped guarantees.

Companion: [#41 execution receipts](2026-10-07-execution-receipt-contract-design.md).
Source baseline: local main `7e2d9778` (v0.6.36). These requirements supersede earlier
conversational recommendations where explicitly noted below.

## 1. Intent and scope

Refactor the existing engine's authority and execution boundaries rather than
replace its agent loop. Maintain one engine codebase and release with native,
HTTP and messaging clients. Use a modular monolith: architectural boxes are
not automatically independent network services. Separate processes where
privilege and termination requirements demand them.

The first hardened multi-tenant target is Linux Docker MT. SL and SR are
required single-user paths, not installations that require company provisioning.
The native client is a required product acceptance surface. An interactive CLI
can wait. MU containment/provisioning is a separate track, not accomplished by
putting the MT engine on a public network.

This is an explicitly breaking cutover. No automatic migration or backward
compatibility is required from the old engine/configuration/storage contracts.
Never delete, reinterpret, attribute or modify retained customer data as a
side effect of adopting the new contracts. Use a fresh deployment store; refuse
an incompatible old layout with a clear explanation. Customer production
changes require separate authorization. Breaking this cutover does not authorize
discarding data on every subsequent upgrade.

## 2. Mode codes and product boundaries

| Code | Meaning | Engine location and boundary |
|---|---|---|
| SL | Solo Local | One user's local runtime; native app/helper on one device |
| SR | Solo Remote | One user's remote runtime, accessed from multiple devices |
| MT | Multi-tenant Trusted | Companies share a server/container; explicit internal privacy and sharing |
| MU | Multi-tenant Untrusted | Hosted individual/small-team tenants with stronger proposed runtime containment |

Codes are documentation/product concepts, not aliases to silently substitute
for existing `DEPLOYMENT_MODE`, sandbox backend or authentication values.

Target packaging is one macOS DMG that can select SL, SR, MT or MU connections.
For SL it contains a private engine helper and Python runtime; users should not
need Docker or a separate Python installation. For remote connections it acts
as a client and need not start that helper. Each connection keeps authentication,
caches, history and active target separate. Switching a target must not transfer
authority or silently run remote work locally. Bundling, connection switching and
native end-to-end behavior remain unverified. Local runtime does not imply local
model inference or local external-tool/tracing destinations.

## 3. Ownership and administrative authority

A company is an MT tenant. It may contain multiple teams; users may belong to
several teams. Teams are access groups, not mandatory separate runtimes.
Personal, team-shared and company-shared resources require explicit ownership
and grants. Cross-tenant sharing is outside this design.

Private-resource protection is symmetric: seniority, billing ownership, company
membership and membership administration do not confer private-content access.
CEO and staff private resources receive the same protection. Apply this to
resource discovery/listing, APIs, arbitrary code, artifacts, conversations,
receipts and caches, not only visible chat messages.

| Scope | Configuration/editing authority |
|---|---|
| SL | Sole user controls the local instance and personal resources |
| SR | Sole user controls personal/instance settings; operator controls infrastructure restrictions |
| MT instance | Authorized company admins control company configuration and shared policy |
| MU instance | Tenant owners/admins configure their instance within service restrictions |
| Private profile | Owner edits its instructions, tools, skills and explicit subagent model |
| Team-shared profile | Explicitly designated maintainers edit it |
| Company-shared profile | Designated maintainers or authorized company admins edit it |

A profile edit affects future runs/launches, not already-running code. Resolve and
pin the definition revision used by each run/launch; current access restrictions
may still deny a later dispatch. Definition editing/publishing does not grant
execution authority. Suspension is an operational
power, not content access. Shared resources have explicit accountable stewards;
no automatic transfer of a departing user's private resources to an admin.

Deployer identity providers remain authoritative. The engine validates mapped
roles/grants; it does not implement a new account or identity platform. Infrastructure
operators are distinct from company admins and remain outside application-level
confidentiality guarantees.

### Operational visibility (approved option 2)

Only explicitly authorized admins receive a tenant-scoped operational projection:
opaque resource/operation identifiers, necessary actor identifiers, installed
public package identity/version, allowed or attempted destination host/protocol/port,
enforced capability scope, health, usage, redacted outcome/violation reason and
policy revision. Private custom resource names and source locations are not
required; use opaque identifiers. Do not expose URL paths/query strings, private
repository URLs, prompts, commands, SQL, results, private source/skills or secrets.

Operational metadata is itself sensitive: restrict and audit access, keep it
local by default, and give deployers explicit retention/deletion controls.
Retention of operational projections is independent of private execution evidence.
No optional exporter is implicitly enabled. Storage is not permission to export
content to tracing. Exceptional support/emergency content access is disabled in
the first scope; adding it requires separate disclosed/scoped/audited design.

## 4. Configuration and model lifetime

Configuration belongs to each logical company/assistant instance, even when
instances share one server/container (approved option B). Infrastructure policy
sets an outer restriction; instance configuration cannot weaken it. Private
profiles, integrations, credentials and conversation settings remain owned
resources, not global configuration knobs.

The main-agent model follows these exact semantics:

1. At conversation creation, persist a permitted explicit model choice if supplied;
   otherwise persist the current instance/admin default.
2. Existing conversations retain that saved model when the instance default changes.
3. New conversations use the new default.
4. User model selection updates only the addressed conversation, persists across
   restart, and takes effect on its next run. It does not change the instance default,
   another conversation or a global per-user model preference.
5. Every run resolves and snapshots its model/provider, profile definition revision,
   tools/skills and authority provenance. Running work does not switch models.
6. A subagent with an explicit model keeps it. Otherwise it snapshots the parent
   run's resolved model at launch. Changing the parent afterward does not change
   running children. Standalone/scheduled subagents without a parent or explicit
   model snapshot the instance default at admission; they do not invent a parent.
7. Persist conversation configuration with a revision. Concurrent device edits
   use an expected revision; stale edits return a conflict rather than silently
   overwrite the other device. A run snapshots the committed conversation revision
   at admission. A model change while it is running affects the next run only.
8. Current policy still applies to a saved model. If prohibited, unavailable or
   lacking required credentials, report a clear error; do not silently choose a
   different provider/model. The user/admin must explicitly resolve the problem.

Authored defaults, persistent conversation choices and resolved-run evidence are
separate records. A main profile model field must not introduce another precedence
chain that overrides the saved conversation model or the creation rule. Record
actual resolved values, not merely profile declarations. The old native picker
currently writes user defaults and one cycling handler changes only memory;
that implementation does not satisfy the new conversation-scoped contract. A failed
save must not look persisted in the native UI. A per-message model field cannot
silently bypass or overwrite the saved conversation setting.

## 5. Execution topology and trusted computing boundary

```text
Authenticated native/HTTP/messaging client
  -> ingress: verified identity, resource access, atomic run admission
  -> trusted tenant coordination: agent runtime, policy, receipt lifecycle
       -> isolated user execution: shell, Python, custom tools, private installs
       -> protected integration execution: narrowly governed credentialed actions
```

Trusted tenant coordination stays in the engine implementation. MT uses explicit
tenant worker boundaries and user-scoped execution within them; one shared container
is packaging, not the authority boundary. No arbitrary user code is imported or
executed in privileged coordination. Protected adapters may use existing engine
components but never share an unrestricted user execution environment.

Execution identity is provisioned and persisted with tenant+user binding. It is
not a modulo hash of user names. Check collisions and ownership before granting
filesystem access. User code receives only private resources and explicitly granted
shared resources; publishing definitions does not share credentials, history,
artifacts or creator privileges. Subagents inherit bounded caller authority,
never the author's or CEO's privileges.

The server derives authority from authenticated identity and current policy.
Worker-supplied JSON IDs/roles are not proof. Dispatch binds the authenticated actor,
tenant, target executor, operation, definition/arguments, allowed resources and
authority revision. Workers have bounded dispatch credentials, not deployment-admin
or unrestricted client authentication. All ingress routes, background jobs,
callbacks and internal/localhost endpoints uphold the same boundary.

## 6. Private code, CLI installations and network access

User execution requires actual filesystem/process/network isolation, a minimal
environment and no access to other workers' processes, deployment secrets or the
Docker socket. Canonical-path checks and declarative tool permissions alone do not
contain arbitrary code. Apply boundaries to installation hooks, MCP startup,
discovery and health checks as well as final tool invocation.

Allow private persistent installation environments within approved package sources,
resource paths and destinations. No general sudo or shared-system modification.
Sharing a tool definition does not give recipients write access to a creator's
installation environment. External dependency provenance/version is operational
evidence, not a new marketplace or mandatory review of every private script edit.

Company/infrastructure network policy is enforced outside arbitrary code, including
raw-IP, internal service, loopback, redirect and DNS paths. A proxy environment
variable alone is insufficient. Privileged integration destinations must not be
reachable with equivalent ambient credentials through a second path. Model inference,
external tools and tracing have independently declared destinations/data flows.

There is no new mandatory fairness, per-user CPU/memory/storage quota or model-spend
control in the first scope. The user explicitly accepts that one user may exhaust
shared resources and disrupt others. Retain existing execution timeouts/output
bounds unless separately changed; absence of new quotas is not permission to drop
containment. Do not claim availability, noisy-neighbor protection or production
MU abuse controls. MU resource containment is a separate future decision.

### Linux feasibility gate

Before implementing the selected MT worker layout, conduct an explicitly authorized
isolated Linux Docker probe covering persistent identities, namespaces, filesystem
ownership, controlled egress, private installations and process termination.
Existing `BwrapSandboxBackend` is a candidate component, not proven provisioning:
it refuses a root service process and currently unshares networking. The probe must
show a viable non-root worker/egress layout or qualify another narrowly scoped
backend through the same tests. Any privileged launch/provisioning helper has fixed
scope and no model/integration secrets; never expose its general interface to user
code. Failure blocks hardened MT; do not fall back to the soft/null sandbox.

## 7. Credentials, integrations and MCP

Credentials have an explicit owner: user, team or company, with authorized use
separate from read access to secret values. Protected credentials are stored outside
user-writable configuration and arbitrary-code environments. Definitions reference
secrets without embedding reusable privileged values. Avoid a general vault platform;
use deployment-operated secret facilities where appropriate.

A user-scoped MCP process may run in that user's execution boundary only with
credentials/DB grants/network access consistent with instance policy. Read-only
prose, regex SQL filters and tool annotations do not prove read-only authority.
When possession of a credential would bypass required approval or grant broader
access, the MCP/integration runs through protected execution instead. It accepts
bounded governed actions, not arbitrary scripts supplied with powerful secrets.
Credential-bearing adapter code must be trusted independently of a tool's name or
annotations. New authority requires approval/review; private code edits alone do
not all require admin review.

Recheck current grants, policy, definition binding and approval before effectful
dispatch. Removing membership, suspending an integration or revoking access blocks
subsequent dispatch and invalidates affected cached authority. Cancellation of work
already running is best-effort. External credential revocation requires separate
authority at that service; suspension does not undo completed actions.

## 8. Same-session concurrency and observation

For two devices submitting to the same authorized session, atomically admit one
turn. Order by server admission, recording server timestamps; device clocks are
not authoritative and timestamps alone are not a lock. Scope sessions by instance/
tenant plus session ownership. API replicas and replacement workers must not create
independent execution owners for the same active run.

The later request is not queued and its prompt is not added to accepted conversation
history. Return an explicit busy/not-accepted result with the active run identity.
After checking read permission, supply its current state and attach the client to
that same run's stream. This is another observer of the same session, not a new
session or execution. The native UI distinguishes its rejected optimistic prompt
from the accepted prompt. Disconnecting an observer does not cancel work; explicit
cancellation requires authority. Recheck access for subsequent observation delivery after revocation;
stop delivery when read access is withdrawn. Revocation cannot retract content
already delivered to an authorized client. See the companion for admission/
deduplication and reconnect rules.

## 9. Operations and upgrades: minimum scope

Server upgrades are manual. Keep runtime assets, mutable state and secrets separate;
replacing an image must not replace persistent state accidentally. Operators handle
identity provisioning, secret rotation, package failures and offboarding. Company
admins administer their granted application scope, not the host.

For SL the intended DMG updates app/helper together while retaining user state;
for remote modes native and server releases are separate. Define and check client/
server contract compatibility; unsupported versions/capabilities fail clearly, not
by silently disabling governance. No automatic updater or fleet dashboard is required.

Before stopping/updating, stop admitting new work and expose active/uncertain runs.
A stopped-copy backup may restore recorded state, but does not reverse external
effects. Restore must not automatically repeat uncertain writes or enable previously
disabled execution/schedules. Rollback to an incompatible state schema is refused;
this cutover's no-migration decision is not a general rollback guarantee.

## 10. Acceptance and exclusions

Required negative tests include cross-company equal user names; symmetric CEO/staff
private access; filesystem traversal/symlink swaps; process/environment inspection;
internal/loopback credential bypass; forged worker identity; revoked/stale grants
and approvals; definition drift; creator-authority inheritance; admin projection
redaction; and failure of required isolation/provisioning. Test real Linux mechanisms
in the qualified Docker target, not only mocked wrappers or Compose parsing.

Required native tests cover SL local helper lifecycle, SR connection/reconnect, MT
individual identity and explicit sharing, connection isolation, persisted conversation
model switching/default updates, profile edit permissions and same-session busy/attach.
Use synthetic stores/scripted providers before separately authorized external-service
checks. Unit tests/builds are not DMG or native end-to-end proof. A future MU connection
requires its separately qualified hosted service; a connection selector is not MU proof.

Measure cold-start and warm-dispatch overhead separately on a fixed workload before
and after the refactor; report regressions rather than impose an invented fairness
or per-user quota system. Runtime rollout requires the security/recovery gates and
aligned native contract. Legacy deployment docs must not claim hardened MT isolation.

Out of scope: an identity/account platform, fleet dashboard, automatic customer
migration, quotas/billing, interactive CLI implementation, MU microVM provisioning,
new scheduler/receipt system, universal tool taxonomy/compiler, messaging/Rust rewrite,
CRDT/offline multi-writer sync, and local replacement for CoreMem unit-of-work support.

## 11. Source observations and research

- `src/sdk/execution_models.py`: existing lifecycle/effect/verification vocabulary;
  normalized requests lack first-class authenticated authority fields today.
- `src/sdk/session_worker.py`: session keys currently use user+session, not tenant.
- `src/sdk/sandbox.py`: current soft/UID and hard backend helpers are not complete MT
  worker/identity provisioning.
- `src/sdk/tools_core/mcp_manager.py`: stdio MCP startup copies server environment.
- `src/sdk/tools_core/mcp_config.py`: literal environment/header secrets are supported.
- `src/sdk/runner.py`, `src/sdk/coordinator.py`: main/subagent model resolution currently
  differs; the target inheritance and conversation lifetime are proposed changes.
- `src/http/routers/settings.py`, `src/config/user_settings_store.py`,
  `native-sdk-experiment/src/main.zig`: saved user settings are not conversation settings.
- `src/storage/paths.py`, `src/sdk/tools_custom.py`: current paths/shared definitions
  must not be mistaken for tenant-aware sharing.

Borrow client/server separation from [Codex app-server](https://developers.openai.com/codex/app-server/),
policy/redacted network evidence from [its network proxy](https://github.com/openai/codex/blob/main/codex-rs/network-proxy/README.md),
and explicit environment/lifecycle separation from [Pi Durable](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md).
Neither personal-workstation escape APIs nor trusted environment interfaces establish
our multi-tenant boundary. Research is documentation inspection, not deployment proof.

Writing this draft does not authorize provisioning, dependency installs, customer
access, Docker startup, a release, merge or push. User review precedes implementation
planning; plan review and execution selection precede runtime changes.
