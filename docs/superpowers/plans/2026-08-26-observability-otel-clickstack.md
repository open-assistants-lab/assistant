# Observability: OTel + ClickStack complementing Langfuse (2026-08-26)

**Status:** planned (not started)
**Owner:** platform engineering
**Spec refs:** §6.1 distribution posture (deployment-first), §6.4 session log / audit,
§6.7 Open SWE research note (H8 deterministic backstops, version baselines)
**Decision record:** Hud/ClickHouse study (2026-08-26) — the "runtime code sensor"
value without eBPF, achieved with semantic spans + tail sampling + version baselines.

---

## 1. The complement boundary (non-overlap rule)

**Langfuse owns agent semantics. ClickStack owns physical execution. If Langfuse
already shows it, do not duplicate it in OTel.**

| Layer | Owner | Examples |
|---|---|---|
| **Agent semantics** | **Langfuse** (existing) | LLM generations (prompt, completion, tokens, cost, model), agent turn traces, tool calls as semantic events (name/args/result/duration), rubric scores, evals, sessions, user feedback |
| **Physical execution** | **ClickStack** (new) | HTTP/WS request lifecycle, DB queries + pool waits, sandbox subprocess spawn, outbound HTTP (Gmail/Graph/registry), scheduler cycles, event-loop lag, GC/thread-pool/process metrics |
| **Version correlation** | **ClickStack** | `service.version` = git SHA; per-version latency/error deltas |

Join key: **trace_id / span_id**. A slow tool call in Langfuse links to the slow
`sandbox.exec` or `db.query` in ClickStack — same trace, two views. Semantic layer
says *what the agent tried*; physical layer says *why it was slow or broken*.

**Explicit OTel non-goals (avoid duplication + PII):** no LLM generations (Langfuse
has them with cost), no tool-call arguments or results (Langfuse has them; PII),
no prompts, no completions, no message content of any kind.

## 2. Span inventory (~8 spans, not 15)

| Span | Key attributes | Why ClickStack owns it |
|---|---|---|
| `http.request` (WS/SSE/REST) | route, status, duration, user_id (hashed), session_id | Server lifecycle Langfuse doesn't see |
| `db.query` | db (sqlite/hybriddb/chroma), operation class, duration, rows | Pool waits + lock contention are invisible in Langfuse |
| `sandbox.exec` | backend (soft/hard), command *class* (python/shell), duration, exit code, cpu/mem peak | Unique to us; never the command string |
| `http.client` | host (gmail.googleapis.com, graph.microsoft.com, models.dev), status, duration | External dependency health |
| `scheduler.cycle` | cycle type, duration, outcome | Background work not tied to a user turn |
| `background.job` | job type (subagent, sync, ingestion), duration, status | Long-running work visibility |
| `middleware.hook` | hook name, duration, outcome | Only where cost matters (not per-hook noise) |
| `process.metrics` (metrics, not spans) | event-loop lag, thread pool saturation, RSS, GC pauses | Runtime health; explains "everything got slow" |

Auto-instrumentation for free coverage: `opentelemetry-instrumentation-fastapi`,
`-sqlalchemy`, `-httpx`. Manual spans only for `sandbox.exec`, `scheduler.cycle`,
`background.job`, and the middleware hook that matters.

## 3. Collector config sketch (tail sampling = Hud's escalation behavior)

```yaml
# otel-collector.yaml (sketch)
receivers:
  otlp: { protocols: { grpc: {}, http: {} } }
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: keep-errors      # escalate: full detail on anomaly
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: keep-slow        # escalate: full detail on anomaly
        type: latency
        latency: { threshold_ms: 2000 }
      - name: sample-rest      # steady state: cheap aggregate
        type: probabilistic
        probabilistic: { sampling_percentage: 1 }
  attributes/scrub:
    actions:
      - key: user_id          # hash before export
        action: hash
      - key: tool.arguments   # never exported
        action: delete
exporters:
  clickhouse: { endpoint: tcp://clickhouse:9000, database: otel }
```

Cheap in steady state, deep forensics on anomaly — the Hud pattern, without eBPF.

## 4. Version baselines — the actual Hud feature (highest value, lowest cost)

1. Bake `service.version` = git SHA (short) into the Docker image at build time;
   set `deployment.commit`, `deployment.channel` (stable/preview) as resource attrs.
