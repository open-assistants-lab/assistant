# Plan: Facts as First-Class Graph Nodes (Option B)

> Date: 2026-05-03
> Context: The memory graph currently only has `memories` rows as nodes. Individual facts in `memory_facts` are a flat SQLite index. This gap prevents graph-boosted fact search (entity-level centrality, graph-expanded recall, connected-fact ranking).

---

## 1. Current State (Why This Matters)

```
Current architecture:

memories (graph nodes) ←── _graph_sync ← register_entity_node("memories", type="memory")
  │                          (line 167 of storage/memory.py)
  │  id = "abc123"
  ├── trigger: "user's job title"
  ├── action: "Senior Backend Engineer"
  └── linked_to: [def456, ghi789]  ← edges between memories

memory_facts (NO graph) ←── NOT registered in _graph_sync
  │
  │  id = "abc123"          ← Same as memory_id (SHA256 hash)
  ├── fact_key = "user:job_title"
  ├── entity: "user"
  ├── attribute: "job_title"
  ├── value: "Senior Backend Engineer"
  ├── previous_value: null  ← Link to superseded fact
  └── memory_id: "abc123"   ← FK to memories.id

graph_sync registrations:
  ✅ memories → type=memory
  ❌ memory_facts → NOT registered
  ❌ No edge rules exist (only explicit memory_connect calls create edges)
```

Consequences:
- `search_facts` does flat FTS5 only, zero graph awareness
- Cannot ask "what else do we know about user?" with graph centrality scoring
- Entity-level queries require separate SQL scan (not graph-operable)
- Correction chains are tracked in data but invisible to graph algorithms

---

## 2. Target Architecture

```
memory_facts (graph nodes, type="fact") ←── register_entity_node("memory_facts", type="fact")
  │
  │  Node ID: id (SHA256, same as memory_id)
  │  Label: "fact:{fact_key}"  e.g. "fact:user:job_title"
  │  
  ├── [belongs_to] ──→ memories (parent memory node)
  │     Edge rule: memory_facts.memory_id = memories.id
  │
  ├── [updates] ──→ older fact node (if previous_value is set)
  │     Edge rule: memory_facts.previous_value references older fact's memory_id
  │
  └── [same_entity] ──→ other facts about same entity (Phase 2)
        Query-time filter: WHERE entity = ? (no pre-built edges needed)

Search flow:
  1. FTS5 on {entity, attribute, value} → fact rows (3 queries, ~15ms)
  2. Resolve to graph nodes → pagerank() for centrality scores
  3. neighbors(fact_node, direction="both") → parent memory → connected facts
  4. Rank: text_relevance * 0.5 + centrality * 0.2 + edge_weight * 0.2 + recency * 0.1
```

---

## 3. Implementation Plan

### Phase 1: Register Facts as Nodes + Memory Bridge Edges

#### Step 1.1 — Register `memory_facts` for graph sync

**File**: `src/storage/memory.py`, after line 220 (after `memory_facts` table creation + indexes)

```python
# Register memory_facts for graph sync — makes facts searchable via graph algorithms
self.db.register_entity_node(
    "memory_facts",
    type="fact",
    id_column="id",                       # Uses SHA256 hash (same as memory_id)
    label_template="fact:{fact_key}",     # Readable identifier
)
```

**Notes**:
- Uses `memory_facts.id` as node ID (unique per fact row, SHA256 hash)
- `fact_key` is used for the label only (e.g., `"fact:user:job_title"`)
- Existing `_auto_sync_graph_nodes()` at `hybrid_db.py:1180` will create a graph node for each row
- Called via `reconcile()` which runs `_auto_sync_graph_nodes()` + `_auto_sync_graph_edges()`

#### Step 1.2 — Create fact-to-memory edge rule

**File**: `src/storage/memory.py`, after Step 1.1 registration

```python
# Connect each fact to its parent memory — enables fact→memory→connected_facts traversal
self.db.register_edge_rule(
    source_table="memory_facts",
    target_table="memories",
    target_match="memory_id = id",        # memory_facts.memory_id JOIN memories.id
    edge_type="belongs_to",
)
```

