"""MCP metadata cache contract."""

from __future__ import annotations

import json
from pathlib import Path

from src.sdk.tools_core.mcp_cache import MCPToolMetadataCache


def _record(name: str, config_hash: str = "hash") -> dict:
    return {
        "server_name": name,
        "source_path": f"/project/{name}.json",
        "source_type": "project",
        "config_hash": config_hash,
        "connected_at": "2026-09-25T12:00:00Z",
        "last_refresh": "2026-09-25T12:00:00Z",
        "tools": [
            {
                "name": "write",
                "description": "Write data",
                "inputSchema": {"type": "object"},
                "annotations": {"destructiveHint": True},
            }
        ],
        "resources": [],
        "cache_status": "cached",
    }


def test_cache_round_trip_omits_secrets(tmp_path: Path) -> None:
    cache = MCPToolMetadataCache(tmp_path / "mcp-cache.json")
    record = _record("github")
    record["headers"] = {"Authorization": "Bearer secret"}
    record["env"] = {"API_KEY": "secret"}

    cache.put(record)

    raw = (tmp_path / "mcp-cache.json").read_text(encoding="utf-8")
    assert "Bearer secret" not in raw
    assert "API_KEY" not in raw
    assert cache.get("github") is not None
    assert cache.get("github")["tools"][0]["name"] == "write"


def test_config_hash_invalidates_only_changed_server(tmp_path: Path) -> None:
    cache = MCPToolMetadataCache(tmp_path / "mcp-cache.json")
    cache.put(_record("github", "old"))
    cache.put(_record("files", "same"))

    cache.invalidate_changed({"github": "new", "files": "same"})

    assert cache.contains("github") is False
    assert cache.contains("files") is True


def test_cache_rejects_unversioned_or_malformed_payload(tmp_path: Path) -> None:
    path = tmp_path / "mcp-cache.json"
    cache = MCPToolMetadataCache(path)
    path.write_text(json.dumps({"version": 999, "servers": {}}), encoding="utf-8")

    assert cache.status()["version"] == 1
    assert cache.get("missing") is None


def test_cache_removal_is_idempotent(tmp_path: Path) -> None:
    cache = MCPToolMetadataCache(tmp_path / "mcp-cache.json")
    cache.put(_record("github"))

    cache.invalidate_server("github")
    cache.invalidate_server("github")

    assert cache.contains("github") is False
