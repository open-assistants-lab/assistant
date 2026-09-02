"""code_execute — SB1-3 thin consumer: runs code through the SandboxBackend.

The backend (null|soft) is a settings choice; swapping isolation level is a
config change, not a code change. Static write-path validation runs BEFORE
spawn on soft backends (SB1-2).
"""

from __future__ import annotations

from pathlib import Path

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.sandbox import SandboxLimits, get_sandbox_backend
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import DEFAULT_USER_ID, get_paths

logger = get_logger()


def _workspace_root(user_id: str, workspace_id: str) -> Path:
    root = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_limits() -> SandboxLimits:
    settings = get_settings()
    shell_cfg = getattr(settings, "shell_tool", None)
    timeout = getattr(shell_cfg, "timeout_seconds", 30) if shell_cfg else 30
    max_kb = getattr(shell_cfg, "max_output_kb", 100) if shell_cfg else 100
    return SandboxLimits(
        timeout_seconds=float(timeout),
        max_output_bytes=max_kb * 1024,
    )


@tool
def code_execute(code: str, user_id: str = DEFAULT_USER_ID, workspace_id: str = "personal") -> str:
    """Execute a Python code snippet in the sandboxed workspace.

    Args:
        code: Python source to execute (stdout is returned)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Program output, or a rejection/error message
    """
    backend = get_sandbox_backend()
    root = _workspace_root(user_id, workspace_id)

    rejection = backend.validate_source(code, root)
    if rejection:
        logger.info(
            "code_execute.rejected", {"reason": str(rejection)}, user_id=user_id
        )
        return str(rejection)

    limits = _get_limits()
    result = backend.run(
        ["python3", "-c", code],
        root,
        limits,
    )
    logger.info(
        "code_execute",
        {"exit_code": result.exit_code, "timed_out": result.timed_out},
        user_id=user_id,
    )
    if result.timed_out:
        return f"Error: code execution timed out after {limits.timeout_seconds}s"
    output = result.stdout
    if result.stderr:
        output += f"\nSTDERR: {result.stderr}"
    return output or "(no output)"


code_execute.annotations = ToolAnnotations(
    title="Execute Code (sandboxed)",
    destructive=True,
    open_world=True,
)
