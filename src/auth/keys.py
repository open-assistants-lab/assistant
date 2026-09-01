"""Per-user API key store (Phase 2 M2.1).

Server-side generated keys for the hosted tier. Only the SHA-256 hash is
persisted; the plaintext is returned exactly once at generation time.
Central `data/auth.db` (one per deployment) — admin-gated generation,
per-request verification. Follows the AuditStore storage pattern:
synchronous SQLite + lock, append-mostly (revoke = timestamped update).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import UTC, datetime

from src.storage.paths import DataPaths, get_paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT
)
"""


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class KeyStore:
    """Central per-user API key store (`data/auth.db`)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def generate(self, user_id: str, scopes: str = "") -> str:
        """Generate a key for user_id; return the plaintext exactly once."""
        plaintext = "oak_" + secrets.token_urlsafe(32)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO api_keys (key_hash, user_id, scopes, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    _hash_key(plaintext),
                    user_id,
                    scopes,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return plaintext

    def verify(self, key: str) -> tuple[str, str] | None:
        """Return (user_id, scopes) for a valid, unrevoked key; else None."""
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            row = self._conn.execute(
                "SELECT user_id, scopes, revoked_at FROM api_keys"
                " WHERE key_hash = ?",
                (_hash_key(key),),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        return row["user_id"], row["scopes"]

    def revoke(self, key: str) -> bool:
        """Revoke a key by plaintext. Returns True if a live key was revoked."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked_at = ?"
                " WHERE key_hash = ? AND revoked_at IS NULL",
                (datetime.now(UTC).isoformat(), _hash_key(key)),
            )
            return cur.rowcount > 0


_STORES: dict[str, KeyStore] = {}


def get_key_store(user_id: str | None = None) -> KeyStore:
    """Process-wide KeyStore keyed by the deployment data root."""
    paths: DataPaths = get_paths(user_id=user_id)
    root = str(paths.root)
    store = _STORES.get(root)
    if store is None:
        store = KeyStore(str(paths.root / "auth.db"))
        _STORES[root] = store
    return store
