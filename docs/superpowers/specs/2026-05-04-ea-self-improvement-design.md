# EA Self-Improvement Design

**Date:** 2026-05-04
**Status:** Exploration Complete

---

## 1. Context

### What We Explored

We investigated how EA could continuously improve itself through user interactions — becoming
tailored to each user's needs over time. The exploration covered:

1. **Claude Code hooks** — 28-event external process hook system with matchers, `if` conditions,
   and 5 handler types (command, HTTP, MCP, prompt, agent). Rejected: subprocess overhead,
   wrong product fit (developer CLI vs assistant), redundant with existing middleware.

2. **Middleware extension** — extending the existing `Middleware` ABC with new hook points
   (`before_tool`, `after_tool`, `on_user_prompt`, `on_interrupt`, `on_error`, matcher filtering)
   and user middleware discovery. Accepted as the right extension architecture, with the deep
   code trace finding two pre-existing bugs (`aafter_model` missing in stream path,
   `wrap_tool_call` with no error handling) that adding hooks forces us to fix.

3. **Hermes Agent Self-Evolution (GEPA)** — Nous Research's Genetic-Pareto Prompt Evolution,
   which reads execution traces, proposes targeted mutations, evaluates variants against
   benchmarks, and opens PRs for human review. Insightful for SKILL.md optimization, but GEPA
   is offline batch, not runtime conversational improvement.

4. **Dream mechanisms** — `openclaw-auto-dream` (562 stars, 5-layer memory consolidation),
   `rem-sleep-skill` (grep-based memory extraction), `dream-weaver-protocol` (Dream → Weave →
   Wake). All consolidate memory. None optimize skills or tools.

5. **GBrain analysis** — 8 adopted improvements (confidence thresholds, query expansion, recency
   boost), 10 deferred (entity enrichment, memory ranker, tiered enrichment). Good for memory
   tuning, not for holistic self-improvement.

6. **SOUL.md** — OpenClaw's identity/personality file (worldview, opinions, voice). Useful concept
   but wrong fit. For EA, memory IS the soul — every preference, correction, and fact is stored
   in memory. SOUL.md is an unnecessary cache layer on top of the memory store.

### What We Learned

No existing framework combines all four dimensions of self-improvement (identity, skills, tools,
timing) into a coherent whole. Each project addresses one piece. EA has the opportunity to
be the first to get all four working together.

---

## 2. Theory: How a Human Assistant Improves

A human assistant improves through one simple loop:

```
Do work → notice what happened → adjust → do better next time
```

The human doesn't overhaul their "email skill document." They don't run benchmarks. They don't
wait for 3 corrections before adapting. They just:

```
DURING WORK:
  "She corrected me on emails twice — use Gmail."
  → adjust immediately next time (inject into working memory)

END OF DAY:
  "Email was rough today. Code review was smooth."
  → reflect once, update internal priors

OVER TIME:
  "She asks for meeting prep every Monday. I'll start Sunday."
  → notice patterns, anticipate needs
```

The human assistant also knows **when to speak and when to shut up**. Monday morning is
heads-down time. Friday at 5pm is reflection time. After a correction, you apply it
immediately — you don't propose a policy change. After a pattern of mistakes, you bring it up
at the right moment.

This is the model EA should follow. The agent is assistant to ONE user. Every interaction is
a signal about how to be a better assistant for that specific person.

---

## 3. Framework: The Four Artifacts

Self-improvement operates on four artifacts. Each improves through its own loop, at its own cadence.

### 3.1 Who: Memory

**What it is:** The agent's entire identity. Every fact, preference, correction, and behavioral
pattern the agent has learned about the user. Stored in the existing `memory.py` HybridDB.
No separate files. No SOUL.md.

**How it improves — continuous, silent, zero user attention:**

```
Every turn:
  user states a fact → memory_store.add_memory(type="fact")
  user states a preference → memory_store.add_memory(type="preference")
  user corrects the agent → memory_store.add_memory(type="correction")
  agent performs work → history logged

Every turn (before_agent):
  MemoryMiddleware injects relevant memories into working context
  Recent corrections are injected immediately for fast adaptation

Periodically (dream consolidation):
  Deduplicate near-duplicate memories
  Archive stale entries (not accessed in 30+ days)
  Score confidence: corrections that held for multiple sessions increase
  Forget: low-confidence single-occurrence memories fade
```

