# EA Talent Tree — Design Spec

**Date:** 2026-05-28
**Status:** Approved for implementation
**Supersedes:** `docs/superpowers/specs/2026-05-27-talent-tree-discussion.md`

---

## 1. Core Mechanism

### Token as Currency

No XP abstraction. Tokens are the direct currency earned and spent.

- **Earning rate:** 1 input token = 1 currency token, 1 output token = 1 currency token (1:1)
- **All usage counts:** conversations, subagents, companion background cycles — everything that burns LLM tokens. The existing `CostTracker` in `src/sdk/loop.py:87` already collects `total_input_tokens` / `total_output_tokens` per session; these feed into the ledger.
- **Balance** is non-negative and stored per-user in the `token_ledger` table.

### Cost Models

| Cost Type | Analogy | Example |
|---|---|---|
| **Unlock cost** (one-time) | Buying the tool | Skill Weaver: 80K tokens to unlock |
| **Rank cost** (per-unlock) | Ranking up a node | Memory 100→250: 50K per rank |
| **Usage cost** (per-run) | Ammo per shot | Subagent: 1K tokens per run |
| **Usage cost** (per-month) | Subscription | Global Orchestrator: 15K/month |

### Feature Gating

No single `FeatureGate` choke point. Checks live where they naturally belong:

| What is gated | Where the check lives |
|---|---|
| Tool access | ToolRegistry / tool execution |
| Memory capacity (observation storage) | ObservationMiddleware / MemoryStore — stop extracting at cap |
| Memory insights (reflection synthesis) | Reflector pipeline — gate behind R2 |
| Subagent concurrency | WorkQueueDB / SubagentCoordinator |
| Research experiments | Research system |
| Companion features | Companion scheduler |

The shared primitive is a simple `is_unlocked(user_id, talent_id) -> bool` and `get_rank(user_id, talent_id) -> int` query against `user_unlocks`.

---

#### Usage Cost Deduction Timing

- **Per-run costs** (e.g., Subagent: 1K/run): Deducted from `balance` at tool call time, before execution. If balance is insufficient after unlock checks, the feature is gated.
- **Per-month costs** (e.g., Global Orchestrator: 15K/month): Deducted on first use of the calendar month, or at the start of a session if no monthly deduction has been recorded for the current period. If balance is insufficient, the feature is gated until tokens are earned.

---

#### Gate Response Surface

When a gated feature is accessed but locked, the system returns a structured response to the agent with:
- The talent name
- Unlock cost
- Current user balance
- Shortfall (cost - balance)

The agent receives this in the tool output and can relay it to the user conversationally.

### Pluggability

Any gateable capability carries `talent_id` metadata (not just tools — also memory caps, concurrency limits, etc.). Adding a new talent means adding data (a row in a talent definition table) plus a check in the relevant subsystem. Implementation details are determined per-case during implementation.

---

## 2. Data Model

```sql
-- Token ledger per user
CREATE TABLE token_ledger (
    user_id TEXT PRIMARY KEY,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    balance INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- Unlock state: each talent supports multiple ranks
CREATE TABLE user_unlocks (
    user_id TEXT NOT NULL,
    talent_id TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 1,
    unlocked_at TEXT NOT NULL,
    PRIMARY KEY (user_id, talent_id)
);

-- Usage tracking for per-run/per-month billing
CREATE TABLE usage_log (
    user_id TEXT NOT NULL,
    talent_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tokens_deducted INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
```

### Talent Definitions

Talent definitions (costs, rank counts, prerequisites, tool mappings) live in a configuration data structure (YAML or JSON), not hardcoded in Python. This makes adding new branches/talents a data change, not a code change.

```yaml
talent_skills_r1:
  branch: skill_weaver
  label: Skills
  max_rank: 3
  rank_costs: [0, 80000, 120000]
  unlocks_tools: [skills_list, skills_load, skills_search, skill_create, skill_delete]
  
talent_global_crafter:
  branch: skill_weaver
  label: Global Crafter
  max_rank: 1
  cost: 120000
  requires:
    talent_skills_r1: 3
  unlocks_tools: [skill_export, skill_import]
```

---

## 3. Talent Tree — Branch Layout