**How it works**:
- `_auto_sync_graph_edges()` at `hybrid_db.py:1203` runs this rule via:
  ```sql
  SELECT s.id as sid, t.id as tid 
  FROM memory_facts s 
  JOIN memories t ON s.memory_id = t.id
  ```
- Creates one directed edge: `fact_node → memory_node`
- Weight defaults to 1.0 (can add weight based on confidence in Phase 2)

#### Step 1.3 — Create fact-to-fact correction edge rule

**File**: `src/storage/memory.py`, after Step 1.2

```python
# Connect superseded facts to their replacements via fact_key chain
# When a fact is corrected (upserted with same fact_key, new value):
#   old_fact.previous_value = "old_value"
#   new_fact.previous_value = "old_value" (carries the old value forward)
# The edge connects new_fact.id → old_fact.id where they share the same fact_key
self.db.register_edge_rule(
    source_table="memory_facts",
    target_table="memory_facts",
    target_match="fact_key = fact_key AND previous_value IS NOT NULL AND s.id != t.id AND s.updated_at > t.updated_at",
    edge_type="updates",
)
```

**Caveat**: The current `_auto_sync_graph_edges` generates edges for all matching pairs. For `updates` edges, we need to ensure only the most recent correction chain is created, not all historical pairs. The `s.updated_at > t.updated_at` clause ensures directionality (new → old). Testing needed to verify correctness with multi-correction chains (A→B→C should create B→A and C→B, not C→A).

#### Step 1.4 — Rewrite `search_facts` with graph boost

**File**: `src/storage/memory.py:768` (replace current implementation)

```python
def search_facts(self, query: str, limit: int = 8, graph_expand: int = 2) -> list[tuple[Memory, float]]:
    """Search facts with graph-boosted ranking.
    
    1. Fast FTS5 text search on entity/attribute/value only (3 queries, not 10)
    2. Resolve to graph nodes, get PageRank centrality
    3. Expand via neighbors (belongs_to → parent memory → connected memories → back to facts)
    4. Rank: text_score * 0.5 + centrality * 0.2 + edge_weight * 0.2 + recency * 0.1
    
    Args:
        query: Search query (matched against fact entity, attribute, value)
        limit: Max results
        graph_expand: Number of graph expansion hops (default 2: fact→memory→connected_fact)
    """
    results: dict[str, tuple[Memory, float]] = {}
    
    # ── 1. Fast text recall: FTS5 on entity, attribute, value only ──
    seen_ids: set[str] = set()
    for col in ("entity", "attribute", "value"):
        for row_id, score in self.db._fts_search("memory_facts", col, query, limit * 3):
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
            row = self.db.get("memory_facts", row_id)
            if row is None:
                continue
            memory = self.get_memory(row["memory_id"])
            if memory is None or memory.is_superseded:
                continue
            results[row["id"]] = (memory, score)
            if len(results) >= limit * 2:
                break
    
    # ── 2. Fallback: scan-based search if FTS5 missed ──
    if not results:
        fallback = self._find_facts_fallback(query, limit * 2, include_superseded=False)
        for mem in fallback:
            fact_row = self.db.get("memory_facts", mem.id)
            if fact_row:
                results[fact_row["id"]] = (mem, 0.3)
    
    if not results:
        return []
    
    # ── 3. Graph boost: get centrality scores for fact nodes ──
    try:
        centrality = self.db.pagerank()
    except Exception:
        centrality = {}
    
    # ── 4. Graph expand: neighbors → parent memory → connected facts ──
    expanded: dict[str, tuple[Memory, float]] = {}
    for node_id, (memory, text_score) in results.items():
        score = text_score
        score += centrality.get(node_id, 0) * 0.2  # graph centrality boost
        
        if graph_expand > 0:
            try:
                neighbors = self.db.neighbors(node_id, direction="out")
                for neighbor_id in neighbors[:graph_expand * 2]:
                    if neighbor_id not in results:
                        neighbor = self.get_memory(neighbor_id)
                        if neighbor and not neighbor.is_superseded:
                            expanded[neighbor_id] = (neighbor, score * 0.5)  # lower score by 50%
            except Exception:
                pass
        
        expanded[node_id] = (memory, score)
    
    # ── 5. Rank and return ──
    ranked = sorted(expanded.items(), key=lambda x: x[1][1], reverse=True)
    
    output = []
    for node_id, (memory, score) in ranked[:limit]:
        self._boost_access(memory.id)
        output.append((memory, score))
    
    return output
```

