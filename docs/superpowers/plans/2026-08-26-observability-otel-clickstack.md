# Observability: OTel + ClickStack complementing Langfuse (2026-08-26)

**Status:** planned — implementation not started; **verified groundwork complete 2026-09-11**
(SDK presence, shared-provider trace-ID sharing, live ClickStack ingest, VM retention)
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
| `http.request` (WS/SSE/REST) | route, status, duration, **session-scoped opaque ID** (rotating; never `user_id` — see Rule 2) | Server lifecycle Langfuse doesn't see |
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

### 2a. Logs — decision (2026-09-11)

**App logs stay local** (`data/logs/*.jsonl` + the per-user audit store); they are
**not** shipped to `otel_logs`. Rationale: our JSONL logs can carry content (tool
results, error bodies), and Rule 2 forbids content in the observability layer —
shipping them would need a scrubbing layer we don't have. The physical layer's job
is spans + metrics.

**Revisit if** we need operational (non-content) log correlation in HyperDX: then add
`opentelemetry-instrumentation-logging` with an explicit **allowlist** of log events
and fields, never a blanket logging bridge.

## 3. Sampling, ingest topology, and provider ownership

### Ingest topology (real — replaces the earlier collector sketch)

```
app / SDK ──HTTPS──► Caddy  /v1/{traces,metrics,logs}  (basic auth)
                       └─► reverse_proxy otel-collector:4318
                             (Authorization rewritten to the OpAMP bearer token)
                                  └─► ClickHouse
```

Collector ports 4317/4318 are intentionally **not** published; the Caddy route is the
only path. Full details in §11.

**Correction (self-review 2026-09-11):** an earlier draft of this section sketched a
`tail_sampling` processor plus a `clickhouse` exporter in `otel-collector.yaml`.
**That is not implementable** — the ClickStack collector is **OpAMP-managed by
HyperDX** (config pushed to `/etc/otel/supervisor-data/effective.yaml`); edits are
overwritten on the next sync. The sketch was fiction.

### Sampling — deferred

| Mechanism | Where | Tradeoff |
|---|---|---|
| Client-side sampler (`ParentBased` + `TraceIdRatio`) | Our SDK | Cheap; decides at span **start**, so it **cannot** "keep errors" |
| Self-managed collector in front | A container we own | Full tail sampling; second moving part + another hop |
| No sampling | — | Simplest; correct at current volume |

**Decision: no sampling now.** The entire OTel dataset is ~128 MiB against 46 GB free —
sampling would optimise a non-problem and add a component. **Trigger to revisit:**
sustained ingest > ~5 GiB / 90 days, or a vendor-channel volume/cost problem. Then:
client-side first (cheapest), self-managed collector only if error-retention is
genuinely required.

### 3a. Provider ownership — OB-1's core requirement (verified 2026-09-11)

Three verified facts make this the delicate part of the integration:

1. `trace.set_tracer_provider()` is **write-once** — first caller wins.
2. Langfuse v4 **attaches to an existing provider** rather than replacing it:
   `_init_tracer_provider` checks `isinstance(get_tracer_provider(), ProxyTracerProvider)`;
   if a real provider exists it only adds its processor.
3. There are **two** existing `Langfuse(...)` sites — `src/app_logging.py:83` (Logger
   init) and `src/sdk/langfuse_tracer.py:53` — and **neither passes `tracer_provider`**.

So whichever initialises first determines the global provider. **Required: one
`src/sdk/observability.py` with a get-or-create helper:**

```python
def configure_observability() -> TracerProvider:
    current = trace.get_tracer_provider()
    if isinstance(current, ProxyTracerProvider):      # nobody set one yet
        provider = TracerProvider(resource=_resource())   # service.name, service.version=git SHA
        trace.set_tracer_provider(provider)
    else:                                              # Langfuse (or another lib) won
        provider = current
    provider.add_span_processor(_clickstack_processor())   # filtered, see below
    provider.add_span_processor(_ring_buffer_processor())  # §6a — always local
    return provider
```

