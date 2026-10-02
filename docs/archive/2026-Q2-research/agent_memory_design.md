# Designing Your Own Agent Memory System

_Last updated: 2026-06-04_

This document summarises common approaches used by current agent-memory and extraction tools, then turns them into a practical design blueprint for building your own memory system on top of stored chat messages.

The focus is not on choosing one vendor or framework. The focus is on understanding the design patterns.

---

## 1. What “Agent Memory” Means

Agent memory is not one thing. It usually combines several layers:

```text
Raw interactions
    ↓
Extraction / compression
    ↓
Structured memory
    ↓
Indexing and retrieval
    ↓
Runtime context injection
    ↓
Feedback, correction, expiry, governance
```

A useful system separates:

| Layer | Purpose |
|---|---|
| Raw message store | Preserve all original conversations and tool traces |
| Extraction layer | Convert messy text into candidate memories |
| Memory store | Keep durable facts, preferences, decisions, project knowledge |
| Retrieval layer | Find the most relevant memory for the current task |
| Runtime layer | Inject selected memories into the agent prompt or state |
| Governance layer | Allow review, deletion, contradiction handling, expiry, sensitivity control |

---

## 2. Approaches Seen in Existing Tools

### 2.1 Mem0-style memory

**Core idea:** A general-purpose memory layer for AI agents and apps.

Mem0 describes itself as a memory layer for AI agents and apps that persists context and enables personalisation across sessions. Its GitHub project describes Mem0 as a universal memory layer that remembers preferences, adapts to users, and learns over time.

Typical architecture:

```text
New user/agent interaction
    ↓
Memory extraction
    ↓
Memory consolidation / update
    ↓
Vector / graph / key-value storage
    ↓
Search relevant memories later
    ↓
Inject selected memories into response context
```

Typical memory objects:

```json
{
  "type": "preference",
  "subject": "user",
  "content": "User prefers concise technical answers.",
  "confidence": 0.91,
  "source": "conversation message IDs"
}
```

Useful design lesson:

> Treat memory as extracted, consolidated knowledge, not just raw chat chunks.

Sources:
- https://github.com/mem0ai/mem0
- https://mem0.ai/
- https://arxiv.org/abs/2504.19413

---

### 2.2 LangMem / LangGraph-style memory

**Core idea:** Long-term memory integrated into an agent framework.

LangMem provides primitives for agents to extract important information from conversations, refine behaviour, and maintain long-term memory. LangGraph distinguishes short-term thread state from long-term memory persisted across sessions and namespaces.

Common memory categories:

| Category | Meaning |
|---|---|
| Semantic memory | Facts and preferences |
| Episodic memory | Past examples, events, experiences |
| Procedural memory | Instructions, behaviour improvements, preferred workflows |

Typical architecture:

```text
Agent interaction
    ↓
Memory manager
    ↓
Store memory in namespace
    ↓
Retrieve memory during later graph execution
    ↓
Update agent behaviour or context
```

Useful design lesson:

> Separate user facts, past experiences, and behavioural instructions. They age and retrieve differently.

Sources:
- https://langchain-ai.github.io/langmem/
- https://www.langchain.com/blog/langmem-sdk-launch
- https://docs.langchain.com/oss/python/concepts/memory

---

### 2.3 LangExtract-style extraction

**Core idea:** Structured extraction from unstructured text with source grounding.

LangExtract is not a full memory system. It is an extraction layer. Its GitHub description says it extracts structured information from unstructured text using LLMs with precise source grounding and interactive visualisation.

Typical architecture:

```text
Stored messages / documents
    ↓
Extraction instructions + examples
    ↓
LLM structured extraction
    ↓
Extracted facts with source offsets
    ↓
Review / validation
    ↓
Save to your memory database
```

Example extraction schema:

```json
{
  "memory_type": "project",
  "entity": "Miss Coconut",
  "fact": "The brand is experimenting with a wellness positioning.",
  "source_message_id": "msg_456",
  "source_span": "Wellness theme for 3 months",
  "confidence": 0.87
}
```

