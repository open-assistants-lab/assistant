from __future__ import annotations

import inspect
import re
import shlex
import time
from pathlib import Path
from typing import Any

import yaml

from src.app_logging import get_logger
from src.sdk.tool_results import CommandKilledError
from src.sdk.tools import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ToolAnnotations,
    ToolDefinition,
    ToolResult,
)
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

CORE_TOOL_NAMES: set[str] = {
    "shell_execute",
    "files_read",
    "files_write",
    "files_edit",
    "message_search",
    "time_get",
    "web_search",
    "skills_load",
    "subagent_delegate",
    "mcp_reload",
    "tool_search",
    "tool_reload",
    "tool_result_read",
}


def run_custom_command(
    rendered: str,
    user_id: str = DEFAULT_USER_ID,
    workspace_id: str = "personal",
    timeout_seconds: float | None = None,
    pipefail: bool = False,
) -> str | ToolResult:
    """Execute a rendered TOOL.md command through the sandbox seam (issue #34).

    TOOL.md commands are shell strings, so the single argv invocation
    ``["sh", "-c", rendered]`` gives them the same transport as every other
    command path: RLIMIT_FSIZE/CPU (plus AS where the platform supports it),
    the uid drop, the scrubbed env, and the workspace-forced cwd. The capture
    and write budgets come from the operator's ``shell_tool.*`` settings; the
    wall-clock cap comes from the tool's own ``annotations.timeout_seconds``
    (#23).

    Raises the same distinct failures as ``shell_execute``: a timeout or a
    signal kill propagates instead of returning a success string, so
    governance never receipts a command that did not finish (#24/#25).
    """
    from src.config import get_settings
    from src.sdk.sandbox import SandboxLimits, get_sandbox_backend
    from src.sdk.tool_results import format_output, raise_command_killed, raise_timeout
    from src.storage.paths import get_paths

    cfg = getattr(get_settings(), "shell_tool", None)
    limits = SandboxLimits(
        timeout_seconds=timeout_seconds,
        max_output_bytes=int(getattr(cfg, "max_output_kb", 100)) * 1024,
        max_write_bytes=int(getattr(cfg, "max_write_mb", 64)) * 1024 * 1024,
    )
    root_path = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()
    root_path.mkdir(parents=True, exist_ok=True)

    command = " ".join(rendered.split())
    started = time.monotonic()
    argv = ["bash", "-o", "pipefail", "-c", rendered] if pipefail else ["sh", "-c", rendered]
    result = get_sandbox_backend().run(
        argv, root_path, limits, user_id=user_id
    )
    elapsed = time.monotonic() - started

    if result.timed_out:
        raise_timeout(command, timeout_seconds, elapsed)
    if result.signalled:
        raise_command_killed(
            command, -result.exit_code if result.exit_code < 0 else None, elapsed
        )
    if 128 < result.exit_code <= 192:
        # Issue #32 part 2: a pipeline member's signal death is reported by
        # the shell as 128+n (positive), which the signalled flag misses.
        raise_command_killed(command, result.exit_code - 128, elapsed)

    output = result.stdout + result.stderr
    if result.exit_code != 0:
        message = f"Command failed (exit {result.exit_code}):\n{output[:2000]}"
        if pipefail:
            return ToolResult(
                content=message,
                structured_content={
                    "executed": False,
                    "error": "command_failed",
                    "exit_code": result.exit_code,
                    "outcome": "failed",
                },
                is_error=True,
            )
        return message
    return format_output(output, user_id, workspace_id)