#### Step 1.5 — Trigger graph sync after `upsert_fact_memory`

**File**: `src/storage/memory.py:736` (after `_upsert_fact_row` call)

After inserting a new fact row, trigger incremental graph sync so the new fact node is immediately available:

```python
# After _upsert_fact_row() at line 736:
# Graph nodes are auto-synced during reconcile(). For real-time availability,
# create the fact node immediately:
try:
    fact_node_id = memory_id  # Same as memory_facts.id
    self.db.add_node(
        fact_node_id,
        label=f"fact:{fact_key}",
        type="fact",
        source="fact_insert",
    )
except Exception:
    pass  # Node already exists or graph unavailable
```

**Alternative**: Keep relying on `reconcile()` for batch sync, and accept eventual consistency. The `add_node` in `_auto_sync_graph_nodes` uses `INSERT OR REPLACE` so it's idempotent.

---

### Phase 2: Entity Virtual Nodes (Future)

#### Step 2.1 — Create entity nodes

For each distinct entity value in `memory_facts`, create a graph node:

```python
def _sync_entity_nodes(self) -> int:
    """Create virtual entity nodes for entity-level graph operations."""
    entities = self.db.raw_query("SELECT DISTINCT entity FROM memory_facts")
    created = 0
    for row in entities:
        entity = row["entity"]
        entity_id = f"entity:{entity}"
        self.db.add_node(entity_id, label=entity, type="entity", source="auto_sync")
        created += 1
    return created
```

#### Step 2.2 — Create fact-to-entity edges

```python
self.db.register_edge_rule(
    source_table="memory_facts",
    target_table="memory_facts",  # Technical: entity nodes are stored in _graph_nodes
    target_match="entity = entity",  # THIS WON'T WORK with current edge rule mechanism
    edge_type="belongs_to_entity",
)
```

**Problem**: Edge rules operate on SQLite tables, but entity nodes are virtual (in `_graph_nodes` only, no entity table). Need either:
- Create a real `_entities` table (synced from distinct entity values)
- Or create edges explicitly in `upsert_fact_memory` via `self.db.add_edge(fact_id, f"entity:{entity}", type="belongs_to_entity")`

The explicit edge creation in `upsert_fact_memory` is simpler and avoids the edge rule complexity.

#### Step 2.3 — Entity-level queries

```python
def search_facts_by_entity(self, entity: str, limit: int = 20) -> list[tuple[Memory, float]]:
    """Get all facts about an entity, ranked by PageRank centrality."""
    facts = self.db.query("memory_facts", where="entity = ?", params=(entity,))
    
    centrality = self.db.pagerank()
    results = []
    for row in facts:
        memory = self.get_memory(row["memory_id"])
        if memory and not memory.is_superseded:
            score = centrality.get(row["id"], 0)
            results.append((memory, score))
    
    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]
```

---

## 4. Edge Rule Design Rationale

### Why Not Same-Entity Edges?

All-pairs entity edges (create edge between every fact pair sharing the same entity) would be O(n²). For 100 facts about "user", that's 9,900 edges. For 1000 facts, it's ~500k edges. This would make `_auto_sync_graph_edges` prohibitively expensive and bloat the graph.

**Better approach**: Entity is a **query-time filter**, not a pre-built edge. Same-entity facts are discovered via:
```sql
SELECT * FROM memory_facts WHERE entity = ?
```
This is instant (indexed) and doesn't require pre-building edges. Entity-level graph algorithms (PageRank per-entity) can be computed by filtering the full PageRank result to nodes where `memory_facts.entity = 'user'`.

### Why Only Two Edge Rules?

| Rule | Type | Cost | Value |
|------|------|------|-------|
| fact → memory | `belongs_to` | 1 edge per fact | Bridges fact graph to memory graph. Essential for traversal. |
| fact → older fact | `updates` | 1 edge per correction | Makes correction chains graph-visible. Enables "latest fact" queries via out-degree. |

