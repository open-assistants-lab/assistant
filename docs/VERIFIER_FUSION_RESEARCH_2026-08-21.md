# Verification & Deliberation Research — LLM-as-a-Verifier + OpenRouter Fusion

**Status**: Research only — both patterns are candidates for LATER adoption, no implementation
**Date**: 2026-08-21
**Context**: Two complementary approaches for improving agent output quality without training. (1) LLM-as-a-Verifier — a training-free continuous scoring framework for verifying trajectories; (2) OpenRouter fusion — a server-side multi-model deliberation tool. Explored together because they answer different halves of the same question: *is the answer right?* (verification) vs. *did we think about all sides?* (deliberation). Both are "spend extra inference compute at test time for quality" patterns — the test-time scaling axis.

---

## Source 1: LLM-as-a-Verifier — A General-Purpose Verification Framework

- **Paper**: https://arxiv.org/abs/2607.05391 (v2, July 2026)
- **Full text (HTML)**: https://arxiv.org/html/2607.05391v2
- **Code**: https://github.com/llm-as-a-verifier/llm-as-a-verifier · **Website**: https://llm-as-a-verifier.com/
- **Authors**: Jacky Kwok, Shulu Li, Pranav Atreya, Yuejiang Liu, Yixing Jiang, Chelsea Finn, Marco Pavone, Ion Stoica, Azalia Mirhoseini

### Core idea

Verification (determining correctness of a solution) is a **scaling axis** of its own, separate from pre-training / post-training / test-time compute. Instead of an LM judge that collapses scoring to a discrete argmax token, compute a **continuous reward as the expectation over the scoring-token logit distribution**:

```
R(x, τ) = (1 / C·K) · Σ_c Σ_k Σ_g p_θ(v_g | x, c, τ) · φ(v_g)
```

where G = score-token granularity, K = repeated evaluations, C = decomposed criteria. The reward is normalized to [0,1] and converted to pairwise preferences via Bradley–Terry: P(τi ≻ τj) = 1 / (1 + exp(−(R_i − R_j))).

**The key insight**: enlarging the token set grants no new information to the model — but it gives the *decoder* a finer space to project its internal belief, so nearby beliefs that round to the same integer become distinct continuous rewards. Evidence: discrete judge ties 88/100 comparisons on a query-optimize task; the same logits as an expectation ties 0/100 and ranks correctly 69/100 (77/100 at G=20).

### Three scaling axes (each targets a different error source)

| Axis | Mechanism | Gain (Terminal-Bench) |
|------|-----------|----------------------|
| **Granularity G** (1→20 score tokens) | Finer projection of belief; improves signal-to-noise of the correct-vs-incorrect gap (SNR 0.775→0.799) | 73.1% → 77.5% |
| **Repeated evaluation K** (1→16) | Monte-Carlo averaging; variance shrinks O(1/K), bias unchanged | 74.7% → 77.4% |
| **Criteria decomposition C** (1→3: Specification / Output / Errors) | Fixes rubric conflation; ensemble beats any single criterion | 75.2–76.4% → 78.3% |

A single-pass verifier (K=1) matches a heavily-ensembled judge (K=16) — fine-grained probabilistic scoring is a stronger signal than ensembling coarse ones.

### Probabilistic Pivot Tournament (PPT) — budget-efficient best-of-N

Selecting the best of N candidates without O(N²) pairwise scoring:
1. **Ring pass**: score N adjacent pairs of a random Hamiltonian cycle — every candidate appears exactly once in each prompt slot, cancelling positional bias
2. **Pivot selection**: rank by ring-pass mean preference, take top-k as pivots
3. **Pivot rounds**: non-pivot vs pivot + pivot vs pivot; select argmax of win-mass / comparison-count (normalization removes pivot participation bias)

Total cost: O(N·k) instead of O(N²). Improves with more pivots; outperforms prior selection methods (e.g., V1) at lower budget.

### Results (same framework, zero per-domain tuning)

| Benchmark | Pass@1 | Oracle | LLM-as-a-Verifier |
|-----------|--------|--------|-------------------|
| Terminal-Bench V2 (code) | 83.1% | 92.1% | **86.5%** (SOTA) |
| SWE-Bench Verified | 76.1% (heterogeneous pool of 3 models) | 84.4% | **78.2%** |
| RoboRewardBench (robotics, video inputs) | 70.8% (discrete judge) | — | **87.4%** — beats *trained* reward models (RoboReward-8B 81.4%, Robometer-4B 78.8%) |
| MedAgentBench (medical) | 70.2% | 75.0% | **73.3%** |

