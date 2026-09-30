# Harness Telemetry & Selective Verification — Design Spec

> **Date:** 2026-08-21
> **Status:** Draft — E (telemetry + baseline) to be implemented immediately; C11 mechanism proposed for approval; A1/A4 are separate user-led workstreams (mechanism notes included).

**Goal:** (E) instrument a per-request latency waterfall over the harness stages — context assembly → provider (TTFT + total) → tool execution → verification → persistence — and establish a baseline; (C11) make verification *selective*: skip the grader for turns where it adds least, via a deterministic decision made after the run (no extra LLM calls).

**Scope:** backend `src/sdk` + `src/http` only. No frontend/native-app changes. No new dependencies (uses the existing `Logger.timer()` JSONL infra and `app_logging`).

---

## Part E — Harness latency waterfall + baseline

### E.1 Current timing infrastructure (what exists)

- `src/app_logging.py: Logger.timer()` — context manager logging `{event}.start` / `{event}.end` with `duration_ms` to `data/logs/YYYY-MM-DD.jsonl`
- `ws.py: ws.pre_loop_timing` — manual timing of `get_store / add_msg / get_msgs / convert` (HTTP-entry only, pre-loop)
- Langfuse tracing (per-observation, not aggregated into a single per-run waterfall)

**Gap**: no single per-run waterfall covering the full harness; no baseline numbers.

### E.2 The five stages (exact instrumentation points)

| Stage | Where it happens today | Instrument at | TTFT available? |
|-------|----------------------|---------------|-----------------|
| **context_assembly** | `get_messages_with_summary` (store read) + `_get_system_prompt` (skills/memory context) + `_prepare_agent_call` (loop) | `AgentLoop._prepare_agent_call` wrap; store read timed in runner/run_service | — |
| **provider_ttft** | `provider.chat()` / `chat_stream()` | `AgentLoop` LLM-call sites (non-stream + stream): time to first yielded chunk (stream) / completion (non-stream) | ✓ stream |
| **provider_total** | same call | same site: total call duration; tokens from `response.usage` | — |
| **tool_exec** | `_execute_tool_batch` / `_execute_single_tool` / streaming variants | per-tool `timer()` around each execution; aggregate sum + count per run | — |
| **verification** | `RubricMiddleware` grader call inside `_verification_engine` | `RubricMiddleware` grade call wrap (also captures grader usage) | — |
| **persistence** | `persist_run` in `run_service.py` | `_run_bounded_orchestration` / `_run_stream` around `persist_run` | — |

### E.3 Data shape

One structured log line per run, event `harness.waterfall`:

```json
{
  "timestamp": "...", "user_id": "...", "channel": "sdk", "level": "info",
  "event": "harness.waterfall",
  "data": {
    "run_id": "...", "session_id": "...", "model": "ollama-cloud:deepseek-v4-flash:0731",
    "stream": true, "attempt": 1, "verification": "satisfied|off|skipped",
    "stages_ms": {
      "context_assembly": 12, "provider_ttft": 810, "provider_total": 4100,
      "tool_exec": 1430, "tool_exec_count": 3, "verification": 890, "persistence": 45
    },
    "total_ms": 6120,
    "tokens": {"input": 8120, "output": 340, "reasoning": 120},
    "messages": 14, "tools": ["time_get", "files_read"]
  }
}
```

Emitted via `Logger.timer("harness.waterfall", ...)`-style **single end-line** (not start/end pairs) at the end of each run from the run-service layer, where all stage values are known. `ws.pre_loop_timing` stays (pre-loop granularity); the waterfall supersedes it for post-loop analysis.

### E.4 Implementation sketch

