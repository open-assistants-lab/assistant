# Builder and deployer conventions

Build your assistant, not its infrastructure.

This guide describes how to package and operate an assistant on the shared
Assistant engine. It is for application builders and deployment teams, not a
new package format or a managed hosting service.

## Status and starting points

| Surface | Current status |
|---|---|
| Profiles, custom tools and skills | Existing `PROFILE.md`, `TOOL.md` and `SKILL.md` extension mechanisms |
| Server interfaces | Existing HTTP APIs, REST/SSE/WebSocket, persistence and governed tool dispatch; consult the selected engine version |
| Authentication | Shared deployment key, opt-in per-user keys and browser OIDC integration exist; deployment configuration and identity enforcement matter |
| Worked deployment example | [Synthetic Jen reference](../examples/jen_reference/README.md): preparation, validation, fixture approvals, readiness and stopped-copy recovery |
| Real Jen integration | Not established by the synthetic reference |
| eddyave and admi adoption | Application-specific assessment/migration and deployment-team validation still required |
| General installation/lifecycle automation | No universal installer, manifest compiler, fleet manager or automatic recovery service is implied |
| Native macOS product | Existing experimental client; further product work is parked, not a prerequisite for builders |

The Jen acceptance tests exercise a synthetic local package. Docker configuration
validation is not startup proof; Docker smoke was intentionally deferred for this
round. Neither establishes live provider, Zii, Telegram or ClickHouse compatibility.

See the [design contract](superpowers/specs/2026-10-06-assistant-builder-deployment-contract.md)
for the wider target and its remaining acceptance gates, and
[deployment guide](../DEPLOYMENT.md) for runtime topology and configuration.

## Who owns what

- **Platform maintainers:** the shared engine, extension/API interfaces, tested
  examples, documentation and platform gaps discovered during adoption.
- **Application builders:** profiles, tools, skills, authored ontology/metrics,
  source mappings, deterministic business safeguards and representative tests.
- **Deployment teams:** installation in their own repository/environment, runtime
  placement, identity provider/accounts, credentials, permissions, data paths,
  networking, budgets, observability, backups, upgrades and recovery.

One team may fill multiple roles. These conventions do not authorise maintainers
to change another team's repository or redeploy its assistant. Assistance with
that work requires a separately agreed scope and access approval.

## Package versus instance

A **package** is reusable, versioned application content. An **instance** is one
installation with its own configuration, credentials, endpoint and mutable state.
An instance label or `user_id` is not organisation membership or an isolation
boundary.

A package should document:

1. Package version and tested engine version; pin the actual image digest for
   reproducible container installations, rather than relying on `latest`.
2. Profile, custom tools, skills and any required service definitions.
3. Required configuration and secret *names*, defaults, validation and consumers.
4. Domain definitions, source bindings and process rules where applicable.
5. Allowed actions, approval paths, safe fixture checks and known limitations.
6. Mutable stores/artifacts, external data flows and an operational runbook.

Use ordinary files and existing formats. These are logical responsibilities,
not a mandatory directory tree, new per-tool taxonomy or universal manifest.
Do not bundle customer secrets, runtime records or dated research dossiers into
an example package.

An instance must explicitly configure its runtime URL, model/provider, storage,
ports and access policy. It must not discover credentials from a developer's
named container, depend on an iCloud checkout or silently reuse another
instance's state. Keep immutable content, configuration, secrets and mutable
state distinct; prefer read-only content mounts where supported.

## Identity, permissions and domain safety

The deployer chooses and operates the identity provider or account provisioning.
The server resolves identity and enforces request ownership; a client-supplied
`user_id` or model-supplied permission flag is not authority.

- A shared `API_KEY` is trusted-deployment access, not an individual account or
  proof of roles. Keep it with operators/trusted intermediaries where users need
  individually scoped access.
- Per-user keys are opt-in (`PER_USER_AUTH=true`, `/auth/keys`). Browser OIDC is
  also opt-in (`OIDC_*`, `/auth/oidc/login`); it does not imply finished native SSO.
- On remote deployments configure authentication, disable localhost bypass
  (`SOLO_BYPASS=false`) and prevent direct access around the trusted edge. Proxy
  loopback must not become an authentication bypass. Treat deployment keys as
  privileged credentials, separate from model-provider keys.
