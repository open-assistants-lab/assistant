from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hybriddb import HybridDB

from src.sdk.tools import ToolDefinition
from src.storage.paths import DEFAULT_USER_ID

_RECONSTRUCT_EMPTY = "{}"


def _rebuild_custom_function(td: ToolDefinition, reconstruct: dict[str, Any]) -> ToolDefinition:
    """Rebuild the function for a custom (TOOL.md) tool from reconstruct metadata."""
    command_template = reconstruct.get("command", "")
    install_cmds = reconstruct.get("install", [])
    tool_dir_str = reconstruct.get("tool_dir", "")

    def fn(**kwargs: Any) -> str:
        from src.sdk.sandbox import custom_command_tools_allowed

        if not custom_command_tools_allowed():
            return "Custom command tools are disabled by the hard sandbox backend."
        rendered = command_template
        if tool_dir_str:
            rendered = rendered.replace("{{tool_dir}}", shlex.quote(tool_dir_str))
        for k, v in kwargs.items():
            rendered = rendered.replace("{{" + k + "}}", shlex.quote(str(v)))

        tool_name = command_template.split()[0] if command_template else ""
        if tool_name:
            try:
                subprocess.run(["which", tool_name], capture_output=True, timeout=10, check=True)
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                if install_cmds:
                    return (
                        f"Tool '{tool_name}' not found. Install it with one of:\n"
                        + "\n".join(f"  {c}" for c in install_cmds)
                    )
                return f"Tool '{tool_name}' not found on PATH."

        try:
            result = subprocess.run(
                rendered,
                shell=True,
                capture_output=True,
                timeout=120,
                text=True,
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                return f"Command failed (exit {result.returncode}):\n{output[:2000]}"
            return output[:5000] or "(no output)"
        except subprocess.TimeoutExpired:
            return "Command timed out after 120 seconds."
        except Exception as e:
            return f"Command error: {e}"

    td.function = fn
    return td


class ToolIndex:
    """Searchable index of all tools using HybridDB, with change detection."""

    def __init__(self, db_dir: Path):
        self.db_dir = db_dir
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db = HybridDB(str(self.db_dir))
        self.db.create_table(
            "tools",
            {
                "name": "TEXT UNIQUE",
                "description": "LONGTEXT",
                "search_text": "LONGTEXT",
                "namespace": "TEXT",
                "tool_type": "TEXT",
                "definition_json": "LONGTEXT",
                "reconstruct": "TEXT",
            },
        )

    def index_tool(
        self,
        td: ToolDefinition,
        tool_type: str,
        namespace: str = "",
        reconstruct: dict[str, Any] | None = None,
    ) -> None:
        existing = self.db.query("tools", where="name = ?", params=(td.name,))
        row = {
            "name": td.name,
            "description": td.description,
            "search_text": f"{td.name} {td.description}",
            "namespace": namespace,
            "tool_type": tool_type,
            "definition_json": td.model_dump_json(exclude={"function"}),
            "reconstruct": json.dumps(reconstruct or {}),
        }
        if existing:
            self.db.update("tools", existing[0]["id"], row)
        else:
            self.db.insert("tools", row)

    def index_tools(
        self,
        tools: list[ToolDefinition],
        tool_type: str,
        namespace: str = "",
        reconstruct: dict[str, Any] | None = None,
    ) -> None:
        """Bulk-index tools in a single upsert pass.

        Reads the existing name set once, then inserts new rows via
        insert_batch and updates existing rows in place (no duplicate
        rows on UNIQUE name).
        """
        if not tools:
            return
        existing_names = {r["name"] for r in self.db.query("tools")}
        new_rows: list[dict[str, Any]] = []
        for td in tools:
            row = {
                "name": td.name,
                "description": td.description,
                "search_text": f"{td.name} {td.description}",
                "namespace": namespace,
                "tool_type": tool_type,
                "definition_json": td.model_dump_json(exclude={"function"}),
                "reconstruct": json.dumps(reconstruct or {}),
            }
            if td.name in existing_names:
                rows = self.db.query("tools", where="name = ?", params=(td.name,))
                if rows:
                    self.db.update("tools", rows[0]["id"], row)
            else:
                new_rows.append(row)
        if new_rows:
            self.db.insert_batch("tools", new_rows)

    def remove_tool(self, name: str) -> None:
        existing = self.db.query("tools", where="name = ?", params=(name,))
        if existing:
            self.db.delete("tools", existing[0]["id"])

    def search(self, query: str, limit: int = 5) -> list[tuple[str, str]]:
        rows = self.db.search("tools", "search_text", query, mode="hybrid", limit=limit)
        return [(r["name"], r["description"]) for r in rows]

    def get_definition(self, name: str) -> ToolDefinition | None:
        rows = self.db.query("tools", where="name = ?", params=(name,))
        if not rows:
            return None
        return ToolDefinition(**json.loads(rows[0]["definition_json"]))

    def get_reconstruct(self, name: str) -> dict[str, Any]:
        rows = self.db.query("tools", where="name = ?", params=(name,))
        if not rows:
            return {}
        raw = rows[0].get("reconstruct", _RECONSTRUCT_EMPTY)
        if not raw:
            return {}
        data: dict[str, Any] = json.loads(raw)
        return data

    def get_tool_type(self, name: str) -> str | None:
        rows = self.db.query("tools", where="name = ?", params=(name,))
        if not rows:
            return None
        return str(rows[0].get("tool_type"))

    def list_all_names(self) -> list[str]:
        rows = self.db.query("tools")
        return [r["name"] for r in rows]

    def count(self) -> int:
        rows = self.db.query("tools")
        return len(rows)

    def clear(self) -> None:
        """Drop all indexed tools with a single table-level DELETE."""
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM tools")

    def close(self) -> None:
        pass


def compute_source_hashes(
    tools_dir: Path,
    workspace_tools_dir: Path | None,
    mcp_config: Path,
) -> dict[str, str]:
    """Hash all tool sources for change detection."""
    hashes: dict[str, str] = {}

    if tools_dir.exists():
        for tool_dir in sorted(tools_dir.iterdir()):
            tool_file = tool_dir / "TOOL.md"
            key = f"user:{tool_dir.name}"
            if tool_file.exists():
                hashes[key] = hashlib.sha256(tool_file.read_bytes()).hexdigest()
            else:
                hashes[key] = ""

    if workspace_tools_dir and workspace_tools_dir.exists():
        for tool_dir in sorted(workspace_tools_dir.iterdir()):
            tool_file = tool_dir / "TOOL.md"
            key = f"workspace:{tool_dir.name}"
            if tool_file.exists():
                hashes[key] = hashlib.sha256(tool_file.read_bytes()).hexdigest()

    if mcp_config.exists():
        hashes["mcp:config"] = hashlib.sha256(mcp_config.read_bytes()).hexdigest()

    return hashes


def check_needs_reindex(hashes_path: Path, current: dict[str, str]) -> bool:
    """Compare current hashes against stored hashes. True if anything changed."""
    if not hashes_path.exists():
        return True
    try:
        stored: dict[str, str] = json.loads(hashes_path.read_text())
    except (json.JSONDecodeError, OSError):
        return True
    return current != stored


def save_source_hashes(hashes_path: Path, hashes: dict[str, str]) -> None:
    hashes_path.parent.mkdir(parents=True, exist_ok=True)
    hashes_path.write_text(json.dumps(hashes, sort_keys=True))


def needs_rebuild(
    tools_dir: Path,
    workspace_tools_dir: Path | None,
    mcp_config: Path,
    index_dir: Path,
) -> bool:
    hashes_path = index_dir / ".index_hashes.json"
    current = compute_source_hashes(tools_dir, workspace_tools_dir, mcp_config)
    return check_needs_reindex(hashes_path, current)


def get_or_create_index(
    tools_dir: Path,
    workspace_tools_dir: Path | None,
    mcp_config: Path,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    index_dir: Path | None = None,
) -> tuple[ToolIndex, Callable[[], None]]:
    """Get or create a ToolIndex. Rebuilds if source hashes changed.

    Returns (idx, commit_hashes): the caller indexes tools via
    index_tool()/index_tools() and then calls commit_hashes() to persist
    source hashes. Hashes are saved ONLY after the caller finishes, so a
    crash mid-indexing leaves stale hashes in place and the next start
    recomputes needs_reindex and self-heals.
    """
    from src.storage.paths import get_paths

    paths = get_paths(user_id=user_id, workspace_id=workspace_id)
    index_dir = index_dir or (paths.user_tools_dir() / ".index")
    hashes_path = index_dir / ".index_hashes.json"

    current_hashes = compute_source_hashes(tools_dir, workspace_tools_dir, mcp_config)
    needs_reindex = check_needs_reindex(hashes_path, current_hashes)

    idx = ToolIndex(index_dir)

    if needs_reindex:
        idx.clear()

    def commit_hashes() -> None:
        save_source_hashes(hashes_path, current_hashes)

    return idx, commit_hashes