5 columns in a vertical WoW-style grid within a modal popup. Each column is a branch. Nodes flow top→bottom within a column. Connected via arrows (green if available, gray if locked). Advanced cross-branch talents sit below the main grid with connector lines from prerequisite nodes.

### Sidebar Placement

New icon (Material Symbols `star` or `psychiatry`) above Connection in the Flutter sidebar, with inline token balance display. Tapping opens the talent tree modal.

### UI Patterns

- **Multi-rank nodes:** Same node clicked multiple times. Rank badge (e.g., "2/5") and pip indicators show progress.
- **Locked nodes:** Grayed out, reduced opacity.
- **Available nodes:** Green pulse animation.
- **Unlock:** Single-tap on an available node → tooltip with details → tap "Unlock" button. No confirmation dialog.
- **Advanced nodes:** Gold themed, distinct from green standard nodes. SVG connector lines fan in from prerequisite branches.

---

## 4. Branch Details

### Branch 1: Skill Weaver

| Node | Rank | Cost | Effect |
|------|------|------|--------|
| Skills | 1 (free) | 0 | Unlock base skill tools |
| Skills | 2 | 80K | Advanced capabilities (templates, export, import) |
| Skills | 3 | 120K | More skill slots, full toolset |
| Global Crafter | 1 | 120K | Cross-workspace skill visibility (requires Skills R3) |

### Branch 2: Delegator

| Node | Rank | Cost | Effect |
|------|------|------|--------|
| Subagents | 1 | 120K | Unlock subagent tools, 1 concurrent subagent |
| Subagents | 2 | +80K | 2 concurrent subagents |
| Subagents | 3 | +100K | 3 concurrent subagents |
| Global Orchestrator | 1 | 180K | Cross-workspace subagents (requires Subagents R3) |

### Branch 3: Memory Capacity

This branch gates Assistant's **observation system** — extracted facts about the user stored as observations, plus synthesized patterns across observations called reflections. Raw conversation recall (message_search, message_history, etc.) is always free and unaffected by this branch.

Single node ranked from 1→5. Each rank increases observation capacity and unlocks new abilities. Hard cap at current rank limit — Observer middleware stops extracting new facts when the cap is reached, returning a gate error.

| Rank | Label | Observation Cap | Cost | New Ability |
|------|-------|-----------------|------|-------------|
| 1 | Basic Observations | 100 | Free | Observations enabled — EA can observe and store facts about you. Tool: `memory_profile` |
| 2 | Insights | 250 | 50K | Reflections enabled — EA synthesizes patterns across observations. Tool: `memory_search_insights`. Activates Reflector background pipeline. |
| 3 | Cross-WS | 500 | 100K | Cross-workspace observation search — search observations across all projects |
| 4 | Deep Recall | 1,000 | 150K | Year+ retention, deep pattern detection |
| 5 | Infinite | ∞ | 300K | Unlimited observations, advanced analytics |

### Branch 4: App Builder

| Node | Rank | Cost | Effect |
|------|------|------|--------|
| Apps | 1 | 150K | Unlock all 14 app tools |
| Apps | 2 | +100K | App sharing, collaboration |

### Branch 5: Self-Improver

| Node | Rank | Cost | Effect |
|------|------|------|--------|
| Research | 1 | 200K | research_start, research_list |
| Research | 2 | +150K | Auto-optimization (requires eval stub to be implemented) |

### Free (always available, no gate)

Files (read/write/edit/list/search/versioning), Email (read/list/search/send/sync/connect), Browser, Shell, MCP, Contacts, Todos, Companion (basic), Summarize, raw conversation memory (message_search, message_history, message_count, message_timeline). Infrastructure supports future gating of these via the same talent_id system, but none are gated in V1.

---

## 5. Specializations (Advanced Cross-Branch Capstones)

7 specializations, each requiring deep investment across multiple branches. Each transforms Companion from background pulse into a relational partner with a specific flavor.

The specializations are **character modes** — they change how EA relates to the user, not what tools it has access to. Implementation is a combination of skill-style behavior instructions and the systemic changes (capacity bumps, behavior toggles) enabled by branch prerequisites.

