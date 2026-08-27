"""Shell tool — SDK-native implementation."""

import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import DEFAULT_USER_ID, get_paths

logger = get_logger()

DEFAULT_ALLOWED_COMMANDS = {
    "python3",
    "node",
    "echo",
    "date",
    "whoami",
    "pwd",
}

SHELL_METACHARACTERS = re.compile(r"[;|&$`\n\r\\!>{<]")

SHELL_INJECTION_PATTERNS = re.compile(
    r"(?:"
    r"\$\(|"
    r"\`|"
    r"\.\.\/|"
    r"~\/|"
    r"\/etc\/|"
    r"\/tmp\/"
    r")",
    re.IGNORECASE,
)


def _get_shell_config() -> Any:
    """Return shell tool configuration from settings."""
    settings = get_settings()
    shell_config = getattr(settings, "shell_tool", None)
    if shell_config:
        return {
            "allowed_commands": set(shell_config.allowed_commands),
            "timeout_seconds": getattr(shell_config, "timeout_seconds", 30),
            "max_output_kb": getattr(shell_config, "max_output_kb", 100),
        }
    return {
        "allowed_commands": DEFAULT_ALLOWED_COMMANDS,
        "timeout_seconds": 30,
        "max_output_kb": 100,
    }


def _get_root_path(user_id: str, workspace_id: str = "personal") -> Path:
    root = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_allowed(cmd: str) -> bool:
    config = _get_shell_config()
    allowed = config["allowed_commands"]
    cmd_base = cmd.split()[0] if cmd.split() else ""
    return cmd_base in allowed


def _validate_command(command: str) -> str | None:
    if not command.strip():
        return "Empty command"

    if SHELL_METACHARACTERS.search(command):
        return (
            "Command rejected: contains shell metacharacters. "
            "Only simple commands are allowed (no ; & | $ ` etc.)."
        )

    if SHELL_INJECTION_PATTERNS.search(command):
        return "Command rejected: contains potentially dangerous patterns."

    cmd_parts = command.split()
    if not cmd_parts:
        return "Empty command"

    cmd_base = cmd_parts[0]
    if "/" in cmd_base or "\\" in cmd_base:
        return f"Command rejected: use command name only, not paths (got '{cmd_base}')."

    return None


def _sweep_old_spill_files(out_dir: Path, max_age_days: int = 7) -> int:
    """Delete spilled ``output-*.txt`` files older than *max_age_days*.

    Spill files accumulate forever otherwise (audit B17). Called
    opportunistically whenever a new spill is written. Returns the number
    of files removed.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        for f in out_dir.glob("output-*.txt"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


@tool
def shell_execute(command: str, user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal") -> str:
    """Run a shell command.

    Args:
        command: Command to execute
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Command output or error message
    """
    try:
        validation_error = _validate_command(command)
        if validation_error:
            return validation_error

        cmd_parts = command.split()
        cmd_base = cmd_parts[0]
        is_allowed_cmd = _is_allowed(cmd_base)

        if not is_allowed_cmd:
            config = _get_shell_config()
            return f"Command not allowed: {cmd_base}. Allowed: {', '.join(sorted(config['allowed_commands']))}"

        root_path = _get_root_path(user_id, workspace_id)
        config = _get_shell_config()

        result = subprocess.run(
            cmd_parts,
            shell=False,
            cwd=str(root_path),
            capture_output=True,
            text=True,
            timeout=config["timeout_seconds"],
        )

        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"

        max_output = config["max_output_kb"] * 1024
        if len(output) > max_output:
            # Spill the full output to a file the agent can read back via
            # files_read (Pi-style: truncate in context, keep full output
            # recoverable). The file lives under the workspace files dir so
            # it is within the agent's readable path scope.
            out_dir = root_path / ".shell_output"
            out_dir.mkdir(parents=True, exist_ok=True)
            _sweep_old_spill_files(out_dir)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            full_path = out_dir / f"output-{ts}.txt"
            try:
                full_path.write_text(output, encoding="utf-8")
                output = (
                    output[:max_output]
                    + f"\n... (truncated, output exceeded {config['max_output_kb']}KB)\n"
                    f"Full output: {full_path}"
                )
            except OSError:
                output = (
                    output[:max_output]
                    + f"\n... (truncated, output exceeded {config['max_output_kb']}KB)"
                )

        logger.info(
            "shell_execute", {"command": command, "return_code": result.returncode}, user_id=user_id
        )
        return output or "(no output)"

    except subprocess.TimeoutExpired:
        config = _get_shell_config()
        return f"Error: Command timed out after {config['timeout_seconds']} seconds"
    except FileNotFoundError:
        return f"Error: Command not found: {cmd_parts[0]}"
    except Exception as e:
        logger.error("shell_execute.error", {"command": command, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


shell_execute.annotations = ToolAnnotations(
    title="Execute Shell Command",
    destructive=True,
    open_world=True,
)
