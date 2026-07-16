# LLM Wiki Integration Plan

## Background

**LLM Wiki** is a concept introduced by Andrej Karpathy where an LLM maintains a structured, interlinked markdown knowledge base that compounds over time rather than re-searching raw documents on every query. It's a write-once, update-incrementally model — the opposite of RAG, where synthesis happens at query time.

### Core Idea

```
raw/        → Immutable source material (write once, never overwrite)
wiki/       → Curated markdown pages maintained by the LLM, interlinked with [[wikilinks]]
Schema      → Instructions telling the LLM how to ingest, query, and lint
```

### LLM Wiki vs RAG

| Approach | Knowledge lives in | Synthesis happens | Good for |
|----------|-------------------|-------------------|----------|
| RAG | Raw chunks + embeddings | At query time | Broad retrieval across large corpora |
| LLM Wiki | Curated markdown pages | During ingest/maintenance | Compounding knowledge, summaries, durable cross-links |

### Key Operations

1. **Ingest** — Collect a source into `raw/`, compile it into `wiki/`, update cross-references
2. **Query** — Search the wiki, answer with citation-backed `[[wikilinks]]`
3. **Lint** — Check broken wikilinks, stale pages, orphan pages, contradictions

---

## Why This Matters for Assistant

The EA already has conversation history, memory, file management, and per-user isolation. But there's a gap:

**Knowledge evaporates between sessions.** Conversations are stored but never synthesized. Memory facts exist but don't cross-reference. A new document about Adyen should automatically update the payment architecture page — but nothing does that today.

An LLM Wiki makes the Assistant's knowledge **compound**:

- A conversation about Stripe rate limits → updates the Stripe entity page and the rate-limiting concept page
- A new pricing document → flags contradictions with the old one, adds a comparison
- An email about a project deadline → updates the project entity and cross-references the email source

The wiki is the durable artifact that gets richer with every interaction.

### Coexistence with Existing Memory System

The EA already has a memory pipeline (`MemoryMiddleware` + `upsert_fact_memory`) that extracts behavioral patterns and stores structured facts. The LLM Wiki is an additional layer — it does not replace the memory system. They serve different purposes:

| What | Stored in | Good for |
|------|-----------|----------|
| **Memory facts** | HybridDB (structured) — trigger/action/confidence/domain | Behavioral patterns ("user prefers X"), atomic recall ("what's Jordan's email?") |
| **Wiki pages** | Filesystem (markdown) — frontmatter + body + `[[wikilinks]]` | Human-readable synthesis ("payment architecture overview"), cross-referenced narratives, audit trail |

**They complement each other:**

1. **MemoryMiddleware continues extracting facts.** It handles the high-volume, low-touch pattern detection (preferences, habits, recurring data) that the wiki shouldn't need manual curation for.
2. **Wiki pages synthesize what memory knows.** A wiki entity page for Stripe can reference the relevant memory facts as source material, combining them into a coherent narrative.
3. **No duplication of extraction logic.** The wiki skill does NOT re-implement fact extraction — it reads memory facts via `memory_search` as one of its source types.
4. **Frontmatter links back to memory.** Wiki pages can include a `memory_facts` field in frontmatter referencing fact keys, creating a bidirectional link between the two systems:
   ```yaml
   memory_facts:
     - fact:stripe_preferred_provider
     - fact:jordan_email
   ```

**When a source is ingested:**
1. MemoryMiddleware processes the conversation and extracts facts → HybridDB (existing, automatic)
2. The wiki skill writes/updates markdown pages synthesizing what was learned (manual trigger in v1, automatic in v2)

When the agent reads a wiki page, the `memory_facts` frontmatter tells it what structured facts back the page's claims. When the agent queries memory, the wiki provides the narrative context those facts live in.

---

## Decision: Option C → Option B

Three possible architectures:

| Option | Storage | Pros | Cons |
|--------|---------|------|------|
| **A** | Filesystem wiki + HybridDB index | Best of both | Two stores, sync step, drift risk |
| **B** | Filesystem wiki + HybridDB accelerator | Single source of truth (filesystem), vector+graph+FTS built-in, rebuildable from filesystem | Requires DB tables, indexing step, write-through sync |
| **C** | Filesystem only (pure Karpathy) | Dead simple, zero new infra | No vector search, no graph queries |

