"""Secret-free durable metadata cache for MCP servers."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_CACHE_VERSION = 1
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "bearer",
    "credential",
    "header",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)


def _safe_value(value: Any, key: str = "") -> Any:
    if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS):
        return None
    if isinstance(value, dict):
        return {
            str(child_key): _safe_value(child_value, str(child_key))
            for child_key, child_value in value.items()
            if not any(part in str(child_key).casefold() for part in _SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return copy.deepcopy(value)


class MCPToolMetadataCache:
    """Small versioned JSON cache for server metadata, not live sessions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {"version": _CACHE_VERSION, "servers": {}}
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
            return {"version": _CACHE_VERSION, "servers": {}}
        servers = payload.get("servers")
        if not isinstance(servers, dict):
            servers = {}
        return {"version": _CACHE_VERSION, "servers": servers}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(_safe_value(payload), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def records(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(record) for record in self._read()["servers"].values() if isinstance(record, dict)]

    def get(self, server_name: str) -> dict[str, Any] | None:
        record = self._read()["servers"].get(server_name)
        return copy.deepcopy(record) if isinstance(record, dict) else None

    def put(self, record: dict[str, Any]) -> None:
        server_name = str(record.get("server_name") or "").strip()
        if not server_name:
            return
        payload = self._read()
        payload["servers"][server_name] = _safe_value(record)
        self._write(payload)

    def contains(self, server_name: str) -> bool:
        return self.get(server_name) is not None

    def invalidate_server(self, server_name: str) -> None:
        payload = self._read()
        payload["servers"].pop(server_name, None)
        self._write(payload)

    def invalidate_changed(self, config_hashes: dict[str, str]) -> None:
        payload = self._read()
        servers = payload["servers"]
        for server_name, record in list(servers.items()):
            if not isinstance(record, dict):
                servers.pop(server_name, None)
                continue
            if str(record.get("config_hash") or "") != str(config_hashes.get(server_name, "")):
                servers.pop(server_name, None)
        self._write(payload)

    def invalidate_all(self) -> None:
        self._write({"version": _CACHE_VERSION, "servers": {}})

    def status(self) -> dict[str, Any]:
        payload = self._read()
        return {
            "version": _CACHE_VERSION,
            "server_count": len(payload["servers"]),
            "servers": sorted(payload["servers"]),
        }
