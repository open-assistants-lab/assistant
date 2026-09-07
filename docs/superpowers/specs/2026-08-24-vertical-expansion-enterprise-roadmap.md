# Assistant Platform — Vertical Expansion & Enterprise Roadmap

**Status:** Reviewed — strategy approved; Phases 0/2 re-cut; one decision due at
Phase 0 exit (audit data model), two more before Phase 1/2 (see §8a Review Notes)
**Date:** 2026-08-24
**Scope:** Product strategy consolidating persona tiers, go-to-market motions,
requirements matrix, knowledge-ingestion architecture, market evidence, and the
phased roadmap toward enterprise readiness.

---

## 1. Strategic frame: one engine, three motions

Assistant's agent engine (SDK core, tools, skills, memory, HITL) serves three
go-to-market motions. Every roadmap item below serves at least one motion and
climbs toward enterprise readiness (§5).

| Motion | Customer | Model |
|---|---|---|
| **A. Direct firms** | Marketing agencies, accounting firms, legal boutiques, recruiters, property managers, insurance brokers | Per-seat subscription |
| **B. Owned product** | SMB owners via our own AI-agency product (extract brand → design system → deliverables) | Low-ticket self-serve subscriptions |
| **C. Platform partners** | Vertical AI startups building "AI-driven X" businesses | Platform fee + usage margin |

**Motion sequencing:** Tier 1 (partners) is the strategic priority, but its
commercial enablers (metering, multi-tenancy) land in Phases 2–3. Commercial
sequencing: **Motion A (direct firms) and B (SMB product) first; Motion C
opens when metering ships** — if a partner knocks early, onboard on the
existing runtime with a pilot agreement and defer platform-fee billing until
Phase 2 metering exists.

## 2. Persona tiers

### Tier 1 — Platform partners (highest leverage)
Vertical AI startup founders. Rent runtime, ingestion, isolation, metering;
bring their own domain expertise and customers.

### Tier 2 — Direct firm customers
| Persona | Revenue engine | Killer need |
|---|---|---|
| Marketing agencies | Brand & throughput | Brand-kit skills, parallel drafting |
| Accounting firms | Precedent & deadlines | Calendar automation, house-style skills |
| Legal boutiques | Precedent & privilege | Self-host confidentiality, precedent libraries |
| Recruiting agencies | Speed & recall | Candidate memory + outreach pipeline |
| Property managers | Statutory obligation | Rubric-checked notices, renewal scheduling |
| Insurance brokerages | Regulated speech | Approved wording + audit trail |

### Tier 3 — End consumers
SMB owners (Motion B); privacy-conscious individuals / self-hosters
(local-model routing, data never leaves the machine).

## 3. Requirements matrix (deal-breakers first)

| ID | Requirement | Status |
|---|---|---|
| A1 | Nothing outbound without human sign-off (HITL default-on profiles) | HITL exists; defaults needed |
| A2 | Key→identity auth: IdentityResolver seam + shared-secret ref impl
(Phase 0); production per-user key table + body `user_id` validation
(Phase 2, hosted tier) | Seam — **Phase 0**; production — **Phase 2** |
| A3 | Audit trail export per user | **Build — Phase 0** (shared event-capture layer, see note) |

