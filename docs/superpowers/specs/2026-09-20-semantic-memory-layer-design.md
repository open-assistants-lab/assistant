# Semantic Memory Layer — Design Spec

**Status:** **PARKED 2026-09-20** — design recorded; implementation gated on the eval in §4 (see §4 precondition). No work starts until the eval returns a result.
**Date:** 2026-09-20
**Decisions taken 2026-09-20:** component rename `dream()` → **`replay()`**; working name **SemanticMem** (provisional — to be confirmed or changed when the project is unparked)
**Related:** `2026-05-06-memory-redesign.md`, `2026-05-27-memory-consolidation-design.md` (observer/reflector — **retired in CoreMem 0.10**), `2026-06-09-unified-data-architecture.md`, `2026-08-24-vertical-expansion-enterprise-roadmap.md` §6.6
**External references studied:** LangChain OpenWiki (claims + versioned evidence), Claude Code auto memory (index + topic files, hard-capped), AutoSchemaKG (autonomous schema induction), *LLM-empowered KG construction* survey (2025)

---

## 1. Context

CoreMem provides **episodic** memory — conversation turns, append-only, retrieved zero-LLM. What has no home is the **semantic** half: durable facts distilled from experience (a client's payment terms, who is who, recurring procedures) that are not attached to any single episode.

This spec exists because several turns of design work produced a naming decision, a set of *negative* results worth preserving, and — importantly — a discovery that the pattern is **already implemented but never run**. It also retires the old Observer→Reflector consolidation design: CoreMem ≥0.10 replaced it with compiler + dreaming + search, and `memory_reflection` was removed.

---

## 2. Verified state of the world (2026-09-20)

### 2.1 What already exists in CoreMem

`coremem/agent_journal/` is a markdown wiki, not a log:

```
agent_journal/
├── MEMORY.md     # index — "# AgentJournal / Current Focus / Read Next"
├── SCHEMA.md     # a schema file already exists
├── index.md, log.md
├── pages/        # topic pages
└── daily/        # daily pages
```

| Mechanism | Where | Note |
|---|---|---|
| Hard-capped index | `bundle.py: boot_budget_chars = 8000` | Errors on overflow — `"MEMORY.md exceeds configured boot budget"` |
| Topics loaded on demand | `pages/` + `daily/` | Not loaded at boot |
| Semantic search over pages | `_embedding_index.refresh(self.pages_dir)` | Something Claude Code's auto memory does **not** have |
| Consolidation | `dreaming.py` → produces **"Promoted facts: durable facts worth keeping in long-term memory"** | LLM path |
| Deterministic path | `compiler.py` — *"intentionally does not call an LLM"* | LLM variant: `llm_compiler.py` |
| Versioned records | `journal_records` table (hash-chained, covered by `checkpoint_memory`/`rollback_memory`) | Not used by retrieval |
| Provider abstraction | `providers.py`, `create_provider` (public in `__all__`) | — |

**This is the pattern that OpenWiki and Claude Code auto memory independently arrived at** — bounded index, topic files on demand, frontmatter metadata. It is already built here.

### 2.2 The gap

```
App calls dream():            never
App touches agent_journal:    never
App uses journal_records:     never
Knowledge entries in existence:  zero
```

**The pipeline has never run.** Every mechanism discussed below operates on an empty set today.

Additionally: `agent_journal` page writes are plain `path.write_text(...)` — **outside HybridDB's version chain**. That is the one gap that matters structurally.

### 2.3 Evidence that already exists

`results/eval_per_question_20_with_journal.json` — 20 questions, LongMemEval, `per_question_haystack`, k=5:

| Mode | session_recall@5 | message_recall@5 |
|---|---|---|
| `memorycore` (baseline) | 0.825 | 0.623 |
| `memorycore_deep` | 0.895 | 0.702 |
| **`memorycore_journal`** | **0.939** | **0.956** |

This **contradicts** the assumption in `docs/versioned-memory-design.md` that the journal/governance layer is *"a product capability, NOT a retrieval lever."* On this sample the journal mode moved `message_recall@5` from 0.62 → 0.96.

**Four caveats before trusting it** (see §4.2).

---

## 3. Scope decision

**In scope:** running the existing pipeline at scale, and adding structure *only in response to observed failure*.

**Out of scope, explicitly** (recorded so it is not re-litigated):

| Rejected | Why |
|---|---|
| **Formal ontology (OWL/RDF)** | No research support for agent memory; A-MEM and Zep don't use it; value is in *reviewable* structure, not formalism |
| **Autonomous schema induction** | AutoSchemaKG reached 95% alignment but needed 78,400 GPU-hours, 50M documents, billions of facts — and its authors report failure in *"extremely technical domains"* and *"sparse knowledge regions"*. A single firm's data **is** that regime |
| **Vocabulary gating (enforced `SCHEMA.md`)** | Premature at zero claims. `SCHEMA.md` stays descriptive |
| **Claims + evidence frontmatter (OpenWiki-style)** | Correct direction, wrong time. Add when a question spans pages |
| **Entity registry + resolution** | Needs claims to exist before duplication is observable |
| **Multiple HybridDBs per application** | Wrong axis. Cost: cross-table transactions, one backup/exit path, one ops surface. The claimed benefit (one semantic index, one version chain) **does not exist** in HybridDB: Chroma collections are `f"{table}_{col}"` and hash chains are per-table. If splitting is ever justified, split by **lifecycle class** (cache / purgable / durable), not by domain |
| **Extracting `agent_journal` into a new repo** | Deferred until §4 returns a result. Extraction without evidence is the same mistake as premature package extraction |

---

## 4. Next step: the eval (precondition for any implementation)

### 4.1 Plan

```
LongMemEval S (500 questions) × { memorycore, memorycore_deep,
                                  memorycore_llm_expansion, memorycore_journal }
LoCoMo (1,986 QA pairs)       × same four modes
→ per-question-type breakdown; resumable via scripts/eval_combined_s.py
```

Both harnesses already exist: `scripts/eval_agent_journal_longmemeval.py`, `scripts/eval_answer_longmemeval.py`, `scripts/adapt_locomo.py` (LoCoMo already adapted; categories: single-hop 841, adversarial 446, temporal 321, multi-hop 282, open-domain 96).

### 4.2 What must be verified first

1. **Double-counting.** If compiled **pages** count as **messages** in `message_recall@5`, the 0.96 is inflated. Runs record `retrieved_page_ids` separately from `retrieved_message_ids`, but the scoring path must be checked.
2. **LLM-matched control.** `memorycore`/`_deep` are zero-LLM; `_journal` compiles with an LLM. `memorycore_llm_expansion` (one LLM call) is the cost-matched baseline that isolates **structure** from **extra LLM spend**.
3. **Per-category breakdown**, specifically **knowledge-update** and **temporal-reasoning** — where a knowledge mechanism should help — versus a uniform lift everywhere, which would just mean "better index".
4. **Sample size.** n=20 is not a result.

### 4.3 Decision matrix (what to build, based on what is observed)

| Observation | Action |
|---|---|
| No lift at scale | **Stop.** Drop the line; keep the negative result |
| Lift is real but the journal pages go stale/misleading | Add claims + evidence refs + staleness |
| Agent invents duplicate categories | Add vocabulary gating |
| Useful, but users want control over what sticks | Add review states |
| Lift depends on LLM spend, not structure | Re-evaluate — the claim "distilled layer helps" is then a spend claim, not an architecture claim |

### 4.4 The one structural gap worth fixing regardless

**Journal page writes are outside the version chain.** If the eval justifies keeping the journal, journaling page writes into HybridDB (hash chain, point-in-time, rollback over knowledge, not just messages) is the differentiator neither OpenWiki nor Claude Code has.

---

## 5. Terminology reference (memory lexicon)

Kept here because the naming decision depends on which register a name comes from.

| Register | Terms |
|---|---|
| **Pop culture** | core memory (vivid, identity-defining — popularised by *Inside Out*), core belief (CBT), flashbulb memory, false memory, muscle memory, photographic/eidetic, memory palace, déjà vu, **institutional memory**, collective memory |
| **Psychology / neuroscience** | working, short-term, long-term, episodic, semantic, procedural, prospective, retrospective, autobiographical, spatial, emotional, sensory, declarative vs non-declarative, **source memory** (*where* you learned it), verbatim vs gist (fuzzy-trace), engram, schema, transactive (who knows what), consolidation / reconsolidation |
| **Sleep consolidation** | **sleep-dependent memory consolidation**; **active systems consolidation** (Born & Wilhelm); **hippocampal replay** (Wilson & McNaughton 1994) during **slow-wave sleep** via **sharp-wave ripples**; **synaptic homeostasis** (Tononi & Cirelli — downscaling as signal-to-noise); **targeted memory reactivation**; **schema assimilation** (Tse et al. — days instead of weeks) |
| **Computing** | RAM, cache, buffer, scratchpad, virtual memory, volatile/non-volatile, ROM, associative/CAM; historical: core, **core rope**, drum, delay-line, bubble, Williams tube |
| **AI / agents** | context window, long-term memory, episodic/semantic/procedural (the standard agent triad), memory bank, vector memory, parametric vs non-parametric, KV cache, reflection, memory stream, skill library |
| **Business** | **institutional memory**, organizational memory (Walsh & Ungson), **tribal knowledge**, knowledge base, playbook / runbook / SOP / handbook, key-person risk |

**Terminology note for the `dream()` step — DECIDED 2026-09-20: rename to `replay()`.** The
science-accurate names are **Replay** (the mechanism: hippocampal replay during slow-wave
sleep) and **Consolidation** (the process). "Dreaming" is the pop-culture term, and REM is
associated with emotional/creative integration rather than declarative consolidation. So
the component should be `replay()`, not `dream()`. Apply when the component is next
touched (extraction, or any edit to the journal path) — not as a standalone change.

---

## 6. Placement

**Decision: a separate OSS project, sibling to CoreMem.**

| Option | Verdict |
|---|---|
| Inside **CoreMem** | ❌ Two promises, one name. CoreMem's headline is *"zero-LLM memory retrieval… without a single API call"*; this layer is LLM-heavy by nature. CoreMem already ships `MemoryCore.__init__(llm_provider=…, agent_journal_model=…)` — LLM parameters in a zero-LLM library's constructor. Also: different lifecycle (append-only immutable vs mutable/reviewed) and a different benchmark story (retrieval recall vs knowledge-update/contradiction) |
| Inside **Assistant** | ❌ Violates *primitives in the engine, semantics in the app*. The **mechanism** (claims, evidence, staleness, review, index+cap) is a primitive; the **vocabulary** (`client`, `matter`, `net_terms`) is app semantics |
| **Separate sibling** | ✅ Depends on HybridDB; reads CoreMem episodes as evidence; no cycles |

Dependency shape:

```
Assistant          vocabulary, process definitions, HITL tiers, review UI
    │
Semantic layer     claims, evidence, staleness, review, index + topic files
    │
CoreMem            episodic retrieval (zero-LLM)
    │
HybridDB           versioned substrate (evidence versions, hash chain)
```

The layer needs an LLM provider abstraction. CoreMem has `providers.py`; **duplicate ~100 lines rather than coupling two OSS repos.**

---

## 7. Naming

### 7.1 The pack pattern

`CoreMem` / `HybridDB` / `AgentProfile` = **[role] + [category]**, PascalCase, technical, no article, no brand. The category is a short artifact noun; the role says which one.

### 7.2 Candidates considered

| Name | Verdict |
|---|---|
| **`SemanticMem`** | ✅ **Recommended.** Exact technical counterpart: `CoreMem` (episodic) / `SemanticMem` (semantic). Fits the pack; no overclaim; PyPI free |
| `CortexMem` | Alternative — cognitive register; scientifically apt (consolidation happens in cortex) but names a *location*, not a function |
| `AgentHandbook` | Alternative — pairs with `AgentProfile` structurally; undersells versioning/querying |
| `SourceMem` | Alternative — names the differentiator (source memory = remembering *where* you learned it = evidence refs); PyPI free |
| `GistMem` | Earlier favourite; dropped at owner's request (the "gist" root) |
| `CoreKnowledge` | Accurate, buyer's word ("firm knowledge") — but collides with the Core Knowledge education curriculum |
| `CoreWisdom` | ❌ Overclaims judgment the system doesn't have — same reason `TotalRecall` fails |
| `DistilledMem` | ❌ "Distillation" already means model compression; names a process; reads as CoreMem's derivative |
| `GistDB` / `The Gist` | ❌ "DB" misdescribes a markdown-first store; "The Gist" breaks the pack pattern |
| `TotalRecall` | ❌ **The film is about implanted false memories** (Rekall) — the precise opposite of an evidence-and-verification pitch. Also overclaims, names CoreMem's function (recall), breaks the pack, and `totalrecall` is taken on PyPI |

### 7.3 Recommendation — **PROVISIONAL: `SemanticMem`**

| | |
|---|---|
| Product / package / repo | **`semanticmem`** (PyPI free) — **working name, confirm or change on unpark** |
| Tagline | *SemanticMem — distilled, evidence-backed knowledge for AI agents. The semantic half of CoreMem.* |
| Pairing | **CoreMem** remembers what happened. **SemanticMem** keeps what's worth knowing. |

Owner decision (2026-09-20): proceed with **SemanticMem** as the working name; revisit at
unpark. If it is changed, §7.2's candidate table and the pack pattern in §7.1 are the
starting point — and any replacement must satisfy all six constraints at once (short,
technical, PascalCase `[role][category]`, PyPI-free, no overclaim, says "knowledge").

**Buyer-register note:** the *market* name for this capability should be institutional-memory language ("your firm's institutional memory", "tribal knowledge"), not the library's technical name. They can differ without conflict.

**Market datapoint:** `evermem` on PyPI is *"EverMem — md-first memory extraction framework"* — someone is already building markdown-first agent memory. Argues for a distinctive name over a generic one.

---

## 8. Open questions

1. Does the double-counting check (§4.2.1) invalidate the 20-question result?
2. Is the journal's lift attributable to **structure** or to **LLM spend** (`memorycore_llm_expansion` control)?
3. If the layer ships: does knowledge live in the **same HybridDB instance** as CoreMem (needed to resolve evidence versions, atomic promotion, one backup) with separate tables — or its own store?
4. ~~Does `dream()` get renamed?~~ — **decided: `replay()`** (see §5).
5. Do the four modes run within budget? Journal compilation is per-question-haystack LLM work; the June run used `deepseek-v4-flash`.
6. Is the name actually SemanticMem? Provisional until unpark (§7.3).

---

## 9. Park notice

**Parked 2026-09-20.** Nothing in this spec is scheduled. It is parked for two reasons:

1. **The precondition hasn't been met.** The eval in §4 must run first — the pipeline has never executed, so there is no basis for building anything here.
2. **Better-ordered work exists.** G5/G6 email tools, LC-5, H8, OB-1 and the D1 decision are all closer to the money path (or unblock others).

**To unpark:** run §4.1, apply the §4.2 checks, and read the §4.3 decision matrix. The
matrix is the whole point of this document: it converts "should we build a semantic memory
layer?" from an architecture argument into an observation-driven decision.

**What would justify deleting this spec entirely:** a null result at scale. That is a
legitimate and cheap outcome — and the reason the eval runs before anything is written.