Useful design lesson:

> Source grounding matters. Every extracted memory should be traceable to its original message.

Sources:
- https://github.com/google/langextract
- https://developers.googleblog.com/introducing-langextract-a-gemini-powered-information-extraction-library/

---

### 2.4 agentmemory-style coding-agent memory

**Core idea:** Persistent memory for coding agents through hooks, MCP, REST, and shared server state.

The `rohitg00/agentmemory` project is focused on AI coding agents. Its README says it works with agents that support hooks, MCP, or REST API, with agents sharing the same memory server. It captures what the coding agent does, compresses that activity into searchable memory, and injects the right context into future sessions.

Typical architecture:

```text
Coding agent tool call / file edit / test run / error
    ↓
Hook captures event
    ↓
Secrets stripped
    ↓
Observation compressed
    ↓
Memory indexed with keyword + vector search
    ↓
Relevant project context injected at next session
```

Typical memory examples:

```text
This repo uses PostgreSQL with SQLAlchemy.
Tests are run with uv.
Do not modify generated migration files manually.
The payment surcharge bug was caused by draft order conversion.
```

Useful design lesson:

> For coding agents, memory should capture actions and outcomes, not only chat text.

Sources:
- https://github.com/rohitg00/agentmemory
- https://github.com/rohitg00/agentmemory/blob/main/benchmark/COMPARISON.md

---

### 2.5 Graphiti / Zep-style temporal graph memory

**Core idea:** Store memories as evolving entities and relationships over time.

Graphiti describes itself as an open-source temporal context graph engine used by Zep. Zep/Graphiti focuses on turning conversations, business data, and documents into temporal context graphs: entities, relationships, and the timeline they live on.

Typical architecture:

```text
Messages + documents + business data
    ↓
Entity and relationship extraction
    ↓
Temporal graph update
    ↓
Old facts invalidated or superseded
    ↓
Hybrid retrieval:
        - vector search
        - full-text search
        - graph traversal
    ↓
Context assembled for the agent
```

Example graph facts:

```text
Eddy -> runs -> Gong Cha Australia
Gong Cha Australia -> has_store_count -> 160
Eddy -> prefers -> self-hosted open-source tools
Project POS System -> uses -> Flutter
Project POS System -> uses -> PostgreSQL
```

Temporal example:

```text
2024: User uses n8n cloud.
2025: User wants to migrate n8n to self-hosted.
2026: User has self-hosted n8n.
```

Useful design lesson:

> Some memories are not static. Good memory systems need versioning, supersession, and time validity.

Sources:
- https://github.com/getzep/graphiti
- https://www.getzep.com/platform/graphiti/
- https://arxiv.org/html/2501.13956v1

---

### 2.6 Letta / MemGPT-style stateful agent memory

**Core idea:** The agent itself manages memory as part of its operating model.

This approach is more agent-centric. Instead of adding memory to a stateless chatbot, the agent has explicit memory areas such as persona, core memory, archival memory, and conversation state.

Typical architecture:

```text
Agent has internal state
    ↓
Agent decides what to keep in active memory
    ↓
Older / larger information moved to archival memory
    ↓
Agent retrieves from archival memory when needed
    ↓
Agent can update its own persistent state
```

Useful design lesson:

> If the agent is long-running and autonomous, memory becomes part of the agent’s control loop, not just a retrieval layer.

Sources:
- https://github.com/letta-ai/letta
- https://www.letta.com/

---

## 3. Design Your Own Memory System

A practical custom design can combine the above approaches.

### 3.1 High-level architecture

