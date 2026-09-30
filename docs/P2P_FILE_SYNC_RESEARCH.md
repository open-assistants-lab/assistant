# P2P File Sync for Single-User Multi-Device — Research & Evaluation

**Status:** Research only. No implementation.
**Date:** 2026
**Question:** For a single user with multiple devices, should our file sync mechanism consider peer-to-peer (P2P) sync?

---

## 1. Executive Summary

**Short answer: No — not for the current architecture, and not as a "file sync mechanism."**

The transport (P2P vs. central server) is not the hard problem here. The hard problem is that this project's data is **not plain files** — it is live SQLite databases (WAL mode), a ChromaDB vector index, and a file tree. P2P file-sync tools like Syncthing are explicitly designed for plain files and are **unsafe for live databases** (their own maintainers say so). The correct way to sync SQLite across devices is a CRDT-based replication layer (e.g. cr-sqlite), which is a data-model change, not a sync-mechanism change — and ChromaDB has no equivalent at all.

For a single user with 2–4 devices, the theoretical benefits of P2P (no single point of failure, no server, privacy) mostly evaporate:

- The **server is already required** in this architecture — it runs the agent loop, LLM calls, subagents, scheduled tasks, and memory. It is not a sync relay; it is the runtime.
- P2P still needs an **always-on node** for availability (the "closed laptop" problem — if the only copy is on a device that's off, nothing syncs). That always-on node is a server by another name.
- The P2P "swarm" advantage (parallel download from many peers) is meaningless at N=2–4 devices.

**What the actual gap is:** offline support and bidirectional file sync against the existing server (the `FileCache` `cloud_only/downloaded/pinned` model is a partial start). That gap is solved with a client-side sync queue + delta protocol against the server — not by replacing the transport with P2P.

**When P2P *would* make sense:** if the product vision changes to a **local-first assistant** — each device runs its own full agent + local store, and devices sync state via CRDTs, with the server demoted to a "cloud peer" (backup/relay, not source of truth). That is a rewrite of the data layer, not a sync mechanism swap. If that vision is on the table, the research below maps the path.

---

## 2. What "file sync" means in this project today

From `DEPLOYMENT.md` and `DATA_ARCHITECTURE.md`:

- **Mode 2 (Solo WAN)** is the single-user multi-device story: *one server = sessions and files in sync everywhere*. Clients are thin viewers over REST/SSE/WebSocket. "Multi-device sync is a property of having one server, not of any client-side sync engine."
- **The server is the single source of truth.** Per-user stores under `ea_root`: `Conversation/messages.db`, `Memory/` (ChromaDB), `Files/`, `Email/emails.db`, `Contacts/contacts.db`, `Todos/todos.db`, `Subagents/work_queue.db`, `.versions/`.
- **One process per user store.** SQLite/ChromaDB are single-writer per user. "Do not run multiple replicas serving the same user's data."
- **Known gap (from DEPLOYMENT.md):** "Offline/sync: server-authoritative only. No client-side offline queue or bidirectional file sync yet (file cache with `cloud_only/downloaded/pinned` statuses is partially built)."
- `src/http/workspace_cache.py` implements that partial model: a JSON cache tracking `cloud_only` / `downloaded` / `pinned` per path, with a `has_update` flag derived from server mtime.

So the question "should we consider P2P for file sync?" is really: **should we replace or augment the server-authoritative model with device-to-device sync?**

---

## 3. What P2P file sync actually is (the landscape)

| Option | Model | Maturity | Fit for this project |
|---|---|---|---|
| **Syncthing** | P2P, block-level delta sync, TLS, NAT traversal (STUN/UPnP/hole-punching), device-ID pairing, MPL-2.0 | Very mature (68k+ stars, 10+ years) | Best-in-class for **plain files** between own devices. Explicitly **not** for databases (see §4). |
| **IPFS / libp2p** | Content-addressed DHT, static-file oriented | Mature but wrong shape | Designed for immutable content; mutable data (IPNS/pubsub) is immature. Not a file-sync tool (no encryption, no auto-add, no conflict handling). |
| **Dat / Hypercore** | Append-only signed logs, P2P | Research-grade (Ink & Switch used it) | Good for mutable data but effectively unmaintained as a general platform. |
| **Briar / SSB / Matrix** | F2F / group-based messaging sync | Niche | Messaging-shaped, not file-shaped. |
| **cr-sqlite (vlcn.io)** | CRDT extension for SQLite — multi-master, per-column LWW, changesets | Working, MIT, 3.7k stars | The *correct* way to sync SQLite across devices — but a data-model change (§5). |
| **sqlite-sync (SQLite Cloud)** | CRDT sync for SQLite, block-level LWW for text | Working, but Elastic License 2.0 | License + requires their cloud backend; not OSS-friendly for this project. |

Key structural fact about P2P: **two peers can only sync while both are online.** Every serious P2P deployment therefore includes an always-on node (a home server, a VPS, a NAS) — Syncthing users run it on a Pi; PushPin calls it a "storage peer"; Ink & Switch calls it a "cloud peer." For a single user, that node is the server. P2P does not remove the server; it relocates and re-roles it.

---

## 4. The core problem: this project's data is not plain files

P2P file sync (Syncthing-class tools) works by hashing files, diffing blocks, and copying whole files between peers. That is safe for documents, photos, code. It is **unsafe for live databases**, for three independent reasons:

### 4.1 SQLite WAL mode cannot be file-synced safely

- SQLite in WAL mode keeps committed data in a separate `-wal` file that is checkpointed back into the main DB asynchronously. Copying the DB without its WAL (or mid-checkpoint) produces a corrupt or stale database. SQLite's own corruption guide lists "backup or restore while a transaction is active" and "mispairing database files and hot journals" as corruption vectors.
- SQLite uses `mmap` for the WAL index; **mmap writes do not update mtime**, so a file-sync tool often doesn't even detect that the DB changed (a documented Syncthing failure mode).
- SQLite is **single-writer**. Two devices writing the same DB file and syncing the file is undefined behavior — the Syncthing maintainers' exact words: *"if you are thinking of doing bidirectional syncing of SQLite files (i.e., open on both sides and synced in both directions), this will break spectacularly no matter what sort of locking we implement. Do not do that."* And: *"Syncthing is not for syncing databases, databases are not supposed to be synced."*

### 4.2 ChromaDB has no safe live-copy story

`DEPLOYMENT.md` already states it: Chroma's HNSW index files "are not transactionally safe to copy live." The current backup guidance is *stop the server, copy, restart*. A P2P sync engine copying ChromaDB directories between devices would be copying a live, single-writer index — the same corruption class as SQLite, with no WAL-style recovery.

### 4.3 The single-writer invariant is architectural, not incidental

The project's design decision is "one process per user store" with single-writer SQLite/ChromaDB. That invariant is what makes the current system simple and correct. P2P file sync would require **multi-writer** semantics on every store — which is a distributed-systems problem (conflict resolution, causal ordering, tombstones), not a file-copy problem.

**Conclusion of §4:** P2P file sync can only ever be applied to the `Files/` tree (user documents) — and even there, only if the DBs and index are excluded. It cannot be the sync mechanism for the data that actually matters.

---

## 5. The local-first alternative: CRDT-based sync (what P2P would actually require)

The research literature (Ink & Switch's *Local-first software*, PushPin, and the CRDT ecosystem) is unambiguous: **P2P is the transport; CRDTs are the merge layer.** If you want devices to write independently and converge, you need conflict-free replicated data types, not a file copier. The viable path for SQLite is **cr-sqlite** (vlcn.io, MIT): a loadable SQLite extension that turns tables into conflict-free replicated relations (CRRs) with per-column last-write-wins, causal-length deletes, and a `crsql_changes` virtual table for exchanging changesets between peers.

What adopting it would cost:

| Dimension | Cost |
|---|---|
| **Schema constraints** | CRR tables need non-nullable PKs, no `AUTOINCREMENT`, no unique indexes, defaults on all `NOT NULL` columns. Every existing table (`messages`, `emails`, `contacts`, `todos`, `work_queue`, app tables) must be audited and migrated. |
| **Write performance** | Local writes to CRRs are ~2.5× slower than plain SQLite (trigger + clock-table overhead). Reads are unchanged. |
| **Application semantics** | LWW per column is a blunt instrument. Message append-only logs merge fine; but counters, ordered lists, and "delete vs. resurrect" need deliberate CRDT choice per table. |
| **ChromaDB** | **No CRDT equivalent exists.** The vector index cannot be replicated this way. It would have to be rebuilt per device (re-embedding) or kept server-only — which reintroduces the server dependency. |
| **Agent runtime** | The agent loop, subagents, work queue, and scheduled tasks are server-side. A device-local agent is a different product. |
| **Migration** | This is a data-layer rewrite with a long tail of edge cases (schema evolution across versions, partial sync, tombstone GC). |

The ecosystem alternatives don't rescue it: `sqlite-sync` is Elastic License 2.0 and requires SQLite Cloud's backend; CouchDB/PouchDB requires application-level conflict resolution and is widely considered impractical for fine-grained data.

**Conclusion of §5:** CRDT-based sync is the *only* correct way to get offline multi-writer SQLite across devices — and it is a major architectural change with a hard blocker (ChromaDB) and a product-model question (device-local agents). It is not a "file sync mechanism" decision.

---

## 6. Evaluation: P2P vs. the alternatives for this project

Scored against what a single-user multi-device assistant actually needs: **correctness of the data, offline behavior, operational simplicity, and fit with the existing architecture.**

| Criterion | A. Central server (current) | B. P2P file sync (Syncthing) | C. CRDT multi-master (cr-sqlite) | D. Hybrid: server + client sync queue |
|---|---|---|---|---|
| **Data correctness** | ✅ Single source of truth, single-writer | ❌ Unsafe for SQLite/ChromaDB; conflicts on files | ✅ Convergent by design (for SQLite) | ✅ Server remains authoritative |
| **Offline writes** | ❌ None today (gap) | ✅ For plain files only | ✅ Full multi-writer | ✅ Queued, applied on reconnect |
| **Offline reads** | ❌ Thin clients | ✅ Full local copies | ✅ Full local copies | ✅ Cached/pinned files (FileCache model) |
| **Conflict handling** | N/A (no concurrent writers) | ❌ Rename-conflict copies | ✅ Automatic (LWW etc.) | ✅ Server wins; last-write-wins per file |
| **ChromaDB across devices** | ✅ Server-only | ❌ Unsafe to copy | ❌ No CRDT for vectors | ✅ Server-only (unchanged) |
| **Always-on requirement** | ✅ Server is the runtime anyway | ❌ Still needs an always-on node | ❌ Still needs a cloud peer | ✅ Server is the runtime anyway |
| **Operational complexity** | Low (already deployed) | Medium (pairing, NAT, discovery) | **High** (schema migration, semantics, GC) | Low–medium (one new client module) |
| **Privacy / no-server** | ❌ Needs a server | ✅ (but see always-on) | ✅ (but needs cloud peer) | ❌ Needs a server |
| **Fit with agent runtime** | ✅ | ❌ Sync only, runtime untouched | ❌ Implies device-local agents | ✅ |
| **Effort** | — | Medium (integration + exclusions) | **Very high** (data-layer rewrite) | Low–medium |

### Reading the table

- **B (P2P file sync)** fails the most important criterion (data correctness) and doesn't remove the server. Its only real win — privacy/no-server — is undercut by the always-on node requirement and by the fact that the agent runtime needs a server regardless.
- **C (CRDT multi-master)** is the technically "purest" answer and the only one that delivers true offline multi-writer — but it is a rewrite, has a hard blocker (ChromaDB), and changes the product model (device-local agents). It should be evaluated as a *product direction* (local-first assistant), not as a sync mechanism.
- **D (server + client sync queue)** is the incremental path that closes the actual documented gap (offline/bidirectional sync) while preserving every correctness and operational property the project already has. It extends the existing `FileCache` model: a client-side outbox for writes, a delta/version protocol for reads, `cloud_only/downloaded/pinned` for storage management.

---

## 7. When P2P *would* be the right call

P2P (or more precisely, local-first CRDT sync with P2P transport) becomes the right answer only if the product vision shifts to:

1. **Local-first assistant** — each device runs its own full agent (local LLM via Ollama, local store, local memory) and syncs state between devices, with the server demoted to a cloud peer (backup + relay + burst compute). This is the Ink & Switch "cloud peer" model: *"The key difference between traditional systems and local-first systems is not an absence of servers, but a change in their responsibilities: they are in a supporting role, not the source of truth."*
2. **Privacy/ownership as a core value prop** — "your data never leaves your devices" as a selling point (Obsidian + Syncthing, local-first note apps).
3. **Offline-first as a hard requirement** — the user must be able to run the assistant fully offline on a laptop/phone for extended periods, with writes that merge later.

Signals that would justify revisiting this evaluation:
- A device-local agent runtime becomes a product goal (the native app grows a full local loop).
- ChromaDB is replaced by something with a replication story (e.g. SQLite-vector, or a CRDT-friendly vector store).
- The team decides the server cost (VPS, Tailscale, backups) is a blocker for adoption.

None of these are true today. The native app is a thin client; the agent runtime is server-side; ChromaDB is the memory backend.

---

## 8. Recommendation

**Do not adopt P2P file sync for the current architecture.** It is unsafe for the data that matters (SQLite WAL DBs, ChromaDB), it does not remove the server (the agent runtime needs it, and P2P needs an always-on node anyway), and it adds pairing/NAT/conflict complexity for zero correctness gain.

**Instead, close the actual gap — offline + bidirectional sync — with a client-side sync layer against the existing server (option D):**

1. **Client write outbox**: queue file writes/edits locally when offline; replay to the server on reconnect (idempotent, versioned).
2. **Server delta protocol**: extend the `FileCache` model with per-file versions and a `since` cursor so clients pull only changed files (the `has_update` mtime check is the seed of this).
3. **Keep the server authoritative**: single-writer SQLite/ChromaDB invariants stay intact; conflicts resolve as last-write-wins at the file level, with `.versions/` already providing recovery.
4. **Storage tiers**: `cloud_only` / `downloaded` / `pinned` already model the mobile storage tradeoff; wire them to the sync engine.

**If the local-first vision ever becomes a goal**, revisit this document: the path is CRDT-based SQLite sync (cr-sqlite) with the server as a cloud peer — and the ChromaDB question must be answered first, because it is the hard blocker.

---

## 9. Sources

- Syncthing maintainers on syncing databases: syncthing/syncthing issue #4242; Syncthing forum "sync conflict with 1 specific (sqlite) file constantly" (2023).
- SQLite: *How To Corrupt An SQLite Database File*; *Write-Ahead Logging*; *Isolation In SQLite* (sqlite.org).
- Ink & Switch: *Local-first software: you own your data, in spite of the cloud* (Kleppmann et al., 2019); *PushPin: Towards Production-Quality Peer-to-Peer Collaboration* (2020).
- cr-sqlite (vlcn.io / shards-lang pure-C port): README, CRR constraints, performance notes.
- sqlite-sync (SQLite Cloud): README (Elastic License 2.0, block-level LWW).
- Self-hosted comparisons: Seafile vs Syncthing, Nextcloud vs Syncthing (selfhosting.sh); Syncthing local-only deployment notes (vdaluz.com).
- Project-internal: `DEPLOYMENT.md`, `DATA_ARCHITECTURE.md`, `src/http/workspace_cache.py`.