def _parse_tool_file(
    tool_path: Path, user_id: str = DEFAULT_USER_ID, workspace_id: str = "personal",
) -> ToolDefinition | None:
    """Parse a TOOL.md file and return a ToolDefinition with a shell-execute wrapper."""
    if not tool_path.exists():
        return None

    content = tool_path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        meta = yaml.safe_load(parts[1].strip())
    except yaml.YAMLError:
        return None

    if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
        return None

    name: str = meta["name"]
    description: str = meta["description"]
    command_template: str | None = meta.get("command")
    parameters: dict[str, Any] | None = meta.get("parameters")
    annotations_raw: dict[str, Any] | None = meta.get("annotations")
    output_schema: dict[str, Any] | None = meta.get("output_schema")
    install: list[str] | None = meta.get("install")

    if not command_template:
        return None

    if parameters is None:
        parameters = _extract_params_from_command(command_template)
    else:
        parameters = _normalize_parameters_schema(parameters)

    annotations = ToolAnnotations(
        title=annotations_raw.get("title") if annotations_raw else None,
        timeout_seconds=(
            annotations_raw.get("timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS)
            if annotations_raw
            else DEFAULT_COMMAND_TIMEOUT_SECONDS
        ),
        read_only=annotations_raw.get("read_only", True) if annotations_raw else True,
        destructive=annotations_raw.get("destructive", False) if annotations_raw else False,
        idempotent=annotations_raw.get("idempotent", False) if annotations_raw else False,
        open_world=annotations_raw.get("open_world", False) if annotations_raw else False,
        requires_approval=annotations_raw.get("requires_approval", False) if annotations_raw else False,
        execution_mode=annotations_raw.get("execution_mode", "sync") if annotations_raw else "sync",
        pipefail=annotations_raw.get("pipefail", False) if annotations_raw else False,
        executor=annotations_raw.get("executor") if annotations_raw else None,
    )

    def make_function(tmpl: str, install_cmds: list[str] | None, tool_dir: Path | None = None) -> Any:
        import subprocess as _subprocess

        command_timeout = annotations.timeout_seconds

        def fn(**kwargs: Any) -> ToolResult | str:
            from src.sdk.sandbox import custom_command_tools_allowed

            if not custom_command_tools_allowed():
                return "Custom command tools are disabled by the hard sandbox backend."
            rendered = tmpl
            if tool_dir:
                rendered = rendered.replace("{{tool_dir}}", shlex.quote(str(tool_dir)))
            for k, v in kwargs.items():
                rendered = rendered.replace("{{" + k + "}}", shlex.quote(str(v)))
            # Issue #14: unfilled optional placeholders must not render
            # literally ("{{user}}" sent to the downstream API). Strip any
            # remaining {{param}} (unfilled optional params default to empty
            # — required-unfilled params fail validation before this point).
            rendered = re.sub(r"\{\{[A-Za-z0-9_]+\}\}", "", rendered)

            tool_name = tmpl.split()[0]
            try:
                _subprocess.run(
                    ["which", tool_name],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
            except _subprocess.TimeoutExpired:
                # An unanswered probe says nothing about the tool (see the
                # reconstructed-wrapper twin in tool_index.py).
                return ToolResult(
                    content=                    f"Tool '{tool_name}' availability could not be verified: "
                    "the PATH probe timed out.",
                    is_error=True,
                )
            except (
                _subprocess.CalledProcessError,
                FileNotFoundError,
                OSError,
            ):
                if install_cmds:
                    return (
                        f"Tool '{tool_name}' not found. Install it with one of:\n"
                        + "\n".join(f"  {c}" for c in install_cmds)
                    )
                return f"Tool '{tool_name}' not found on PATH."

            try:
                return run_custom_command(
                    rendered,
                    user_id,
                    workspace_id,
                    command_timeout,
                    pipefail=annotations.pipefail,
                )
            except (_subprocess.TimeoutExpired, CommandKilledError):
                # A cap-killed or signal-killed command must propagate: the
                # catch-all below would turn it back into a string and
                # governance would record executed: true for a command that
                # never finished (issues #24/#25).
                raise
            except Exception as e:
                return ToolResult(content=f"Command error: {e}", is_error=True)

        fn.__name__ = name
        properties = parameters.get("properties", {})
        required = set(parameters.get("required", []))
        fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [inspect.Parameter(
                param_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=(inspect.Parameter.empty if param_name in required else None),
            ) for param_name in properties]
        )
        return fn

    return ToolDefinition(
        name=name,
        description=description,
        parameters=parameters,
        annotations=annotations,
        output_schema=output_schema,
        function=make_function(command_template, install, tool_dir=tool_path.parent),
    )


def _extract_params_from_command(command: str) -> dict[str, Any]:
    """Extract JSON Schema from {{param}} placeholders in a command template."""
    import re

    placeholders = re.findall(r"\{\{(\w+)\}\}", command)
    properties = {}
    for p in placeholders:
        properties[p] = {"type": "string", "description": f"Value for {p}"}

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
    }