```text
                            ┌────────────────────┐
                            │  Raw chat messages │
                            └─────────┬──────────┘
                                      │
                                      ▼
                            ┌────────────────────┐
                            │ Conversation       │
                            │ summarisation      │
                            └─────────┬──────────┘
                                      │
                                      ▼
                            ┌────────────────────┐
                            │ Memory extraction  │
                            │ rules + LLM        │
                            └─────────┬──────────┘
                                      │
                                      ▼
                            ┌────────────────────┐
                            │ Candidate memories │
                            └─────────┬──────────┘
                                      │
                                      ▼
          ┌─────────────────────────────────────────────────┐
          │ Deduplicate / merge / validate / classify       │
          └──────────────────────┬──────────────────────────┘
                                 │
                                 ▼
          ┌─────────────────────────────────────────────────┐
          │ Durable memory store                            │
          │ Postgres tables + pgvector + optional graph     │
          └──────────────────────┬──────────────────────────┘
                                 │
                                 ▼
          ┌─────────────────────────────────────────────────┐
          │ Retrieval and context assembly                  │
          │ semantic + keyword + recency + graph traversal  │
          └──────────────────────┬──────────────────────────┘
                                 │
                                 ▼
                            ┌────────────────────┐
                            │ Agent runtime      │
                            └────────────────────┘
```

---

## 4. Memory Types

Use explicit memory types. Do not store everything in one flat bucket.

| Type | Description | Example |
|---|---|---|
| `profile` | Stable user or organisation facts | User runs a beverage franchise business in Australia |
| `preference` | Style, tooling, workflow, product preferences | User prefers open-source/self-hosted solutions |
| `project` | Active or historical project context | User is building a POS app with Flutter |
| `decision` | Chosen direction or rejected option | User prefers Rocket.Chat over Mattermost |
| `technical_stack` | Languages, tools, infra | User uses PostgreSQL, ClickHouse, Docker, Caddy |
| `business_context` | Business-specific durable context | Gong Cha Australia has many stores and franchisees |
| `people` | Important recurring people/entities | Dr Jingyi Cao is associated with the surgical website project |
| `constraint` | Hard restrictions | Public AI tools require approval |
| `workflow` | Repeated processes | Inventory movements are recorded via stocktakes, orders, transfers |
| `episodic` | Past experience or event | A previous Shopify surcharge issue involved draft orders |
| `procedural` | How the agent should behave | When unsure about sensitive data, ask for approval before saving |

---

## 5. Suggested Database Schema

### 5.1 Raw messages

```sql
CREATE TABLE chat_messages (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);
```

### 5.2 Conversation summaries

```sql
CREATE TABLE conversation_summaries (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    summary TEXT NOT NULL,
    summary_type TEXT NOT NULL DEFAULT 'conversation',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    embedding VECTOR
);
```

### 5.3 Memories

```sql
CREATE TABLE memories (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,

    memory_type TEXT NOT NULL,
    subject TEXT,
    content TEXT NOT NULL,

    confidence NUMERIC(4,3) NOT NULL DEFAULT 0.800,
    status TEXT NOT NULL DEFAULT 'active',
    sensitivity_level TEXT NOT NULL DEFAULT 'normal',

    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,

    source_message_ids UUID[] DEFAULT '{}',
    source_conversation_ids UUID[] DEFAULT '{}',

    embedding VECTOR,
    metadata JSONB DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

### 5.4 Memory events

```sql
CREATE TABLE memory_events (
    id UUID PRIMARY KEY,
    memory_id UUID NOT NULL REFERENCES memories(id),
    event_type TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB,
    source_message_id UUID,
    created_at TIMESTAMPTZ NOT NULL
);
```

Event types:

```text
created
updated
merged
contradicted
superseded
archived
deleted
user_confirmed
user_rejected
expired
```

### 5.5 Memory conflicts

```sql
CREATE TABLE memory_conflicts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    memory_id_a UUID NOT NULL REFERENCES memories(id),
    memory_id_b UUID NOT NULL REFERENCES memories(id),
    conflict_type TEXT NOT NULL,
    resolution_status TEXT NOT NULL DEFAULT 'unresolved',
    created_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ
);
```

---

## 6. Extraction Pipeline

### 6.1 Batch extraction from stored chat

For existing stored messages:

```text
For each conversation:
    1. Load messages.
    2. Create or update conversation summary.
    3. Extract candidate memories.
    4. Attach source message IDs.
    5. Classify memory type and sensitivity.
    6. Compare against existing memories.
    7. Insert, merge, supersede, or discard.
