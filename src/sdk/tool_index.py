from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hybriddb import HybridDB

from src.sdk.tool_results import CommandKilledError
from src.sdk.tools import ToolDefinition
from src.storage.paths import DEFAULT_USER_ID

_RECONSTRUCT_EMPTY = "{}"


@dataclass(frozen=True, eq=False)
class IndexRow:
    """One row the persisted tool index should contain."""

    name: str
    td: ToolDefinition
    tool_type: str
    namespace: str
    reconstruct: dict[str, Any] | None = None


def desired_index_rows(
    *,
    native_tools: Sequence[ToolDefinition],
    custom_tools: Sequence[ToolDefinition],
    mcp_tools: Sequence[ToolDefinition],
    caps: dict[str, Any],
    user_id: str = DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> list[IndexRow]:
    """Return the rows the index should hold for one user/workspace.

    Single definition of the per-type indexing rules — which families are
    indexed, what is skipped, and the provenance/reconstruct payloads — so the
    completeness check and the indexing step cannot disagree. The runner and
    tool_reload both build the index from this, because two hand-written copies
    of these rules is what let tool_reload stop indexing native rows (#28) and
    left rows unrecoverable (#29).

    Callers pass catalogues they have already filtered for deployment policy;
    capability filtering is applied here so a scope=none tool is not treated as
    a missing row (which would re-index on every session while it is disabled).
    """
    from src.sdk.capabilities import resource_enabled
    from src.sdk.tools_custom import find_tool_file, is_core_tool, load_tool_meta
    from src.storage.paths import get_paths

    paths = get_paths(user_id=user_id, workspace_id=workspace_id)
    user_tools_dir = paths.user_tools_dir()
    workspace_tools_dir = paths.workspace_tools_dir()

    rows: list[IndexRow] = []

    for td in native_tools:
        if not is_core_tool(td.name) and resource_enabled(caps, "tools", td.name):
            rows.append(IndexRow(td.name, td, "native", "native"))

    for td in custom_tools:
        if not is_core_tool(td.name) and resource_enabled(caps, "tools", td.name):
            reconstruct_data = {"command": "", "install": [], "tool_dir": ""}
            tool_file = find_tool_file(td.name, user_tools_dir, workspace_tools_dir)
            if tool_file:
                meta = load_tool_meta(tool_file)
                if meta:
                    reconstruct_data = {
                        "command": meta.get("command", ""),
                        "install": meta.get("install", []),
                        "tool_dir": str(tool_file.parent),
                    }
            rows.append(IndexRow(td.name, td, "custom", "custom", reconstruct_data))

    for td in mcp_tools:
        if not is_core_tool(td.name) and resource_enabled(caps, "tools", td.name):
            parts = td.name.split("__", 2)
            server_name = parts[1] if len(parts) == 3 else ""
            rows.append(
                IndexRow(
                    td.name,
                    td,
                    "mcp",
                    f"mcp__{server_name}",
                    {"server_name": server_name, "mcp_tool_name": td.name},
                )
            )

    return rows


def index_rows(idx: ToolIndex, rows: Sequence[IndexRow]) -> None:
    """Upsert the desired rows (by unique name)."""
    for row in rows:
        idx.index_tool(
            row.td,
            tool_type=row.tool_type,
            namespace=row.namespace,
            reconstruct=row.reconstruct,
        )


def missing_index_rows(idx: ToolIndex, rows: Sequence[IndexRow]) -> set[str]:
    """Names in `rows` that the persisted index does not hold.

    Presence, not a row count: an index can be non-empty and still be missing
    rows whose sources never changed (#29).
    """
    return {row.name for row in rows} - set(idx.list_all_names())


def _rebuild_custom_function(
    td: ToolDefinition, reconstruct: dict[str, Any],
    user_id: str = DEFAULT_USER_ID, workspace_id: str = "personal",
) -> ToolDefinition:
    """Rebuild the function for a custom (TOOL.md) tool from reconstruct metadata."""
    command_template = reconstruct.get("command", "")
    install_cmds = reconstruct.get("install", [])
    tool_dir_str = reconstruct.get("tool_dir", "")
    command_timeout = td.annotations.timeout_seconds

    def fn(**kwargs: Any) -> str:
        from src.sdk.sandbox import custom_command_tools_allowed

        if not command_template.strip():
            # Issue #27: an index row rebuilt without its command used to run an
            # empty shell command and return "(no output)" — a non-error result
            # for a tool that never ran. Fail loudly instead; the loop converts
            # this into an is_error tool result.
            raise RuntimeError(
                f"Tool '{td.name}' has no command recorded in its index entry. "
                "Run tool_reload() to re-index custom tools. If the tool's "
                "directory name does not match its frontmatter 'name', the index "
                "cannot resolve its TOOL.md and a reload will not help."
            )

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
            started = time.monotonic()
            result = subprocess.run(
                rendered,
                shell=True,
                capture_output=True,
                timeout=command_timeout,
                text=True,
            )
            output = result.stdout + result.stderr
            if result.returncode < 0:
                # Issue #25: killed by a signal (RLIMIT_AS/CPU/NPROC/FSIZE
                # or another). Here returncode is the OS status directly,
                # so a negative value is a real signal — unlike the sandbox
                # seam, which also reports a synthetic -1 for its own
                # timeout and therefore needs an explicit flag. Returning
                # the partial output made governance record executed: true
                # for a command that never finished.
                from src.sdk.tool_results import raise_command_killed

                raise_command_killed(
                    " ".join(rendered.split()),
                    -result.returncode,
                    time.monotonic() - started,
                )
            if result.returncode != 0:
                return f"Command failed (exit {result.returncode}):\n{output[:2000]}"
            from src.sdk.tool_results import format_output

            return format_output(output, user_id, workspace_id)
        except subprocess.TimeoutExpired:
            from src.sdk.tool_results import raise_command_timeout

            raise_command_timeout(rendered, command_timeout, started)
        except CommandKilledError:
            # A signal kill must propagate: the catch-all below would turn it
            # back into a string and governance would record executed: true for
            # a command that never finished (issue #25).
            raise
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
        return [(name, description) for name, description, _ in self.search_with_tool_types(query, limit)]

    def search_with_tool_types(self, query: str, limit: int = 5) -> list[tuple[str, str, str]]:
        """Return indexed results with persisted provenance for policy filters."""
        rows = self.db.search("tools", "search_text", query, mode="hybrid", limit=limit)
        return [
            (row["name"], row["description"], str(row.get("tool_type", "")))
            for row in rows
        ]

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


def _hash_tool_file(tool_file: Path) -> str | None:
    """Hash a TOOL.md, tolerating unreadable files.

    The scan tolerates a bad file (logs custom_tool.skipped), so hashing must
    too: an unreadable TOOL.md in the admin-provisioned shared dir must not
    abort session construction (review P2 on #27).
    """
    try:
        return hashlib.sha256(tool_file.read_bytes()).hexdigest()
    except OSError:
        return None


def _iter_tool_dirs(scan_dir: Path) -> list[Path]:
    """Candidate tool directories under a Tools/ dir.

    Everything except the index's own bookkeeping directory counts as a
    source. Counting `Tools/.index` made the hash set depend on whether the
    index already existed: the first commit (written before `.index` was
    created) could therefore never match a later call, so
    `check_needs_reindex` reported a change and `idx.clear()` wiped every row.

    Only `.index` is skipped — not hidden directories in general — so hashing
    stays consistent with scan_tools_dir(), which loads any directory holding
    a TOOL.md, including dot-named ones.
    """
    if not scan_dir.exists():
        return []
    return [
        entry
        for entry in sorted(scan_dir.iterdir())
        if entry.is_dir() and entry.name != ".index"
    ]


def compute_source_hashes(
    tools_dir: Path,
    workspace_tools_dir: Path | None,
    mcp_config: Path,
) -> dict[str, str]:
    """Hash all tool sources for change detection."""
    hashes: dict[str, str] = {}

    if tools_dir.exists():
        for tool_dir in _iter_tool_dirs(tools_dir):
            tool_file = tool_dir / "TOOL.md"
            key = f"user:{tool_dir.name}"
            if tool_file.exists():
                digest = _hash_tool_file(tool_file)
                if digest is not None:
                    hashes[key] = digest
            else:
                hashes[key] = ""

    if workspace_tools_dir and workspace_tools_dir.exists():
        for tool_dir in _iter_tool_dirs(workspace_tools_dir):
            tool_file = tool_dir / "TOOL.md"
            key = f"workspace:{tool_dir.name}"
            if tool_file.exists():
                digest = _hash_tool_file(tool_file)
                if digest is not None:
                    hashes[key] = digest

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