> **REVIEW:** Understated as an "export endpoint." Current JSONL logs are app logs, not an audit trail — HITL approvals and tool executions are not systematically captured. Compliance-grade audit needs complete, append-only coverage of *state-changing* actions first; the export endpoint is trivial once that data model exists. Scope the Phase 0 item as "audit-event data model + capture in agent loop/HITL paths + export," not just the endpoint. Design the capture layer **once, shared with telemetry** (§8a decision 1): one event stream at the loop/tool boundary with two sinks — audit store and aggregation sidecar.
| A4 | Confidentiality posture (self-host / residency, no-training contracts) | Architecture native; business work |
| A5 | Predictable pricing (per-seat; usage capped) | Metering — Phase 2 |
| A6 | Exit ramp (full export + deletion API) | Data model ready; API needed |
| B1 | Knowledge ingestion from messy sources | §4 — Phase 1 core |
| C1 | Draft-delivery surface (email-draft pattern) | Phase 2 |
| D1 | Owner dashboard (hours saved, throughput, cost/seat) | Phase 2 |
| K1 | Main-agent-from-profile bootstrap: a user-level PROFILE.md
(AgentProfile) instantiates the *main* loop, not just subagents | **Build — Phase 0** (see §4.5) |
| R-SB1 | **SandboxBackend seam** (dsh-inspired): one interface; *all*
process-spawning tools (shell, code_execute, CLI adapters, future terminal)
route through it; backends = soft / hard / microVM; per-agent selection | Seam + soft — **Phase 2**; hard — **Phase 3** |
| R-SL1 | **Event-sourced session log**: append-only; *model-visible ⟺ logged*
invariant; model history derived from log; no checkpoints (round-time
preserved); fork/resume/replay derive from the stream | Schema — **Phase 0** (P0-T9); capture/derivation — **Phase 1** (P1-T10..T12); fork/resume — **Phase 3** |
| R-PL1 | **Selective pluggability**: strategic seams at package boundaries
(sandbox, session log, providers, tools, skills, persistence); agent loop
stays concrete | Seams kept as in-repo design boundaries; **no package
extraction** — PyPI dropped (deployment-first, 2026-08-26) |
| R-A2A | **A2A protocol adoption** — Agent Cards, outbound delegation,
inbound task acceptance; MCP + A2A simultaneously (MCP = tools, A2A =
agent collaboration) | **Phase 3** (T3.8, spec §6.5a) |
| H1 | **Tiered HITL approval** — per-tool risk tiers (autonomous reads /
show-then-auto-send / explicit approval / hard block); 93% approval-fatigue
stat makes binary HITL unusable at scale (§8.1) | Tiered profiles — **Phase 1** |
| H2 | **Per-run tool-call budget** (avalanche prevention) | `RunConfig` — **Phase 1/2** |
| H3 | **Injection sanitization at ingestion boundaries** — external content
(email/web/docs) passes strict-schema triage before entering model context
(#1 cited tool-boundary failure mode) | **Phase 1** (with P1-T3) |
| H4 | **Eval-on-deploy regression gate** — persona/kit evals run on
default-model change (silent provider upgrades regress quality) | **Phase 1** |
| H5 | **Model routing per kit/task complexity** (frontier for high-stakes,
cheap for high-volume — price-war bifurcation) | **Phase 2** (pricing lever) |
| H6 | **Failure→constraint loop** — review-queue flags auto-surface as
rubric/skill updates in the kit factory | **Phase 1** (with P1-T8) |
| H7 | **Success-triggered skill drafts + compounding metric** — complex
task success auto-drafts a SKILL.md (→ review queue); task-duration trend
per recurring workflow surfaced on the owner dashboard (Hermes-style
compounding, review-gated); **outcome-conditioned refinement on re-runs**
(load skill → refine by outcome — Phase 2) | Drafts — **Phase 1** (with P1-T8); metric + refinement —
**Phase 2** (dashboard D1-1) |

## 4. Knowledge ingestion (Phase 1 core)

### 4.1 Evidence base
Firm knowledge does not live in tidy folders. Verified findings:

- **69%** of organizations store documents in email inboxes (#1 repository)
- **55%** shared drives, **54%** local desktops; **only 24%** use a real DMS
  (M-Files/Vanson Bourne, n=1,500, 9 countries)
- Average firm uses ~4 repositories; 82% report version-hunting hurts productivity
- **80–90%** of business data is unstructured
- Fifth source beyond files/mail/sheets/systems: **tribal knowledge in people's
  heads** — recurring finding in SME deployment literature

Implication: an assistant that requires clean, centralized data fails exactly
the customers we target. Meeting the chaos is the product.

### 4.2 Two kinds of knowledge (triage before ingest)
| Kind | Examples | Destination |
|---|---|---|
| **Methodology** (how the firm works) | house style, playbooks, precedent structures, brand rules | Skills (drafted → reviewed → versioned) |
| **Reference** (what the firm knows) | client records, case history, price tables | Indexed corpus (HybridDB FTS5 + ChromaDB), queryable via existing search tools |

Never bake reference data into skills. Skills stay lean; facts stay searchable.

### 4.3 Source adapters (read-only, OAuth via ConnectKit)
| Source | Existing plumbing | To build | Priority |
|---|---|---|---|
| Email (Gmail/M365) | `email_db`, `email_sync`, OAuth, message tools | Pattern-mining pass → methodology drafts + reference corpus | **P0** (largest repository per research) |

> **REVIEW / DECISION REQUIRED:** Email mining (P0) contradicts the A4 confidentiality posture for legal and insurance personas unless routing is constrained. Mining quality pushes toward cloud LLMs; the pitch for these tiers is data never leaves the machine. Decide before Phase 1: opt-in per workspace, local-model-routed only, or cloud-with-explicit-consent — per persona tier. Unresolved, this is a sales-blocker in the exact segment that pays most.
| Files / Dropbox / Drive / OneDrive | `files_*` tools, `FileCache` cloud states | Sync adapter → workspace `Files/` tree | **P0** |
| Spreadsheets (XLSX/CSV) | — | Parser → `app_*` tables + summarization pass ("what this workbook is") | P1 |
| Databases / practice systems | MCP bridge, ConnectKit credential vault | Read-only SQL connector; per-SaaS later against named design-partner systems | P2 (build on demand) |
| People's heads | user_prompt, interview tooling | Structured **knowledge interview**: gap-report-driven questioning of the owner when sources run dry | P1 (differentiator) |

### 4.4 Pipeline shape
```
sources ──adapters──▶ normalized corpus ──LLM triage──▶ skill drafts
                                                      ▶ indexed corpus
                                                      ▶ rubric hints
                                                      ▶ gap report ──▶ knowledge interview
                                        everything ──▶ human review queue (partner approves)
```

Non-negotiables: read-only at ingest; nothing auto-commits; gaps surfaced as
interview questions rather than silently ignored; **external content passes a
sanitization/strict-schema triage step before entering model context (H3)** —
prompt injection at tool boundaries is the #1 cited agent failure mode.

### 4.5 Main-agent profile bootstrapping (kit runtime)

Today an AgentProfile only ever instantiates **subagents**
(`SubagentCoordinator`); the main loop is assembled from config/settings/
capabilities and never reads a profile (`runner.get_sdk_loop`). To make kits
installable as single artifacts — and to give Motion-C partners a checked-in
definition of their product's agent — add a composition layer:

`load_main_agent_profile(user_id)` reads a user-level `PROFILE.md` and drives
loop creation. All underlying machinery exists; this is composition only
(estimated 2–4 days):

| Piece | Mechanism |
|---|---|
| Model resolution | `profile.model` → registry validation (exists) → `create_model_from_config()` → loop |
| Persona wiring | Profile body → `_get_system_prompt()` user-prompt slot |
| Limits | `max_llm_calls` / `cost_limit_usd` / `timeout_seconds` → `RunConfig` (fields already exist) |
| Skills | Catalog injection unchanged; `profile.skills` validated against registry |
| Lifecycle | Re-validate + `reset_sdk_loop(user_id)` on profile change; must also detach active WS streams (session registry, E26) so a mid-session profile swap never leaves a stale loop serving an approved turn |

**Precedence rules (the one real design decision):**
1. Capabilities/scopes always win over `profile.tools` — governance outranks
   convenience, consistent with the enterprise ladder.
2. Profile persona wins over `user_prompt_set` free text; the prompt remains
   an override channel.
3. Absent fields fall back to current settings-derived behavior (profiles are
   additive; no profile = today's behavior).

> **REVIEW:** Scope estimate (2–4 days) verified against `runner.py` — accurate; precedence rules consistent with capabilities governance. One nit: `profile.model` validation should also check provider-key availability at loop creation (the registry knows thousands of models; failing at first call instead of bootstrap is a poor partner experience).

## 5. Enterprise-ready summit (destination definition)

Every roadmap item must climb one of five rungs:

1. **Identity** — SSO/OIDC, per-user keys, RBAC
2. **Governance** — audit trails, retention, approval workflows
3. **Isolation & residency** — org tenancy, VPC/private deploy, data residency
4. **Compliance posture** — SOC 2 / ISO 42001, DPA readiness
5. **Operations** — SLAs, observability, admin surface, cost controls

Decision rules: (a) no dead-end shortcuts — every feature survives the
enterprise customer's arrival; (b) each release names its rung; (c) borrow
enterprise requirements cheaply and early (docs, versioning, policy).

Enterprise is **not** claimed as ready. Language discipline: "enterprise-grade
architecture" (true today: isolation, audit, self-host) with a funded path to
"enterprise-ready." Beachhead when reached: in-house legal/tax/compliance teams.

## 6. Packaging, sandboxing & deployment

### 6.1 Distribution posture — deployment-first (PyPI dropped)

**Decision (2026-08-26): Docker is the distribution. No PyPI package is
published or planned.** The earlier "pip install → ready-to-customize agent"
goal is superseded by "`docker run` + mount a `PROFILE.md` + env" — onboarding
via a deployment artifact with one trust boundary, not a package import.

Reasons: (a) no current consumer imports the Python engine — firms deploy the
server (Motion A), subscribers use the hosted product (Motion B), partners
integrate via the HTTP/SSE/WS API + TS SDK + `PROFILE.md` (Motion C);
(b) the repo currently packages the whole monorepo as `assistant-sdk`, so a
PyPI publish today would ship the wrong artifact and force the S3-1 extraction
before it earns its keep; (c) dropping it removes a full workstream (agents
consume the product via API, not Python imports — and OSS tinkerers clone).

What remains: **module hygiene stays a design discipline** — the seams
(`IdentityResolver`, `SandboxBackend`, session log, registries) remain in-repo
boundaries (§6.5) so a package split, if ever reversed, needs no refactor. The
TS SDK npm preview (P0-T6) is **retained** — it is the *client* artifact for
integrations, not the engine.

| Channel | Distributes | Consumers |
|---|---|---|
| Docker images | The running product | Firms, hosted tier, VPC/enterprise, partners (self-host) |
| npm `assistant-client@0.1.0-preview` | Typed client for integrations | Partner frontends, native/web clients |
| git clone | Source (OSS development) | Self-hosters, contributors |
| ~~PyPI~~ | ~~engine package~~ | ❌ dropped 2026-08-26 — revisit only if a consumer demands an importable Python engine |

**Production config requirements (deployment-first):** `CONNECTKIT_VAULT_KEY`
(else each per-user vault mints an ephemeral Fernet key and OAuth refresh
tokens are lost on restart), `API_PUBLIC_URL` (OAuth redirect base),
`DEFAULT_GWS_CLIENT_ID/SECRET` or per-user connector creds, `EA_API_KEY` on
non-localhost (auth spectrum, §6.2).

### 6.2 Tool trust tiers (single-container defaults)

| Tier | Tools | Single-container default |
|---|---|---|
| **T1 — Safe by default** | `files_*` (path-scoped to user root), `web_fetch`, `web_search`, firecrawl, time, memory, todos, contacts, skills | ✅ On |
| **T2 — On with caveats** | `agent-browser` (egress policy: block private ranges; auth-on recommended), MCP servers (per-server allowlist) | ✅ On, with mitigations |
| **T3 — Trusted-user only** | `shell_execute`, any CLI adapter executing arbitrary code | ❌ Off per user; operator opt-in |
| **T4 — Real isolation required** | Untrusted code execution at scale | Container-per-user or hard sandbox |
| **T5 — Untrusted at scale** | MicroVM (Firecracker/Kata) per task | Commercial/hosted tier |

Auth is not binary — it scales with trust domains: solo/localhost (none,
default) → trusted network (shared-secret, today's `API_KEY` model) →
untrusted network (per-user key→identity mapping, commercial tier). The
mechanism ships in all cases; deployment config decides.

### 6.3 Sandbox strategy (make-vs-buy + seam)

Never build the sandbox itself — build the integration layer. A sandbox is
security-critical commodity; our value is the `code_execute` *wrapper*
(agent-facing tool, workspace path policy, env scrub, limits, file-lifecycle
wiring into files_read/versions).

**OSS trajectory (2026-08-26 — honest framing):** the wrapper's adoption
vehicle is **Assistant itself** — sandbox libraries don't get adopted as
libraries (E2B/Daytona = services; bubblewrap = only-tool-for-the-job;
LangChain backends = framework-bound). The **pattern** is the contribution:
the `execute()`-only contract, the soft/hard ladder, the no-root rule, and
the UID-drop pattern are documented here and in blog posts for others to
copy. The soft tier is ~200 lines of stdlib Python — the value is knowing
what to build, not the artifact. Extraction stays a cheap *option* (zero-
import discipline keeps it a directory move), triggered only by external
pull — never a roadmap item. This avoids the permanent maintenance tax of a
public security-critical package (every vulnerability report is yours
forever) for an audience that may not exist.

**Adopt dsh's sandbox seam (R-SB1) + LangChain's `execute()`-only contract:**
define a `SandboxBackend` interface (Service Definition / Provider / Consumer
split) and route **every process-spawning tool** through it — `shell_execute`,
`code_execute`, CLI adapters, future terminal — so no tool can forget to
sandbox. Backends are implementations behind the seam, swappable per deployment
and per agent (`isolate` realm concept).

**The `execute()`-only contract (LangChain-validated):** the **only method a
backend must implement is `execute(command) → {output, exit_code, truncated}`**
— every other filesystem operation (`read`, `write`, `edit`, `ls`, `glob`,
`grep`) is *derived* by the base class, which constructs scripts and runs them
via `execute()`. Adding a new provider = implementing one method. The
`code_execute` tool is **conditionally available** — if no backend is
configured, the tool is filtered out and the agent never sees it.

**Lifecycle:** sandboxes are **session-scoped** (default) — created on first
`code_execute` for a session, reused on follow-up turns, TTL-expired when
idle. **User-scoped** sandboxes persist across sessions for state that must
survive (agent-built tables, installed packages). Document the mapping
(`session_id → sandbox_id`) so follow-up turns resolve to the same sandbox.

**Security boundaries (LangChain-validated):**

✅ Sandboxes protect: local files, env vars, credentials, other processes.

❌ Sandboxes DON'T protect: **context injection** (attacker controls the
agent's input → instructs it to run commands *inside* the sandbox), **network
exfiltration** (unless blocked), **secrets in the prompt**. Mitigation: scrub
env at injection, never put secrets in the prompt, block network where the
provider supports it (H3 sanitization, egress policy).

| Backend | What it is | Threat model | When |
|---|---|---|---|
| **Soft** (subprocess + cwd/env/timeout caps) | Guardrails, not isolation | Trusted user, untrusted code | Phase 2 |
| **Soft+UID** (per-user OS account drop: `os.setuid/setgid` in `preexec_fn`, per-user homes, data-dir chowns) | Adds kernel-enforced filesystem/env/process isolation between *trusted* users — Unix permissions block cross-user reads and signals | Trusted users who collide by accident | Optional Phase-2/2.5 refinement |
| **Hard** (bubblewrap / runc / nsjail per task) | OS-level isolation per execution | Untrusted tenants in single container | Phase 3 |
| **MicroVM** (Firecracker / Kata / remote E2B) | Separate kernel per task — strongest practical isolation | Untrusted at scale; snapshot/resume for agent sessions | Commercial tier |

Decision matrix — when container-per-user is required:

| Scenario | Soft sandbox enough? | Container-per-user? |
|---|---|---|
| Single tenant, trusted users | ✅ | No |
| Multi-tenant, trusted (vetted) users | ✅ | No |
| Multi-tenant, untrusted, no code exec | ✅ (path-scoped tools + quotas + auth) | Optional (resource/blast-radius) |
| Multi-tenant, untrusted, with code exec | ❌ | **Yes — or hard sandbox per task** |

**Deployment model:** single container by default for trusted users (soft
sandbox OK); container-per-user for untrusted tenants (or hard sandbox per
task as the cheaper path to the same place); container-per-user offered as
the enterprise isolation product tier, not the default. Scaling rule: shard
by user (consistent hashing), never replicas of the same user (single-writer
stores + in-memory caches). Loop-cache eviction (LRU, idle-only) bounds memory
in single-container mode.

**Security rule — no agent runs as root (non-negotiable):**
the assistant process (parent) runs as root or CAP_SETUID to manage UID drops,
but **every agent subprocess — including admins' — drops to a non-root UID via
`preexec_fn` before exec.** The admin's elevated capabilities come from
admin-level *app permissions* (user management, policy editing, deployment),
not from the agent's runtime privileges. If the admin's agent ran as root:
prompt injection → root shell → full container compromise → all users' data
exposed. The UID-drop is uniform — no exceptions for any role.

### 6.4 Event-sourced session log (R-SL1)

Adopt dsh's session-log pattern **without checkpoints** — the loop stays
stateless-ish and model history is *derived* from an append-only event log,
so round time is preserved (append-only writes, no per-step state
serialization). This extends the existing message store rather than replacing
it.

- **Invariant:** *model-visible ⟺ logged* — everything that reaches a model
  request (system-prompt assembly, injections, steering, reasoning, tool
  calls/results) must be reconstructable from the log; enforced by a runtime
  assert in dev/tests
- **Derivation:** `deriveMessages()` projects model history from the log
  (replaces/augments current history reconstruction)
- **Derived features:** fork, resume, replay, telemetry, persistence all
  derive from the same stream (Phase 3+)
- **Shared capture layer:** the session log is the natural home of the A3
  audit + telemetry capture (one event stream, two sinks) — design together
  with §8a decision 1
- **Engine-enforced tamper-evidence (2026-08-26):** HybridDB 0.6.0 shipped
  `versioned=True, hash_chain=True` — the engine maintains a SHA256 hash
  chain on every write (`verify_chain()`, `diff()`, `as_of()`,
  `checkpoint()/rollback()`, `archive()/prune()` with chain anchors). The
  session log's audit trail upgrades from app-computed hashes to
  **engine-enforced** tamper evidence; `prune()` is chain-safe (anchors keep
  `verify_chain` valid for the retained tail). Measured write overhead: ~13%.
  `fork` was deferred by HybridDB (checkpoint/rollback covers the rewind
  workflow) — B7 fork/resume stays app-level until a consumer demands it.

  workflow) — B7 fork/resume stays app-level until a consumer demands it.

### 6.5 Selective pluggability (R-PL1)

Adopt the *philosophy* selectively, not the full plugin-everything refactor.
Strategic seams at package boundaries; the agent loop stays concrete.

| Seam | Status | Formalize at |
|---|---|---|
| LLM provider | ✅ exists | in-repo seam (design discipline) |
| Tool registry | ✅ exists | in-repo seam |
| Skills | ✅ exists | in-repo seam |
| MCP bridge | ✅ exists | in-repo seam |
| Sandbox backend | 🔴 new (R-SB1) | Phase 2 |
| Session log | 🔴 new (R-SL1) | Phase 0/1 (schema P0-T9; derivation P1-T10..T12) |
| Session persistence | 🟡 partial | in-repo seam |

Decision: interfaces defined now (cheap), implementations later; full
plugin-everything deferred until a partner demands it or a Python-native
composability framework (e.g. Ouroboros) matures. **No PyPI / package
extraction** — the seams are in-repo design boundaries, not a release surface
(deployment-first, 2026-08-26).

Decision: interfaces defined now (cheap), implementations later; full
plugin-everything deferred until a partner demands it or a Python-native
composability framework (e.g. Ouroboros) matures. **No PyPI / package
extraction** — the seams are in-repo design boundaries, not a release surface
(deployment-first, 2026-08-26).

### 6.5a Protocol stack — A2A adoption (2026-08-26)

The agent interoperability landscape has converged into a **three-layer
stack** under Linux Foundation governance (Agentic AI Foundation —
Anthropic, Google, Microsoft, AWS). We adopt all three layers:

| Layer | Protocol | Our status | When |
|---|---|---|---|
| **Tool Integration** (agent → capabilities) | **MCP** | ✅ shipped (mcp_bridge, mcp_manager) | Phase 0 |
| **Agent Coordination** (inter-agent) | **A2A** | 🔜 adopt | Phase 3 |
| **Identity / Trust** (cross-cutting) | **OAuth 2.1 + Agent Cards** | ✅ seam exists (IdentityResolver) | Phase 0/3 |

**A2A adoption (Phase 3, Motion C):**
- **Agent Card**: expose each Assistant agent's capabilities as an A2A Agent
  Card (JSON discovery endpoint) — partners' agents can find and delegate to
  us
- **Outbound delegation**: our agents can delegate tasks to remote
  A2A-speaking agents (partner ecosystems, vertical agents) — same task
  lifecycle as our SUB-1/SUB-2, different transport (HTTP/SSE/JSON-RPC)
- **Inbound acceptance**: our agents accept tasks from remote A2A agents
  (routed through H1 middleware + capabilities tiers)
- **Not ACP**: ACP (IBM/AGNTCY) is a lighter REST alternative — we don't need
  both; A2A covers the task lifecycle and artifact exchange that partners
  need
- **Not ANP**: decentralized DID-based agent identity is architecturally
  compelling but not production-ready — watch, don't build

**Strategic statement:** MCP for tools/context within one agent; A2A for
collaboration between agents; HybridDB for versioned trust. Three standards,
one engine, zero proprietary protocols.

### 6.6 Data topology — one HybridDB per user + fundamental tools

**Decision (2026-08-26): one HybridDB instance per user; domains live as
namespaced versioned tables. Not one DB per tool.**

- Why per-user (not per-tool): one instance = **one semantic index spanning
  all domains** ("everything I know about Alice" spans email + contacts +
  notes in a single query), one journal/queue, one backup/exit path (A6),
  and matches the single-writer-per-user rule. Per-tool stores chop the
  user's world into islands and force a fan-out search layer.
- Tables get domain prefixes (`contacts`, `todos`, `knowledge`, `email`,
  `session_events`). All versioned (`versioned=True, hash_chain=True`).
- **Growth → archive, not split:** heavy domains archive to Parquet
  (JSONL for the git-review view) and prune chain-safely. **Escape hatch:**
  a domain that outgrows the shared store moves to its own HybridDB instance
  — safe because tools are thin consumers over tables, never over paths.

**The two-layer tool model:**

| Layer | What | Examples |
|---|---|---|
| **Fundamental tools** (built-in) | Workspace files, web search/fetch, agent-browser, memory + hybrid query, `code_execute` (sandbox) | the agent's hands and eyes |
| **Domain tools** (thin, HybridDB-backed) | `email_search/get`, `contact_upsert`, `knowledge_query` — thin consumers over versioned tables | G5+ |
| **Agent-built tools** | via `code_execute`: agents write workspace scripts over their own tables; the kit factory packages the good ones | self-extension |

**How the agent uses it:**
- **Governed process definitions (the maintained layer):** business-editable
  definitions per process (policy rules + workflow steps + data bindings +
  HIL tier + tests — one artifact, GoRules-JDM principle generalized to
  processes). The platform **compiles** each definition to: the LLM knowledge
  section, the middleware enforcement (deterministic rules), and the
  workflow/skill content. **The ontology view (objects/links) is DERIVED**
  from these definitions + data introspection — never separately maintained
  (correction from the business-process review: business people maintain
  *processes*, not semantic models).
- **System prompt:** the conditional **"Your Data"** section is generated from
  the compiled definitions + introspection — which versioned tables exist,
  the process rules in prose, and that history/diff/rollback are available —
  never advertising absent tables (same conditional-governance pattern as
  `_MEMORY_TOOL_GUIDANCE`)
- **Skills/kits:** kits ship **starter process definitions** (the vertical's
  standard processes, business-editable); customer review refines them
- **Search:** one semantic index per user spanning all domains →
  cross-domain recall is a single query
- **Trust tiers (H1)** apply per process definition and domain table via
  capabilities — versioned data inherits the approval ladder

### 6.7 Research note — Open SWE / production coding-agent convergence (2026-08-26)

LangChain's Open SWE (MIT, on Deep Agents/LangGraph) productizes patterns
three production internal coding agents converged on independently: Stripe
Minions, Ramp Inspect, Coinbase Cloudbot. Convergence = validated
requirements. Full comparison table in the source post; mapping to
Assistant below.

**Validated (already our design — no action):** isolated sandboxes with
full permissions inside the boundary (= §6.3 ladder); curated toolsets
(= capabilities filtering + schema budget; Stripe curates ~500, Open SWE
ships ~15); subagent orchestration with isolated child contexts (= SB2
V2); composition-vs-own-engine is a legitimate choice (Stripe forked,
Coinbase scratch-built — no verdict against our own engine); mid-run
message injection via queue-check middleware (= SB2-3 completion bus +
SubagentContext instruction drain — human follow-ups and subagent
completions should share this path long-term); "sandbox auto-recreate if
unreachable" → adopt as behavior for session-scoped sandboxes.

**Adopted (3 patterns):**
1. **Deterministic completion backstops (H8)** — model-driven
   orchestration is flexible, middleware is reliable; pair them. If the
   agent finishes without the critical step (draft saved? file version
   changed? artifact present?), middleware catches it. Complements
   RubricMiddleware (model-graded) with a deterministic net.
2. **Context pre-hydration at trigger time** — assemble full context
   (email thread, memory, knowledge entries) into the prompt BEFORE the
   loop starts; never make the agent discover requirements via tool
   calls. Applies to every trigger-sourced run (email events, scheduled
   check-ins, subagent wake via SB2-3).
3. **Deterministic session derivation + ack→work→report** — external
   invocations derive session IDs from the source (email thread-id →
   session-id) so follow-ups continue the same conversation; surfaces
   acknowledge receipt before working (Linear 👀 pattern → email ack).

**Skipped:** cloud sandbox providers (Modal/Daytona/Runloop — opposite of
deployment-first; ladder covers it self-hosted); Deep Agents composition
(own engine); pre-warmed sandboxes (T3.5 container-tier optimization note
only).

## 7. Phased roadmap

### Phase 0 — Trust foundation (weeks 1–3)
- **Auth seam + shared-secret reference impl**: `IdentityResolver` protocol
  (§6.1). Never trust `user_id` past the resolver boundary. Production
  per-user key→identity deferred to Phase 2 (hosted tier) — OSS default is
  trusted-network with shared-secret
- Audit-log export endpoint per user
- `/v1` route aliasing
- Publish TS SDK to npm as **`0.1.0-preview`** (explicit non-frozen contract;
  distribution is deployment-first — see §6.1); live-server integration smoke
  tests; stable release after a partner has exercised both transports (SSE +
  WS) for ~a month
- **Main-agent-from-profile bootstrap (K1, §4.5)**: `load_main_agent_profile()`
  composition layer — a user-level PROFILE.md instantiates the *main* loop.
  Foundational runtime for kits, Motion-C partners, and the
  `create_agent(profile)` entry point (K1-API surface; no PyPI — §6.1)
- **Session-event schema (R-SL1 foundation, P0-T9)**: `SessionEvent` schema as
  the CaptureBus substrate — audit (A3), metering (P2), and model-context
  derivation (P1) all read one stream; no checkpoints
- **Rungs:** Identity (seam), Governance
- **Gate:** external partner authenticates (shared-secret), streams a response,
  pulls an audit export (from the new event-capture layer, A3); a partner's
  checked-in PROFILE.md instantiates their agent.

> **REVIEW:** Two concerns, both resolved. (1) TS SDK publish no longer
> freezes the wire contract — it ships as `0.1.0-preview` (non-frozen),
> stable after partner exercise of both transports. (2) The auth seam is
> easy; enforcement is not — `user_id` arrives as query/body across **19
> routers** (verified), and "never trust past the resolver boundary" implies
> a sweep of every router, which is the real Phase-0 cost and must be
> reflected in the estimate. Gate wording aligned with the re-scoped A3
> deliverable (data model + capture, then export).

### Phase 1 — Vertical value & the kit factory (weeks 3–9)
- Knowledge-ingestion pipeline with source adapters (§4): P0 adapters first
  (file sync + email mining), then sheets, then interviews
- Design-system extractor (URL → look & feel → design-system SKILL.md) —
  keystone demo artifact
- First vertical kits authored: marketing agency + accounting firm; third
  vertical created end-to-end via the factory (or partner) without engine
  changes — exit criterion proving verticals are content, not code
- Kit format: skills + agent templates + rubrics + eval sets (pure content)
- **Gate:** a real firm onboarded < 1 day, producing client-ready drafts from
  *their own* scattered sources by day 2.

> **REVIEW:** Product-level gate only; add an engineering gate for ingestion quality, e.g. "≥70% of auto-drafted skills accepted without human edits" on the design-partner firm. Without it the factory can pass while producing junk. Kit eval sets should run through the existing evaluation harness (`tests/evaluation`) — currently unwired.

### Phase 2 — Money & habit (weeks 9–13)
- Usage metering per user/tenant → queryable usage/billing API
- Owner dashboard: drafts produced, hours saved, cost per seat
- Draft-delivery UX (email-draft pattern first)
- **`code_execute` via SandboxBackend seam (R-SB1)**: define the
  `SandboxBackend` interface; ship `SoftSandboxBackend` (cwd = user workspace,
  scrubbed env, timeout/output/memory caps, write-path validation). Unlocks
  file-producing scripts (charts, PDFs, reports) for **trusted users in
  single-container mode**; `shell_execute` + CLI adapters routed through the
  seam for uniform coverage
- Pricing live: seats (firms) / subscriptions (SMB) / platform fee (partners)
- **Production per-user key→identity auth** (hosted tier opens): key table,
  server-side key→user mapping, body `user_id` validated against caller's key
- **Rungs:** Operations
- **Gate:** 3–5 paying customers across ≥2 motions; month-one retention; one
  customer presenting their ROI dashboard unprompted. Seed raise follows.

> **REVIEW:** Overloaded — metering + billing API + dashboard + draft-delivery UX + soft sandbox + production auth + SDK-extraction start will not fit in weeks 9–13 at solo-plus-AI pace. Re-cut into tracks: money-path (metering → per-user auth → pricing live) vs habit-path (dashboard, delivery UX); SDK extraction start moves behind money-path completion. §10's cut-order already concedes this — make the phase structure match.

### Phase 3 — Mid-market & multi-tenancy (months 3–6)
- Org-level tenancy schema (org → sub-tenant → user)
- RBAC (owner/staff/admin views)
- SSO via OIDC
- **Hard sandbox as SandboxBackend backend (R-SB1)**: `BwrapSandboxBackend`
  (bubblewrap) / `RuncSandboxBackend` (container per task) — untrusted tenants
  in single container; **container-per-user offered as the enterprise tier**;
  per-agent sandbox selection (`isolate` realm)
- **Fork / resume / replay from the session log (R-SL1)**: product features
  derived from the event stream
- **Shared sessions (multiple humans, one agent)**: trusted-team collaboration
  — participants share one agent's session context (OpenClaw 2.0-confirmed
  model: usability features, **not** a security boundary; per-user data
  isolation remains via container-per-user or per-user stores). R-SL1's
  event-sourced log provides per-participant identity tagging and the shared
  context substrate. Ownership layers (creator/owner/participants) for
  session accountability.
- **Backup / persistence / restore**: per-user backup API (wraps HybridDB
  atomic backup + workspace files + vault), scheduled backup job (daily,
  rolling retention), export API (JSONL/Parquet per table — rides the
  session log and audit exports), restore + verification. Per-user stores
  make backup per-user (no cross-user coordination). Shared sessions back
  up via the org-level store (Phase 3 tenancy).
- Partner program formalized: Vertical Starter Kit repo, kit versioning &
  distribution (seed-hash refresh pattern generalized)
- **SDK extraction completed, replaced by deployment packaging (PyPI dropped
  2026-08-26)**: partners onboard via Docker image + `PROFILE.md` + kits — no
  `pip install` path; module seams stay in-repo (§6.5)
- **Rungs:** Identity complete, Isolation begun
- **Gate:** first partner running their product atop our engine with isolated
  sub-tenants; one 50+ staff account onboarded via SSO unaided.

### Phase 4 — Enterprise readiness (months 6–18+)
- SOC 2 Type II process (evidence clock starts early)
- SLAs, observability stack, status page
- Private VPC deployment packaging; AU-region residency option
- Compliance pack: DPA, security posture, pen-test summary
- **Rungs:** all closed.

## 8. Market evidence (for investor materials)

- AI agents platform market ≈ **$11–12B (2026)** → $48–57B by 2030–31,
  CAGR 42–46% (Grand View Research, BCC Research, Mordor Intelligence)
- Gartner: **AI-agent software spend $206.5B in 2026, +139% YoY**; 40% of
  enterprise apps embed task-specific agents by end-2026 (<5% in 2025)
- Deployment gap: **93% intent vs 23% at production scale** (McKinsey /
  MuleSoft) — the packaged-platform opportunity
- Gartner: >40% of agentic projects canceled by 2027 (cost overruns, missing
  risk controls) — HITL + audit + predictable pricing directly answer stated
  failure causes
- Bessemer: vertical AI startups reach **80% of traditional SaaS contract
  values growing 400% YoY**; vertical agents cut domain error rates 20–40%
- SMEs fastest-growing adopter segment (**43.6% CAGR**); hybrid/on-prem
  deployment growing **44.6% CAGR** — privacy-first positioning tracks demand
- Competitive context: Anthropic Managed Agents (beta), OpenAI Agents SDK,
  LangSmith Deployment, Letta Cloud, E2B/Browserbase/Composio component layer.
  Differentiation: personal-data primitives (email/contacts/memory), privacy-
  first self-host with local models, packaged vertical patterns.
  Even Anthropic's managed offering excludes ZDR/HIPAA coverage.

### 8.1 Deep-dive refresh (2026-08-26) — memory, pricing, sandbox, HITL

**Agent memory (the fragmented layer):** Mem0 (~48K★, general-purpose), Zep
(temporal KG, 63.8% LongMemEval), LangMem, Letta (~18K★, 83.2%) compete on
*retrieval quality*; analysts predict consolidation by Q1 2027, survivors =
"strongest self-hosting story and lowest latency". **None offer versioning,
tamper-evidence, or audit** — and the market's stated open question is
verbatim our product: *"where does your company's knowledge live, who
maintains it, and how does the agent participate in that loop without quietly
rewriting things humans haven't reviewed?"* Our answer is the two-OSS-product
stack: **CoreMem** (zero-LLM memory semantics — compiler, dreaming, search)
+ **HybridDB 0.6.0** (versioned, tamper-evident, hybrid-searchable storage).
We do not compete on retrieval benchmarks; we own versioning + audit +
self-host + files-first AI-readability. (The comparison literature itself
calls out a "5th path — plain markdown + semantic search — small teams
overlook": ours is that, with versioning and audit.)

**Metered economics arrived (validates metering-first, M1):** Anthropic
replaced flat-rate with per-user, non-poolable Agent Credits (May 2026);
DeepSeek's price war is bifurcating frontier-vs-routine workloads; Zendesk
pilots **outcome-based pricing** (pay per resolution) — a watch-option that
only verifiable agents can sell (our rubric/eval layer is the prerequisite).
Model routing per task complexity (H5) is now a confirmed cost *and* product
feature.

**Sandbox ladder validated by Anthropic's own containment patterns:**
gVisor microVMs (claude.ai), OS sandboxing (Claude Code — bubblewrap/Seatbelt,
our Phase-3 hard sandbox), full VM with credentials-never-enter-guest
(Cowork — our container-per-user). Their weakest layer was their own custom
code, not the kernel layers — confirming our discipline: soft sandbox =
guardrails for trusted users, never a security boundary; hard isolation =
untrusted tenants.

**Approval fatigue is the HITL risk:** users approve **93% of permission
prompts**; auto-classifiers cut prompts 84% but missed ~17% of risky commands.
→ Tiered HITL (H1: autonomous reads / show-then-auto-send / explicit /
hard-block) with deterministic risk rules — not approve-everything fatigue.

**Hermes Agent (Nous Research) — the #1-agent validation (Aug 2026):**
Hermes hit #1 on OpenRouter (224B tokens/day vs OpenClaw's 186B) on three
architectural bets that match our roadmap 1:1: (1) **persistent memory with
cross-session recall** (SQLite + FTS5 + summarization — "the agent curates its
own memory"); (2) **the self-improving loop** — task success auto-generates a
skill file, compounding to 2–3× task speedups within weeks; (3) model
agnostism (200+ models, one key) on a $5-VPS persistent runtime. **Two
lessons to adopt:** (a) *success-triggered skill drafts* — draft the skill on
task success (H7), not only from failures; (b) **make compounding felt** —
task-duration trend per recurring workflow on the owner dashboard (D1) is the
renewal argument in the user's own numbers. **Deliberate difference kept:**
Hermes's self-improving loop rewrites itself *without review* — the market's
stated trust question ("who reviews agent rewrites?") is exactly where our
review-gated, versioned compounding (kit factory + HybridDB 0.6.0 + tiered
HITL) is the compliance-grade answer. Also validates deployment-first: single
install, self-host, no lock-in — and the OpenRouter daily-velocity signal is
the health metric our versioning layer enables for the same race.

**MCP contested as protocol layer** (A2A, AGENTS.md, CUA contesting): the MCP
bridge stays one transport seam among several (already the design) — never an
architectural bet. Injection at tool boundaries remains the #1 cited failure
mode at boundaries → H3 sanitization is the mitigation.

## 8a. Review notes (2026-08-24)

Verdict: **strategy approved; sequencing revised before execution.**

- **Keep:** one-engine-three-motions frame; §4.2 methodology/reference triage;
  kit-factory thesis with falsifiable third-vertical exit criterion; enterprise
  language discipline; decisive non-goals list.
- **Re-cut:** Phase 0 (defer TS SDK publish; expand A3 to data model + capture,
  not just export) and Phase 2 (split money-path from habit-path).
- **Decisions required, with due dates:**
  1. Audit-trail data model — **Phase 0** (redefines the A3 deliverable; design
     as a shared event-capture layer with telemetry — one capture at the
     loop/tool boundary, two sinks: audit store + aggregation sidecar)
  2. Email-mining privacy posture per persona tier — **before Phase 1** (blocks
     the Phase 1 email P0 adapter)
  3. Telemetry aggregation approach — **before the Phase 2 metering schema
     freezes** (reuses the shared capture layer from decision 1)
- Verified against the codebase during review: §4.5 composition claims and
  estimate, single-writer/shard-by-user consistency, trust-tier table matches
  AGENTS.md deployment reality.
- **Incorporated (dsh/Cordis design intelligence, 2026-08-24):** SandboxBackend
  seam (§6.3), event-sourced session log without checkpoints (§6.4), selective
  pluggability (§6.5) — requirements R-SB1/R-SL1/R-PL1; actionable items in
  `docs/superpowers/plans/2026-08-24-dsh-learnings-todos.md`.

Inline `> **REVIEW:**` annotations are placed at the relevant sections above.

## 9. Non-goals & known gaps (v1)

- **Native vision / image ingestion** (business-card extraction for Motion B)
  — non-goal for v1; route via sidecar OCR/vision service or partner
  integration feeding structured text into the existing pipeline
- **Native platform vision** — succeeding at retrieval benchmarks vs
  Mem0/Zep/Letta is also judged a lower priority than the trust/audit
  differentiator; CoreMem+HybridDB compete on versioning/audit/self-host, not
  on memory-retrieval leaderboard scores
- **Image generation** (raster brochure/billboard art) — non-goal; ship
  HTML/SVG-native design output, integrate an image-gen API as a custom tool
  only when a customer demands it
- **No per-user RBAC beyond owner/staff/admin** until Phase 3
- **No RAG pipeline construction** — retrieval is HybridDB FTS5 + Chroma
  (existing), never a bespoke RAG service

## 10. Open risks

- Knowledge-ingestion quality: auto-generated skills requiring heavy editing
  collapses time-to-value (mitigate via review queue + eval sets per kit)
- Draft-delivery UX depends on external email/platform APIs (least controllable
  dependency)
- Vertical sprawl: factory lowers marginal cost but not to zero; scoring gate
  required before adopting any new vertical (workflow fit, willingness to pay,
  structure, ≥70% primitive reuse, reachable channel, design partner)
- Startup-partner mortality: price for low-usage survival; self-serve support
- **Unit economics unvalidated**: token cost as % of subscription unknown per
  motion; pricing hypothesis per tier must be tested in Phase 2 pilot before
  seed raise
- **Approval fatigue (H1)**: binary approve/reject flows train users to
  rubber-stamp (93% approval rate in the field); tiered risk-tier profiles are
  the mitigation — ship H1 with the Phase-1 kits, measure override rates
- **Telemetry centralization vs isolation (gap found in review)**: D1 dashboard
  ("cost per seat", hours saved across staff) and Phase 3 RBAC admin views need
  centralized usage events, but usage lives in per-user SQLite/Chroma by design.
  Requires an opt-out sidecar aggregation store — plan before the Phase 2
  metering schema freezes. Design with the A3 audit capture as **one shared
  event-capture layer** (two sinks), not two pipelines.
- **Capacity**: phases assume solo-plus-AI-agents pace; if behind schedule,
  cut order is: extra verticals → draft-delivery polish → RBAC depth
  (auth/quota/sandbox/ingestion gates are non-negotiable, never cut)
