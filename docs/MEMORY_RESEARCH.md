# Memory System Reference Research

Deep analysis of four memory system references, compiled for comparison with our Assistant memory design.

---

## 1. claude-mem (thedotmack/claude-mem)

**Stars**: 47.7k | **Language**: TypeScript | **License**: AGPL-3.0 | **Version**: v12.1.0

### What It Is

A Claude Code plugin that automatically captures everything Claude does during coding sessions, compresses it with AI, and injects relevant context back into future sessions. Built with Claude Agent SDK.

### Architecture

**Core Components**:
1. **5 Lifecycle Hooks**: SessionStart, UserPromptSubmit, PostToolUse, Summary, SessionEnd
2. **Worker Service**: HTTP API on port 37777 with web viewer UI and 10+ search endpoints (Bun-managed)
3. **SQLite Database**: Stores sessions, observations, summaries
4. **ChromaDB**: Hybrid semantic + keyword search
5. **mem-search Skill**: Natural language queries with progressive disclosure

### Data Model

**Observations** (rich structured records):
- `id`, `memory_session_id`, `project`, `text`
- `type`: decision | bugfix | feature | refactor | discovery | change
- `title`, `subtitle`, `narrative` (LLM-extracted)
- `facts`: JSON array of fact strings
- `concepts`: JSON array of concept strings
- `files_read`, `files_modified`: JSON arrays of file paths
- `prompt_number`, `discovery_tokens` (ROI metric)
- `created_at`, `created_at_epoch`

**Session Summaries**:
- `request`, `investigated`, `learned`, `completed`, `next_steps`
- `files_read`, `files_edited`, `notes`
- Links to `memory_session_id` and `project`

### Key Design Decisions

1. **Hook-based capture (100% deterministic)**: Uses PreToolUse/PostToolUse hooks, not probabilistic skill firing. Every tool call is observed.

2. **AI-powered compression**: The SDK Agent (or Gemini/OpenRouter fallback) processes observations into structured records. The LLM extracts title, subtitle, facts, narrative, concepts.

3. **Progressive disclosure context injection**:
   - **3-layer workflow**: `search` (compact index, ~50-100 tokens) → `timeline` (chronological context) → `get_observations` (full details, ~500-1000 tokens)
   - **~10x token savings** by filtering before fetching details
   - Configurable observation count, type filters, concept filters
   - Discovery tokens metric tracks ROI of memory investment

4. **Granular vector indexing**: Each semantic field (narrative, facts, concepts) becomes a **separate ChromaDB document** with rich metadata, enabling field-level semantic search.

5. **Project-scoped with multi-project support**: Each project gets its own ChromaDB collection (`cm__<project_name>`). Sessions track which project they belong to.

6. **Backfill and sync strategy**: On startup, compares SQLite vs ChromaDB and syncs missing documents. Delete+add strategy for idempotency.

7. **Session tracking**: `sdk_sessions` table tracks active/completed/failed sessions. `user_prompts` records every prompt with ordinal positioning. `transcript_events` captures raw JSON for full replay.

8. **Private content filtering**: `<private>` tags in user messages exclude sensitive content from storage.

### Search Architecture

- **Structured search** (SessionSearch): Direct SQLite queries with project, type, date, concept, file path, folder filters. Uses `json_each` for JSON array queries.
- **Semantic search** (ChromaSync): Vector search via MCP protocol. Per-project collections. Granular documents per field.
- **SearchManager**: Orchestrates both, deduplicates by `sqlite_id`.

---

## 2. everything-claude-code Instincts Pattern (affaan-m/everything-claude-code)

### What It Is

A pattern for giving AI coding agents persistent, learned behaviors across sessions. The "instincts" system is the evolution of their continuous learning feature (v2.1).

### Core Concept

An **instinct** is an atomic trigger-action pair stored as YAML with structured metadata. Unlike skills (static, human-written), instincts are **dynamically learned** from session observations.

### Instinct File Format

```yaml
---
id: prefer-functional-style
trigger: "when writing new functions"
confidence: 0.7
domain: "code-style"
source: "session-observation"
scope: project
project_id: "a1b2c3d4e5f6"
project_name: "my-react-app"
---

# Prefer Functional Style

## Action
Use functional patterns over classes when appropriate.

## Evidence
- Observed 5 instances of functional pattern preference
- User corrected class-based approach to functional on 2025-01-15
```