- Test a scoped actor's own access, a mismatched user request and an unauthorised
  request against the chosen deployment. Never substitute configuration presence
  for those checks. Do not treat arbitrary client identity headers as trusted.
- Use dedicated runtime/storage/credential boundaries for unrelated customers.
  Shared-UID trusted-user deployments are not hostile-tenant isolation. API
  identity and tool governance do not contain arbitrary code given equivalent
  service credentials; restrict shell, interpreters, mounts and egress accordingly.

Keep business safeguards outside prompts: bind approved targets and tool
versions, refuse stale state, preserve protected fields and report uncertain
external outcomes rather than automatically replaying them. Authored ontology,
certified metrics and source authority belong to the application; schema
introspection alone cannot supply them.

Current deployment native-tool selection is documented in
[Native tool policy](../DEPLOYMENT.md#native-tool-policy). A proposed simpler
exclude-shaped policy is not a shipped configuration format. Email, contacts and
todos code still exists; its planned deletion is separate work. Do not infer
that disabling tools performs a data migration or deletion. ConnectKit and
coding/browser capabilities are not being removed by this documentation pass.

## Operation and data flows

Treat these as four separate choices:

1. **Runtime/storage:** your machine or an explicitly operated server.
2. **Inference:** local or cloud model provider; cloud inference sends selected
   context to that provider.
3. **Tools/integrations:** external services may receive arguments, files or
   other application data.
4. **Tracing:** configured semantic tracing can contain customer content;
   operational telemetry has a different payload and destination policy.

Document each destination and its authorisation/retention requirements. A local
runtime does not mean everything stays local. Do not print secret values,
resolved environments or credential-bearing diagnostics.

Readiness must check the application's required profile/tools/configuration and
state, not just HTTP liveness. Optional source/provider checks must be explicitly
safe and authorised; readiness must not perform a consequential business action.
No generic engine health endpoint alone certifies an application's readiness.

Use `DataPaths` and the selected release's configuration, not copied historical
paths. Set both `DEPLOYMENT_DATA_ROOT` and `DEPLOYMENT_DATA_PATH` where needed and
inventory both trees. Under the current user root, install `PROFILE.md`, custom
`Tools/<tool>/TOOL.md` content and `Skills/<skill>/SKILL.md` content. The default
user root is `data_root`; non-default user roots are `data_root/Users/{user_id}/`.
Current conversation storage is `Messages/messages.db` beneath that user root.
Do not assume `/app/profile/PROFILE.md` is an engine profile path, or run multiple
runtime replicas against the same user's mutable store.

For upgrades and recovery, document how to quiesce work, capture versions, make
consistent backups and restore into a separate location. Keep restored writers
and schedules off until reviewed. Code rollback, database recovery and reversal
of an external action are different operations. The synthetic Jen stopped-copy
recipe is a worked example, not a universal production backup guarantee.

## Adoption checklist for deployment teams

- [ ] Inventory existing application content, mutable stores, integrations and
      source authority; preserve customer records and research archives.
- [ ] Pin the package/runtime combination and replace implicit developer paths
      or credential discovery with explicit configuration.
- [ ] Install profile/tools/skills using the engine's real paths and loader.
- [ ] Establish actor identity, least-privilege source credentials, domain action
      permissions and required sharing/retention policy.
- [ ] Prove safe fixture workflows, including failures, rejection and no writes
      without the required approval.
- [ ] Validate startup and application readiness in a separately authorised test
      environment; keep structural checks distinct from runtime evidence.
- [ ] Test user ownership boundaries and inspect source permissions. For admi,
      verify database read-only authority safely; a model's `allow_pii` flag must
      not grant PII access.
- [ ] Test restart and separate-location recovery; document pending-work handling
      and approval invalidation on definition changes.
- [ ] Record deployment limitations, external flows, versions and operator steps.
- [ ] Report required engine changes and undocumented help back to maintainers.

For eddyave, representative acceptance includes sourced reports, failure/evidence
labels, follow-up continuity and recompilation. For admi it includes reviewed
ontology/metric versions, source freshness, read-only access and controlled
artifact sharing. These are adoption targets, not claims that those deployments
already comply.

Two unlike workflows should reuse these conventions without customer-specific
engine edits. An independent builder must then attempt the instructions; record
setup time, failures and manual interventions before claiming self-service adoption.