**LLM cost:** 0 calls per turn (injection is DB query + prompt assembly). 1 LLM call per
dream consolidation cycle (batch summarization of memory clusters).

**User attention:** Zero. The agent silently gets better at facts, defaults, and preferences.

### 3.2 What: Skills

**What it is:** Task-specific instructions and workflows. SKILL.md files in `src/skills/`.
Defines HOW to do specific tasks (code review, email management, meeting prep).

**How it improves — correction-driven, companion-gated, user-approved:**

```
Session ends:
  corrections in a domain this session?
    → companion checks: should I propose an improvement?
    → if yes: "Email has been rough lately. Want me to formalize
              what we've been correcting into a skill?"
    → if user approves: agent drafts .improvement.md proposal
    → companion: "2/3 tests pass. Review?"
    → user approves → merge into SKILL.md
    → user dismisses → log preference, don't ask again soon
```

**LLM cost:** 1 call per proposal (draft generation). 1 call for `skill_test` evaluation.

**User attention:** Per proposal. User approves, edits, or dismisses. Agent never auto-applies.

**What triggers a proposal is learned, not hardcoded.** The companion tracks:
- How many corrections occurred in this domain
- Whether the user approved or dismissed the last proposal for this domain
- Whether the user is mid-session or done for the day
- Whether the user has ignored companion notifications recently
- Time since last proposal for this domain

A user who always approves email skill proposals gets them at 2 corrections. A user who
dismissed the last email proposal gets a longer cooldown. A user who ignores all companion
notifications gets them throttled back.

### 3.3 How: Tools

**What it is:** The agent's capabilities. SDK-native `@tool` functions and connector tools.
What the agent CAN do.

**How it improves — pattern-driven, companion-gated, user-approved:**

```
Weekly:
  same shell command pattern repeated ≥ N times?
    → companion: "You've run `git diff main...feature | grep -v test` 5 times
                  this week. Want me to create a diff-summary tool?"
    → user says yes → agent generates tool → user approves → deploy

  existing tool got corrected ≥ N times?
    → same companion proposal flow as skills

N is learned:
  user approved last tool suggestion? → N = 3 next time
  user dismissed last tool suggestion? → N = 8 next time
  user ignored companion for a week? → stop suggesting tools
```

**LLM cost:** 1 call per proposal (tool generation).

**User attention:** Per proposal. Same approval flow as skills.

### 3.4 When: Companion

**What it is:** The scheduler and consciousness. Not a file — a running process that knows when
to reflect, when to propose, and when to be silent. The existing `CompanionScheduler` at
`src/sdk/companion_scheduler.py` is the vehicle.

**What it does:**

```
Every session end:
  reflect: "What one thing would have made this session smoother?"
  → store as memory type=reflection

Every session end:
  check: any domain with accumulating corrections?
  → if companion_relevance_score > threshold → propose skill improvement

Weekly:
  scan for repeated command patterns
  → if score > threshold → propose tool creation

Weekly:
  scan reflections for patterns
  → if patterns across sessions → propose SOUL improvement
  → companion notification: "Suggested behavioral updates from recent sessions"

Every proposal cycle:
  track: did user approve, dismiss, or ignore?
  → adjust proposal thresholds for next time
  → if user ignores everything → reduce frequency
  → if user never opens companion on weekends → don't send then
  → store in memory: "user prefers skill proposals, ignores tool suggestions"
```

**How the companion learns:**

The companion stores its own behavioral model as memory entries:
- `companion.preference.proposal_frequency.skills = "high"`
- `companion.preference.proposal_frequency.tools = "off"`
- `companion.preference.quiet_hours.weekend = true`
- `companion.preference.notification_channel = "digest, not per-proposal"`

These are just `memory_type=preference, domain=companion` entries. The companion queries
its own preferences before every action. The user's approval/dismissal/ignore patterns ARE
the training signal. No separate config. No hardcoded thresholds.

**LLM cost:** 1 call per session for reflection. 1 call per proposal. 1 call weekly for
pattern scanning and digest generation. Total: ~3-5 calls per week for an active user.

**User attention:** Variable, adaptive. The companion starts with conservative defaults
and adjusts. If the user engages, it suggests more. If the user ignores, it suggests less.
If the user explicitly sets a preference, that overrides.

