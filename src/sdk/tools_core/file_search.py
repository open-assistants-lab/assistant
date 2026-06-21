"""File search tools — SDK-native implementation."""

import re
from collections import Counter
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import get_paths

logger = get_logger()


def _get_root_path(user_id: str) -> Path:
    return get_paths(user_id).user_files_dir()


def _resolve_path(path: str | None, user_id: str) -> Path:
    root = _get_root_path(user_id).resolve()

    if path is None:
        return root

    paths = get_paths(user_id)
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
def files_glob_search(pattern: str = "**/*", path: str = ".", user_id: str = "default_user") -> str:
    """Search for files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g., "*.py", "**/*.txt")
        path: Directory to search in (default: current dir)
        user_id: User identifier

    Returns:
        List of matching files
    """
    try:
        root = _get_root_path(user_id)
        target = _resolve_path(path, user_id)

        if not target.exists():
            return f"Directory not found: {path}"

        matches = list(target.glob(pattern))
        matches = [m for m in matches if str(m).startswith(str(root))]

        if not matches:
            return f"No files matching {pattern} in {path}"

        matches.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        results = []
        for m in matches:
            rel_path = m.relative_to(root)
            size = m.stat().st_size if m.is_file() else 0
            results.append(f"{rel_path} ({size} bytes)")

        return "\n".join(["", *results, f"\n{len(results)} files found"])
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
    user_id: str = "default_user",
) -> str:
    """Search file contents using regex.

    Args:
        pattern: Regex pattern to search for
        path: Directory to search in (default: current dir)
        include: File pattern to filter (e.g., "*.py", "*.txt")
        count: If True, return only count of matches
        user_id: User identifier

    Returns:
        Matching lines with context
    """
    try:
        root = _get_root_path(user_id)
        target = _resolve_path(path, user_id)

        if not target.exists():
            return f"Directory not found: {path}"

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Invalid regex: {e}"

        matches = []
        max_size = 10 * 1024 * 1024

        for file_path in target.rglob(include or "*"):
            if not file_path.is_file():
                continue

            try:
                if file_path.stat().st_size > max_size:
                    continue
            except OSError:
                continue

            if not str(file_path).startswith(str(root)):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for line_num, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    rel_path = file_path.relative_to(root)
                    if count:
                        matches.append(f"{rel_path}:{line_num}")
                    else:
                        matches.append(f"{rel_path}:{line_num}: {line[:200]}")

        if not matches:
            return f"No matches for '{pattern}' in {path}"

        if count:
            file_counts = Counter(m.split(":")[0] for m in matches)
            result = [f"{k}: {v} matches" for k, v in file_counts.most_common()]
        else:
            result = matches[:100]
            if len(matches) > 100:
                result.append(f"... and {len(matches) - 100} more matches")

        return "\n".join(["", *result, ""])
    except Exception as e:
        logger.error(
            "files_grep_search.error", {"pattern": pattern, "error": str(e)}, user_id=user_id
        )
        return f"Error: {e}"


files_grep_search.annotations = ToolAnnotations(
    title="Grep Search Files", read_only=True, idempotent=True
)
