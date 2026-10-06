# Website handoff: builder-platform positioning

Purpose: help the site owner update openassistants.org after the repository
convention/documentation pass. This is guidance, not a website change, release
announcement or new feature commitment. Check the current release before posting.

## Suggested core message

**Build your assistant, not its infrastructure.**

Assistant is a self-hostable engine for assistants that work with your files and
tools—not just your questions. Bring your profiles, tools, skills and domain
rules; reuse agent execution, persistence, APIs and model-provider integrations.
Operate the runtime yourself and choose the model and integrations appropriate
for your data.

Application builders own behaviour and business safeguards. Deployment teams own
identity provisioning, credentials, permissions, data and operations. The project
provides the engine, documented conventions and worked examples—not a managed
identity, hosting or fleet service.

A finished native product is a separate future goal using the same engine.
Current macOS development is parked; do not make it the entry requirement for
builders or advertise the experimental app as a finished distributed product.

## Documentation destinations

Use published repository links (adjust branch/release links after integration):

- [README](../README.md): introduction, capabilities, configuration and data flows.
- [Builder/deployer guide](builder-deployment-guide.md): responsibilities,
  package/instance conventions and adoption checklist.
- [Deployment guide](../DEPLOYMENT.md): runtime topology, auth and operations.
- [Synthetic Jen example](../examples/jen_reference/README.md): executable local
  reference and its explicit limitations.

Prefer these entry points over internal planning specs as the first visitor links.

## Claims supported by current source and scoped evidence

- Self-hostable engine with profiles, custom tools, skills and existing HTTP APIs.
- Configurable local/cloud inference and externally configured integrations.
- Existing governed tool dispatch and approval paths; applications must preserve
  deterministic domain safeguards and appropriate credential boundaries.
- Shared deployment keys, opt-in per-user keys and browser OIDC integrations;
  deployment teams choose/configure identity and must test authorisation.
- A **synthetic Jen-shaped** reference demonstrating independent local instance
  preparation, fixture approvals, readiness and stopped-copy recovery.

These are not blanket production/security certifications. The synthetic example
has not established real Jen parity; Docker smoke was deferred for that round.

## Do not publish as shipped claims

- "Everything stays local", "no cloud" or "no accounts" as universal promises.
  Runtime, inference, tools and tracing have distinct destinations and policies.
- Finished native macOS distribution, native SSO or verified multi-instance login.
- Turnkey production templates or verified convention adoption for Jen,
  eddyave and admi. Their existing applications are not three completed platform
  migration proofs, and deployment-team/customer names are not endorsements.
- Hostile-tenant isolation in one shared server, complete organisation-wide
  sharing/roles, or containment of arbitrary code with service credentials.
- A universal package compiler, fleet dashboard, automatic crash recovery,
  general online backup service or completed external-builder trial.
- The proposed exclude-shaped policy as completed; it remains separate work.
- Automatic legacy email/contacts/todos data migration or a first-party mail
  replacement. Their built-in families/APIs are removed by an unreleased breaking
  change; only advertise removal for a release that actually contains it.
  Generic `app_*` tools and optional starter templates provide structured data;
  ConnectKit, coding and CoreMem remain.

## Next evidence gate

A contrasting eddyave adoption is the recommended next candidate. The deployment
team applies conventions in its own environment; no migration is authorised by
this handoff. Demonstrate reuse across unlike workflows and have an independent
builder attempt the instructions before advertising proven self-service adoption.