---

## 4. The Complete Loop

```
┌─────────────────────────────────────────────────────────────────┐
│                        EVERY SESSION                             │
│                                                                  │
│  User interacts with EA                                          │
│    │                                                             │
│    ├── Every turn: memory absorbs facts, preferences, corrections │
│    │   (silent, 0 LLM calls, 0 user attention)                   │
│    │                                                             │
│    └── Session ends                                              │
│         │                                                        │
│         ├── Memory: store all interactions                       │
│         ├── Reflection: 1 LLM call → what to do differently?     │
│         ├── Companion: check thresholds                          │
│         │    ├── corrections ≥ learned_threshold? → proposal     │
│         │    │    (1 LLM call, user reviews)                     │
│         │    └── no signal? → skip (saved tokens)                │
│         └── Companion: log user behavior                         │
│              (approve/dismiss/ignore → adjust thresholds)         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                           WEEKLY                                  │
│                                                                  │
│  Companion wakes (idle-time, not during session)                  │
│    │                                                             │
│    ├── Scan command history for repeated patterns                │
│    │    ├── pattern ≥ learned_threshold? → tool proposal         │
│    │    └── no pattern? → skip                                   │
│    │                                                             │
│    ├── Scan reflections for behavioral patterns                  │
│    │    ├── same suggestion across 3+ sessions? → behavioral edit│
│    │    └── no pattern? → skip                                   │
│    │                                                             │
│    └── Self-evaluate: were last week's proposals helpful?        │
│         ├── yes → keep going                                     │
│         └── no → recalibrate, reduce frequency                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        DREAM (periodic)                           │
│                                                                  │
│  Memory consolidation:                                           │
│    Deduplicate, archive stale entries, score confidence          │
│    1 LLM call to summarize clusters                              │
│    0 user attention                                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. User Experience (Expected Behavior)

As a user of EA, here is what I would experience:

### Immediate corrections just stick

```
Me: "List my unread emails"
EA: [shows Outlook emails]
Me: "No, use Gmail"

Next turn:
Me: "List my unread emails"
EA: [shows Gmail emails]  ← fixed, no proposal, no notification
```

### After accumulating issues, a quiet suggestion at the right time

```
Friday 5pm, after my last meeting:

Companion: "Email has been rough this week — I got corrected 3 times on account selection.
           Want me to formalize the fix into a skill so I stop messing up?"
           [Approve] [Dismiss]
```

Not mid-session. Not after every correction. At the right moment.

### Noticing my patterns and offering help

```
Thursday afternoon:

Companion: "You've been running `git diff main...feature -- '*.py'` every morning
           this week. Want me to handle that automatically?"
           [Yes, create tool] [No, I prefer manual] [Not now]