These two rules provide the entire traversal path: `any_fact ↔ parent_memory ↔ all_connected_memories ↔ their_facts`. Combined with entity-as-query-filter, this gives entity-scoped graph operations without O(n²) edges.

### Edge Weights

| Edge Type | Default Weight | Rationale |
|-----------|---------------|-----------|
| `belongs_to` | 1.0 | Direct relationship, always relevant |
| `updates` | 0.8 | Correction chain, slightly lower than direct connection |

Weights can be tuned via `register_edge_rule` if a `weight` parameter is added to the method (not currently supported — would need a schema change to `_edge_rules`).

---

## 5. Testing Plan

### New tests in `tests/sdk/test_memory_facts_graph.py`

| Test | What It Verifies |
|------|-----------------|
| `test_facts_registered_as_graph_nodes` | After `reconcile()`, `pagerank()` includes fact node IDs |
| `test_fact_to_memory_edge` | `neighbors(fact_id, direction="out")` returns the parent memory ID |
| `test_correction_chain_edge` | After correcting a fact (upsert with same `fact_key`), an `updates` edge connects new fact to old fact |
| `test_graph_boosted_search` | `search_facts` returns results with graph-expanded facts not directly matching the FTS5 query |
| `test_entity_query_time_filter` | `search_facts_by_entity("user")` returns all facts about user, sorted by centrality |
| `test_no_column_explosion` | `search_facts` only does 3 FTS5 queries (entity, attribute, value), not 10 |
| `test_search_performance` | With 200 facts, `search_facts` completes in <50ms (down from 80-163ms) |

### Existing tests that must still pass
- `tests/sdk/test_hybrid_db.py` — all graph tests
- `tests/sdk/test_memory.py` — all memory store tests
- `tests/sdk/test_memory_store.py` — all fact store tests

---

## 6. Files Modified

| File | Lines Changed | Change |
|------|--------------|--------|
| `src/storage/memory.py:220` | +2 | Register `memory_facts` for graph sync |
| `src/storage/memory.py:222` | +6 | Register fact→memory edge rule |
| `src/storage/memory.py:228` | +8 | Register correction chain edge rule |
| `src/storage/memory.py:768-792` | ~80 | Rewrite `search_facts` with graph boost |
| `src/storage/memory.py:736` | +8 | Immediate graph node creation after `upsert_fact_memory` |
| `tests/sdk/test_memory_facts_graph.py` | ~200 | New test file (7 tests) |

**Total**: ~304 lines added/changed across 3 files.

---

## 7. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `_auto_sync_graph_edges` becomes slow with many facts | Medium | Edge rules create O(n) edges (1 per fact), not O(n²). Test with 10k facts. |
| `updates` edge rule creates wrong edges for multi-correction chains | Low | Test with 3-level correction chain (A→B→C) to verify edge directionality |
| `pagerank()` becomes slower with all facts as nodes | Low | PageRank is O(nodes + edges). 1k facts + 2k edges is negligible. Test scaling. |
| Existing `_auto_sync_graph_edges` may be called on stale data before facts are inserted | Low | `reconcile()` already runs `_auto_sync_graph_nodes` before `_auto_sync_graph_edges`. Ordering is correct. |
| `search_facts` return type change breaks callers | Medium | Current callers: `find_facts_for_query()` (line 553), deprecation tool (line 1012), migration (line 977-1000). Need compat layer. |

---

## 8. Rollout

1. **Phase 1a**: Register facts as nodes + memory bridge edge (Steps 1.1, 1.2) — no search changes yet
2. **Phase 1b**: Add correction chain edge rule (Step 1.3) — verify with existing test suite
3. **Phase 1c**: Rewrite `search_facts` with graph boost (Step 1.4) — behind feature flag
4. **Phase 1d**: Remove feature flag, enable graph-boosted search by default
5. **Phase 2** (future): Entity virtual nodes + entity-level PageRank

Feature flag for Phase 1c:
```python
GRAPH_FACTS_ENABLED = os.environ.get("GRAPH_FACTS_ENABLED", "true").lower() in {"1", "true", "yes"}
```
