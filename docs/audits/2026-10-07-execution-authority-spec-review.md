# #40/#41 draft self-review — 2026-10-07

This is the author's design self-review, not an independent audit, implementation
review or runtime proof. User review of the written drafts remains required.

Reviewed drafts:

- [#40 authority boundaries](../superpowers/specs/2026-10-07-execution-authority-boundaries-design.md)
- [#41 receipt contract](../superpowers/specs/2026-10-07-execution-receipt-contract-design.md)

## Decision reconciliation

The drafts incorporate the latest user decisions rather than older recommendations:

- SL/SR/MT/MU codes; one engine and intended native DMG connection surface.
- Native required; interactive CLI deferred.
- Breaking cutover without automatic migration/backward compatibility, while retained
  customer stores remain untouched.
- Configuration per logical instance/company, with infrastructure restrictions above.
- Conversation model captured at creation; existing conversations do not follow later
  admin default changes; persistent conversation-only user overrides.
- Explicit subagent models pinned; otherwise parent-model inheritance at launch.
- Profile editing by owner or designated/authorized shared-resource maintainers.
- One accepted same-session turn, later prompt not accepted/queued, authorized attach
  to the active run; server admission order rather than untrusted device clocks.
- No new fairness/resource/spending quotas; resource exhaustion is an accepted risk.
- Symmetric private-resource protection and option-2 admin operational visibility.

## Findings resolved in the drafts

1. **Modules versus processes:** one release/codebase is not one privileged address
   space. Preserve tenant/user boundaries without requiring microservices for every box.
2. **Execution before execution:** installation, discovery, MCP startup and health
   checks must not run arbitrary code in privileged coordination.
3. **Model/configuration races:** add committed conversation revisions and expected-
   revision conflicts; a run keeps its admission snapshot. Failed persistence must
   not appear saved. Per-message inputs do not silently replace conversation settings.
4. **Definition changes:** pin run/launch definition revisions while retaining current
   permission/approval checks at subsequent dispatch.
5. **Idempotency versus new defaults:** repeated client intent retrieves its original
   resolved admission binding; changing defaults does not resolve its retry differently.
6. **Revoked observation access:** stop subsequent authorized delivery after revocation;
   do not promise that previously delivered content can be retracted.
7. **Cancellation versus termination:** thread/wait cancellation is not physical or
   remote-job termination. The current kernel's terminal assumptions require changes.
8. **Late evidence:** immutable original attempt plus separately derived evidence
   assessment avoids rewriting a timeout into success.
9. **Recovery ownership:** require fencing/exclusive admission across replacements,
   without claiming exactly-once external delivery.
10. **Scope/durability claims:** CoreMem unit-of-work remains upstream; final receipt
    durability is not full token/primary-run durability. MU provisioning and mandatory
    abuse/resource controls remain outside the first MT scope.

## Implementation prerequisites, not missing shipped proof

The Linux Docker backend/provisioning choice is gated by a separately authorized
isolated feasibility probe. Current sandbox/MCP code is not proof of the proposed
boundary. Native unit tests/builds are not SL/SR/MT end-to-end or DMG verification.
The implementation plan must stage the native contract and real isolation/recovery
acceptance without customer credentials or live fixture inference.

No runtime code, customer deployment, dependency installation, Docker startup,
website, release, merge or push is part of this documentation change.

## Documentation validation scope

Validate local Markdown links, cited source-path existence, balanced fences,
placeholder absence, source-backed lifecycle enum spellings, decision coverage,
whitespace and the documentation-only staged diff. This checks the draft artifacts;
it does not execute the proposed contracts or establish security guarantees.