```

### Learning when to speak and when to shut up

I dismiss 3 proposals in a row → companion goes quiet for a week.
I approve 3 proposals in a row → companion suggests more, more proactively.
I never open the companion → it stops sending push notifications, switches to a weekly digest.
I explicitly say "stop suggesting tools" → it never does again.

### I never need to configure anything

No `config.yaml` for thresholds. No `AGENTS.md` for preference rules. The system learns
by doing:

```
I approve skill proposals → keep suggesting them
I dismiss tool proposals → suggest them less often
I ignore companion on weekends → don't send on weekends
I say "use Gmail" twice → default to Gmail
```

---

## 6. Implementation Path

### Phase 1: Foundation (what already exists)

| Component | Status | File |
|---|---|---|
| Memory store with correction/preference types | Done | `src/storage/memory.py` |
| MemoryMiddleware with before_agent injection | Done | `src/sdk/middleware_memory.py` |
| Skill registry with load/create | Done | `src/skills/registry.py` |
| Tool system with `@tool` decorator | Done | `src/sdk/tools.py` |
| CompanionScheduler with notification DB | Done | `src/sdk/companion_scheduler.py` |
| Conversation history logging | Done | `data/logs/` + `data/users/{id}/conversation/` |

### Phase 2: Immediate Adaptation (silent improvement)

1. **Enhanced correction injection** — `MemoryMiddleware.before_agent` injects recent corrections
   from the current session into working context. One line change.

2. **Session-end reflection** — `after_agent` hook calls a single LLM prompt: "What one thing
   would have made this session smoother?" Store result as `memory_type=reflection`.

**Lines of code:** ~80. **LLM cost:** 1 call per session. **User attention:** 0.

### Phase 3: Companion Proposals (gated improvement)

1. **Threshold evaluation** — Companion checks accumulated corrections at session end. Queries
   its own learned preferences for proposal thresholds.

2. **Skill improvement proposals** — When score exceeds learned threshold, companion drafts
   `.improvement.md` for the domain's SKILL.md. Shows proposal with `skill_test` results.
   User approves or dismisses.

3. **Tool creation proposals** — Weekly scan for repeated command patterns. Companion suggests
   tool creation. Same approval flow.

4. **Behavioral digest** — Weekly scan of reflection memories. Companion surfaces patterns
   across sessions as suggested behavioral updates.

**Lines of code:** ~400. **LLM cost:** 3-5 calls per week. **User attention:** per proposal.

### Phase 4: Companion Self-Throttling (learned improvement)

1. **Interaction tracking** — Companion stores user responses (approve/dismiss/ignore) as
   `memory_type=preference, domain=companion`.

2. **Adaptive thresholds** — Before every proposal, companion queries its own learned
   preferences and adjusts thresholds accordingly.

3. **Quiet hours learning** — Companion learns when user engages and when they don't.

**Lines of code:** ~200. **LLM cost:** 0 additional (pure counting + threshold comparison).
**User attention:** 0.

### Phase 5: Dream Consolidation (periodic maintenance)

1. **Memory deduplication** — Cluster near-duplicate memories, produce summaries.

2. **Confidence scoring** — Boost corrections that hold across multiple sessions. Fade
   single-occurrence low-confidence entries.

3. **Stale entry archiving** — Archive memories not accessed in 30+ days.

**Lines of code:** ~300. **LLM cost:** 1 call per consolidation cycle. **User attention:** 0.

---

## 7. Literature Review: Self-Improvement Best Practices

### 7.1 Key Findings

**Reflexion (Shinn et al., 2023):** Language agents improve through verbal reflection stored
in a separate episodic memory buffer. The critical architectural insight: maintain a distinct
"reflective text" buffer containing learned principles, separate from raw interaction memory.
The agent reflects and stores conclusions — it does not rewrite its own prompts or tools.
Achieved 91% pass@1 on HumanEval (vs 80% GPT-4 baseline).

**Constitutional AI (Bai et al., 2022):** Self-critique works *only* when guided by explicit
principles (a "constitution"). Without a constitution, self-critique produces worse behavior
than no critique at all. The agent needs a stable definition of "good" to self-improve against.

**Self-Rewarding LMs (Yuan et al., 2024):** Models can self-improve through iterative
self-evaluation as judge, but they **plateau after ~3 iterations**. Even with fine-tuning,
the improvement curve flattens. Continuous unbounded improvement is not a realistic expectation
— convergent improvement to a stable state is.

**Practical deployments:** ChatGPT's memory feature stores explicit facts and preferences
but does not learn from corrections. Claude's project knowledge is user-provided and static.
Neither modifies its own behavior through conversation. Model improvements happen through
offline training, not runtime learning.

### 7.2 Implications for Assistant's Design

1. **Separate memory from reflection.** Reflexion's key finding: raw experience (what happened)
   and learned principles (what I concluded) must live in different stores. The agent needs
   both: facts go to memory, patterns validated across sessions are promoted to a reflection
   buffer that injects into context.

2. **Add a constitution.** The agent cannot self-evaluate without explicit principles for what
   "good assistant behavior" means. Every proposal must be evaluated against stable criteria
   before reaching the user: correctness, efficiency, defaults, anticipation.

3. **Proposals need replay testing.** The same model that made the mistake should not be the
   sole author of the fix (self-amplification risk). Every skill/tool proposal must be
   tested against past session data: "Would the corrected behavior have produced the right
   result in the sessions where the original behavior failed?"

4. **Multi-session evidence, not single-session.** Reflexion shows effective improvement
   requires multiple episodes. A single session's corrections may be noise. The threshold
   for proposing a skill change: corrections must appear across at least 2 sessions.

5. **Version and rollback.** Every proposal change must be reversible. The companion tracks
   whether outcomes improve or worsen and can suggest rollback.

6. **Convergent, not continuous.** The agent converges toward a stable state for each artifact.
   Once corrections in a domain drop to zero for multiple sessions, stop proposing changes
   for that domain. Improvement is asymptotic, not linear.

### 7.3 The Three Guardrails

```
Proposal → replay-test → evaluate vs constitution → present to user
```

1. **Constitution** — stable principles defining "good assistant." Static, not learned.
2. **Replay test** — validate proposal against historical session data. Evidence-based.
3. **User gate** — human approval required for every change. Reversible.

Without all three, self-improvement becomes self-amplification.

---

## 8. Revised Architecture

The original four-artifact framework is preserved but structural relationships change:

```
┌──────────────────────────────────────────────────────────────┐
│                        MEMORY STORE                           │
│  Raw facts, corrections, preferences, events                  │
│  Continuous, silent, zero-attention                           │
│  "What happened"                                              │
└──────────────────────────┬───────────────────────────────────┘
                           │ dream consolidation (periodic)
                           │ promotions require multi-session validation
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                     REFLECTION BUFFER                          │
│  Stable learned principles, validated across sessions         │
│  Promoted only when pattern holds across 2+ sessions          │
│  Injected into context via before_agent                       │
│  "What I learned"                                             │
│  Converges to steady state, not unbounded growth              │
└──────────────────────────┬───────────────────────────────────┘
                           │ companion queries for proposals
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                        CONSTITUTION                            │
│  Stable principles (not learned, not modified by agent):      │
│  - Correctness: output matches what user requested            │
│  - Efficiency: uses fewest tool calls needed                  │
│  - Defaults: respects user's stated preferences               │
│  - Anticipation: notices patterns, offers help before asked   │
│  Evaluates every proposal before user sees it                 │
└──────────────────────────┬───────────────────────────────────┘
                           │ gates
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                       COMPANION                                │
│  Proposes skill/tool changes at the right time                │
│  Pipeline: draft → replay-test → evaluate vs constitution →   │
│            present → user approves/dismisses → track outcome  │
│  Self-throttles based on engagement                           │
│  Detects convergence → stops proposing for stable domains     │
│  Tracks versions → suggests rollback if outcomes worsen       │
└──────────────────────────────────────────────────────────────┘
```

### Revised artifact table

| Artifact | Stores | Improves | Cadence | LLM cost | User attention |
|---|---|---|---|---|---|
| Memory | Raw facts, corrections, events | Continuous, silent | Every turn | ~0 (dream: 1/week) | 0 |
| Reflection Buffer | Validated principles | Converges over sessions | Multi-session promotion | ~0 (promotion is counting) | 0 |
| Skills | Task instructions | Correction-driven, replay-tested | When pattern holds across 2+ sessions | 2/proposal (draft + test) | Per proposal |
| Tools | Capabilities | Pattern-driven, replay-tested | Weekly | 2/proposal (draft + test) | Per proposal |
| Companion | Timing preferences | Self-throttling, convergence detection | Every proposal cycle | 0 | 0 |

### Key differences from original design

| Original | Revised |
|---|---|
| Memory IS identity | Memory feeds identity; reflection buffer is the stable personality |
| Agent drafts improvements from corrections | Agent drafts, replay-tests against past sessions, evaluates against constitution, then presents |
| Single-session correction thresholds | Corrections must appear across 2+ sessions before triggering proposal |
| Continuous unbounded improvement | Convergent improvement — plateau detected, proposals stop for stable domains |
| Companion just times proposals | Companion also evaluates proposal quality via replay testing against constitution |
| No rollback mechanism | Every change versioned, reversible, auto-flagged if outcomes worsen over subsequent sessions |

---

## 9. Human Self-Improvement Frameworks

Research into how humans actually improve validates and extends the design.

### 9.1 Deliberate Practice (Ericsson, 1993)

The most validated framework in the literature. Expert performance is not innate talent — it's
a product of life-long deliberate effort. Five criteria:

| Criteria | Maps to EA |
|---|---|
| Well-defined task with clear goal | Constitution: correctness, efficiency, defaults, anticipation |
| Learner can perform independently | Agent runs autonomously |
| Immediate feedback on performance | User corrections (implicit), replay testing (explicit) |
| Repeated execution | Same task types across sessions |
| Structured by a teacher | LLM acting as coach via companion scheduler |

**Critical finding:** Practice without feedback is near-useless (r < 0.20). Simply doing a task
many times has very low benefit compared to deliberate practice with feedback. The "10,000 hour
rule" is misleading — quality of feedback matters far more than quantity of repetition. The
correlation between deliberate practice and performance is 0.40 (large effect size).

**For EA:** The constitution provides the standard. Replay testing provides structured feedback.
The companion scheduling ensures practice sessions happen when there's evidence to work with.
The missing piece historically was the teacher role, now filled by the LLM coach.

### 9.2 Self-Efficacy (Bandura, 1977)

The single strongest predictor of improvement: belief that improvement is possible. People
with identical skills get dramatically different results based on self-efficacy alone, because it:

1. Creates expectation of success (approach, not avoidance)
2. Enables risk-taking and challenging goals
3. Sustains effort after failure ("I can fix this")
4. Regulates emotional response to difficulty ("this is hard = I'm growing")

**For EA:** The agent doesn't have "self-efficacy" psychologically, but the companion's
adaptive thresholding is the equivalent. It shouldn't stop proposing improvements
just because some were dismissed — it should learn from each response and persist
selectively. The constitution provides the growth-oriented framing: every proposal
is evaluated against stable principles, not past rejections.

### 9.3 Successive Approximation (Skinner, Bandura)

The behavioral model of improvement: break complex goals into incremental steps, reward
each step, shape behavior gradually. Trying to improve everything at once fails. Focus on
one dimension, improve it, get feedback, stabilize, then move to the next.

**For EA:** The LLM coach identifies the ONE most impactful sub-skill to improve per cycle.
Never "here's 5 changes." One thing. Approved. Stabilized. Then the next.

### 9.4 Growth vs Fixed Mindset (Dweck, 2006)

People who believe abilities can be developed (growth mindset) outperform those who
believe abilities are fixed. The mindset itself determines whether effort is applied.

**For EA:** The constitution embodies a growth orientation: "correctness can improve,"
"efficiency can be optimized," "defaults can be refined." The companion tracking
convergence (stopping when a domain stabilizes) is the equivalent of recognizing
that improvement is asymptotic — effort shifts to the next domain when one plateaus.

### 9.5 Levinson's "The Dream" (1978)

Human development across life stages is shaped by aspirations. A person's "Dream"
defines the direction of their growth. If their life structure aligns with the Dream,
they thrive. If opposed, the Dream dies and with it, their sense of purpose.

**For EA:** The constitution IS the Dream — a stable vision of what good assistance
looks like. Without it, the agent drifts. With it, every improvement has a direction.

### 9.6 Ideal Self vs Real Self (Rogers, 1961)

Healthy development occurs when the ideal self (who you want to be) and the real self
(who you actually are) are accurate and the real self is lifted toward the ideal.
Incongruence — when the gap is denied or the ideal is lowered — prevents growth.

**For EA:** The constitution is the ideal self. The reflection buffer captures the real
self (what the agent actually learned from sessions). Proposals bridge the gap: "here's
what you should be doing (constitution) vs what you did (session history) — here's a fix."

---

## 10. Companion as Coach

### 10.1 Companion already has the infrastructure

The companion (`companion_scheduler.py`) currently has:

- An LLM with AgentLoop and its own system prompt
- Idle-time background cycles
- Notification DB for surfacing proposals
- Personality memory DB for learning user preferences
- Time-of-day awareness, email urgency detection, workspace activity awareness

It is NOT just a counter. It's a full agent running during idle time, deciding whether to
check in or stay silent.

### 10.2 The companion is not the coach — the LLM is. The companion is the training scheduler.

The companion has one job: decide if NOW is the right time to run a coaching cycle. It
delegates the actual coaching to the LLM.

```
Companion (scheduler):            LLM (coach):
"Time to work on email"    →      Reads relevant sessions
                                  Identifies specific skill gap
                                  Designs surgical improvement
                                  Replay-tests against historical sessions
                                  Evaluates against constitution
                                  Proposes improvement or skips