- New `src/sdk/harness_timings.py` — a `HarnessTimings` dataclass: `record(stage, ms)`, `to_log_data()`; thread-safe not required (single run per loop instance).
- `AgentLoop` gains a `timings: HarnessTimings` attribute; instrumentation:
  - `_prepare_agent_call` → `context_assembly`
  - provider call sites (both `run` and `run_stream` paths) → `provider_ttft` (first chunk) + `provider_total`
  - tool execution (all four execute helpers) → per-tool `tool_exec` accumulation
- `RubricMiddleware.grade()` wrap → `verification`
- `run_service` `_run_bounded_orchestration` / `_run_stream` → `persistence`, then emit `harness.waterfall` (always emit — even failed runs, with status).

### E.5 Baseline methodology

- Script `scripts/baseline_harness.py` (mirrors the 140-round stress approach, smaller): starts the server with isolated data dirs, runs **N=40 turns** (mixed: plain Q&A + tool-using + a few verification-enabled), with the configured cloud model, then reads the JSONL waterfall lines and prints per-stage **p50 / p95 / p99 / max** + tokens.
- Baseline numbers recorded in `docs/BASELINE_HARNESS_2026-08-21.md` (status, date, model, hardware, table of per-stage p50/p95).
- Acceptance: every run emits exactly one `harness.waterfall` line; no exceptions from instrumentation (wrapped in try/except that degrades to no-op).

---

## Part C11 — Selective verification (the mechanism)

### C11.1 The problem

Today `verification` is binary: `off` (default) or `on` (grader runs on every turn). The grader is the most expensive post-run move (an extra LLM call + potential retries). Many turns don't need it: short Q&A with no tools and no code. Running the grader unconditionally adds latency + cost to exactly the turns where the quality gate adds least.

### C11.2 The mechanism — a deterministic post-run decision (zero extra LLM calls)

The insight: **you don't need to know the answer to know whether the turn was risky.** The decision uses *run signals already in hand*:

```
run ends
  → collect run signals (deterministic, from state + run result)
  → should_verify(signals, policy)?
      ├─ NO  → skip grader; mark run verification=skipped; log the reason
      └─ YES → existing verification loop (grader, retries)
```

**The signals** (all already available after the run, no new computation):

| Signal | Source | Meaning |
|--------|--------|---------|
| `tool_names` | executed tools (loop state) | risk proxy; code/file tools → risky |
| `destructive_tool_used` | ToolAnnotations | always verify |
| `response_chars` | final response length | trivial turns are short |
| `history_tokens` | usage input tokens | long multi-step runs → verify |
| `has_code` | response contains fenced code blocks | code → verify (cheap regex, no LLM) |
| `risk_keywords` | prompt/response scan (`password`, `api key`, `secret`, `financial`, `medical`, `delete`, ...) | domain risk |
| `run_status` | completed/failed/cancelled | failed → verify (diagnosis) |

**The decision function** (default policy):

```
should_verify = True IF any of:
  - verification mode == "on" (explicit request — ALWAYS wins)
  - run_status != completed
  - destructive tool used OR any tool in {files_write, files_edit, files_delete,
    files_rename, shell_execute, browser_eval, browser_click, email_send, ...}
  - has_code
  - risk_keywords hit
  - history_tokens >= 4000
  - response_chars >= 800

should_verify = False ONLY IF (mode == "auto" AND none of the above AND
  tool_names is empty AND response_chars < 200)
```

I.e. **auto skips only the cheapest, safest turns**: no tools, short answer, no code, no risk keywords, short history. Everything else verifies exactly as today. The failure mode of a wrong skip (a wrong short answer to a simple question) is cheap — the user re-asks; the failure mode of a wrong verify is only latency.

### C11.3 Where it hooks (two lines of change)

- **`src/sdk/runner.py: _verification_engine`** — after the run, before `load_rubric_middleware`/first grade: if mode is `auto` and `should_verify(...)` is False → yield a `skipped` marker and return without creating the middleware. This is the single choke point for both stream and non-stream paths (both funnel through `_verification_engine`).
- **`src/config/settings.py: VerificationConfig`** — new fields: `mode: "off" | "on" | "auto"` (default `off`, preserving current behavior), `skip_max_response_chars: int = 200`, `verify_min_history_tokens: int = 4000`, `verify_min_response_chars: int = 800`.
- **Request level**: `VerificationRequest` gains `mode` (per-request override); absent → settings default.

