from __future__ import annotations

import inspect
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from src.sdk.tools import ToolAnnotations, ToolDefinition
from src.storage.paths import DEFAULT_USER_ID

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
}


def _parse_tool_file(tool_path: Path) -> ToolDefinition | None:
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
        read_only=annotations_raw.get("read_only", True) if annotations_raw else True,
        destructive=annotations_raw.get("destructive", False) if annotations_raw else False,
        idempotent=annotations_raw.get("idempotent", False) if annotations_raw else False,
        open_world=annotations_raw.get("open_world", False) if annotations_raw else False,
    )

    def make_function(tmpl: str, install_cmds: list[str] | None, tool_dir: Path | None = None) -> Any:
        import subprocess as _subprocess

        def fn(**kwargs: Any) -> str:
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
            except (_subprocess.CalledProcessError, FileNotFoundError, OSError):
                if install_cmds:
                    return (
                        f"Tool '{tool_name}' not found. Install it with one of:\n"
                        + "\n".join(f"  {c}" for c in install_cmds)
                    )
                return f"Tool '{tool_name}' not found on PATH."

            try:
                result = _subprocess.run(
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
            except _subprocess.TimeoutExpired:
                return "Command timed out after 120 seconds."
            except Exception as e:
                return f"Command error: {e}"

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


def scan_tools_dir(tools_dir: Path) -> list[ToolDefinition]:
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
        td = _parse_tool_file(tool_file)
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

    shared_tools = scan_tools_dir(paths.workspace_tools_dir())
    user_tools = scan_tools_dir(paths.user_tools_dir())

    merged = {t.name: t for t in shared_tools}
    for t in user_tools:
        merged[t.name] = t

    return list(merged.values())


def is_core_tool(name: str) -> bool:
    return name in CORE_TOOL_NAMES


def find_tool_file(name: str, user_dir: Path, workspace_dir: Path | None) -> Path | None:
    for d in [workspace_dir, user_dir]:
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