Companion (scheduler):
"User approved. Track outcomes."
"User dismissed. Note preference."
"User ignored. Reduce email coaching frequency."
```

The companion's intelligence is in learning when. The LLM's intelligence is in coaching how.

### 10.3 Expanded companion system prompt

The companion's existing system prompt (check-in mode) gets an appended coaching section:

```
## Coaching Mode

When user feedback patterns indicate a skill or behavior needs improvement,
switch from check-in to coaching mode:

1. IDENTIFY: Analyze the relevant sessions. What specifically went wrong?
   Isolate the ONE most impactful sub-skill to improve (not the entire domain).

2. DESIGN: Draft a surgical fix — not a full rewrite. The smallest change
   that addresses the specific issue. Edit the skill prompt, tool description,
   or agent default.

3. VALIDATE: Replay-test the fix against the sessions where it failed.
   Would the corrected behavior have produced the right result?
   Yes → proceed. No → revise or skip. Don't propose what can't be validated.

4. EVALUATE: Score against each constitution principle:
   - Correctness: does it produce factually correct output?
   - Efficiency: does it reduce unnecessary tool calls?
   - Defaults: does it respect user's stated preferences?
   - Anticipation: does it surface the right help at the right time?
   Any score < acceptable? Revise or skip.

5. NOTIFY (if improvement validated): Draft a brief proposal.
   Show: what was wrong, what the fix is, how the fix performed in replay tests.
   [Approve] [Dismiss] [Edit]