Called **once at startup, before any Langfuse client is constructed** —
ordering-independent by construction.

**Caveats:**
- `sample_rate` / `id_generator` passed to `Langfuse()` are **ignored** when a global
  provider exists (Langfuse logs a warning) — configure sampling on the provider.
- `LangfuseResourceManager` is a singleton per `public_key`, so the two clients share
  one resource manager → **one** Langfuse processor, no duplicate export.
- The ClickStack processor must **drop Langfuse-scoped spans** (see the consequence
  note below).

### Exporter approach — DECIDED 2026-09-11: use the OTel SDK for everything.
The SDK is **already installed** as a transitive dependency of `langfuse` 4.14.1
(`opentelemetry-sdk` 1.39.1, `-api`, `-exporter-otlp-proto-http`, `-proto`,
`-semantic-conventions`), so the dependency-weight objection is void — the only new
packages are the instrumentation shims (`-instrumentation-fastapi`, `-sqlalchemy`,
`-httpx`, `-asyncio`). Decisive reason: **Langfuse v4 is OTel-native and accepts
`tracer_provider` / `span_exporter`** — one shared `TracerProvider` with two
span processors (Langfuse + ClickStack OTLP) means **the same trace IDs in both
systems**, which is the join key the two-layer design depends on. Hand-rolling
spans would mean hand-rolling context propagation — the part that actually breaks.

The SDK also gives us for free: auto-instrumentation covering most of the OB-2 span
inventory, batch export with retry/backoff, flush-on-shutdown, and correct resource
semantic conventions.

**VERIFIED 2026-09-11 — shared provider works, with one consequence to handle.**
A smoke test configured one `TracerProvider` with a ClickStack OTLP exporter and
passed it to `Langfuse(tracer_provider=...)`. Result:

- OTel span `verify-shared-provider` and Langfuse generation `verify-langfuse-gen`
  both landed in ClickStack with the **same `TraceId`** (`e5178c20…cf6f6`) — the join
  key works exactly as designed.
- **Consequence:** because Langfuse uses our provider, its semantic spans are exported
  to ClickStack too. That duplicates prompt/completion content into the physical layer
  and breaks the §1 boundary (and would leak T2 content into ClickStack).
- **Required at implementation:** filter the ClickStack exporter to drop
  Langfuse-scoped spans (match on instrumentation scope, e.g. `langfuse`), keeping
  the shared trace context so correlation survives. Langfuse's `should_export_span`
  / `mask_otel_spans` control the Langfuse side; a filtering `SpanProcessor`
  controls the ClickStack side. Same trace ID, different span sets per layer.

**Pin note:** langfuse requires `opentelemetry-sdk>=1.33.1,<2`; declare our OTel
deps **explicitly** (1.39.x) so they survive if langfuse ever drops them.

Also borrowed from their setup: `argMax(Value, TimeUnix)` for latest-gauge reads,
`service.version`-style resource attributes (`deployment.environment`,
`host.name`), and a documented metrics reference (`docs/CLICKSTACK_METRICS.md`)
with naming convention + alert thresholds — a good template for `assistant.*`
metrics.

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

**Verified 2026-09-11:** the entry point exists — `POST /webhooks/{trigger_id}` in
`src/http/routers/webhooks.py` fires `AgentEvent(trigger_type="webhook")`.

**VM-side prerequisite (self-review 2026-09-11):** HyperDX's alert currently POSTs to
the Telegram bridge (`172.105.168.22:8081`). Adding our TriggerRegistry as a
destination is a **second webhook configured on the ClickStack VM**, not purely local
code — budget it in OB-4.

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

