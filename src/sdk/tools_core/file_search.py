"""File search tools — SDK-native implementation."""

import os
import re
from collections import Counter
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import DEFAULT_USER_ID, get_paths

logger = get_logger()

#: Directories never walked during grep (version control + hidden).
_VCS_DIRS = {".git", ".hg", ".svn", ".bzr"}
#: Extensions treated as binary — skipped during grep walks.
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".pdf", ".zip", ".gz", ".tar", ".7z", ".rar", ".bz2", ".xz", ".zst",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".ogg", ".m4a",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".img",
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".pyd", ".class", ".jar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".o", ".a", ".lib", ".obj",
    ".min.js", ".min.css", ".map", ".lock", ".parquet", ".orc", ".arrow",
}
#: Maximum matches returned by a non-count grep (walk stops at this many).
_GREP_DISPLAY_CAP = 100
#: Maximum files listed by files_glob_search.
_GLOB_RESULT_CAP = 500


def _is_binary(path: Path) -> bool:
    return path.suffix.lower() in _BINARY_EXTS


def _get_root_path(user_id: str, workspace_id: str = "personal") -> Path:
    return get_paths(user_id, workspace_id=workspace_id).workspace_files_dir()


def _resolve_path(path: str | None, user_id: str, workspace_id: str = "personal") -> Path:
    root = _get_root_path(user_id, workspace_id).resolve()

    if path is None:
        return root

    paths = get_paths(user_id, workspace_id=workspace_id)
    is_skills_path = str(paths.user_skills_dir()) in path or path.startswith(
        "data/private/skills/"
    )
    if is_skills_path:
        expected_prefix = str(paths.user_skills_dir()) + "/"
        if not (str((Path.cwd() / path).resolve())).startswith(
            expected_prefix
        ) and not path.startswith(expected_prefix):
            raise ValueError(f"Can only search in your own skills directory: {expected_prefix}")
        resolved = (Path.cwd() / path).resolve()
    else:
        resolved = (root / path).resolve()

        if not resolved.is_relative_to(root):
            raise ValueError(f"Path outside user directory: {path}")

    return resolved


@tool
def files_glob_search(pattern: str = "**/*", path: str = ".", user_id: str =  DEFAULT_USER_ID, workspace_id: str = "personal") -> str:
    """Search for files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g., "*.py", "**/*.txt")
        path: Directory to search in (default: current dir)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        List of matching files
    """
    try:
        root = _get_root_path(user_id, workspace_id)
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"Directory not found: {path}"

        matches = list(target.glob(pattern))
        matches = [m for m in matches if str(m).startswith(str(root))]

        if not matches:
            return f"No files matching {pattern} in {path}"

        matches.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        results = []
        for m in matches[:_GLOB_RESULT_CAP]:
            rel_path = m.relative_to(root)
            size = m.stat().st_size if m.is_file() else 0
            results.append(f"{rel_path} ({size} bytes)")

        shown = len(results)
        if len(matches) > _GLOB_RESULT_CAP:
            results.append(f"... and {len(matches) - _GLOB_RESULT_CAP} more files")
        results.append(f"{shown} files found")
        return "\n".join(["", *results])
    except Exception as e:
        logger.error(
            "files_glob_search.error", {"pattern": pattern, "error": str(e)}, user_id=user_id
        )
        return f"Error: {e}"


files_glob_search.annotations = ToolAnnotations(
    title="Glob Search Files", read_only=True, idempotent=True
)


@tool
def files_grep_search(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    count: bool = False,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Search file contents using regex.

    Args:
        pattern: Regex pattern to search for
        path: Directory to search in (default: current dir)
        include: File pattern to filter (e.g., "*.py", "*.txt")
        count: If True, return only count of matches
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Matching lines with context
    """
    try:
        root = _get_root_path(user_id, workspace_id)
        target = _resolve_path(path, user_id, workspace_id)

        if not target.exists():
            return f"Directory not found: {path}"

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex: {e}"

        matches = []
        max_size = 10 * 1024 * 1024
        capped = False

        try:
            walk_root = target.resolve()
            for dir_path, dir_names, file_names in os.walk(walk_root):
                # Prune dot-dirs and VCS dirs in place so os.walk never descends.
                dir_names[:] = [
                    d for d in dir_names if not d.startswith(".") and d not in _VCS_DIRS
                ]
                for file_name in file_names:
                    file_path = Path(dir_path) / file_name

                    if include is not None and not Path(file_name).match(include):
                        continue
                    if not file_path.is_file() or _is_binary(file_path):
                        continue

                    try:
                        if file_path.stat().st_size > max_size:
                            continue
                    except OSError:
                        continue

                    if not str(file_path).startswith(str(root)):
                        continue

                    try:
                        with file_path.open(encoding="utf-8", errors="ignore") as fh:
                            for line_num, line in enumerate(fh, 1):
                                if regex.search(line):
                                    rel_path = file_path.relative_to(root)
                                    if count:
                                        matches.append(f"{rel_path}:{line_num}")
                                    else:
                                        matches.append(f"{rel_path}:{line_num}: {line[:200]}")
                                        if len(matches) >= _GREP_DISPLAY_CAP:
                                            capped = True
                                            break
                            if capped:
                                break
                    except Exception:
                        continue
                if capped:
                    break
        except OSError:
            return f"Directory not found: {path}"

        if not matches:
            return f"No matches for '{pattern}' in {path}"

        if count:
            file_counts = Counter(m.split(":")[0] for m in matches)
            result = [f"{k}: {v} matches" for k, v in file_counts.most_common()]
        else:
            result = matches[:_GREP_DISPLAY_CAP]
            if capped and len(matches) >= _GREP_DISPLAY_CAP:
                result.append(f"... and more matches (capped at {_GREP_DISPLAY_CAP})")
            elif len(matches) > _GREP_DISPLAY_CAP:
                result.append(f"... and {len(matches) - _GREP_DISPLAY_CAP} more matches")

        return "\n".join(["", *result, ""])
    except Exception as e:
        logger.error(
            "files_grep_search.error", {"pattern": pattern, "error": str(e)}, user_id=user_id
        )
        return f"Error: {e}"


files_grep_search.annotations = ToolAnnotations(
    title="Grep Search Files", read_only=True, idempotent=True
)
