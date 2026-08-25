"""Filesystem tools — SDK-native implementation."""

import itertools
from contextvars import ContextVar
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import get_paths

logger = get_logger()

#: files_read refuses files larger than this (helpful message instead).
MAX_READ_FILE_SIZE = 50 * 1024 * 1024

_current_user_id: ContextVar[str] = ContextVar("current_user_id", default="default_user")
_current_workspace_id: ContextVar[str] = ContextVar("current_workspace_id", default="personal")


def set_user_id(user_id: str) -> None:
    _current_user_id.set(user_id)


def get_user_id() -> str:
    return _current_user_id.get()


def set_workspace_id(workspace_id: str) -> None:
    _current_workspace_id.set(workspace_id)


def _resolve_path(path: str | None, user_id: str, workspace_id: str = "personal") -> Path:
    if user_id == "default_user":
        user_id = _current_user_id.get()

    paths = get_paths(user_id, workspace_id=workspace_id)
    root_path = paths.workspace_files_dir().resolve()

    if path is None:
        return root_path

    from src.config import get_settings
    if get_settings().filesystem.workspace_root:
        if path.startswith("/"):
            resolved = Path(path).resolve()
            data_root = paths.root.resolve()
            if resolved.is_relative_to(data_root) or str(resolved) == str(data_root):
                return resolved
            raise ValueError(f"Absolute path outside EA root: {path}")
        return (root_path / path).resolve()

    if path.startswith("/"):
        resolved = Path(path).resolve()
        data_root = paths.root.resolve()
        if resolved.is_relative_to(data_root) or str(resolved) == str(data_root):
            return resolved
        raise ValueError(f"Absolute path outside EA root: {path}")

    is_skills_path = str(paths.user_skills_dir()) in path or path.startswith(
        "data/private/skills/"
    )
    is_tools_path = str(paths.user_tools_dir()) in path
    if is_skills_path:
        expected_prefix = str(paths.user_skills_dir()) + "/"
        if not (str((Path.cwd() / path).resolve())).startswith(
            expected_prefix
        ) and not path.startswith(expected_prefix):
            raise ValueError(f"Can only write to your own skills directory: {expected_prefix}")
        resolved = (Path.cwd() / path).resolve()
    elif str(paths.user_subagents_dir()) in path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(paths.user_subagents_dir()):
            raise ValueError(f"Path outside subagents directory: {path}")
        resolved = resolved
    elif is_tools_path:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(paths.user_tools_dir()):
            raise ValueError(f"Path outside tools directory: {path}")
    else:
        resolved = (root_path / path).resolve()
        if not resolved.is_relative_to(root_path):
            raise ValueError(f"Path outside user directory: {path}")

    return resolved