**Rule 2a — no `user_id` in spans (2026-09-11).** In a single-user self-hosted
deployment a stable hash *is* that user, so hashing buys nothing. Use a
**session-scoped opaque ID** that rotates and cannot be correlated across sessions.
`user_id` is never a span attribute.

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
| **T1 — usage stats** | Aggregate metrics: counts, timings, error classes, versions, tool names, **tokens + cost by provider and model** | Our ClickStack | "Send anonymous usage statistics" |
| **T2 — full observability** | Traces incl. prompts, tool calls, session structure | Our Langfuse + ClickStack | **"Let us look it up for you"** — with T2 on a bug report is a *timestamp*, not an export: no attachments, no reproduction, no waiting. Explicit, informed, DPA-covered |

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
4. **Instance identity — discoverable by the admin, stamped in both layers.**
   A **short, human-copyable ID** (8 chars base32, e.g. `k7m2p9qx` — *not* a
   36-char UUID), stable across upgrades, resettable via `assistant telemetry reset`.

   **Surfaced in four places** (an admin must never have to hunt for it):
   `assistant telemetry status`; Settings → General → About in the native app;
   **every user-facing error line** — `Error — instance k7m2p9qx · ref e5178c20`,
   so a screenshot alone carries both identifiers; and the support bundle /
   `telemetry preview`.

   **Stamped in both observability layers:**
   - **OTel Resource** (`instance.id`, `service.version`) on the shared provider →
     present on every span → queryable in ClickStack as
     `ResourceAttributes['instance.id']`.
   - **Langfuse trace attributes** via the existing `propagate_attributes(...)` call
     in `langfuse_tracer.py` (which already sets `user_id`, `session_id`, `tags`):
     add `metadata={"instance_id": ...}`, `tags=[..., f"instance:{id}"]`
     (UI-filterable), and `version=<git sha>` — matching ClickStack's
     `service.version` so both layers filter by release identically.

   **Why both:** resource attributes alone are **not surfaced in the Langfuse UI** —
   trace-level fields are what make "find everything for this instance" a click
   rather than an API call. Without the tag, an instance-ID lookup in Langfuse is
   impractical.

   **Consequence of reset:** regenerating the ID breaks correlation with past
   reports — that is the privacy-correct trade, and `reset` should say so.
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
3. **Metrics via OTLP, not a bespoke payload (2026-09-11)** — T1 rides the OTel
   **metrics** pipeline (SDK meter API), never the span pipeline. Signals:
   `{instance_id, version, platform, deployment, providers_used,
tokens_by_provider_model, cost_by_provider_model, tool_counts, error_classes,
provider_errors, sessions, latency_buckets}` — no content, no IDs, no paths, no
   email data, no prompts. Ever.

   **Provider AND model, always together.** The same model name can be served by
   different providers (`ollama:` vs `openai:` routes differ in cost, latency, and
   failure mode), so every token/cost/error metric carries `gen_ai.provider.name`
   **and** `gen_ai.request.model`. Rollups by provider alone *and* by model alone
   must both be derivable. (Feeds H5 model routing + the D1-1 dashboard.)
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

