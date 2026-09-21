# Hister — research note (file system / LLM wiki angle)

**Date:** 2026-09-20
**Subject:** [Hister](https://hister.org) (`github.com/asciimoo/hister`, Go, AGPLv3, single binary) — "your own search engine": a private full-content index of visited pages + local files, with web/TUI/HTTP/MCP surfaces.
**Why looked at:** competitor scan for the parked `agent_journal` / SemanticMem spec (§9). Does it do what we need? What should we adopt?
**Bottom line:** it validates four of our design intuitions in production, and it demonstrates the two features we don't have yet: **untrusted-content transport framing** and **recency as a query field**. It does *not* do claims, versioned pages, or knowledge curation. **No adoption, no spec changes** — two ideas noted in the parked spec's §7 (competitor scan, committed `a18bef71`).

---

## 1. What Hister is (architecture, from their docs)

### Data layout (single-user SQLite deployment)

| Path | Contents |
|---|---|
| `index.db`, `index_{LANG}.db` | searchable document fields + full-text indexes (per language) |
| `data/html/`, `data/favicon/` | compressed HTML previews / favicons, **addressed by content hash** (dedup) |
| `db.sqlite3` | users, sessions, search history, crawl jobs, **version-diff records**, job state |
| `vectors.sqlite3` | semantic-search chunks + vectors (optional; PG available) |
| `rules.json` | skip / priority / versioning rules + query aliases |
| `.secret_key` | token-derivation secret |

- **No expiry, no quota, no automatic deletion.** Their docs say it plainly: "Hister keeps current searchable documents until you replace or delete them. It does not apply an automatic expiry period… the server operator is responsible for monitoring storage." Same choice as HybridDB.
- **~100 KB/page average stored.**
- Re-submission of a normalized URL *replaces* the current document + bumps a `submission_count`; a versioning rule *also* stores a **diff-match-patch record** (for HTML and text) in SQL. The preview panel shows "previous version count" and a changelog; you can reconstruct archived versions.
- Watched files: re-index on change; on removal the document is kept **unless** `delete_on_remove: true`.
- Multi-user: per-user rules in DB (not `rules.json`); per-user isolation of documents and results.

### Ingestion

- **Browser extensions** (Chrome/Firefox): index pages as visited — the primary source.
- **Local file watchers** (directory → full text of files that change).
- **History import** from browser history/bookmarks; **website crawler** (multiple backends).
- **Extractors** — pluggable content extraction for supported formats and sites (their "extraction" layer, equivalent to our markdown-normalised extraction).
- **Remote file snapshots** (`hister import file`): extraction happens on the client; only prepared document fields ship to the server.
- **Metadata**: author, published/modified dates, description, site name, type, language, image, **JSON-LD structured data**, embedded video URLs, user-defined `label`.

### Query language (the part closest to "LLM wiki")

Free text + structured operators:

- **Phrases**: `"privacy policy"` (exact phrase match)
- **Field filters**: `title:`, `text:`, `url:` (bare paths auto-resolved to `file://`), `url_re:` (Go regexp), `domain:`, `label:`, `language:` (detected lang, `unknown` supported), `metadata.KEY:` (exact value), `type:` (`web` | `file` | `local` | `remote`), `visits:` (exact / bounded `2..4` / open `10..`), `added:` / `updated:` (relative `>90d` **or absolute** `>=2026-04-01`; operators `< <= > >=`), `user_id:`
- **Negation, wildcards, priorities** (mentioned on the landing page; full syntax in the docs page I didn't fully pull — see §5)
- **`sort:`** directive: `relevance` | `date` | `visits` | `domain`
- **Aliases**: query-time keyword expansion (`"gh" → "domain:github.com"`); the user searches `work deployment` and it's executed as `domain:(internal.example.com|jira.example.com) deployment`.

**Skip / priority / versioning rules** (Go regex on the full URL; `utm_*` stripped; trailing `$` does not match URLs with query strings — a documented sharp edge):
- **Skip**: match → document silently discarded at index time; a per-document `ignore_skip_rules` override ("Index this page now") survives reindex; the override is *stored with the document and preserved in exports* — i.e. per-record decision provenance.
- **Priority**: large score boost — "always surface your personal wiki before other results."
- **Versioning**: diff-match-patch records per change; changelog view.
- All three can be applied retroactively ("delete matching documents already in the index" with count + confirm).

### Surfaces

One index, five front doors: **web UI, TUI (their demo is the terminal), HTTP API, CLI, MCP server**.

**MCP** (Streamable HTTP, `POST /mcp`; Bearer token auth; same auth model as the API):
- `search(query, limit, date_from, date_to, semantic, fields[])` — `semantic: true` degrades gracefully to keyword when the server has no embeddings configured; `fields` opts in to `text` / `html` / `language` / `label` / `domain` / `score` / `type` / `visits` / `added` / `updated`
- `get_preview(url, extractor?)` — title, URLs, dates, metadata, **complete stored plain text**, complete rendered HTML
- `get_history(mode: indexed|opened, limit, cursors)` — with pagination via `next_page_key` / `next_last_id`

**Auth**: static `app.access_token` (Bearer or `X-Access-Token`), per-user tokens in multi-user mode, or `app.public: true` public mode (public routes open; `get_history` still requires auth — history is personal data even when search is public).

## 2. What it gets right (production evidence, worth stealing)

### 2.1 Untrusted-content framing in the *transport layer*

Their MCP docs are explicit:

> Every indexed title, URL, metadata value, document body, and history field is untrusted source data. A page can contain instructions aimed at the assistant that reads it. Those instructions must never override the user request, cause secret disclosure, or trigger another tool.

And the mechanism: *every* MCP response puts source-controlled values under **`structuredContent.untrusted_content`**, each record carries **`trust: "untrusted"`** + **`trust_scope: "all values in fields"`**, a security instruction identifies the exact untrusted path, and the required text block repeats the same JSON **after** a security notice. They also **strip invisible control characters** on the way out, and HTML is only ever returned on explicit request (`fields` or `get_preview`) — and still inside the untrusted record. They state plainly that controls "reduce risk but cannot guarantee that every consuming model will resist prompt injection."

This is exactly our `ToolResult` dual-format + audience model — but Hister has made "the retrieved content is untrusted" a **per-record, transport-enforced invariant** rather than a documented convention. Our MCP bridge passes `content` (human) + `structured_content` (machine); it does not attach a `trust` annotation to bridged records, and our *own* search tools return content without such framing.

### 2.2 Recency as a query field

`added:` / `updated:` with relative *and* absolute ranges is a first-class query operator, not a sort option. `updated:>90d` answers "which of my knowledge is stale" in one query; `added:<7d` answers "what's new since I last looked". This is the *direct answer* to the parked spec's unresolved staleness question ("how does a semantic page signal staleness?"): **make timestamps query-able, not just display-able.**

### 2.3 Retrieval-frequency ranking

`visits:` filter + `sort:visits` + "most opened results" history. This is our `frequency` signal (retrieval count, not mention count) — Hister runs it in production against a 1,924-page index and uses it for ranking and inspection.

### 2.4 Per-record provenance decisions

The skip-rule override is stored **on the document** ("save the choice with the document, so the page survives `hister reindex`"), preserved in exports, and overridable per record. Contrast our HybridDB tombstones: deletion is per-table prefix-scoped, not per-record, and there's no per-record "why" record. Their model — *decisions attach to the record, and the record is auditable* — is the right shape for knowledge curation (exactly what claims + `evidence` frontmatter would add).

### 2.5 Honest lifecycle posture

No expiry, no quota, "operator is responsible for monitoring storage" — the same decision we made with HybridDB (the ledger outlives the context; deletion is deliberate, not scheduled). And their growth levers (`disable_previews`, skip rules before capture, versioning *only where earlier content matters*) are sensible because they're documented honestly instead of hidden behind fake "retention policy" settings.

### 2.6 Graceful semantic degradation

`semantic: true` → keyword search when the server has no embeddings endpoint. No error, no "feature unavailable" wall. Small, correct default that we should copy in the search layer.

### 2.7 One index, many doors

Web + TUI + HTTP + CLI + MCP all read the same store, with **per-field opt-in** in MCP responses (`fields: []` default → cheap; `fields: [text]` → full body). This is the anti-pattern for LLM-consumed search APIs: defaulting to the whole document body on every result is what makes every call cost ~100KB. Their default is 10 results × snippet.

### 2.8 **The path *is* the identity** — and a query field

For watched files the document URL is the absolute path (`file://` — `url:/home/user/documents/report.pdf` auto-resolves to `file:///home/user/documents/report.pdf`); re-indexing the same path *replaces* the document (same source + absolute path per the lifecycle table), so the path is the primary key of a local document. It's searchable (`url:`) and queryable as a bare path, and deletion of the source file **does not delete the document** (kept unless `delete_on_remove: true`) — so the path is a *stable index key that outlives the file*, not a live reference. For remote imports the original source path is preserved in the document metadata (a snapshot re-import from a *different* path creates a new snapshot; the old one stays).

**LLM-wiki lesson:** a file-based wiki can make *path an indexed field* rather than just an identity — `url_re:~/.*/notes/.*` is "all my notes" as a query. That's something a directory-listing approach (our `files_list`) can't express. **But note the mirror trap:** Hister's "path outlives file" model is the opposite of our `prune`/tombstone direction for knowledge (we want deletion to propagate); for *documents* their model is right (archive semantics), for *claims* it isn't (stale-forever is exactly the staleness problem).

## 3. What it does *not* do (our differentiators, confirmed)

| | Hister | We |
|---|---|---|
| **Unit of knowledge** | the *document* (page/file) | **the claim** — atomic, versioned, evidenced (parked spec §4.4) |
| **Versioning of knowledge** | diff-match-patch of *documents* in SQL (changelog, reconstructable) | **hash-chained versions of the file itself** (HybridDB v0.6.0 `verify_chain`) — "what did the wiki say at commit N" |
| **Targeted erasure** | delete current document; old SQL diff records *may* remain (they say so) | tombstones (per-table, prefix-prune; still not targeted — see §4) |
| **Curation** | skip/priority rules on *URLs*; a human decides what to keep | claims with `evidence`, review states, and the parked spec's decision matrix (no lift → delete; staleness → claims) |
| **Provenance on knowledge** | source = where the document came from (domain, import path) | **provenance on claims** — which session/message/file the claim came from (`provenance` field in the spec) |
| **Multi-user** | per-user isolation on a shared server | one store per user, container-per-user (deployment decision, different trade) |

So: Hister proves the *search* layer well; we already have the *ledger* layer better; neither has the *claims* layer — that's still our differentiator to build (or deliberately not build, per the eval-gated plan).

## 4. What's wrong / sharp edges worth knowing

- **"Some related SQL records can remain after current document deletion"** — they document that deletion is not surgical. Same class of gap as our `prune()` being prefix-only. Both are honest about it; neither has targeted erasure yet.
- **Trailing-`$` rule sharp edge**: `/login$` does **not** match `https://hister.org/login?auth=1` (query string present). Classic docs sharp edge — the kind we should write a test for if we ship URL rules.
- **`visits` counts submissions, not human attention** (the browser extension indexes *as you browse*, so a page auto-visited and a page you read both count). Our `frequency` (retrieval-by-agent count) is a different, arguably cleaner, signal — but Hister's proves users actually read the "most visited" view.
- **Go regex rules** (no lookaround) — fine, just a porting note.
- **`max_file_size_mb` default 1 MiB** — a watched PDF over 1 MiB is silently not indexed. Their docs call it out; ours (if we add file watchers) should default higher for a personal wiki.

## 5. What I did *not* pull (materially incomplete, doesn't change the conclusion)

- The full negation / wildcard / priority / alias syntax (query-language page only partially scraped)
- The extractors catalogue and per-format behaviour
- Their Go schema (we'd need the repo for internals)
- TUI ergonomics
- `server-setup` / user-handling multi-user detail

None of these would change the two adoption candidates below; they're depth, not direction.

## 6. The two adoptions (cheap, high value, both fit the parked spec)

### 6.1 `trust` framing on bridged + search results

Mirror Hister: every record our MCP bridge forwards and every search-family tool returns gets **`trust: "untrusted"` on source-controlled fields** (plus `trust_scope`), with retrieved content placed under a named sub-key rather than at top level. Our invariant already says "content never enters spans" and "retrieved data is untrusted" — this makes it *machine-readable in the transport* instead of a convention the LLM has to remember. It's a `ToolResult.structured_content` shape change, not new machinery.

**Not a spec change, a convention note** — but worth doing before any partner consumes our search via MCP.

### 6.2 Recency fields on knowledge records

`added_at` / `updated_at` on journal pages and claims, **exposed in the query interface** (not just stored). For the parked spec this converts the staleness question from a design problem into a schema problem:

- `pages.updated_at`, `claims.evidence_updated_at`
- a `stale_since` view = "claims whose evidence hasn't been confirmed since N days"
- the agent's recall path can then *ask* "which of my knowledge is old?" without LLM inference

One field, one index column, and the eval harness (§4.1) gains a fourth category test: "retrieval prefers the most recent page on a conflicting topic."

## 7. Competitive picture

- Hister is a **search engine for your stuff** — documents are the unit, search is the product.
- `evermem` (PyPI, noted in spec §7) is **markdown-first memory extraction** — closest to our artifact shape.
- **Hister + evermem together ≈ our spec, minus the ledger**: the search + the markdown, but no version chain, no claims, no hybrid transactional backing. Our differentiator remains the **versioned ledger underneath** — which is what makes the knowledge *auditable* ("what was known at commit N") rather than merely *searchable*.
- For Motion A (law firms), the Hister gap is the whole product: a firm doesn't need better search of its own files; it needs **versioned, evidenced, erasable knowledge with provenance** — which is what we're actually building.

## 8. Disposition

- No spec amendments (spec stays parked; two adoption ideas noted in §7, committed `a18bef71`).
- If the SemanticMem eval un-parks: **add recency fields to the schema in §4.4** and **add a `trust` annotation to any MCP-exposed knowledge search** — both cheap, both validated by this scan.
- No engineering tasks filed; no roadmap impact. This is competitor knowledge, recorded for the unpark decision.

---

## Appendix — scrape inventory

- `https://hister.org` (landing)
- `https://github.com/asciimoo/hister` (README)
- `https://hister.org/docs/mcp` (tools + auth + untrusted-content framing)
- `https://hister.org/docs/data-lifecycle` (storage model, versioning diffs, deletion behaviour)
- `https://hister.org/docs/query-language` (operators, fields, rules semantics)
- `https://hister.org/docs/rules` (skip / priority / versioning, per-record override)

Local copies: `.firecrawl/hister-{org,github,mcp,lifecycle,query,rules}.md`