Rules:
- Never propose more than ONE improvement per cycle
- Never propose an improvement that can't be replay-validated
- Never propose an improvement that fails the constitution check
- If the user dismisses → note the preference; don't propose the same thing again
- If the user approves → track outcome; if outcomes worsen → suggest rollback
- If the user ignores repeatedly → reduce coaching frequency
- If a domain has zero corrections for 5+ sessions → mark as stable, stop proposing
```

### 10.4 Who to coach, what to coach

| Learner | What's coached | Signal | Cadence |
|---|---|---|---|
| Agent's skills | Sub-skill in a domain (e.g., "email account selection") | Corrections in domain across 2+ sessions | Per session |
| Agent's tools | Specific tool behavior (e.g., "shell_execute: confirm before destructive") | Corrections targeting a specific tool across 2+ sessions | Weekly |
| Agent's defaults | Behavioral patterns (e.g., "be more concise," "lead with conclusion") | Reflection memory patterns across 5+ sessions | Weekly |
| User's patterns | Productivity/automation (e.g., "you run this every morning") | Repeated shell commands, task patterns | Weekly |

### 10.5 One agent, two modes

```
Every idle cycle:

  Default: Check-in mode
    Context: time of day, email urgency, workspace activity
    Output: 1-2 sentence warm message OR skip

  If coaching conditions met: Coaching mode
    Context: session history, corrections, domain patterns
    Output: analyze → design → replay-test → evaluate → propose OR skip
