"""File cache tracking for sync status."""

import json
from datetime import UTC, datetime
from typing import Any

from src.storage.paths import get_paths


class FileCache:
    """Track which files are cached/downloaded."""

    def __init__(self, user_id: str, workspace_id: str = "personal"):
        self.user_id = user_id
        self.workspace_id = workspace_id
        self.cache_file = get_paths(user_id, workspace_id=workspace_id).workspace_cache()
        self._cache: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.cache_file.exists():
            try:
                self._cache = json.loads(self.cache_file.read_text())
            except Exception:
                self._cache = {}

    def _save(self) -> None:
        # Atomic write (audit B17): a crash mid-write must not leave a
        # truncated cache file that silently resets all sync state.
        import os

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2))
        os.replace(tmp, self.cache_file)

    def get_status(self, path: str) -> str:
        """Get sync status: 'cloud_only', 'downloaded', 'pinned'"""
        return str(self._cache.get(path, {}).get("status", "cloud_only"))

    def _workspace_file(self, path: str) -> Any | None:
        """Resolve a cached path against the workspace files dir; None if absent."""
        try:
            fp = (
                get_paths(self.user_id, workspace_id=self.workspace_id).workspace_files_dir()
                / path
            )
        except Exception:
            return None
        return fp if fp.exists() else None

    def mark_downloaded(self, path: str) -> None:
        entry: dict[str, Any] = {
            "status": "downloaded",
            "downloaded_at": datetime.now(UTC).isoformat(),
        }
        # Stamp the current file version so get_all() can compute has_update
        # against a real baseline (audit B17: missing baseline read as
        # "permanently outdated").
        file_path = self._workspace_file(path)
        if file_path is not None:
            entry["server_modified"] = str(file_path.stat().st_mtime)
        self._cache[path] = entry
        self._save()

    def mark_pinned(self, path: str) -> None:
        entry: dict[str, Any] = {
            "status": "pinned",
            "pinned_at": datetime.now(UTC).isoformat(),
        }
        file_path = self._workspace_file(path)
        if file_path is not None:
            entry["server_modified"] = str(file_path.stat().st_mtime)
        self._cache[path] = entry
        self._save()

    def mark_cloud_only(self, path: str) -> None:
        if path in self._cache:
            del self._cache[path]
            self._save()

    def get_all(self, workspace_path: str | None = None) -> dict[str, dict[str, Any]]:
        """Get all sync status with has_update flag."""
        result: dict[str, dict[str, Any]] = {}
        workspace_root = get_paths(self.user_id, workspace_id=self.workspace_id).workspace_files_dir()

        for path, data in self._cache.items():
            file_path = workspace_root / path
            server_modified = str(file_path.stat().st_mtime) if file_path.exists() else ""
            current_version = data.get("server_modified", "")

            result[path] = {
                **data,
                # Both sides must exist (audit B17): entries without a
                # baseline are not "outdated", they are "never compared".
                "has_update": bool(server_modified)
                and bool(current_version)
                and server_modified != current_version,
            }

        return result

    def get_last_synced(self, path: str) -> str | None:
        """Get last sync timestamp for a file."""
        return self._cache.get(path, {}).get("synced_at")

    def update_sync(self, path: str, server_modified: str) -> bool:
        """Update sync timestamp. Returns True if file changed (needs re-download)."""
        existing = self._cache.get(path, {})
        last_synced = existing.get("synced_at")

        if last_synced is None or existing.get("server_modified") != server_modified:
            self._cache[path] = {
                "status": "downloaded",
                "synced_at": datetime.now(UTC).isoformat(),
                "server_modified": server_modified,
            }
            self._save()
            return True
        return False

    def get_downloaded_files(self) -> list[str]:
        return [
            path
            for path, data in self._cache.items()
            if data.get("status") in ("downloaded", "pinned")
        ]


def get_file_cache(user_id: str, workspace_id: str = "personal") -> FileCache:
    return FileCache(user_id, workspace_id)