2. Baseline queries per signal, comparing version N against N-1:
   - tool error rate by version
   - p95 duration of `sandbox.exec` / `db.query` / `http.client` by version
   - scheduler cycle failure rate by version
3. HyperDX alerts fire on **deltas**, not static thresholds:
   *"error rate on v1.4.2 is 2.3× v1.4.1"*.

This is the sentence Hud sells and it needs no profiler, no eBPF, no sensor.

## 5. Alerts → agent (the feedback loop)

HyperDX alert on a version regression → webhook → **existing TriggerRegistry
(`webhook` trigger type)** → agent investigates, correlates with Langfuse, files an
issue or opens a draft. Pairs with H8 (deterministic backstops) and H6
(failure→constraint): production behavior feeds back into the constraint library.

## 6. Privacy & tenancy rules (non-negotiable)

**Rule 1 — self-hosted deployments phone home to nobody by default.**
- Langfuse today: `enabled=False`, empty keys in `docker/.env.example`, tracer is a
  no-op when disabled. ✅ correct, keep it.
- **Footgun to fix:** `LangfuseConfig.host` defaults to `https://cloud.langfuse.com`.
  An operator who enables Langfuse without setting a host ships their data to a
  third party. **Fix: when `enabled=True` and no host is explicitly configured,
  fail startup with a clear error (or require host explicitly).**
- ClickStack inherits the same rule: OTel exporter endpoint must be explicitly
  configured; no vendor default endpoint, ever.

**Rule 2 — content never enters spans.** IDs, timings, counts, error types, command
*classes*. No email bodies, no file contents, no tool arguments, no prompts. The
per-user HybridDB audit store remains the only place content lives.