```

Same companion. Same LLM. Same idle cycles. Just richer context and coaching responsibilities.

---

## 11. What We Explicitly Avoided

- **Hardcoded thresholds** — No magic number 3. The companion learns per-user.
- **SOUL.md** — Memory feeds the reflection buffer, which IS the agent's personality.
  No separate identity file. No stale cache layer.
- **Auto-applied changes** — Every skill edit, tool creation, and behavioral change requires
  user approval. The agent proposes, the user decides. All reversible.
- **Self-authored fixes without testing** — The agent cannot be the sole author of its own
  improvement. Every proposal is replay-tested against historical sessions and evaluated
  against the constitution before reaching the user.
- **Mid-session interruptions** — All proposals happen via companion notification during
  idle time. Never during active work.
- **Single-session overfitting** — Patterns must appear across 2+ sessions to trigger
  coaching. One-off corrections are applied immediately (memory), not promoted to
  permanent skill or tool changes.
- **Unbounded improvement assumption** — Each artifact converges to a stable state. The
  companion detects convergence and stops proposing for stable domains.
- **Bulk proposals** — ONE improvement per cycle. Successive approximation, not
  multi-suggestion overwhelm.
- **Companion as coach** — The companion is the scheduler that triggers coaching cycles.
  The LLM is the actual coach. The companion does not need to "become" the coach — it
  already has an LLM. It just needs coaching responsibilities in its system prompt.
- **External hook systems** — Extended the existing in-process Middleware ABC. No subprocess
  overhead, no shell scripts, no JSON contracts.
- **Motivation-first approaches** — Deliberate practice is not inherently motivating. The
  user engages because the agent becomes more helpful, not because proposals are pleasant.

---

## 12. Final Conclusion

Six artifacts, six improvement loops, three guardrails:

```
Session → Memory absorbs → Dream consolidates → Reflection buffer promotes patterns
                                                    ↓
                            Companion detects coaching conditions (when)
                                                    ↓
                            LLM coach: identify → design → replay-test → constitution-evaluate
                                                    ↓
                            User-gate: approve/dismiss → track outcome → converge