**Decision: Start with Option C, graduate to Option B when needed.**

**Reasoning:**
- Option C adds zero new infrastructure — markdown files served by existing file tools
- The skill is the product in v1, not the storage engine
- If the concept proves valuable, Option B adds HybridDB for queries the LLM can't answer in-context (e.g., "show me every entity connected to payment processing across all conversations")
- Filesystem remains source of truth even in Option B — HybridDB is an accelerated query layer, not a replacement

---

## Phase 1: Schema & Layout

Per-user directory under existing `data/users/{user_id}/`:

```
data/users/{user_id}/wiki/
├── raw/                     # Immutable sources (write-once, never overwrite)
│   ├── conversations/       # Extracted highlights from conversations
│   │   └── 2026-05-09-q3-planning.md
│   ├── documents/           # Uploaded PDFs, docs, spreadsheets
│   │   └── pricing-v2.pdf.md
│   └── web/                 # Scraped URLs, search results
│       └── 2026-05-09-karpathy-gist.md
├── index.md                 # Auto-maintained table of contents
├── log.md                   # Append-only operation log (every ingest recorded)
├── concepts/                # Abstract ideas, terminology, patterns
│   └── rate-limiting.md
├── entities/                # People, projects, companies, tools, products
│   ├── stripe.md
│   └── adyen.md
├── sources/                 # One page per raw/ source, with summary + metadata
│   └── 2026-05-09-pricing-doc.md
├── syntheses/               # Multi-source summaries, architectural overviews
│   └── payment-architecture.md
├── questions/               # Questions asked + answers with citations
│   └── why-not-stripe-billing.md
└── comparisons/             # Side-by-side analyses
    └── stripe-vs-paddle-vs-adyen.md
```

### Page Format

Every page is plain markdown with YAML frontmatter:

```markdown
---
type: entity
title: Stripe
confidence: 0.85
status: reviewed
created: 2026-05-09T10:00:00Z
updated: 2026-05-09T14:30:00Z
sources:
  - raw/documents/pricing-doc.md
  - raw/conversations/2026-05-09-q3-planning.md
tags: [payment, infrastructure]
related:
  - concepts/rate-limiting
  - entities/adyen
  - syntheses/payment-architecture
---

# Stripe

## Summary
Stripe is the primary payment processor used across all products...

## Key Facts
- Processes $X/month in transaction volume [source: raw/documents/pricing-doc.md]
- Rate limited at 100 req/s per API key [source: raw/conversations/2026-05-09-q3-planning.md]
- Supports 135+ currencies

## Decisions
- Chose Stripe over Adyen in Q2 2026 for better developer experience

## Open Questions
- Should we migrate to Stripe Billing for subscription management?

## See Also
- [[adyen]] — payment competitor evaluated in Q2
- [[payment-architecture]] — how providers fit together
- [[rate-limiting]] — Stripe's API constraints
```

### Frontmatter Fields

| Field | Required | Values |
|-------|----------|--------|
| `type` | yes | `concept`, `entity`, `source`, `synthesis`, `question`, `comparison` |
| `title` | yes | Human-readable title |
| `confidence` | yes | 0.0–1.0 based on source count, source quality, recency, cross-references |
| `status` | yes | `draft`, `reviewed`, `verified`, `stale`, `archived` |
| `created` | yes | ISO 8601 timestamp |
| `updated` | yes | ISO 8601 timestamp |
| `sources` | no | Array of paths to `raw/` files |
| `tags` | no | Lowercase, kebab-case |
| `related` | no | Array of `[[wikilink]]` paths |

### Design Principles