def _normalize_parameters_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    """Normalize YAML-loaded input schema while preserving declared string values."""
    schema = dict(parameters)
    schema.setdefault("type", "object")
    raw_properties = schema.get("properties", {})
    if not isinstance(raw_properties, dict):
        raw_properties = {}
    properties: dict[str, Any] = {}
    for raw_name, raw_property in raw_properties.items():
        name = str(raw_name)
        prop = dict(raw_property) if isinstance(raw_property, dict) else {"type": "string"}
        if prop.get("type") == "string" and isinstance(prop.get("enum"), list):
            prop["enum"] = [str(value).lower() if isinstance(value, bool) else value for value in prop["enum"]]
        properties[name] = prop
    schema["properties"] = properties
    required = schema.get("required", [])
    schema["required"] = [str(name) for name in required] if isinstance(required, list) else []
    return schema


def scan_tools_dir(
    tools_dir: Path, user_id: str = DEFAULT_USER_ID, workspace_id: str = "personal",
) -> list[ToolDefinition]:
    """Scan a Tools/ directory for TOOL.md files and return ToolDefinitions."""
    results: list[ToolDefinition] = []
    if not tools_dir.exists():
        return results

    for entry in sorted(tools_dir.iterdir()):
        if not entry.is_dir():
            continue
        tool_file = entry / "TOOL.md"
        if not tool_file.exists():
            continue
        # Issue #23 review F3: a malformed declaration must skip only its own
        # tool. Parsing happens during loop construction, so an unguarded
        # raise here would take down the whole session over one typo.
        try:
            td = _parse_tool_file(tool_file, user_id, workspace_id)
        except Exception as e:
            logger.warning(
                "custom_tool.skipped",
                {"tool_file": str(tool_file), "error": str(e), "error_type": type(e).__name__},
                user_id=user_id,
            )
            continue
        if td:
            results.append(td)

    return results


def get_custom_tools(user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal") -> list[ToolDefinition]:
    """Load custom tools from shared (deployment) and per-user dirs.

    Merge order: deployment-shared Tools/ is the base; per-user Tools/
    OVERRIDES same-name shared tools (per-user customization wins —
    consistent with user settings beating host config everywhere else).
    """
    from src.storage.paths import get_paths

    paths = get_paths(user_id=user_id, workspace_id=workspace_id)

    shared_tools = scan_tools_dir(paths.workspace_tools_dir(), user_id, workspace_id)
    user_tools = scan_tools_dir(paths.user_tools_dir(), user_id, workspace_id)

    merged = {t.name: t for t in shared_tools}
    for t in user_tools:
        merged[t.name] = t

    return list(merged.values())


def is_core_tool(name: str) -> bool:
    return name in CORE_TOOL_NAMES


def find_tool_file(name: str, user_dir: Path, workspace_dir: Path | None) -> Path | None:
    # Precedence must match get_custom_tools(): the per-user Tools/ OVERRIDES a
    # same-name shared tool, so the user copy resolves first. Shared-first made
    # the runner record the shared file's command for a tool the model saw as
    # the user's (review P2 on #27).
    for d in [user_dir, workspace_dir]:
        if d and d.exists():
            candidate = d / name / "TOOL.md"
            if candidate.exists():
                return candidate
    return None


def load_tool_meta(tool_file: Path) -> dict[str, Any] | None:
    content = tool_file.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    import yaml
    try:
        meta: dict[str, Any] | None = yaml.safe_load(parts[1].strip())
        return meta
    except yaml.YAMLError:
        return None
