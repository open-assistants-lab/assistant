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
stays concrete | Seams formalized at SDK extraction — **Phase 2/3** |

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
interview questions rather than silently ignored.

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

### 6.1 OSS packaging posture

Extract the SDK into a self-contained PyPI package (Phase 9 work). Imports
from `src.app_logging` / `src.config` / `src.storage.paths` / `src.skills`
must be replaced by package-local config, logging, and paths conventions
(e.g. `~/.assistant/`). Adopters bring their own auth: the package ships an
`IdentityResolver` seam plus reference implementations (shared-secret,
per-user keys), never dictating identity. Auth ships **present-but-off** —
operator chooses the trust boundary.

| Install | Includes |
|---|---|
| `assistant` (core) | AgentLoop, providers, registry, files, web, time, skills, sessions, HITL, memory-lite (SQLite/FTS5) |
| `assistant[memory-vector]` | ChromaDB (heavy native deps — optional) |
| `assistant[email]` / `assistant[browser]` / `assistant[mcp]` / `assistant[subagents]` | Heavy integrations as extras |
| `assistant[server]` | FastAPI + SSE/WS (hosted-API edition) |

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
wiring into files_read/versions), which is also our OSS artifact.

**Adopt dsh's sandbox seam (R-SB1):** define a `SandboxBackend` interface
(Service Definition / Provider / Consumer split) and route **every
process-spawning tool** through it — `shell_execute`, `code_execute`, CLI
adapters, future terminal — so no tool can forget to sandbox. Backends are
implementations behind the seam, swappable per deployment and per agent
(`isolate` realm concept):

| Backend | What it is | Threat model | When |
|---|---|---|---|
| **Soft** (subprocess + cwd/env/timeout caps) | Guardrails, not isolation | Trusted user, untrusted code | Phase 2 |
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

### 6.5 Selective pluggability (R-PL1)

Adopt the *philosophy* selectively, not the full plugin-everything refactor.
Strategic seams at package boundaries; the agent loop stays concrete.

| Seam | Status | Formalize at |
|---|---|---|
| LLM provider | ✅ exists | SDK extraction |
| Tool registry | ✅ exists | SDK extraction |
| Skills | ✅ exists | SDK extraction |
| MCP bridge | ✅ exists | SDK extraction |
| Sandbox backend | 🔴 new (R-SB1) | Phase 2 |
| Session log | 🔴 new (R-SL1) | Phase 0/1 (schema P0-T9; derivation P1-T10..T12) |
| Session persistence | 🟡 partial | SDK extraction |

Decision: interfaces defined now (cheap), implementations later; full
plugin-everything deferred until a partner demands it or a Python-native
composability framework (e.g. Ouroboros) matures.

## 7. Phased roadmap

### Phase 0 — Trust foundation (weeks 1–3)
- **Auth seam + shared-secret reference impl**: `IdentityResolver` protocol
  (§6.1). Never trust `user_id` past the resolver boundary. Production
  per-user key→identity deferred to Phase 2 (hosted tier) — OSS default is
  trusted-network with shared-secret
- Audit-log export endpoint per user
- `/v1` route aliasing
- Publish TS SDK to npm as **`0.1.0-preview`** (explicit non-frozen contract);
  live-server integration smoke tests; stable release after a partner has
  exercised both transports (SSE + WS) for ~a month
- **Main-agent-from-profile bootstrap (K1, §4.5)**: `load_main_agent_profile()`
  composition layer — a user-level PROFILE.md instantiates the *main* loop.
  Foundational runtime for kits, Motion-C partners, and the pip package's
  `create_agent(profile)` entry point
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
- **SDK extraction begins** (Phase 9 pulled forward for Motion C): decouple
  `src/sdk` from `src.app_logging`/`src.config`/`src.storage`/`src.skills`;
  package-local paths (~/.assistant/), IdentityResolver seam, extras split per
  §6.1
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
- Partner program formalized: Vertical Starter Kit repo, kit versioning &
  distribution (seed-hash refresh pattern generalized)
- **SDK extraction completes**: PyPI `assistant` core + `assistant[server]`
  hosted-edition packaging; `pip install` → running agent demo
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
- **Telemetry centralization vs isolation (gap found in review)**: D1 dashboard
  ("cost per seat", hours saved across staff) and Phase 3 RBAC admin views need
  centralized usage events, but usage lives in per-user SQLite/Chroma by design.
  Requires an opt-out sidecar aggregation store — plan before the Phase 2
  metering schema freezes. Design with the A3 audit capture as **one shared
  event-capture layer** (two sinks), not two pipelines.
- **Capacity**: phases assume solo-plus-AI-agents pace; if behind schedule,
  cut order is: extra verticals → draft-delivery polish → RBAC depth
  (auth/quota/sandbox/ingestion gates are non-negotiable, never cut)