### Key Properties

| Property | Description |
|-----------|-------------|
| **Atomic** | One trigger, one action — never combines multiple behaviors |
| **Confidence-weighted** | Score 0.3 (tentative) → 0.9 (near-certain) |
| **Domain-tagged** | Categorized: code-style, testing, git, debugging, workflow |
| **Evidence-backed** | Tracks which observations led to the instinct |
| **Scope-aware** | `project` (default) or `global` |

### Confidence Lifecycle

| Score | Meaning | Behavior |
|-------|---------|----------|
| 0.3 | Tentative | Suggested but not enforced |
| 0.5 | Moderate | Applied when relevant |
| 0.7 | Strong | Auto-approved |
| 0.9 | Near-certain | Core behavior |

**Increases when**: pattern repeatedly observed, user doesn't correct, similar instincts agree.
**Decreases when**: user explicitly corrects, pattern not observed for extended periods, contradicting evidence.

### Project Scoping (v2.1)

- **Project-scoped** (default): React patterns stay in React projects, Python in Python projects
- **Global instincts**: Universal behaviors like "always validate input"
- **Auto-promotion**: When same instinct appears in 2+ projects with avg confidence ≥0.8, candidate for global promotion via `/promote`

### File Structure

```
~/.claude/homunculus/
├── identity.json           # User profile, technical level
├── projects.json           # Registry: project hash → name/path/remote
├── observations.jsonl      # Global observations
├── instincts/
│   ├── personal/           # Global auto-learned instincts
│   └── inherited/          # Global imported instincts
├── evolved/                # Clustered into skills/commands/agents
└── projects/
    └── <project-hash>/
        ├── project.json
        ├── observations.jsonl
        └── instincts/
            ├── personal/
            └── inherited/
```

### Hook-Based Capture (v2)

v1 used skills (fired probabilistically, ~50-80%). v2 uses **CLI hooks** (PreToolUse/PostToolUse) which fire 100% of the time, deterministically. Every tool call is observed.

A **background observer agent** (using a cheaper model like Haiku) analyzes observations and creates/updates instinct YAML files.

### Instinct Evolution Pipeline

```
Session Activity → Hooks capture prompts + tool use (100%)
                      ↓
         projects/<hash>/observations.jsonl
                      ↓
         Observer agent reads (background, Haiku)
                      ↓
         PATTERN DETECTION
           • User corrections → instinct
           • Error resolutions → instinct
           • Repeated workflows → instinct
           • Scope decision: project or global?
                      ↓
         Instinct YAML files (per-project or global)
                      ↓
         /evolve clusters + /promote
                      ↓
         Evolved artifacts: commands, skills, agents
```

### Management Commands

- `/instinct-status` — Show all instincts with confidence
- `/evolve` — Cluster related instincts into skills/commands, suggest promotions
- `/instinct-export/import` — Share instincts between projects
- `/promote [id]` — Promote project instincts to global scope

---

## 3. Google Always-On Memory Agent

**Repo**: GoogleCloudPlatform/generative-ai/gemini/agents/always-on-memory-agent
**Language**: Python | **Framework**: Google ADK | **Model**: Gemini 3.1 Flash-Lite
**License**: MIT

### What It Is

An always-on AI memory agent that continuously processes, consolidates, and serves memory. Runs 24/7 as a lightweight background process. **No vector database. No embeddings. Just an LLM that reads, thinks, and writes structured memory.**

### Architecture

**Multi-Agent Design** (using Google ADK):
1. **IngestAgent** — Processes raw text, images, audio, video, PDFs into structured memory
2. **ConsolidateAgent** — Finds connections, patterns, contradictions between memories (runs on timer, default 30min)
3. **QueryAgent** — Answers questions using stored memories + consolidation insights
4. **Orchestrator** — Routes requests to the right specialist agent

### Data Model (SQLite)

**Memories**:
- `id`, `source`, `raw_text`, `summary`
- `entities` (JSON array), `topics` (JSON array), `connections` (JSON array of `{linked_to, relationship}`)
- `importance` (float 0.0-1.0)
- `consolidated` (boolean flag)
- `created_at`

**Consolidations**:
- `id`, `source_ids` (JSON array of memory IDs), `summary`, `insight`
- `created_at`

**Processed Files**:
- `path`, `processed_at` — tracks which files have been ingested

### Key Design Decisions