**Mechanism (OB-7):** no file exporter ships with the installed set (only
`-otlp-proto-{http,grpc}`), so the ring buffer is a **custom `SpanProcessor`**.
Preferred: a small **local SQLite** table (we already run SQLite everywhere) with a
row cap + time cap, queryable by `assistant telemetry export`; JSONL-to-rotating-file
is the fallback if SQLite proves heavy. It is registered on the shared provider
**unconditionally** — local capture is independent of consent and of the exporters.

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
| **OB-1** OTel SDK wiring — explicit deps (`opentelemetry-sdk`, `-exporter-otlp-proto-http`, `-instrumentation-{fastapi,sqlalchemy,httpx}`), `src/sdk/observability.py` get-or-create provider (§3a), ClickStack span processor **filtered to drop Langfuse-scoped spans**, startup ordering before any Langfuse client | per §3, §3a | 1–2d |
| **OB-2** Semantic spans (`sandbox.exec`, `scheduler.cycle`, `background.job`, middleware hook) + PII scrub policy in code | manual spans + attribute policy + tests asserting no content leaks | 2–3d |
| **OB-3** Version attributes + baseline queries + HyperDX delta alerts | git SHA in image build, 3 baseline queries, 1 alert, `assistant.*` dashboard | 1d |
| **OB-4** Alert → webhook → TriggerRegistry → agent investigation | wire the loop end-to-end **+ add the second HyperDX webhook destination on the VM** (§5) | 0.5d |
| **OB-5** Privacy hardening | fail-closed host requirement (Langfuse + OTel), DEPLOYMENT.md privacy section, test that disabled = zero export | 0.5d |
| **OB-6** Product telemetry (topology 3) — OTLP metrics, OFF by default, consent prompt, published schema (`docs/telemetry.md`), `telemetry preview/reset/disable` commands, anonymous instance ID, **tokens/cost by provider+model** | per §6 Rule 4 | 1–1.5d |
| **OB-7** Debugging spans — local SQLite `SpanProcessor` ring buffer (bounded, rotated, never egresses), `telemetry export` bundle (user-inspected, optional content), `telemetry diagnose --ttl` live session, stack-trace PII scrubber | per §6a | 1.5–2d |
| **OB-10** Admin → vendor bug reporting — error reference IDs, `assistant support bundle` (both layers, preview + toggles), documented bundle format, T2 prompt reframing | per §12 | 1d |
| **OB-8** (DEFERRED) Profiling | per §7, only on trigger | — |
| **OB-9** (DEFERRED 2026-09-11) Vendor ingest gateway — per-instance tokens at consent time | per §10 | 1–2d |

**Total for active scope (OB-1..OB-7 + OB-10): 9.5–13.5 days ≈ 2–2.5 weeks** — the 90%
of Hud that is worth having, plus a debugging and bug-reporting story better than most
vendors'. (OB-8/OB-9 are deferred and excluded.)

## 9. Open questions

1. Does the self-hosted ClickStack run **inside** each customer deployment (per-tenant,
   local-only) or only on the operator's own fleet? Per Rule 1 the former is the
   distribution shape; the latter is for us.
2. ~~Langfuse OTLP ingestion vs native SDK path~~ — **RESOLVED 2026-09-11**: shared
   `TracerProvider` via get-or-create; verified matching trace IDs; Langfuse attaches
   to a pre-existing provider. See §3a.
3. Retention alignment: ClickStack side is now **90 days** (2026-09-11, §11).
   Langfuse's own retention is still unverified — check the deployed instance before
   relying on the trace_id join beyond 90 days.
4. Cross-UI linking: correlation is by trace_id, but there is no hyperlink between
   HyperDX and Langfuse. Cheap version = a URL template in each direction; not
   required for OB-1.

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

## 11. Remote ClickStack VM — verified state (2026-09-11)

Deployment source of truth: `~/Library/Mobile Documents/com~apple~CloudDocs/Agents/clickstack_gongchatea_com_au`
(live: `/home/eddy/clickstack` on `172.105.184.203`, user `eddy`).

**Verified in sync** — Caddyfile and docker-compose.yml hashes match repo ↔ VM exactly.
No drift to reconcile.

| Check | Value |
|---|---|
| Containers | 5/5 healthy (app, caddy, ch-server, db, otel-collector), up 5–6 weeks |
| Memory | 2.6 GiB / 3.8 GiB used (1.2 GiB available) — watch if data volume grows |
| Disk | 29 GB / 79 GB used (46 GB free) |
| OTel data | ~128 MiB total (otel_metrics_sum 62 MiB, otel_traces 50 MiB, gauge 7.5 MiB, logs 4 MiB) |
| TTL | **changed 30 → 90 days** on all 8 `otel_*` tables (2026-09-11) |