Verifier model: Gemini 2.5 Flash (G=20, K=8, C=3), Qwen 3.6 35B VLM for robotics. MAE vs human annotations on RoboRewardBench: 1.11 → 0.72.

### VOC — verifier score as a task-progress proxy

Spearman rank correlation between chronological step index and verifier score on trajectory prefixes:
- Code: 0.848 (successful), 0.769 (failed)
- Robotics: 0.966 (vs 0.877 RoboReward-8B, 0.565 TOPReward)

Successful rollouts show near-monotonic rising scores; failed ones stay flat. Dual use: progress measure + early-warning signal. Ships as **TurboAgent** — a drop-in inference-time proxy for Claude Code (OpenAI-API compatible clients) that samples N candidates in parallel and selects via PPT, with a web UI for live monitoring.

### Dense rewards for RL

- **Off-policy (DSRL-SAC on LIBERO)**: progress score as shaped reward → **1.8× sample efficiency**, higher final success (0.76 vs 0.69)
- **On-policy (GRPO on MATH)**: reasoning-trace preference score breaks the zero-advantage collapse when all group responses are wrong → ~1.1× sample efficiency

### My findings / caveats

**Strong:**
1. Training-free, plug-and-play — no reward-model training, same framework across domains
2. The logit-expectation trick is simple, mechanism-driven (SNR analysis), and clearly correct
3. PPT ring-pass positional-bias cancellation is a genuinely useful algorithmic detail
4. Beating trained robotics reward models zero-shot is the strongest claim
5. VOC as progress signal has big practical value (early stopping, monitoring, doom-loop detection)

**Weak / open:**
1. Still an LLM judge — length bias, sycophancy, self-preference toward the generator's style are unaddressed
2. **Requires top-k token logprobs** — Gemini/OpenAI expose them, **Anthropic does not** (Appendix B.6 two-stage workaround is acknowledged as weaker). Provider-dependent.
3. Absolute gains modest: Terminal-Bench 83.1→86.5% recovers ~40% of oracle headroom; SWE-Bench ~30%
4. Cost accounting thin: G=20 × K=8 × C=3 ≈ 480 scoring passes per trajectory + tournament pairs; no end-to-end latency/cost analysis vs. simply using a better generator
5. VOC → early-stopping leap under-evidenced: Spearman measured on *completed* trajectories; no end-to-end "paused before committing broken state" study

---

## Source 2: OpenRouter fusion — server tool

- **Docs**: https://openrouter.ai/docs/guides/features/server-tools/fusion
- **Related**: fusion router alias (`openrouter/fusion`) · fusion plugin · `/labs/fusion` playground
- **Status**: Beta (API/behavior may change)

### What it is

A **server-side tool** (`{"type": "openrouter:fusion"}` in the `tools` array) giving any model access to multi-model deliberation. The outer model decides when to invoke it; OpenRouter runs a two-stage pipeline and returns structured analysis as a tool result:

1. **Panel**: 1–8 models answer the prompt **in parallel**, each with `openrouter:web_search` + `openrouter:web_fetch` enabled (max 4 tool-call steps each)
2. **Analyst**: a separate model (defaults to the outer model, **always temperature 0**) compares panel responses — not merges — producing structured JSON:
   - `consensus` (points all/most agreed on)
   - `contradictions` (topic → per-model stances)
   - `partial_coverage` (point only some models covered)
   - `unique_insights` (something only one model raised)
   - `blind_spots` (topics no model addressed)
3. **Outer model** writes the final answer from the analysis — explicitly *not* a majority vote

### Parameters

| Field | Default | Notes |
|-------|---------|-------|
| `analysis_models` | quality preset (opus-latest, gpt-latest, gemini-pro-latest) | 1–8 panel models |
| `model` | outer model | the analyst |
| `max_tool_calls` | 4 | per panel model / analyst web-tool steps (1–16) |
| `max_completion_tokens` | 16000 | per inner call, incl. reasoning |
| `reasoning` / `temperature` | provider default | analyst always at temperature 0 |

### Degradation semantics (the notable design)