@tool
def files_list(path: str = ".", user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """List files in a directory.

    Args:
        path: Directory path relative to user files (default: current dir)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Formatted list of files and directories
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"Directory not found: {path}"

        if not target.is_dir():
            return f"Not a directory: {path}"

        items = []
        for item in sorted(target.iterdir()):
            item_type = "DIR" if item.is_dir() else "FILE"
            size = item.stat().st_size if item.is_file() else 0
            items.append(f"{item_type:6} {size:>10} {item.name}")

        if not items:
            return f"Empty directory: {path}"

        return "\n".join(["", *items, ""])
    except Exception as e:
        logger.error("files_list.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_list.annotations = ToolAnnotations(title="List Files", read_only=True, idempotent=True)


@tool
def files_read(path: str, offset: int = 0, limit: int = 100, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Read file content.

    Args:
        path: File path relative to user files
        offset: Line number to start from (default: 0)
        limit: Maximum number of lines to read (default: 100)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        File content or error message
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"File not found: {path}"

        if not target.is_file():
            return f"Not a file: {path}"

        try:
            if target.stat().st_size > MAX_READ_FILE_SIZE:
                size_mb = target.stat().st_size / (1024 * 1024)
                return (
                    f"File too large to read: {path} is {size_mb:.1f} MB "
                    f"(max {MAX_READ_FILE_SIZE // (1024 * 1024)} MB). "
                    "Use files_grep_search with a pattern or files_glob_search "
                    "to find the relevant section instead."
                )
        except OSError:
            pass

        with target.open(encoding="utf-8") as handle:
            lines = list(itertools.islice(handle, offset, offset + limit))

        total = offset + len(lines)
        content = "\n".join(lines)
        return f"--- {path} ({offset}-{total}/{target.stat().st_size} bytes) ---\n{content}"
    except Exception as e:
        logger.error("files_read.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_read.annotations = ToolAnnotations(title="Read File", read_only=True, idempotent=True)


@tool
def files_write(path: str, content: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Write content to a file (creates or overwrites).

    The path is relative to the workspace files directory. For skills,
    use the absolute path shown in the skills directory section of your
    system prompt (e.g. /Users/.../Skills/my-skill/SKILL.md).

    Args:
        path: File path (relative to workspace files, or absolute)
        content: Content to write
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        old_content = None
        if target.exists() and target.is_file():
            old_content = target.read_text(encoding="utf-8")
            if old_content != content:
                try:
                    from src.sdk.tools_core.file_versioning import capture_version

                    capture_version(user_id, path, content, workspace_id)
                except Exception as e:
                    logger.error(
                        "version_check_error", {"path": path, "error": str(e)}, user_id=user_id
                    )
        elif target.exists() and target.is_dir():
            return f"Cannot write to directory: {path}"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        logger.info("files_write", {"path": str(target), "size": len(content)}, user_id=user_id)
        return f"Successfully wrote to {path}"
    except Exception as e:
        logger.error("files_write.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_write.annotations = ToolAnnotations(title="Write File", destructive=True)


@tool
def files_edit(path: str, old: str, new: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Edit a file by replacing text.

    Args:
        path: File path relative to user files
        old: Text to replace
        new: Replacement text
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"File not found: {path}"

        if not target.is_file():
            return f"Not a file: {path}"

        content = target.read_text(encoding="utf-8")

        if old not in content:
            return f"Text not found in file: {old[:50]}..."

        new_content = content.replace(old, new)

        from src.sdk.tools_core.file_versioning import capture_version

        capture_version(user_id, path, new_content, workspace_id=workspace_id)

        target.write_text(new_content, encoding="utf-8")

        logger.info("files_edit", {"path": str(target)}, user_id=user_id)
        return f"Edited {path}"
    except Exception as e:
        logger.error("files_edit.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_edit.annotations = ToolAnnotations(title="Edit File", destructive=True)


@tool
def files_delete(path: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Delete a file.

    NOTE: This tool requires human approval before execution.

    Args:
        path: File path relative to user files
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"File not found: {path}"

        if target.is_dir():
            import shutil

            shutil.rmtree(target)
        else:
            target.unlink()

        logger.info("files_delete", {"path": str(target)}, user_id=user_id)
        return f"Deleted {path}"
    except Exception as e:
        logger.error("files_delete.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_delete.annotations = ToolAnnotations(title="Delete File", destructive=True)


@tool
def files_mkdir(path: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Create a directory.

    Args:
        path: Directory path relative to user files
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        if target.exists():
            return f"Path already exists: {path}"

        target.mkdir(parents=True, exist_ok=True)

        logger.info("files_mkdir", {"path": str(target)}, user_id=user_id)
        return f"Created directory: {path}"
    except Exception as e:
        logger.error("files_mkdir.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_mkdir.annotations = ToolAnnotations(title="Create Directory")


@tool
def files_rename(path: str, new_name: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Rename a file or directory.

    Args:
        path: Current path relative to user files
        new_name: New name for the file or directory
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"Path not found: {path}"

        if "/" in new_name or "\\" in new_name or ".." in new_name:
            return f"Invalid name: '{new_name}'. Must be a simple filename without path separators."

        new_path = target.parent / new_name

        root_path = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir().resolve()
        if not new_path.resolve().is_relative_to(root_path):
            return f"Invalid name: '{new_name}' resolves outside user directory."

        if new_path.exists():
            return f"Name already exists: {new_name}"

        target.rename(new_path)

        logger.info("files_rename", {"old": str(target), "new": str(new_path)}, user_id=user_id)
        return f"Renamed {path} to {new_name}"
    except Exception as e:
        logger.error(
            "files_rename.error",
            {"path": path, "new_name": new_name, "error": str(e)},
            user_id=user_id,
        )
        return f"Error: {e}"


files_rename.annotations = ToolAnnotations(title="Rename File", destructive=True)