### C11.4 Safety properties

1. Explicit `on` always verifies — auto never overrides an explicit request
2. Auto never skips: destructive tools, code output, risk keywords, failed runs, long runs
3. Every skip is logged (`verification.skipped` with the reason) — auditable
4. Skipped runs report `verification: "skipped"` in the run result and the waterfall line (E) — the two specs compose

### C11.5 Expected effect

From the E baseline: a typical trivial turn (no tools, ~100-token response) currently pays the full grader call (~0.5–1s on a flash model + potential retry). On a workload with ~50% trivial turns, p50 latency drops by roughly the grader duration on those turns; token cost per run drops by the grader's input/output tokens on skipped turns.

---

## Part A1 / A4 — mechanism notes (user-led workstreams)

### A1 — Prompt-cache audit (checklist)

Goal: verify the message ordering keeps a **stable cacheable prefix** across turns for providers with prompt caching (ollama-cloud deepseek supports caching; OpenAI/Anthropic automatic).

Checklist:
1. System prompt is first and byte-stable across turns (skills catalog, user prompt, memory guidance — confirm no per-turn variance beyond intended)
2. History loads oldest→newest; summaries sit mid-prefix — compaction **replaces** a middle message (cache-busting?) vs. appends — measure with provider cache metrics if exposed
3. Mid-turn injections that land in the prefix region: steer messages (injected after tools), FR-4 content holds, duplicate-guard nudges (system messages mid-history) — each busts the cache for all subsequent calls
4. Recommendation candidates: group all system/nudge messages after the user turn (postfix), keep the prefix = system + summary + history

### A4 — Selective context loading (mechanism sketch)

Replace the fixed `limit=50` history load with a task-adaptive load:
- Signal: prompt length + presence of tool-requesting phrases + session recency
- Policy: short/simple prompts → smaller window (e.g. 10) + summary; long/code prompts → full window
- ACON's lesson: irrelevant history degrades reasoning, not just cost — the window should be *chosen*, not defaulted

---

## Files

**Create:**
- `src/sdk/harness_timings.py` — `HarnessTimings` accumulator
- `scripts/baseline_harness.py` — baseline runner (starts isolated server, runs N turns, aggregates waterfall JSONL)
- `tests/sdk/test_harness_timings.py` — unit tests (accumulator, no-op degradation)
- `tests/sdk/test_selective_verification.py` — decision-function unit tests (all skip/verify cases)
- `docs/BASELINE_HARNESS_2026-08-21.md` — baseline numbers (populated by the script run)

**Modify:**
- `src/sdk/loop.py` — `timings` attr; instrument `_prepare_agent_call`, provider call sites, tool execution
- `src/sdk/middleware_rubric.py` — time the grade call; expose a `skipped` path
- `src/sdk/runner.py` — `_verification_engine`: C11 decision + skip marker; emit waterfall data hooks
- `src/sdk/run_service.py` — persist timing, emit `harness.waterfall`
- `src/config/settings.py` — `VerificationConfig.mode` + thresholds
- `src/http/models.py` — `VerificationRequest.mode`

## Acceptance criteria

- [ ] Every run emits exactly one `harness.waterfall` JSONL line with all five stages; instrumentation failure degrades to no-op
- [ ] Baseline: 40-turn run produces per-stage p50/p95 table recorded in `docs/BASELINE_HARNESS_2026-08-21.md`
- [ ] `verification.mode=auto` skips only no-tool, short, code-free, keyword-free, short-history turns; explicit `on` unaffected; every skip logged with reason
- [ ] All existing tests pass (default mode `off` preserves current behavior)