```

### 6.2 Real-time extraction

For new messages:

```text
On conversation end, or every N messages:
    1. Run lightweight extraction.
    2. Save candidates.
    3. Mark high-risk memories for review.
    4. Make active only after confidence/safety checks.
```

### 6.3 Extraction prompt skeleton

```text
Extract long-term useful memories from the conversation.

Include only:
- stable user facts
- durable preferences
- recurring projects
- business context
- technical stack
- decisions and constraints
- important recurring people/entities
- workflows likely to be useful in future conversations

Exclude:
- one-off questions
- temporary facts
- uncertain guesses
- sensitive personal information unless the user explicitly asked to remember it
- information from text the user asked to rewrite or translate

Return JSON:
[
  {
    "memory_type": "profile | preference | project | decision | technical_stack | business_context | people | constraint | workflow | episodic | procedural",
    "subject": "...",
    "content": "...",
    "confidence": 0.0,
    "sensitivity_level": "normal | personal | sensitive",
    "source_message_ids": ["..."],
    "valid_from": null,
    "valid_to": null,
    "expires_at": null,
    "reason": "why this is durable"
  }
]
```

---

## 7. Deduplication and Consolidation

Before inserting a memory:

```text
1. Search existing memories by embedding similarity.
2. Search existing memories by subject/type keywords.
3. Check if the new memory:
   - duplicates an existing memory
   - refines an existing memory
   - contradicts an existing memory
   - supersedes an older memory
   - should be discarded as temporary
```

### 7.1 Merge example

Old memory:

```text
User is using n8n cloud.
```

New memory:

```text
User wants to migrate n8n cloud to self-hosting.
```

Merged memory:

```text
User has used n8n cloud and is interested in migrating to self-hosting.
```

### 7.2 Supersession example

Old memory:

```text
User is considering Mattermost.
```

New memory:

```text
User prefers Rocket.Chat over Mattermost.
```

Action:

```text
Archive or supersede old memory.
Keep the decision memory active.
```

---

## 8. Retrieval Design

Memory retrieval should not be simple top-k vector search only.

Use a hybrid ranking formula:

```text
score =
    semantic_similarity * 0.40
  + keyword_match * 0.20
  + subject_match * 0.15
  + recency_score * 0.10
  + confidence_score * 0.10
  + user_confirmed_bonus * 0.05
```

Adjust weights by task type.

### 8.1 Retrieval stages

```text
User request
    ↓
Classify request:
    - general answer
    - coding task
    - business task
    - personal preference
    - legal/medical/high-stakes
    - document recall
    ↓
Retrieve candidate memories
    ↓
Filter by:
    - active status
    - sensitivity rules
    - expiry
    - relevance threshold
    ↓
Compress selected memories
    ↓
Inject into agent context
```

### 8.2 Context injection format

```text
Relevant user memory:
- User prefers open-source/self-hosted tools where practical.
- User is comfortable with Python, Dart, Flutter, PostgreSQL, Docker and Caddy.
- User manages a beverage franchise network in Australia.
- User prefers practical implementation steps over abstract theory.
```

Keep injected memory short. Do not dump the full memory table into the prompt.

---

## 9. Coding-Agent Memory

If designing for coding agents, store more than chat messages.

Capture:

| Event | Example |
|---|---|
| File edits | `app/models/order.py` changed surcharge logic |
| Test runs | `pytest tests/test_orders.py` failed due to decimal rounding |
| Errors | Migration failed because column type mismatch |
| Fixes | Converted Decimal to float before JSON serialisation |
| Architectural decisions | SQLite for local OLTP, DuckDB for analytics |
| Commands | `uv run pytest` is the standard test command |
| Repo conventions | Use Caddy for reverse proxy |

Suggested table:

```sql
CREATE TABLE agent_observations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    project_id UUID,
    repo_path TEXT,
    agent_name TEXT,
    event_type TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    files_touched TEXT[],
    command TEXT,
    success BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);