1. **Raw is immutable.** New versions of a file get a new timestamped entry. Overwriting raw sources destroys the audit trail.
2. **Wikilinks use relative paths.** `[[entities/stripe]]` not `[[/wiki/entities/stripe]]`. Portable across users.
3. **Confidence decays.** Pages not updated in 90 days become `stale`. Confidence drops with fewer sources or older sources.
4. **Every page has sources.** No unsourced claims in the wiki. Every key fact links back to `raw/`.
5. **Git per-user.** Each user's wiki is its own git repo at `data/users/{user_id}/wiki/.git`, maintaining per-user isolation and version history.
6. **`index.md` works up to ~200 pages.** Beyond that, the LLM can't effectively scan the index in one read. At this scale, graduate to Option B where hybrid search replaces index scanning. The threshold is approximate — if the agent struggles with index-driven queries, it's time for Option B.

---

## Phase 2: The Skill

A single `llm-wiki` skill that teaches the agent three operations using **existing file tools** — no new code required.

### Ingest

```
User: "Save this conversation about Stripe rate limits to the wiki"
  or "Ingest this document: <path or URL>"

Agent:
  1. Write source to raw/<category>/<timestamp>-<slug>.md
  2. Read existing wiki pages that might be affected
  3. Write/update wiki pages (entity, concept, synthesis)
  4. Add [[wikilinks]] to cross-reference
  5. Update wiki/index.md with new/updated pages
  6. Append to wiki/log.md: timestamp, operation, files changed
```

File tools used: `files_write`, `files_read`, `files_edit`

### Query

```
User: "What do we know about our payment infrastructure?"

Agent:
  1. Read wiki/index.md for relevant pages
  2. Read wiki/concepts/payment-*.md, wiki/entities/*pay*.md
  3. Follow [[wikilinks]] to related pages
  4. Answer with inline citations: [[entities/stripe]]
  5. Flag if any page is stale (status: stale, confidence < 0.5)
```

File tools used: `files_read`, `files_glob_search`, `files_grep_search`

### Lint

```
User: "Lint the wiki"
  or run automatically after ingest

Agent:
  1. Parse all frontmatter for validity
  2. Check every [[wikilink]] resolves to an existing page
  3. Flag orphan pages (not linked from index.md or any other page)
  4. Flag stale pages (status: stale or updated > 90 days ago)
  5. Flag duplicate content (same entity described in multiple pages)
  6. Check index.md is accurate (lists all pages, no dead links)
  7. Report results with fix suggestions
```

File tools used: `files_read`, `files_glob_search`, `files_grep_search`

### Multi-Format Output

Beyond text answers with citations, the wiki can generate richer artifacts filed back as new pages:

| Format | When to use | Tool |
|--------|------------|------|
| Comparison table | "Compare Stripe vs Adyen" | Markdown table in `comparisons/*.md` |
| Slide deck | "Turn the payment architecture into a presentation" | Marp (markdown-based slides) |
| Chart | "Show confidence distribution across entity pages" | matplotlib (embedded as image in wiki) |

### Skill File Location

```
src/skills/llm_wiki/SKILL.md
```

Contains:
- Full ingest/query/lint workflows with examples
- Page format spec
- Confidence scoring rules
- Lifecycle state machine (draft → reviewed → verified → stale → archived)
- Template pages for each type (entity, concept, synthesis, etc.)

---

## Phase 3: Conversation Feeding

How conversations become wiki knowledge:

### v1: Manual Trigger

```
User: "Remember this for the wiki"
```

Agent extracts the relevant facts, decisions, preferences from the current conversation context and runs the ingest workflow.

### v2: Auto-Extraction (Future)

Hooks into the existing `MemoryMiddleware.after_agent()` callback (fires after every agent turn):

1. After a session ends, `after_agent()` writes the conversation transcript to `raw/conversations/`
2. On the next user message, the agent checks `raw/conversations/` for unprocessed sources
3. If found, the agent reads the source, extracts highlights, and runs the standard ingest workflow
4. The source file is marked as processed (appending `status: ingested` to frontmatter)

The existing memory extraction is NOT replaced — `MemoryMiddleware` continues extracting behavioral facts. The auto-extraction here produces narrative wiki pages from the same source material.

### questions/ Directory

The `wiki/questions/` directory stores "Questions asked + answers with citations." Both the question and the answer are filed as a single page:

