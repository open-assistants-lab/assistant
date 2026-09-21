# D2 Independent Re-Review — PASS (2026-09-21)

**Gate:** D2 — Provider, resource, and event-history contracts
(`.superpowers/sdd/enterprise-roadmap/desktop-v0.1-plan.md`).

**Verdict:** **PASS** — every D2 code item is closed with no P0/P1 open; what
remains is decision-only (session-log enablement, failed-compression producer,
`/validate-endpoint` hardening note) plus residual D3-owned carry-overs.

## Review rounds

| Round | Verdict | Outcome |
|---|---|---|
| 1 | BLOCKED | F1–F3 (code) and F4–F7 (evidence) — see the first review's archive in `/tmp/desktop-d2-review-evidence/` |
| 2 | BLOCKED | N1 (code): a compression on a rubric rerun attempt failed the streamed run; plus P2 evidence items N2–N7 |
| 3 | **PASS** | N1 fixed and verified; N2/N3/N6 fixed; N4's behavioural hole fixed; the remaining P2 evidence notes were closed in `3bc210d8` |

## What was fixed

| Item | Fix |
|---|---|
| F1 — `context_compressed` had no producer | `session_events.log_compression` writes the durable record from both loop compression sites; `run_stream` emits one `context_compressed` chunk per compression; `run_service` maps it; WS gained the forwarding branch SSE already had; end-to-end tests |
| F2 — wrong-vendor probing | Candidates come from the longest matching prefix only; `check-likely` probes the client's explicit approved `providers` list, else the single best candidate; generic `sk-` is low-confidence |
| F3 — Gemini keys always invalid | `GeminiProvider.get_client()` added; key material redacted from error bodies and transport exceptions |
| F4–F7 — evidence gaps | Detail/mutation-route fixtures, whole-data-root secret scan, bounded `key_prefix`, dead uncapped `/providers/models` removed, non-tautological unknown-token assertion |
| N1 — rerun attempt failure | `runner._verification_engine` sets `loop._flow_attempt = attempt` before each attempt; regression test; mutation-checked (removing the line fails at the snapshot-attempt assertion) |
| N2 — stale-cache sequence collision | `log_compression` always allocates from the store; regression test with a stale per-loop cache |
| N3 — tautological disabled-subagent fixture | The definition is created first; the 404 detail must say "disabled" |
| N4 — key could reach the log | Redaction computed before both the log line and the response; test asserts the key never appears |
| N6 — approve/resume dropped the payload | `StreamEvent.context` carries it; approve stream forwards it; test added |
| N7 — decision bookkeeping | Decision A recorded in the memo §4.3; the plan notes decision B's deferral |

**Decisions recorded 2026-09-21:**

- **A** — the discovery allowlist governs automatic scanning only;
  `validate-endpoint` accepts any explicit http(s) URL the user typed.
- **B** — the Connections panel is deferred with connector OAuth; no
  Connections destination in v0.1.0.
- `SessionLogConfig` gained its `SESSION_LOG_` env prefix, so the documented
  `SESSION_LOG_ENABLED=true` form works for the D3 enablement decision.

## Exit gate (third review)

| Clause | Status |
|---|---|
| No automatic multi-provider probe | MET |
| No secret in durable output | MET (refusal-before-write, redaction, bounded prefix, log-dir scan) |
| Loopback-only discovery | MET (scan allowlist; manual validation is the decision-A carve-out) |
| Correct desktop filtering | MET (list/detail/mutation/job fixtures) |
| History replay: known / unknown / failed compression | MET (producer + replay fixtures) |

## Residual risks and D3 carry-overs (not blockers)

1. **Session-log enablement** — the durable compression record and the reload
   disclosure only exist with `SESSION_LOG_ENABLED=true`; enabling it also
   switches `_load_history` onto the projection path, which changes what the
   model sees. Decide before D3 consumes the event.
2. **No failed-compression producer** — `status:"failed"` is schema-valid but
   never emitted; threshold failures are silence, overflow failures surface as
   error chunks. Replay exclusion is proven.
3. **`/validate-endpoint` accepts any host** — decision-A aligned; a blind
   `reachable` GET, so an SSRF-*trigger* note for D5 hardening.
4. **No end-to-end HTTP proof of the live event** — loop-level and approve-leg
   coverage only; D3's typed consumer is the first real client.
5. **Zig client**: still GETs the removed `/providers/models` (D3-owned), and
   reads `before.tokens`/`after.tokens` while the backend emits
   `estimated_tokens` (live disclosure degrades to "Context updated"; the
   client's fixture is tautological in the same way N3 was).
6. **Sequence allocation TOCTOU** — `next_sequence` + `append` remains
   read-then-insert for all writers; `_log_session_header` and `_drain_steer`
   still use the cache and can drop a row on collision.
7. **Durable-log attempt stamps** — message/injection/header rows still hardcode
   `attempt: 1` while compression rows carry the true attempt; cosmetic today,
   worth pinning when D3 consumes `attempt`.

## Evidence

- `/tmp/desktop-d2-review-evidence/` — first review scope + logs.
- `/tmp/desktop-d2-rereview-evidence/` — N-fix delta and focused log.
- `/tmp/desktop-d2-rereview2-evidence/` — final delta, focused log, scope note.
- Reviewer archives: async runs `c88c06b7`, `73d81ba1`, `9bebbe92`.