```

Then compress observations into memories:

```text
Raw observation:
pytest failed because surcharge calculation used pre-discount amount.

Compressed memory:
In this repo, surcharge must be calculated after discounts and before final order total rounding.
```

---

## 10. Optional Graph Memory

Add graph memory when flat memories become too limited.

### 10.1 Graph entities

```text
User
Business
Project
Repository
Tool
Person
Store
Database
Table
Workflow
Decision
```

### 10.2 Graph relationships

```text
USER_PREFERS_TOOL
USER_RUNS_BUSINESS
PROJECT_USES_TECH
PROJECT_HAS_DECISION
PERSON_ASSOCIATED_WITH_PROJECT
MEMORY_SUPERSEDES_MEMORY
WORKFLOW_HAS_STEP
```

### 10.3 Temporal fields

Every graph fact should support:

```text
valid_from
valid_to
observed_at
source_message_id
confidence
```

This allows the system to answer:

```text
What is true now?
What used to be true?
What changed?
Why did we believe this?
```

---

## 11. Sensitivity and Governance

A memory system needs explicit controls.

### 11.1 Sensitivity classes

| Level | Meaning | Default behaviour |
|---|---|---|
| `normal` | Business, technical, general preferences | Can save automatically |
| `personal` | Family, personal logistics, named people | Save cautiously |
| `sensitive` | Health, legal, political, religion, sexuality, precise location, children | Do not save unless explicitly requested |

### 11.2 User controls

Minimum controls:

```text
Show all memories
Search memories
Edit memory
Delete memory
Disable memory
Export memory
Show source messages
Confirm / reject candidate memories
```

### 11.3 Auditability

Every memory should answer:

```text
Where did this come from?
When was it created?
When was it last updated?
Was it inferred or explicitly stated?
Was it confirmed by the user?
Has it been contradicted?
```

---

## 12. Memory Lifecycle

```text
candidate
    ↓
active
    ↓
confirmed / user_confirmed
    ↓
updated / merged
    ↓
superseded / archived
    ↓