```

| Artifact | Role | Improves | Converges to |
|---|---|---|---|
| Memory | Raw experience | Every turn, silent | Grows, pruned by dream |
| Reflection Buffer | Stable personality | Multi-session pattern promotion | Steady state of validated principles |
| Skills | Task instructions | Coaching cycle: correction-driven | Optimal per-domain instructions |
| Tools | Capabilities | Coaching cycle: pattern-driven | Optimal capabilities per need |
| Constitution | Evaluation standard | Static, not learned | — |
| Companion | Training scheduler | Self-throttling, convergence detection | Optimal cadence per user |

The three guardrails prevent self-amplification:
1. **Constitution** — stable evaluation criteria against which every proposal is scored
2. **Replay test** — evidence-based validation using historical session data
3. **User gate** — human approval with rollback (every change reversible)

Everything the agent becomes comes from paying attention to one person, validating patterns
across time, coaching surgically through an LLM, and testing improvements against a
constitution before applying them. The companion is the training scheduler. The LLM is the
coach. The user is the final authority.

---

## 13. Decision Log

- **2026-05-03:** Explored Claude Code hooks vs middleware extension. Rejected external hooks,
  accepted middleware ABC extension with user discovery.
- **2026-05-03:** Deep code trace of AgentLoop found two pre-existing bugs (`aafter_model`
  missing in stream path, `wrap_tool_call` with no error handling). Extending middleware
  forces these fixes.
- **2026-05-03:** Reviewed `docs/OBSERVATIONAL_MEMORY_DESIGN.md`. Approved Phase 1 with 9
  recommendations. Observation-based memory design is sound.
- **2026-05-03:** Studied Hermes Agent Self-Evolution (GEPA). Offline batch text optimization
  is the right model for skill improvement, but the trigger should be conversational correction
  patterns, not deliberate invocation.
- **2026-05-03:** Removed Firecrawl tools from codebase. Implemented built-in `web_fetch` and
  `web_search` as zero-config SDK-native tools using `httpx` + `html2text`.
- **2026-05-04:** Researched dream mechanisms (openclaw-auto-dream, rem-sleep-skill,
  dream-weaver-protocol). All consolidate memory. None optimize skills or tools.
- **2026-05-04:** Analyzed SOUL.md. Rejected — memory IS the soul. No need for a separate
  identity file.
- **2026-05-04:** Arrived at four-artifact framework: Memory (who), Skills (what), Tools (how),
  Companion (when). Each improves through its own loop. The companion learns thresholds,
  nothing is hardcoded.
- **2026-05-04:** Literature review of self-improvement best practices (Reflexion, Constitutional AI,
  Self-Rewarding LMs, ChatGPT/Claude deployments). Revised design: added constitution, separated
  memory from reflection buffer, added replay testing, require multi-session evidence, added
  versioning and rollback, acknowledged convergent (not continuous) improvement.
- **2026-05-04:** Research into human self-improvement frameworks (Deliberate Practice, Self-Efficacy,
  Successive Approximation, Growth vs Fixed Mindset, The Dream, Ideal vs Real Self). Validated
  the constitution (standard), replay testing (feedback), successive approximation (one improvement
  per cycle), and convergence (asymptotic improvement). Identified the teacher role as the
  prior gap, now filled by the LLM coach delegated by the companion.
- **2026-05-04:** Companion re-architected: companion is the training scheduler, LLM is the coach.
  Companion already has LLM + AgentLoop + idle cycles — just needs coaching responsibilities
  in its system prompt and access to session data. One agent, two modes (check-in and coaching).
  Coaching pipeline: identify → design → replay-test → constitution-evaluate → notify.
