"""Reference-knowledge corpus store (P1-T1).

Ground-truth storage for *reference* knowledge — client records, case
history, price tables — distinct from skills (methodology lives in
SKILL.md files; facts live here). Read-only-at-ingest discipline: the
corpus is an index of sources, nothing is ever auto-committed into
skills from here.

Per (user_id, workspace_id) store at Corpus/{workspace_id}/corpus.db
under the user's data dir. HybridDB owns the canonical `corpus` table;
a companion FTS5 virtual table in the same file drives keyword search
with honest miss semantics (HybridDB's hybrid search falls back to
approximate matches, which would leak unrelated rows).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from src.storage.paths import DEFAULT_USER_ID

_CHUNK_SIZE = 900  # characters per indexed chunk
_CHUNK_OVERLAP = 100


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks on paragraph boundaries."""
    text = text.strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) > _CHUNK_SIZE:
            # Long paragraph: window with overlap.
            start = 0
            while start < len(para):
                chunks.append(para[start : start + _CHUNK_SIZE])
                start += _CHUNK_SIZE - _CHUNK_OVERLAP
        elif chunks and len(chunks[-1]) + len(para) + 2 <= _CHUNK_SIZE:
            chunks[-1] = chunks[-1] + "\n\n" + para
        else:
            chunks.append(para)
    return chunks


def _fts_query(query: str) -> str:
    """Quote each token as an FTS5 phrase so user text is safe syntax."""
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in query.split())


class CorpusStore:
    """User- and workspace-scoped reference knowledge index."""

    def __init__(self, user_id: str = DEFAULT_USER_ID, workspace_id: str = "personal"):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._db: Any = None

    def _corpus_dir(self) -> Any:
        from src.storage.paths import get_paths

        paths = get_paths(self.user_id, workspace_id=self.workspace_id)
        return paths.user_dir / "Corpus" / self.workspace_id

    @property
    def db_path(self) -> Any:
        """SQLite file holding both tables (HybridDB convention: dir/app.db)."""
        return self._corpus_dir() / "app.db"

    def _get_db(self) -> Any:
        if self._db is None:
            from hybriddb import HybridDB

            path = self._corpus_dir()
            path.mkdir(parents=True, exist_ok=True)
            db = HybridDB(path=str(path))
            try:
                db.create_table(
                    "corpus",
                    {
                        "id": "TEXT PRIMARY KEY",
                        "source": "TEXT NOT NULL",
                        "chunk_idx": "INTEGER DEFAULT 0",
                        "text": "LONGTEXT",
                        "indexed_at": "TEXT",
                    },
                )
            except Exception:
                pass  # table already exists
            self._db = db
        return self._db

    def _fts_conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS corpus_fts USING fts5("
            "text, source, source_pk UNINDEXED)"
        )
        return conn

    def index(self, text: str, source: str) -> int:
        """Index text under `source`. Idempotent: re-indexing the same
        source replaces its previous chunks. Returns chunks indexed."""
        if not source or not source.strip():
            raise ValueError("source must be a non-empty string")
        chunks = _chunk_text(text)
        if not chunks:
            raise ValueError("text must be a non-empty string")
        db = self._get_db()
        # Idempotency: drop previous chunks for this source, then insert.
        try:
            existing = db.read_query(
                "SELECT id FROM corpus WHERE source = ?", (source,)
            )
            for row in existing:
                try:
                    db.delete("corpus", row["id"])
                except Exception:
                    pass
        except Exception:
            pass  # first index for this source
        now = datetime.now(UTC).isoformat()
        for i, chunk in enumerate(chunks):
            cid = f"{source}#c{i}"
            data = {
                "id": cid,
                "source": source,
                "chunk_idx": i,
                "text": chunk,
                "indexed_at": now,
            }
            try:
                db.insert("corpus", data)
            except Exception:
                # Row exists (delete path failed): update in place.
                db.update("corpus", cid, {
                    k: v for k, v in data.items() if k != "id"
                })
        fts = self._fts_conn()
        try:
            fts.execute("DELETE FROM corpus_fts WHERE source = ?", (source,))
            for i, chunk in enumerate(chunks):
                fts.execute(
                    "INSERT INTO corpus_fts(text, source, source_pk) "
                    "VALUES (?, ?, ?)",
                    (chunk, source, f"{source}#c{i}"),
                )
            fts.commit()
        finally:
            fts.close()
        return len(chunks)

    def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """FTS5 keyword search with honest miss semantics. Returns up to
        `k` ranked results: {source, text, snippet, chunk_idx, confidence}.
        """
        q = query.strip()
        if not q:
            raise ValueError("query must be a non-empty (non-whitespace) string")
        conn = self._fts_conn()
        try:
            rows = conn.execute(
                "SELECT source, source_pk, text, "
                "snippet(corpus_fts, 0, '', '', '…', 24) AS snip, "
                "bm25(corpus_fts) AS rank "
                "FROM corpus_fts WHERE corpus_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (_fts_query(q), k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # malformed/unsupported FTS syntax → honest miss
        finally:
            conn.close()
        results: list[dict[str, Any]] = []
        for source, source_pk, text, snip, rank in rows:
            # bm25 rank is negative (lower = better). Map magnitude to a
            # 0.5..1.0 confidence band: any real keyword hit floors at 0.5.
            confidence = max(0.5, min(1.0, 1.0 - abs(rank) / 20.0))
            idx = 0
            if "#" in source_pk:
                try:
                    idx = int(source_pk.rsplit("#c", 1)[1])
                except (ValueError, IndexError):
                    idx = 0
            results.append({
                "source": source,
                "text": text,
                "snippet": snip or text[:200],
                "chunk_idx": idx,
                "confidence": round(confidence, 3),
            })
        return results

    def count(self) -> int:
        db = self._get_db()
        try:
            return int(db.count("corpus"))
        except Exception:
            return 0