deleted / expired
```

Status values:

```text
candidate
active
needs_review
confirmed
rejected
superseded
archived
deleted
expired
```

---

## 13. Evaluation

Measure memory quality before trusting it.

### 13.1 Extraction metrics

| Metric | Question |
|---|---|
| Precision | Of saved memories, how many are actually useful and true? |
| Recall | Of useful durable facts, how many did we capture? |
| Sensitivity error rate | Did we save something we should not have saved? |
| Duplication rate | Are we saving the same thing repeatedly? |
| Contradiction handling | Did we resolve changed facts correctly? |

### 13.2 Retrieval metrics

| Metric | Question |
|---|---|
| Relevance@k | Are retrieved memories relevant to the current task? |
| Noise rate | How often do irrelevant memories enter the prompt? |
| Freshness | Does the system prefer newer, valid facts? |
| Token efficiency | How many tokens are spent on memory? |
| Answer improvement | Does memory improve the final response? |

### 13.3 Human review set

Create a test set:

```text
100 conversations
manually labelled durable memories
expected active memories
expected rejected memories
expected contradictions
expected retrieval results for 50 user queries
```

---

## 14. Minimal Viable Build

### Phase 1: Basic memory extraction

```text
- Store raw messages
- Summarise conversations
- Extract candidate memories with an LLM
- Save memories to Postgres
- Store source message IDs
- Add manual review UI
```

### Phase 2: Retrieval

```text
- Add embeddings with pgvector
- Retrieve memories per user query
- Inject top relevant memories into prompt
- Add keyword search fallback
```

### Phase 3: Memory management

```text
- Deduplicate memories
- Merge related memories
- Supersede outdated memories
- Add expiry
- Add user edit/delete controls
```

### Phase 4: Agent memory

```text
- Capture tool calls
- Capture code edits and test outcomes
- Compress observations into project memories
- Retrieve project memories for coding agents
```

### Phase 5: Graph memory

```text
- Extract entities and relationships
- Add temporal validity
- Use graph traversal for multi-hop context
- Combine graph, vector, and full-text retrieval
```

---

## 15. Example End-to-End Flow

### Input message

```text
For future Shopify surcharge issues, remember that BOLD can create draft orders and convert them into normal orders, and the surcharge app may not apply if the order bypasses checkout.
```

### Extracted memory

```json
{
  "memory_type": "workflow",
  "subject": "Shopify BOLD surcharge issue",
  "content": "BOLD custom pricing may create draft orders and convert them into normal orders; surcharge apps may not apply if the order bypasses normal checkout.",
  "confidence": 0.96,
  "sensitivity_level": "normal",
  "source_message_ids": ["msg_001"],
  "expires_at": null
}
```

### Retrieval later

User asks:

```text
Why was surcharge missing on that BOLD order?
```

Injected memory:

```text
Relevant memory:
- BOLD custom pricing may create draft orders and convert them into normal orders; surcharge apps may not apply if the order bypasses normal checkout.
```

---

## 16. Implementation Notes

### 16.1 Storage

Good default stack:

```text
PostgreSQL
pgvector
JSONB metadata
optional ClickHouse for analytics
optional graph DB later
```

### 16.2 Extraction model

Use a cheaper model for normal extraction. Use a stronger model for:

```text
- conflict resolution
- sensitive memory classification
- summarising long conversations
- extracting legal/medical/business-critical context
```

### 16.3 Retrieval

Use hybrid retrieval:

```text
- vector search over memories
- full-text search over memory content
- recency and confidence ranking
- source-aware filtering
- optional graph traversal
```

### 16.4 Runtime memory budget

Example budget:

```text
System prompt: fixed
Recent conversation: 40%
Task-specific docs/files: 30%
Retrieved memories: 10-15%
Tool outputs: 15-20%
```

Do not let memory consume the whole context.

---

## 17. Key Design Principles

1. **Keep raw messages forever if policy allows, but do not use raw history as memory directly.**
2. **Extract durable memories, not every fact.**
3. **Every memory needs a source.**
4. **Separate semantic, episodic, and procedural memory.**
5. **Use hybrid retrieval, not vector search only.**
6. **Treat coding-agent memory differently from user-profile memory.**
7. **Track time validity and supersession.**
8. **Give users memory controls.**
9. **Classify sensitive information before saving.**
10. **Measure precision, recall, relevance, and noise.**

---

## 18. Reference Architecture

```text
                 ┌─────────────────────┐
                 │    Chat frontend    │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │   Message service   │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ PostgreSQL messages │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ Extraction worker   │
                 └──────────┬──────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌────────────────┐
│ Summary table │   │ Memory table  │   │ Observation log │
└───────┬───────┘   └───────┬───────┘   └────────┬───────┘
        │                   │                    │
        └──────────┬────────┴──────────┬─────────┘
                   ▼                   ▼
          ┌────────────────┐   ┌─────────────────┐
          │ pgvector index │   │ optional graph   │
          └────────┬───────┘   └────────┬────────┘
                   │                    │
                   └──────────┬─────────┘
                              ▼
                    ┌───────────────────┐
                    │ Retrieval service │
                    └─────────┬─────────┘
                              ▼
                    ┌───────────────────┐
                    │ Agent / Assistant │
                    └───────────────────┘
```

---

## 19. Final Mental Model

Use this distinction:

```text
LangExtract-style:
    Extract structured facts from text.

Mem0-style:
    Store and retrieve useful user/agent memories.

LangMem-style:
    Integrate semantic, episodic, and procedural memory into agent workflows.

agentmemory-style:
    Capture coding-agent actions and outcomes through hooks/MCP.

Graphiti/Zep-style:
    Represent evolving facts as a temporal knowledge graph.

Your own system:
    Use extraction + structured storage + hybrid retrieval + governance.
```

The most important architectural choice is not the library. It is the memory contract:

```text
What is allowed to be remembered?
How is it extracted?
Where is the source?
How is it updated?
How is it retrieved?
How can the user correct or delete it?
```