**Why the TTL change:** version-baseline comparison (the Hud value) needs the
*previous* version's data to still exist when the next one ships. At 30 days and
monthly releases, the baseline expires exactly when it's needed. Cost is negligible
(~400 MiB for 90 days vs 46 GB free). Re-apply if the collector recreates the schema
on an image upgrade.

**Ingest path (Caddyfile):** `/v1/{traces,metrics,logs}` → `basic_auth` (otel:<pass>)
→ `reverse_proxy otel-collector:4318` with `header_up Authorization {$OTEL_BEARER_TOKEN}`.
Caddy strips the client Authorization and injects the collector's bearer token
(pushed by HyperDX via OpAMP). Collector ports 4317/4318 are intentionally **not**
published — the Caddy route is the only path.

**Not needed for our work:** no new source, connection, or collector config —
the collector accepts any `service.name`. Dashboards/alerts for `assistant.*`
metrics come later (OB-3), using the same `argMax(Value, TimeUnix)` pattern.

## 12. Admin → vendor bug reporting (OB-10)

**Purpose:** turn "something went wrong" into an artifact we can act on, without
requiring the admin to understand the observability stack.

### Two paths

| Path | Mechanism | Admin effort | What we get |
|---|---|---|---|
| **T2 consent on** | Admin reports *"instance k7m2p9qx, ~14:32 UTC"* | **None** — no attachment | We query our Langfuse + ClickStack directly: full correlated evidence |
| **T2 consent off** | Admin exports a **support bundle** and sends it | Moderate — review + send | Only what the bundle contains |

**With T2, observability *is* the bug-report channel** — the report is a pointer and
we already hold the evidence. Without T2 the stack is not the reporting mechanism at
all; the bundle is, and the stack only contributes spans to it.

### Verified constraint: no programmatic trace sharing

Langfuse's Python API exposes only `get`, `list`, `delete`, `delete_multiple` on
`trace` — **no share-link method**. So "click to share this trace with support"
cannot be built from inside the app; handoff must be **lookup** or **bundle**.

### The bundle is bigger than spans

Spans alone are often insufficient — Rule 2 excludes exactly what explains
content-triggered bugs. A support bundle contains:

| Component | Source | Notes |
|---|---|---|
| Instance ID, version, git SHA, platform | App | Attribution + regression lookup |
| Timestamp window + session/conversation ID | App | Lets us join later if T2 is enabled |
| Local spans (ring buffer window) | OTel `SpanProcessor` (§6a) | Physical layer |
| Recent errors + stack traces | Local logs | **Redacted** — exception messages carry PII |
| Config (redacted) | Settings | Providers/models, feature flags |
| Semantic trace (**optional**) | **Their** Langfuse, via the app's keys | The app already holds their keys, so it can assemble this *for* them |
| Content (**optional, explicit**) | Conversation store | Second confirmation; never default |

The optional-semantic-trace row is the useful property: because the app has the
admin's Langfuse credentials, **it can assemble a rich bundle from both layers even
when they live on the admin's own infrastructure** — no T2 required, no access grant.

### Error reference IDs — the missing bridge

Every user-facing error carries a short ref derived from the trace ID
(`Error — instance k7m2p9qx · ref e5178c20`). This:
- identifies the exact local trace/span,
- makes `assistant support bundle --ref e5178c20` pull the *right* window instead of
  "last hour, hope",
- gives a support ticket something resolvable to quote even from a screenshot.

Cheapest high-value item in the plan.

### Task scope

1. Error reference IDs on all user-facing errors (short, copyable, trace-derived)
2. `assistant support bundle` (CLI + a "Report a problem" affordance in the native app):
   assembles the table above from both layers, shows a **preview with per-section
   toggles**, writes the bundle; content off by default behind a second confirmation
3. Bundle format documented so the admin can verify exactly what leaves their machine
   — same trust principle as `telemetry preview`
4. T2 consent prompt rewritten to the "let us look it up" framing (§6)