1. **No vector database**: Deliberately avoids embeddings/ChromaDB. Uses Gemini's multimodal capabilities for ingestion and the LLM itself for retrieval (reads all memories, synthesizes answers).

2. **LLM-as-memory-engine**: The LLM reads all memories when answering queries. The `query_agent` calls `read_all_memories()` and `read_consolidation_history()`, then synthesizes an answer with source citations. This is "memory through attention" — the model simply attends to all stored context.

3. **Multimodal ingestion**: Supports 27 file types including images, audio, video, PDFs via Gemini's multimodal input.

4. **Scheduled consolidation**: Runs on a configurable interval (default 30 min). Only triggers when ≥2 unconsolidated memories exist. The consolidate agent:
   - Reviews unconsolidated memories
   - Finds connections between them
   - Generates cross-cutting insights
   - Compresses related information into consolidated summaries

5. **Connections graph**: Memories have a `connections` field storing `{linked_to: id, relationship: string}` — a lightweight graph structure where the LLM defines the edge labels.

6. **Importance scoring**: Each memory has a 0.0-1.0 importance score set by the ingest agent, used for prioritization.

7. **File watcher**: Drops files in `./inbox/`, agent auto-processes within 5-10 seconds.

8. **HTTP API**: `/query`, `/ingest`, `/consolidate`, `/memories`, `/delete`, `/clear`, `/status`

9. **Streaming dashboard**: Streamlit UI for visual browsing and deletion of memories.

### Ingest Agent Prompt (key excerpt)

```
You are a Memory Ingest Agent. You handle ALL types of input.
1. Thoroughly describe what the content contains
2. Create a concise 1-2 sentence summary
3. Extract key entities
4. Assign 2-4 topic tags
5. Rate importance from 0.0 to 1.0
6. Call store_memory with all extracted information
```

### Consolidation Agent Prompt (key excerpt)

```
You are a Memory Consolidation Agent.
1. Call read_unconsolidated_memories
2. If fewer than 2 memories, say nothing to consolidate
3. Find connections and patterns across the memories
4. Create a synthesized summary and one key insight
5. Call store_consolidation with source_ids, summary, insight, and connections
Connections: list of dicts with 'from_id', 'to_id', 'relationship' keys.
Think deeply about cross-cutting patterns.
```

### Key Differences from Other Systems

- **No embeddings = no retrieval system**: Query agent reads ALL memories each time (scales to ~hundreds, not thousands)
- **Single model for everything**: Gemini 3.1 Flash-Lite is cheap and fast enough to run 24/7
- **Agent tools are the database interface**: `store_memory`, `read_all_memories`, `read_unconsolidated_memories`, `store_consolidation`, `read_consolidation_history`, `get_memory_stats`, `delete_memory`, `clear_all_memories`
- **Consolidation creates new artifacts**: Not just merging duplicates — generates new insights and connections

---

## 4. Supermemory (Dhravya Shah / supermemory.ai)

### What It Is

A universal memory layer for LLMs that provides infinite context with sub-400ms latency. Started as a bookmark manager, evolved into a RAG toolkit, now a drop-in memory layer. Raised $3M from OpenAI, Google, DeepMind, Meta executives.

### Core Architecture Principles

Inspired by human memory — doesn't store everything perfectly, applies intelligent forgetting and decay:

1. **Smart Forgetting & Decay**: Less relevant information gradually fades. Important, frequently-accessed content stays sharp. No drowning in irrelevant context.

2. **Recency & Relevance Bias**: Recent and frequently-referenced content gets priority. Mirrors the brain's tendency to surface what's actually useful right now.

3. **Context Rewriting & Broad Connections**: Continuously updates summaries and finds links between seemingly unrelated information. That random insight from last month might be exactly what you need today.

4. **Hierarchical Memory Layers**:
   - **Hot/Working memory**: Recent, instantly accessible (Cloudflare KV)
   - **Deep/Long-term memory**: Retrieved when needed, not before

### Products Built on Top

1. **Memory as a Service**: Storing and querying multimodal data at scale. API: `/add`, `/connect`, `/search`. Supports external connectors (Google Drive, Notion, OneDrive).

2. **Supermemory MCP**: Model-interoperable MCP server for carrying memories across LLM apps without losing context. Works with Claude.ai, editors, etc.

3. **Infinite Chat API**: Manages memories inline with conversation history, sending only what's needed to model providers. Claims 90% token savings while improving performance.