```markdown
---
type: question
title: "Why not use Stripe Billing?"
asked: 2026-05-09T14:30:00Z
related:
  - entities/stripe
  - syntheses/payment-architecture
---
# Why not use Stripe Billing?

## Answer
We evaluated Stripe Billing in Q2 2026 but chose to stay with custom invoicing because...

## Sources
- [[payment-architecture]] — §Decisions
- [[stripe-vs-paddle]] — §Billing comparison
```

This is triggered manually ("save this answer to the wiki") or automatically when the agent generates a citation-backed answer longer than 2 paragraphs. The threshold prevents every one-line response from becoming a wiki page.

---

## Phase 4: Sources That Feed In

| Source | Trigger | Format in raw/ |
|--------|---------|----------------|
| Conversation highlights | Manual (v1) / Auto (v2) | Markdown with speaker labels |
| User-uploaded files | Manual | PDFs → markdown, spreadsheets → tables |
| Web pages | Manual (via `scrape_url`) | Cleaned markdown |
| Email threads | Manual | Extracted body + metadata |
| Memory facts | Auto? | Structured fact list |

---

## Phase 5: Path to Option B (HybridDB)

When filesystem-only hits its natural limits — can't answer questions like "show me every entity connected to payment processing across all conversations" or "find the most stale concepts" without reading every file — graduate to HybridDB.

### What Changes

1. **New HybridDB table:** `wiki_pages` per user
   ```sql
   CREATE TABLE wiki_pages (
       path TEXT PRIMARY KEY,
       type TEXT,
       title TEXT,
       confidence REAL,
       status TEXT,
       frontmatter JSON,
       created TEXT,
       updated TEXT,
       body TEXT  -- FTS5 indexed
   );
   ```

2. **Index on startup:** Walk `wiki/` directory, upsert all pages into HybridDB

3. **Write-through:** Every `files_write` to `wiki/` also indexes into HybridDB via the journal

4. **New capabilities:**
   - Vector search across wiki content (ChromaDB)
   - Graph traversal: "find all entities related to Stripe" (recursive CTE or NetworkX)
   - Analytics: "confidence distribution by type" (DuckDB)
   - Hybrid search: keyword + semantic + recency scoring

5. **Filesystem remains source of truth.** HybridDB is an accelerated query layer — can always be rebuilt from the filesystem with a reindex.

### What Stays the Same

- Markdown files on disk (git-friendly, LLM-readable, editor-friendly)
- Page format (frontmatter + body)
- Ingest/query/lint workflows (tools get faster, not different)

---

## Phase 6: Graph View

Once wiki pages are indexed in HybridDB (Option B), render the `[[wikilinks]]` as an Obsidian-style interactive graph in the HTTP dashboard.

### Data Model

The graph already exists — every `[[wikilink]]` in a wiki page IS an edge. The `[[entities/stripe]]` link from `syntheses/payment-architecture.md` creates a directed edge between those two nodes. The `related` frontmatter field also creates edges.

**Extraction strategy (two options):**

| Option | How | Used by |
|--------|-----|---------|
| **Inline wikilinks** | Parse `[[path]]` from page body during index | Karpathy's approach — wikilinks are in markdown text |
| **Frontmatter `related`** | Read `related: [entities/stripe, concepts/rate-limiting]` from YAML | Structured, machine-parseable |

**Recommendation: parse inline wikilinks from body at index time and store as structured edges in `_graph_edges`.** This makes the graph queryable without re-parsing markdown on every request, while keeping the source format human-writable.

### HybridDB Changes

1. **New graph sync rule** during Phase 5 indexing:
   ```python
   db.register_entity_node("wiki_pages", type="wiki_page", id_column="path")
   db.register_edge_rule("wiki_pages", "wiki_pages", 
       source_column="path", target_column="path", edge_type="wikilink")
   ```
   Wait — wikilinks are directional, but `register_edge_rule` uses JOIN conditions. The correct approach: parse wikilinks at index time and call `db.add_edge()` directly. This gives correct directional edges and preserves the source→target relationship.