**Rule 3 — operator observability ≠ customer audit trail.** Langfuse/ClickStack is
*our* engineering visibility (and the operator's, if they self-host the stack). The
versioned audit trail is the *customer's* trust artifact. Different audiences,
retention, and privacy rules; never conflate them in docs or code paths.

**Rule 4 — two independent observability channels (2026-08-26, corrected model).**

There are **two channels**, not one endpoint choice. Both may be active simultaneously
(fan-out), and neither implies the other.

| | **Vendor channel** (ours) | **Admin channel** (theirs) |
|---|---|---|
| Purpose | Support/debugging on our side, product improvement | The deployer tracing *their own* deployment |
| Endpoints | **Baked into the app** — never in `.env` | Set in `.env` (`LANGFUSE_BASE_URL`, `OTEL_EXPORTER_OTLP_*`) |
| Gated by | **Consent** (explicit flag) | Nothing — their stack, their choice |
| Default | Off (no consent → nothing sent) | Off (nothing configured → nothing sent) |
| Reader | Us | The admin |

**Consequence:** consent given → the app sends to our Langfuse/ClickStack with **zero
configuration required** from the admin. The admin's `.env` is a separate optional
channel for their own tracing. The OTel collector fans out: one pipeline, two
exporters (admin OTLP if configured + vendor OTLP if consented). Langfuse accepts
OTLP, so both destinations ride the same spans — no double instrumentation.

**Consent tiers (T1/T2, agreed 2026-08-26):**

| Tier | What flows | Destination | Consent prompt |
|---|---|---|---|
| **T1 — usage stats** | Aggregate metrics: counts, timings, error classes, versions, tool names | Our ClickStack | "Send anonymous usage statistics" |
| **T2 — full observability** | Traces incl. prompts, tool calls, session structure | Our Langfuse + ClickStack | "Send full traces so we can support and debug your deployment" — explicit, informed, DPA-covered |

T1 is the easy yes for everyone; T2 is what makes support possible (you cannot debug
a customer's failing agent run from counters). Both off by default, both fail-closed.

**Default depends on who holds the customer's data:**

| Topology | Data holder | Default | Rationale |
|---|---|---|---|
| Self-hosted (their Docker) | Them | **OFF** — consent prompt opt-in | They chose self-hosting *because* data doesn't leave; default-on contradicts the purchase reason |
| Hosted by us (Motion B hosted / Phase 3 multi-tenant) | **Us** | **ON** (disclosed, toggleable) | We already hold the data under the service agreement; aggregate usage stats are normal |
| Partner-embedded (Motion C) | Partner's client | **OFF** — partner decides | Vendor must not create a direct data relationship with a partner's customers |

**Legal rationale (decisive):** in a self-hosted deployment the customer is the
*controller*; default-on egress makes the vendor a *processor without a DPA* — a
GDPR gap requiring DPAs with every self-hosted customer. Opt-in with explicit
consent is dramatically simpler. Hosted deployments are already covered by the
service agreement.

**Vendor channel implementation rules (2026-08-26):**

1. **Vendor endpoints are constants in the image**, not env vars — rotating an
   endpoint must not require customer config edits, and it must not be possible
   to point the vendor channel elsewhere by accident.
2. **Consent flags gate the exporter:** `CONSENT_USAGE_STATS` (T1),
   `CONSENT_OBSERVABILITY` (T2).
3. **Per-instance tokens, never a shared static key.** A single ingestion key
   across all customers has no per-customer revocation, no attribution, and a
   fleet-wide blast radius if leaked. Mint a per-instance token at consent time
   (revocable, attributable), sent as Bearer to a dedicated ingest host. The
   shared ClickStack ingestion key is for our own dogfooding only.
4. **Instance identity:** anonymous instance UUID + version on every batch, so a
   support ticket can locate the customer's traces ("instance abc123, 14:32Z").
5. **Fail-closed both directions:** vendor endpoint without consent → refuse to
   start. Consent given but endpoint unreachable (firewalled corporate network) →
   do not crash; degrade to the local ring buffer and surface status in UI/CLI:
   *"Support data: enabled, last delivery failed — outbound HTTPS may be
   blocked."* Legal/enterprise customers will block it and must see why.
6. **Ingest path — VERIFIED WORKING 2026-09-11 (gateway deferred).** Decision:
   **direct ingestion for now** (OB-9 gateway deferred by owner). The public route
   is Caddy **Basic auth**, credentials `otel:<password>` — *not* the ingestion key
   and not Bearer. Source of truth: the ziiCloud sync daemon's systemd unit
   (`sync_ziicloud_gongchaaus/ziicloud-pos-sync.service`).

   | Setting | Value |
   |---|---|
   | Endpoint | `https://clickstack.gongchatea.com.au` (+ `/v1/{metrics,traces,logs}`) |
   | Protocol | `http/protobuf` (JSON also accepted) |
   | Headers | `Authorization: Basic <base64(otel:...)>` |
   | Service name | `OTEL_SERVICE_NAME` (e.g. `assistant`) |

   Verified end-to-end: `POST /v1/metrics` → `200 {"partialSuccess":{}}` → metric
   (`pi.test.gauge = 42.5`, service `pi-otel-test`) queryable via the ClickStack MCP.
   Collector ports 4317/4318 are closed externally — the Caddy route is the only path.

   **Consequence while deferred:** the shared Basic credential is in every sender's
   env, so per-instance revocation/attribution/tier-enforcement do not exist yet.
   Acceptable for our own dogfooding; **must not ship to customers** until OB-9
   (per-instance tokens) lands. Track as a known gap, not a design.

**Design (consent, not configuration):**
1. Consent prompt at onboarding — first run in native app/CLI:
   *"Help improve Assistant — send anonymous usage statistics? You can see
exactly what we send."* [Yes / No / Show me the data]
2. Anonymous instance ID — random UUID stored locally, resettable
   (`assistant telemetry reset`); never derived from user_id/hostname/license.
3. **Metrics, not spans** — product telemetry must NOT ride the OTel span
   pipeline. Separate aggregate-metrics payload:
   `{instance_id, version, platform, deployment, providers_used,
tool_counts, error_classes, sessions, latency_buckets}` — no content, no
   IDs, no paths, no email data, no prompts. Ever.
4. **Publish the schema** — exact payload in `docs/telemetry.md` + repo.
   "Read the JSON we send" is a trust feature.
5. **Local preview** — `assistant telemetry preview` prints what would be sent;
   `assistant telemetry disable` turns it off.
6. **Retention** — 90 days, aggregate only; no per-instance drill-down beyond
   diagnostics (see §6a).

**D1 coupling:** this is the same class of decision as D1 (email-mining privacy
posture per persona tier). Decide them together or the posture becomes
inconsistent and hard to explain.

## 6a. Debugging spans — local by default, exported by choice

Product telemetry is metrics-only, but debugging needs spans. Three modes, none
ambient:

| Mode | What | Egress | When |
|---|---|---|---|
| **1. Local ring buffer** (default, always on) | OTel spans to a bounded local file (last 1h / last 500 spans), rotated, in the data dir | **None — never leaves** | Always (black-box recorder) |
| **2. Export-on-demand** | `assistant telemetry export --last 1h` → bundle the user inspects before attaching to a ticket | User action, per file | Customer reports a bug |
| **3. Live support session** | `assistant telemetry diagnose --ttl 24h` streams spans for a scoped window | Opt-in, TTL-bounded, revocable, visible | Hard cases, customer asks us to look |

**Why the ring buffer is the key move:** it turns "enable diagnostics,
reproduce, hope" into "export the last hour and attach it" — the failure is
already recorded, no ambient egress, and the customer reviews the file before
sending. Strictly better than remote debugging *and* better for privacy.

**Content rule:** modes 1–3 carry metadata spans only (timings, tool names,
error classes, stack frames, IDs — same scrub as OB-2). If a bug genuinely needs
payload content, that is **mode 2 only**: the export bundle may optionally
include content, generated locally, inspected and attached by the user. Content
never streams automatically, even in a live session. Stack-trace scrubber:
exception *messages* can carry PII ("email to john@firm.com not found") — scrub
emails/paths/tokens, keep exception type + frame locations.

## 7. Profiling decision record (2026-08-26)

**Decision: no eBPF profiler now. No profiler at all until spans + version
baselines prove insufficient.**

| Option | Verdict | Why |
|---|---|---|
| **Parca agent** | ❌ not now | eBPF = Linux-only, needs a **privileged container** (contradicts the "no agent runs as root" discipline; it can read all host process memory). ~1 day infra. **Python fidelity is poor** — native stacks surface `_PyEval_EvalFrameDefault`, not clean Python frames |
| **Pyroscope Python SDK** | 🟡 if/when needed | In-process sampling, **real Python function names + line numbers**, works on macOS dev *and* Linux prod, no privileges, ~2–4h to add. Lives in Grafana Alloy (which also has an eBPF component if a mixed-language fleet ever justifies it) |
| **OTel Profiles signal** | 🔭 watch | The eventual convergence — profiles ride the same collector, no separate UI. Still experimental |
| **py-spy** | ✅ ad-hoc only | Not always-on; perfect for "what is it doing right now" during an incident |

Trigger to revisit: a production question that spans cannot answer ("which function
inside `summarize` is hot?"). Order when that happens: **Pyroscope SDK → Parca (only
for mixed-language Linux fleets) → OTel Profiles (when stable)**.

Cost note: neither Parca nor Pyroscope ingests via OTel today — each brings its own
pipeline, storage, retention policy, and UI. That's the real price, not the install.

## 8. Phased tasks

| Task | Scope | Est. |
|---|---|---|
| **OB-1** OTel SDK + auto-instrumentation + collector + ClickStack wiring | deps, config, collector, tail sampling, exporter; verify Langfuse OTLP fan-out (or keep Langfuse SDK path as-is) | 1–2d |
| **OB-2** Semantic spans (`sandbox.exec`, `scheduler.cycle`, `background.job`, middleware hook) + PII scrub policy in code | manual spans + attribute policy + tests asserting no content leaks | 2–3d |
| **OB-3** Version attributes + baseline queries + HyperDX delta alerts | git SHA in image build, 3 baseline queries, 1 alert | 1d |
| **OB-4** Alert → webhook → TriggerRegistry → agent investigation | wire the loop end-to-end | 0.5d |
| **OB-5** Privacy hardening | fail-closed host requirement (Langfuse + OTel), DEPLOYMENT.md privacy section, test that disabled = zero export | 0.5d |
| **OB-6** Product telemetry (topology 3) — metrics only, OFF by default, consent prompt, published schema (`docs/telemetry.md`), `telemetry preview/reset/disable` commands, anonymous instance ID | per §6 Rule 4 | 1–1.5d |
| **OB-7** Debugging spans — local ring buffer (bounded, rotated, never egresses), `telemetry export` bundle (user-inspected, optional content), `telemetry diagnose --ttl` live session, stack-trace PII scrubber | per §6a | 1.5–2d |
| **OB-9** Vendor ingest gateway (per-instance tokens at consent time) — see §10 | per §10 | 1–2d |
| **OB-8** (deferred) Profiling | per §7, only on trigger | — |

Total for OB-1..OB-7: **~1.5–2 weeks** — the 90% of Hud that is worth having,
plus a debugging story better than most vendors'.

## 9. Open questions

1. Does the self-hosted ClickStack run **inside** each customer deployment (per-tenant,
   local-only) or only on the operator's own fleet? Per Rule 1 the former is the
   distribution shape; the latter is for us.
2. Langfuse OTLP ingestion vs its native SDK path — one collector fanning out to both,
   or keep the existing `LangfuseTracer` SDK path? Verify against the deployed Langfuse
   version before OB-1.
3. Retention: OTel spans (ClickHouse) vs Langfuse traces (its own schema) — align
   retention windows so the trace_id join doesn't break for one layer first.

## 10. Vendor ingest gateway — per-instance tokens at consent time (OB-9) — **DEFERRED 2026-09-11**

> **Owner decision (2026-09-11): defer the gateway; ingest directly to ClickStack
> and Langfuse for now.** Direct ingestion is verified working (see §6 Rule 4.6).
> The gateway design below stays as the target state for customer-facing consent —
> it is required before T1/T2 ships to anyone but us, because the shared Basic
> credential cannot be revoked, attributed, or tier-enforced.

**Principle: never ship a shared ingestion key.** The customer gets a token minted
for *their instance*; the ClickStack/Langfuse credentials stay server-side. The
gateway is also the fix for the Caddy basic-auth blocker (§6 Rule 4.6): the only
public ingest surface becomes one Bearer-authenticated route.

### Topology

```
app (customer)                vendor                         backends
  │                             │                               │
  │ POST /v1/register ────────► mint token (tier T1|T2)         │
  │ ◄──── {token, expires_at}   │                               │
  │                             │                               │
  │ POST /v1/traces ──────────► validate + tier-enforce         │
  │  Authorization: Bearer <t>  stamp instance.id/consent.tier ─┼─► ClickStack collector
  │                             │                               └─► Langfuse OTLP
```

One endpoint, one token, server-side fan-out with server-side credentials.

### Endpoints (FastAPI — same stack we already run)

| Route | Purpose |
|---|---|
| `POST /v1/register` | Mint token: `{instance_id, version, platform, tier, consent_at}` → `{token, expires_at}` |
| `POST /v1/refresh` | Rotate (old token valid for a short grace window) |
| `DELETE /v1/register` | Revoke (consent off) |
| `POST /v1/{traces,logs,metrics}` | Validate → tier-check → stamp → forward |
| `GET /healthz` | Liveness |

### Rules

1. **Token = opaque random (32B urlsafe)**, stored **sha256-hashed** in the gateway
   DB (`instance_id, token_hash, tier, created_at, last_seen, revoked_at`). A DB leak
   must not yield usable tokens. Signed JWTs only if lookup ever becomes a bottleneck
   (it won't for a long time).
2. **Tier is enforced server-side.** A T1 token posting to `/v1/traces` → **403**.
   Client-side gating is a suggestion; consent is only real if the server enforces it.
3. **Token lives in app state, never `.env`**: `data/private/observability.json`
   (0600) — `{instance_id, token, tier, consent_at, expires_at}`. `.env` stays the
   admin channel's config, preserving the two-channel separation.
4. **Stamping**: gateway adds `instance.id`, `consent.tier`, `client.version` as
   resource attributes so the vendor's ClickStack can attribute/filter per instance.
5. **Registration abuse control**: rate-limit per IP; one active token per
   `instance_id` (re-register rotates); require a valid first batch within N minutes
   or discard the token; optional activation code for enterprise/partner deploys.
6. **Per-token rate limit** on ingest (one runaway client cannot flood the vendor).
7. **Client behavior**: consent toggle → register/revoke; `401` on ingest →
   re-register once, then disable the vendor channel and surface status; registration
   failure → exponential backoff, status visible, local ring buffer unaffected.
8. **Revocation works both ways**: customer revokes (consent off) *and* vendor can
   revoke a token without customer action (support/abuse).

### Rejected alternatives

| Option | Why not |
|---|---|
| Shared ingestion key in the image | No per-customer revocation or attribution; fleet-wide blast radius if leaked |
| Per-customer ClickHouse users directly | Leaks vendor topology; couples customer tokens to ClickStack config |
| Client → ClickStack **and** Langfuse directly | Two credentials client-side; Langfuse keys would have to ship |

### Acceptance

- T1 token → `/v1/traces` returns 403; T2 token → accepted and visible in Langfuse
- Revoked token → 401; consent off → local token deleted + server-side revocation
- Traces land in ClickStack carrying `instance.id` + `consent.tier`
- No ClickStack or Langfuse credential exists anywhere in the shipped image