- Partial panel failure → `status: "ok"` + `failed_models` array
- **Analyst failure** → `status: "ok"` with raw panel `responses`, `analysis` omitted — the outer model can still write an answer from the responses
- Hard failure only when *no* useful output: typed `failure_reason` (`all_panels_failed`, `insufficient_credits`, `rate_limited`, `fusion_invocation_capped`, `unexpected_error`)
- **Recursion protection**: inner calls carry `x-openrouter-fusion-depth`; panel/analyst cannot re-invoke fusion; capped at one fusion per turn

### Latency

Works on `/chat/completions` today but slower; docs steer latency-sensitive use to the **Responses API**.

### My findings

**Strong:**
1. **Model-directed cost** — the tool description tells the model to invoke only when "being wrong is expensive"; simple prompts don't trigger it. Pay-per-use deliberation.
2. **Degradation is a first-class design**, not an edge case — partial results always usable, typed failure reasons, fallback path always available
3. Panel + analyst separation with structured analysis (consensus/contradictions/blind-spots) is more useful than a merged answer — the outer model keeps authorship

**Weak / open:**
1. Deliberation ≠ verification: agreement is not correctness — consensus/blind-spots analysis is a poor *correctness* signal (this is exactly why the verifier paper's logit-expectation scoring exists)
2. OpenRouter-specific (server-side, their API, Responses API recommended) — adopting means adding an OpenRouter provider
3. Beta; cost structure of panel + analyst calls not detailed on this page

---

## How they relate

| Dimension | LLM-as-a-Verifier | OpenRouter fusion |
|-----------|-------------------|-------------------|
| Purpose | **Verification** — is it *right*? | **Deliberation** — all *sides*? |
| Parallelism | Candidate trajectories (same generator, N samples) | Panel of models on the same prompt |
| Output | Continuous correctness score + ranking | Structured synthesis (consensus/contradictions/blind-spots) |
| Cost trigger | Fixed verification pass (G×K×C) | Model-directed, pay-per-use |
| Training | None | None |
| Provider constraint | Needs token logprobs (Gemini/OpenAI; not Anthropic) | OpenRouter API (Responses API preferred) |

Potential combination (later stage): fusion-style panel for breadth on open-ended questions, verifier-style continuous scoring for correctness ranking on generated candidates.

---

## Project relevance (for a later adoption decision)

Both are on the **test-time scaling** axis — spend more inference compute at request time for quality, trading latency/cost.

**Verifier → our RubricMiddleware is the direct mapping:**
- Current: grader LLM → discrete verdict → retry loop (`needs_revision`)
- Upgrade path: decompose the rubric into sub-criteria (Specification/Output/Errors analog), average the grader's scoring-token *expectation* (needs logprobs from the grader provider — deepseek-v4-flash via OpenAI-compatible Ollama cloud exposes them; Anthropic would not), K repetitions for variance reduction. Eliminates tie-induced verdict flapping and gives the retry loop a graded signal.
- **VOC** connects to existing machinery: doom-loop detection (3× same tool call) and steering are coarse progress heuristics; a continuous verifier score could replace them.
- **PPT** fits `subagent_delegate` / research best-of-N if we ever sample multiple candidates.
- Cost reality for an interactive chat app: Shape A (upgrade the grader on the single run) is cheap and natural; Shape B (parallel N agent loops per message) is 5× latency — a non-starter as a default, an opt-in for high-stakes tasks only.

**Fusion → architectural lesson:**
- The **server-tool pattern** (execution lives server-side, model chooses when to pay) maps conceptually to our MCP bridge (`mcp__{server}__{tool}`).
- Adoption requires an **OpenRouter provider** (we have OpenAI/Anthropic/Gemini/Ollama today).
- The **degradation semantics** are a checklist for any parallel-models machinery we build ourselves: partial failure → usable partial result, typed failure reasons, recursion cap — not fail-hard.

**Decision to revisit when**: the rubric middleware needs a real upgrade, we add an OpenRouter provider, or we build best-of-N candidate selection for subagents/research.

---

## Sources

- https://arxiv.org/abs/2607.05391 — LLM-as-a-Verifier (abstract)
- https://arxiv.org/html/2607.05391v2 — full text
- https://github.com/llm-as-a-verifier/llm-as-a-verifier · https://llm-as-a-verifier.com/
- https://openrouter.ai/docs/guides/features/server-tools/fusion — fusion server tool
- Related: https://openrouter.ai/docs/guides/routing/routers/fusion-router · https://openrouter.ai/docs/guides/features/plugins/fusion · https://openrouter.ai/fusion/