### Technical Approach

- **Not just vector DB**: Explicitly calls out that vector databases get too expensive/slow at scale, and graph databases require traversing too many edges
- **Cloudflare infrastructure**: Uses Cloudflare KV for hot memory, Workers for compute
- **Drop-in integration**: Replace your LLM provider endpoint with Supermemory's, and memory is handled automatically
- **Semantic & non-literal queries**: Handles metaphors, ambiguity, and general questions requiring knowledge of the entire dataset

### Key Innovation

The system emphasizes that memory isn't just retrieval — it's about:
- **What to forget** (decay, irrelevance filtering)
- **What to prioritize** (recency, frequency)
- **What to connect** (context rewriting, cross-referencing)
- **What to send** (token-efficient context management)

---

## Comparison Matrix

| Aspect | Our System | claude-mem | ECC Instincts | Google Always-On | Supermemory |
|--------|-----------|------------|---------------|-----------------|-------------|
| **Storage** | SQLite + ChromaDB | SQLite + ChromaDB | YAML files (filesystem) | SQLite only | Cloudflare KV + vector DB |
| **Capture method** | Keyword trigger + LLM extraction (daemon thread) | 5 lifecycle hooks (100% deterministic) | CLI hooks (100% deterministic) | File watcher + HTTP API + LLM ingestion | API-based (MCP, /add) |
| **Extraction** | LLM (after keyword detection) | LLM (Agent SDK, always runs) | Observer agent (Haiku, background) | Gemini Flash-Lite (always) | LLM-based (managed service) |
| **Memory shape** | trigger + action + domain + confidence + type | observations (title, subtitle, facts, narrative, concepts, files) | trigger + action + confidence + domain + scope + evidence | summary + entities + topics + connections + importance | Abstract (managed, not disclosed) |
| **Search** | FTS5 keyword + ChromaDB vector + hybrid (weighted) | SQLite structured + ChromaDB per-field vectors + MCP | YAML file matching (no built-in search) | LLM reads all memories (no search index) | Managed hybrid search |
| **Consolidation** | Periodic (every N messages), LLM-based contradiction detection | Continuous (on every tool use) + session summaries | Background observer pattern detection + /evolve clustering | Timer-based (30 min), agent finds connections + insights | Continuous decay + rewriting (managed) |
| **Context injection** | Working memory (confidence ≥0.3) injected into system message | Progressive disclosure via SessionStart hook | YAML files loaded at session start | LLM reads all at query time | Infinite Chat API manages what to send |
| **Progressive disclosure** | No — injects all working memory at once | 3-layer: search → timeline → full details | Instincts loaded by scope (project/global) | N/A — reads all at once | Token-managed context window |
| **Confidence/deacy** | 0.2-0.7 range, decays by 0.1 every 30 days, capped at 0.7 | Implicit via recency ordering + token economics | 0.3-0.9, explicit increase/decrease rules | 0.0-1.0 importance score (no decay mentioned) | Smart forgetting + recency bias (managed) |
| **Superseding** | Old memory → superseded_by new memory | No superseding (old observations retained) | Confidence decrease, no superseding | Consolidation marks memories as "consolidated" | Decay + rewriting (managed) |
| **User management** | No tools for list/delete/edit | Web viewer UI for browse/delete | /instinct-status, /instinct-export/import | Dashboard UI + HTTP API for delete/clear | API + dashboard |
| **Correction handling** | Detects correction keywords, updates existing memory | Observations accumulate (no update path) | Confidence decrease on correction, new instinct created | Observations accumulate | Managed |
| **Project scoping** | Per-user (data/users/{user_id}/) | Per-project (git worktree aware) | Per-project + global with auto-promotion | Single database (no multi-tenancy) | Per-user (managed) |
| **Async safety** | Daemon thread (broken — see bug report) | Worker service (Bun, proper async) | Background observer (separate process) | asyncio (proper event loop) | Managed service |
| **Connection between memories** | linked_to field (JSON array of IDs) | By session_id, project grouping | By domain tagging, evolve clustering | connections field ({linked_to, relationship}) | Abstract (managed) |
| **Insights** | Separate insights table (broken — FTS table missing) | Session summaries with discovery_tokens | Evolved artifacts (skills, commands, agents) | Consolidations with cross-cutting insight | Context rewriting (managed) |