# Loop Engineering — Deep Research

**Status**: Research only — synthesis for future reference, no implementation
**Date**: 2026-08-21
**Context**: Deep research on "loop engineering" — the design of agent loops (act → observe → decide → repeat with a stop condition). Anchored to this project's existing loop-engineering plan (`docs/superpowers/plans/2026-07-27-loop-engineering.md`: Loop 1 AgentLoop / Loop 2 RubricMiddleware / Loop 3 TriggerRegistry / Loop 4 AnalysisJob). Companion research: `docs/VERIFIER_FUSION_RESEARCH_2026-08-21.md` (LLM-as-a-Verifier + OpenRouter fusion — verification & deliberation).

---

## The field in one paragraph

Loop engineering is the **fourth layer** of the agent-engineering stack: prompt engineering (what to say) → context engineering (what's in the window) → harness engineering (tools, actions, "done" for one run) → **loop engineering** (making it run itself, over and over, to a stop condition). The term crystallized in June 2026 — independently surfaced by Peter Steinberger and Boris Cherny, named and structured by Google's Addy Osmani — and was formalized by Anthropic's Claude Code team (Delba de Oliveira, Michael Segner) as "agents repeating cycles of work until a stop condition is met." Karpathy's framing: "the delicate art and science of filling the context window with just the right information for the next step." The field's core thesis: **the hardest part is not building the loop, but putting something inside it that can say "no."**

---

## Source 1: Anthropic — "Loop engineering: Getting started with loops"

- **URL**: https://claude.com/blog/getting-started-with-loops (June 30, 2026, by Delba de Oliveira & Michael Segner)

### The loop taxonomy (trigger × stop × primitive)

| Loop type | Triggered by | Stop criteria | Best for | Usage management |
|-----------|-------------|---------------|----------|------------------|
| **Turn-based** | user prompt | Claude judges done or needs context | short one-off tasks | specific prompts; verification skills to cut turns |
| **Goal-based (`/goal`)** | manual prompt | **goal achieved OR max turns** | tasks with verifiable exit criteria | explicit completion criteria + turn caps ("stop after 5 tries") |
| **Time-based (`/loop`, `/schedule`)** | time interval | cancel, or work completes | recurring work, external systems (PR reviews, CI) | longer intervals; event-driven instead of time |
| **Proactive** | event/schedule, no human | each task exits at its goal; routine runs until turned off | recurring streams (bug reports, triage, migrations) | route routine to small models; best model for judgment calls |

### Key practices

- **Deterministic stop criteria beat judgment**: "/goal get the homepage Lighthouse score to 90 or above, stop after 5 tries" — an **evaluator model checks the condition** after each turn, not the agent itself.
- **Encode verification into SKILL.md**: turn manual review steps into a skill ("never report a UI change complete based on a successful edit alone — start the dev server, interact, screenshot, check console"). The more **quantitative** the checks, the easier self-verification.
- **A second agent reviews** (fresh context, less biased — `/code-review` skill). "Loops that write code need loops that check it."
- **Token management = loop boundaries**: right primitive + model per job; cheap model for routine, capable model for judgment calls.
- **Loop-level learning**: when a result doesn't meet the standard, encode the lesson into the system for all future iterations.

---

## Source 2: The Loop Engineering Playbook (working note / conference-style synthesis)

- **URL**: https://asixiv.org/pdf/curated/2606.00001 (June 2026; framework credited to Addy Osmani, generator/evaluator findings to Anthropic's Prithvi Rajasekaran, enterprise case to Stripe's Steve Kaliski; built on HuaShu's "Loop Engineering: Stop Asking Me What It Is")

### The four-layer stack

Prompt → Context → Harness → Loop. Each layer minds something larger; loop engineering "automates the waiting for you." Three verbs separate harness from loop: **runs on a timer, spawns helpers, feeds itself** (its own output becomes next round's input — memory across conversations is why it's a loop).

**Blast-radius intuition**: the same bug at each layer — prompt: caught on the spot; context: wrong answer, human notices; harness: diff is visible, human reviews; loop: written into the state file, read back as established fact, built upon across turns. *"The cost of a mistake scales with the number of turns it survives before someone catches it, and a loop is, by construction, a machine for maximizing the number of turns."* Everything else exists to shorten that distance.

### The five moves of one turn

1. **Discovery** — find this turn's work (a skill reads CI/issues/commits; "letting the agent find its own work"). Sets the ceiling on the whole loop's quality.
2. **Handoff** — move the task to the agent that does it (isolated git worktree per task = parallelism without collisions).
3. **Verification** — the move that can say "no." *"A loop without a real check is just an agent nodding at itself."*
4. **Persistence** — land results where they survive the conversation (PR, board, state file). *"The agent forgets, the repo does not."*
5. **Scheduling** — what makes one turn a loop.

Realized by **six parts**: Automations (scheduling), Worktrees (handoff), Skills (discovery, pays off "intent debt"), Connectors/MCP (radius of vision), Sub-agents (verification), Memory (persistence).

### Generator vs Evaluator (the core doctrine)

- **"It always praises itself"**: an agent grading its own work sees its chain of self-persuasion, not the result. Inside a loop this is amplified — each round it nods at itself and drifts from quality.
- **Tune a skeptic, don't fix a modest author**: making the generator self-critical works poorly; a standalone evaluator with different instructions, carrying none of the self-persuasion, is far more tractable. GAN-ported: one network builds, one picks faults.
- **The evaluator should act, not just read**: hook it to Playwright MCP — click, screenshot, inspect DOM. Judge "does it run right," not "does it look right." Swap the underlying model too (same model with new instructions keeps its blind spots). Default stance: **assume broken until proven otherwise**.
- **Fresh model judges the stop condition**: after each turn, a *small fast model* checks whether `/goal`'s condition holds. Completion decided by a fresh model, not the one doing the work. This is the **maker–checker principle** (decades old in banking) applied to stop conditions.
- "The generator's level decides what a loop can produce; the evaluator's level decides what it will not produce."

### Five ways a loop goes wrong (each = one move skipped)

| Anti-pattern | Skipped move | Symptom | Fix |
|--------------|-------------|---------|-----|
| **Nodding loop** | verification | never said "no" across hundreds of turns | generator/evaluator split |
| **Amnesiac loop** | persistence | no cumulative progress; redoes work | state file on disk |
| **Manual loop** | scheduling | last run was the day it was demoed | a real trigger |
| **Blind loop** | discovery | human still picks the work each morning | teach discovery into a skill |
| **Tangled loop** | handoff | parallel agents collide in one directory | one worktree per task |

They cluster: the hasty loop installs only discovery + handoff (the two that produce visible output) and skips the three that produce safety.

### Case studies

- **One engineer's morning** (Osmani): automation → triage skill (reads CI/issues/commits) → per-finding worktree → fixer + reviewer sub-agents → connector opens PR/ticket → inbox for the uncertain → state file for tomorrow. "No step needs a hand, yet it stops to wait for a human exactly where it should."
- **Stripe's Minions** (Kaliski): 1,300+ PRs/week, zero hand-written lines. Trigger = Slack @bot / emoji. The reliability comes from the stretch *before* the model wakes: a **deterministic orchestrator assembles context** (links, Jira, Sourcegraph + MCP). Hard-coded gates interleave with LLM steps (linter runs and the agent *cannot skip it*; commit is hard-coded). "Anything deterministic logic can solve never goes to a probabilistic model." Not built on a stronger model — a fork of Goose; *reliability comes from the quality of the constraints, not the size of the model.* Sandbox: Devbox on EC2, "cattle not pets." The 1,300 PRs are still human-reviewed — the human moved from writing to reviewing.

### Four silent costs (they reinforce each other)

1. **Verification debt** — unverified output accumulating in the gap between "runs" and "right"
2. **Comprehension rot** — the codebase grows while the map in the builder's head stalls
3. **Cognitive surrender** — the more reliable the loop, the easier to outsource judgment
4. **Token blowout** — the only cost that hits the bill directly; guards = per-run budget, daily budget, max retries, set *before* shipping ("circuit breakers that convert an open-ended risk into a bounded one. A loop without caps is a loop that has delegated its spending authority to its own bugs")

### Operational discipline

- **Read a sample, always** — explain each sampled change; inability to explain = the map has fallen behind
- **Cap before you ship**
- **Keep one door open** — at least one human checkpoint; "the day it is removed is the day comprehension rot begins in earnest"
- **First-loop checklist**: discovery source · state file · evaluator · isolation · token cap · human review — "a first loop is better small, but with the 'no'-saying check and the human review point fully installed"
- **Grow safely**: parallelism last, after the checks are proven. "A loop earns the right to run more agents by first demonstrating it can stop a single bad one."

---

## Source 3: Scaling Test-Time Compute for Agentic Coding

- **URL**: https://arxiv.org/abs/2604.16529 (April 2026, 70 pages, Joongwon Kim et al.)

### Core claim

Test-time scaling for long-horizon agents is "fundamentally a problem of **representation, selection, and reuse**" — not just generating more attempts. Each rollout is converted into a **structured summary preserving hypotheses, progress, and failure modes** while discarding low-signal trace detail. Two complementary scalings:

- **Parallel — Recursive Tournament Voting (RTV)**: recursively narrow a population of rollout summaries through small-group comparisons (related in spirit to the verifier paper's PPT).
- **Sequential — Parallel-Distill-Refine (PDR)**: condition new rollouts on summaries distilled from prior attempts (rollout summaries become the loop's memory).

Results: Claude-4.5-Opus 70.9%→77.6% on SWE-Bench Verified; 46.9%→59.1% on Terminal-Bench v2.0. Relevance: our compaction summaries + `RunOutcome` records are exactly this "rollout summary" representation — the paper validates that summaries-as-memory + tournament selection is a sound loop design.

---

## Source 4: LLM-as-a-Verifier (cross-reference)

Already documented in `docs/VERIFIER_FUSION_RESEARCH_2026-08-21.md`. Key loop-engineering connection: verification itself can be *scaled* (granularity × repetition × criteria decomposition); continuous verifier scores serve as progress signals (VOC) — the loop's "check that can say no" can be a graded, calibrated scalar rather than a binary verdict. The evaluator doctrine (Source 2) and the verifier framework (Source 4) are the same idea at different resolutions: **separate generation from judgment, and make the judgment fine-grained**.

---

## Source 5: Reflexion — Language Agents with Verbal Reinforcement Learning

- **URL**: https://arxiv.org/abs/2303.11366 (Shinn et al., 2023 — the classic)

Agents verbally reflect on task feedback, then **maintain reflective text in an episodic memory buffer** to improve subsequent trials — reinforcement without weight updates. Flexible over feedback types (scalar or free-form) and sources (external or internally simulated). This is the archetype of the "feeds itself" loop: the reflection buffer is loop memory; the reflection step is a mini evaluator. Relevant to Loop 4 (AnalysisJob) — reflexion-style verbal feedback is the cheapest form of hill-climbing.

---

## Source 6: ACON — Optimizing Context Compression for Long-horizon LLM Agents

- **URL**: https://arxiv.org/abs/2510.00615 (Microsoft, Oct 2025) · code: https://github.com/microsoft/acon

Unbounded context growth in long-horizon agentic tasks causes two bottlenecks: inference memory cost and **reasoning degradation from irrelevant information**. ACON optimizes context compression for agent loops — minimizing redundant memory growth while preserving decision-critical information. Directly relevant to our compaction work (`SummarizationMiddleware`): the research validates that context policy is a first-class loop-engineering concern (the "context layer" of the four-layer stack, and what Karpathy's definition points at).

---

## Source 7: Self-Reflection in LLM Agents — Effects on Problem-Solving Performance

- **URL**: https://arxiv.org/abs/2405.06682 (2024)

Empirical study of **eight types of self-reflecting agents** — instructing agents to reflect on mistakes and re-answer. Counterpoint to the generator/evaluator doctrine: self-reflection helps on some tasks but is not a reliable substitute for independent verification; the playbook's structural fix (separate evaluator) exists precisely because self-reflection is the weaker form.

---

## Source 8: Awesome Agent Failures (community failure taxonomy)

- **URL**: https://github.com/vectara/awesome-agent-failures

Community-curated list of AI-agent failure modes, real-world case studies, and mitigations: legal/financial incidents, customer-service disasters, institutional failures, safety/misinformation, **autonomous-agent failures**, security incidents. Useful as a failure-mode checklist when designing loop stop conditions and human checkpoints (the "blast radius" intuition of Source 2, grounded in production incidents).

---

## Source 9: Claude Code agent-loop internals (reference)

- **URL**: https://code.claude.com/docs/en/agent-sdk/agent-loop

The Agent SDK exposes the autonomous agent loop programmatically (tools, permissions, cost limits, output control) — the harness layer beneath the loop layer. Reference for how a production loop is embedded: `stop` conditions, `max_turns`, permission gates, cost limits as the circuit breakers.

---

## Source 10: Tosea.ai — "What Is Loop Engineering?" (2026 complete guide)

- **URL**: https://tosea.ai/blog/loop-engineering-ai-agents-complete-guide-2026

History + FAQ: term crystallized June 2026 (Steinberger → Osmani named it); loops = "act, observe, decide, repeat" with a defined goal and stopping condition; loop engineering ≠ needed for one-off tasks ("an interactive session with a capable agent is often faster") — it pays off for repetitive, long-running, unattended work **with a checkable success condition**. ReAct vs Reflexion distinction: ReAct interleaves reasoning/acting within a turn; Reflexion adds the cross-turn reflection buffer.

---

## Synthesis — what all loop engineering converges on

1. **The stop condition is the product.** Every source independently arrives at the same place: the hard part is the "check that can say no." Deterministic, quantitative conditions beat model judgment; a *fresh, small model* should judge the condition; the evaluator should act (run tests, click buttons), not read.
2. **Generator/evaluator must be structurally separate** — maker–checker. Self-reflection is the weak version. Tune a skeptic, not a modest author.
3. **Deterministic over probabilistic wherever possible** (Stripe): anything rule-bound leaves the model; reliability comes from constraints, not model size.
4. **Memory must persist outside the context window** — state files, summaries (Reflexion buffers, RTV/PDR summaries, ACON compression, our compaction). The loop feeds itself; what it feeds on must survive.
5. **Budget caps are circuit breakers, not accounting** — set before shipping; convert unbounded risk to bounded risk.
6. **A human checkpoint is a permanent feature** — "keep one door open." The loop can execute; it cannot decide.
7. **Failure taxonomies are the curriculum**: nodding / amnesiac / manual / blind / tangled loops; four silent costs (verification debt → comprehension rot → cognitive surrender → token blowout — one failure wearing four faces).

---

## Mapping to this project (what's already there vs. what the research adds)

| Loop | Existing (2026-07-27 plan) | Research additions |
|------|---------------------------|--------------------|
| **Loop 1 — AgentLoop (ReAct)** | `_run_react_loop`: LLM → tools → repeat, `max_iterations`, CostTracker, duplicate-call guard | Doom-loop guard = anti-*nodding-loop* machinery. Context policy (compaction) is validated by ACON. Karpathy's "fill the window right" = our incremental summarization. Consider: `_check_subagent_before_llm` style progress checks as a **VOC-style early-warning** (verifier paper) |
| **Loop 2 — RubricMiddleware (verification)** | Generator/evaluator split already ✓ (grader LLM, rubric, retry) | Upgrade path: (a) criteria decomposition + logit-expectation continuous scores (verifier paper); (b) **evaluator acts, not reads** — give the grader browser/tool access like Playwright-MCP (playbook); (c) fresh-small-model stop check matches our "grader on a cheap flash model"; (d) deterministic conditions where possible ("tests pass" > "looks right") |
| **Loop 3 — TriggerRegistry (events)** | External events → agent runs | Maps to time-based/proactive loops (Anthropic taxonomy). Stripe lesson: a **deterministic orchestrator assembles context before the LLM wakes** — triggers should normalize inputs deterministically, not hand raw events to the model |
| **Loop 4 — AnalysisJob (hill-climbing)** | RunOutcome records → improvement suggestions | Reflexion is the archetype: RunOutcome + summaries = episodic buffer; suggestions = verbal reinforcement. RTV/PDR (Source 3) validates summaries-as-memory + selection tournaments |
| **Cross-cutting** | CostTracker, steering (open door ✓), compaction, subagent handoff (work_queue) | Token caps as *circuit breakers* (pre-set budgets); human checkpoint design (steering = the open door — keep it); **verification debt** tracking (unverified-output counter); **read-a-sample** discipline for the native app's review UI |

**Gaps the research highlights for a later pass**: (1) evaluator-by-acting (grader with tool access); (2) deterministic pre-processing in the event loop; (3) per-run budget caps surfaced to users; (4) VOC-style progress signals feeding steering/doom-loop detection; (5) "encode the lesson" loop-level learning (AnalysisJob's natural end-state).

---

## Sources

- https://claude.com/blog/getting-started-with-loops — Anthropic loop taxonomy (Jun 2026)
- https://asixiv.org/pdf/curated/2606.00001 — Loop Engineering Playbook (five moves, six parts, generator/evaluator, five failures, four costs)
- https://arxiv.org/abs/2604.16529 — Scaling Test-Time Compute for Agentic Coding (RTV/PDR)
- https://arxiv.org/abs/2607.05391 — LLM-as-a-Verifier (see VERIFIER_FUSION_RESEARCH_2026-08-21.md)
- https://arxiv.org/abs/2303.11366 — Reflexion
- https://arxiv.org/abs/2510.00615 — ACON (context compression for long-horizon agents) · https://github.com/microsoft/acon
- https://arxiv.org/abs/2405.06682 — Self-Reflection in LLM Agents
- https://github.com/vectara/awesome-agent-failures — agent failure taxonomy
- https://code.claude.com/docs/en/agent-sdk/agent-loop — Claude Code agent loop (SDK internals)
- https://tosea.ai/blog/loop-engineering-ai-agents-complete-guide-2026 — loop engineering guide/FAQ
- Project anchor: https://github.com/earendil-works/pi — docs/superpowers/plans/2026-07-27-loop-engineering.md (repo-local)