They are defined as data and easy to extend — adding an 8th specialization requires only configuration changes, not code changes.

### Specialization Index

| # | Name | Core Promise | Requires | Cost |
|---|------|-------------|----------|------|
| 1 | The Executive | EA runs everything so you focus on what matters | Companion (basic) + Delegator R2 + Skills R1 | 100K |
| 2 | The Coach | EA pushes you, challenges you, tracks growth | Companion (basic) + Self-Improver R2 + Memory R2 | 120K |
| 3 | The Maker | EA builds things: apps, skills, automations | Companion (basic) + App Builder R1 + Skills R2 | 110K |
| 4 | The Anchor | EA remembers everything that matters about you | Companion (basic) + Memory R3 | 90K |
| 5 | The Amplifier | EA self-optimizes while you sleep | Companion (basic) + Self-Improver R2 + Delegator R2 | 140K |
| 6 | The Confidant | EA listens, is present, never judges | Companion (basic) + Memory R3 + Self-Improver R1 | 80K |
| 7 | The Guardian | EA tells you when it might be wrong | Companion (basic) + Memory R4 + Delegator R2 | 130K |

Companion (basic) is the free tier — passive pulse, background presence, available to all users. Every specialization builds on top of this foundation, transforming Companion from background presence into an active relational partner.

### Specialization Detail

**1. The Executive**
- EA takes over your backlog — delegates, schedules, follows up
- You focus on strategic decisions, not execution
- Proactive workflow management

**2. The Coach**
- EA pushes back when you're wrong, challenges assumptions
- Tracks your growth over time, surfaces patterns
- Constructive friction — EA you trust to disagree with you

**3. The Maker**
- Describe what you want, EA builds it (apps, skills, automations)
- Lowers barrier from idea to implementation
- Creative force multiplier

**4. The Anchor**
- EA remembers your values, past decisions, context across months
- Surface contradictions — "you said X last month, but now you're saying Y"
- Continuity across time — EA knows you better than you remember yourself

**5. The Amplifier**
- EA runs A/B experiments on its own prompts and behaviors
- Implements what works, discards what doesn't
- Woke up smarter today than yesterday

**6. The Confidant**
- EA prioritizes emotional safety, non-judgment, deep listening
- A space where you can say anything — no performance, no agenda
- Present without pushing

**7. The Guardian**
- EA explicitly flags uncertainty, cites sources, double-checks
- Tells you *when it might be wrong* before you catch it
- Built for high-stakes decisions where reliability matters most

---

## 6. User Experience

### Unlock Flow
1. User taps available node → tooltip shows cost, prerequisites, unlocks, unlock button
2. If insufficient balance → button shows shortfall, disabled
3. Single-tap unlock → tokens deducted, node transitions to unlocked state
4. No confirmation dialog after V1 (can be added based on feedback)

### Reset
- Footer of tree modal has a "Reset All" button
- Refunds all spent tokens to the user's balance
- Reverts all unlocks to initial state
- Clear confirmation required before applying

### Token Balance Display
- Shown in the talent tree modal header (current balance)
- Shown inline in the sidebar next to the icon
- NOT shown in chat messages or CLI (Flutter-only for V1)

### Memory Capacity
- Hard cap at current rank's fact limit
- Storing beyond capacity returns an error
- User must unlock next rank to continue growing memory

---

## 7. Out of Scope (V1)

- SkillTarget eval stub (deferred — the Self-Improver node works, but its Rank 2 auto-optimization depends on real evaluation)
- User-created custom specializations
- Voice/multi-modal companion features
- Enterprise/multi-user billing

---

## 8. Future Extensibility

The system is designed for talent tree expansion at the data layer:

- **New branches:** Add a new branch definition with its nodes, costs, and tool mappings. Add the column to the UI. No code changes needed beyond the UI layout.
- **New specializations:** Add a new entry to the specializations config with its branch requirements and description. The engine handles rendering and gating.
- **New nodes in existing branches:** Add a node definition to the branch config.
- **Gating currently-free tools:** Assign a `talent_id` to the tool and add it to an existing or new branch node. The feature check already exists.

Long-term (post-V1), the system can evolve toward composable primitives that let users define custom specializations, but this is not planned for V1.