2. **New method** on HybridDB: `graph_export(focus, depth, types, limit) → {nodes, edges}`

3. Nodes inherit metadata from wiki frontmatter: type (concept/entity/synthesis), confidence, status, tags.

### FastAPI Endpoint

```
GET /wiki/graph?focus=entities/stripe&depth=2&types=entity,concept&limit=300
```

Returns pre-filtered nodes and edges in D3-friendly format:
```json
{
  "nodes": [
    {"id": "entities/stripe", "label": "Stripe", "type": "entity", "confidence": 0.85, "group": 2},
    ...
  ],
  "edges": [
    {"source": "syntheses/payment-architecture", "target": "entities/stripe", "type": "wikilink"},
    ...
  ]
}
```

### Frontend

A new route in the HTTP dashboard (`/wiki/graph`) with:

| Feature | Implementation |
|---------|---------------|
| Force-directed layout | D3.js force simulation |
| Node size | Confidence (higher = bigger) |
| Node color | Type (entity=#4A90D9, concept=#50C878, synthesis=#FF6B6B, etc.) |
| Edge thickness | Page count (more links = thicker) |
| Click node | Focus mode — show 1-2 hop neighbors around clicked node |
| Search bar | Type to find a page, graph centers on it |
| Filter panel | Toggle page types, min confidence, depth slider |
| Global/local toggle | "Global" shows all indexed pages; "Local" shows N hops from focused node |

### Testing

- Verify 3-hop subgraph around a known page matches expected neighbors
- Verify global graph shows all indexed pages
- Verify filter by type excludes non-matching nodes
- Verify clicking a node refocuses the graph correctly

---

## Execution Order

| Step | Effort | What | Success Criteria |
|------|--------|------|------------------|
| 1. Schema spec | Small | Document page format, frontmatter fields, directory conventions | Clear template any agent can follow |
| 2. Skill file | Medium | Write `SKILL.md` with ingest/query/lint prompts and examples | Agent follows instructions correctly |
| 3. Ingest test | Small | Feed 1 real conversation, verify wiki pages look right | Pages have correct frontmatter, working wikilinks |
| 4. Query test | Small | Ask 5 pre-written questions, verify citations are correct | Answers cite actual wiki pages, no hallucinations |
| 5. Lint test | Small | Run lint, verify it catches broken links and stale pages | Lint finds at least 80% of intentionally planted issues |
| 6. Iterate | Small | Fix what the agent gets wrong on steps 3–5 | Steps 3–5 pass consistently |
| 7. Auto-ingestion | Medium | Hook `after_agent()` to write transcripts → `raw/conversations/` | New conversations produce raw/ sources without user intervention |
| 8. HybridDB index | Medium | Add wiki_pages table, indexing, vector search, graph queries | Query "show me entities connected to X" returns correct results |
| 9. Graph view | Medium | `GET /wiki/graph` endpoint + D3.js force-directed frontend | Local graph (3-hop), global graph, filter by type, click-to-focus all work |

---

## What This Costs vs What It Gives

### Cost (Steps 1–6)
- 1 skill file
- 0 new tools
- 0 new storage infrastructure
- 0 new dependencies
- ~2 days to spec + build + test

### Gain
- Knowledge that compounds across sessions instead of evaporating
- The agent builds a second brain that improves with every conversation
- Queryable, cited knowledge base (no more "what did we decide about X?")
- Foundation for personalized, context-aware responses
- Human-readable audit trail of what the agent knows and why

### What Option B Adds (Steps 7–8)
- ~2 more days
- Semantic search ("find concepts similar to this one")
- Graph queries ("what entities are connected to payment?")
- Analytics ("which areas of knowledge are stale?")
- Scales to thousands of wiki pages without reading every file

### What the Graph View Adds (Step 9)
- ~1-2 more days
- Obsidian-style interactive graph in the HTTP dashboard
- Click-to-focus local graph view (1-3 hops)
- Global graph with type-based coloring and confidence-based sizing
- Filter by page type, min confidence, search by title
- All built on existing HybridDB graph infrastructure — no new graph engine needed
