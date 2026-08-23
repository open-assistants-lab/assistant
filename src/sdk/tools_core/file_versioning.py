"""File versioning system — SDK-native implementation."""

import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import get_paths

logger = get_logger()


def _get_version_root(user_id: str, workspace_id: str = "personal") -> Path:
    return get_paths(user_id, workspace_id=workspace_id).versions_dir()


def _resolve_path(path: str | None, user_id: str, workspace_id: str = "personal") -> Path:
    root_path = get_paths(user_id, workspace_id=workspace_id).workspace_files_dir().resolve()

    if path is None:
        return root_path

    if path.startswith("/"):
        raise ValueError(f"Use relative paths only. Path: {path}")

    resolved = (root_path / path).resolve()
    if not resolved.is_relative_to(root_path):
        raise ValueError(f"Path outside user directory: {path}")

    return resolved


_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def _validate_version(ver_dir: Path, version: str) -> Path | None:
    """Validate a version identifier and return its contained path.

    Audit B9: the ``version`` parameter used to be joined onto the versions
    directory unvalidated, letting a crafted relative path read (restore)
    or unlink/rmtree (delete) arbitrary locations. A version must be a
    plain timestamp AND must resolve inside ``ver_dir``.
    Returns ``None`` when the identifier is invalid or escapes containment.
    """
    if not _VERSION_RE.match(version):
        return None
    vf = (ver_dir / version).resolve()
    return vf if vf.is_relative_to(ver_dir.resolve()) else None


def _version_path(user_id: str, file_path: str, workspace_id: str = "personal") -> Path:
    root = _get_version_root(user_id, workspace_id)
    resolved = (root / file_path).resolve()
    # Audit B9 (reviewer-found gap): ``path`` itself was never contained,
    # so files_versions_delete(path="../../..", version=None) rmtree'd an
    # arbitrary directory and traversal paths leaked listings. Guard covers
    # list/restore/delete in one place.
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside versions directory: {file_path}")
    return resolved


def capture_version(user_id: str, file_path: str, new_content: str, workspace_id: str = "personal") -> str | None:
    try:
        target = _resolve_path(file_path, user_id, workspace_id)

        if not target.exists() or not target.is_file():
            return None

        current_content = target.read_text(encoding="utf-8")
        if current_content == new_content:
            return None

        ver_dir = _version_path(user_id, file_path, workspace_id)
        ver_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        version_file = ver_dir / timestamp

        version_file.write_text(current_content, encoding="utf-8")

        logger.info("version_captured", {"path": file_path, "version": timestamp}, user_id=user_id)
        return timestamp
    except Exception as e:
        logger.error("version_capture.error", {"path": file_path, "error": str(e)}, user_id=user_id)
        return None


@tool
def files_versions_list(path: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """List all versions of a file.

    Args:
        path: File path relative to user files
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        List of versions with timestamps
    """
    try:
        ver_dir = _version_path(user_id, path, workspace_id)

        if not ver_dir.exists():
            return f"No versions found for: {path}"

        versions = sorted(ver_dir.iterdir(), reverse=True)

        if not versions:
            return f"No versions found for: {path}"

        result = [f"Versions for: {path}", ""]
        for v in versions:
            size = v.stat().st_size
            result.append(f"{v.name}  ({size} bytes)")

        return "\n".join(result)
    except Exception as e:
        logger.error("versions_list.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_versions_list.annotations = ToolAnnotations(
    title="List File Versions", read_only=True, idempotent=True
)


@tool
def files_versions_restore(path: str, version: str, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Restore a file to a specific version.

    Args:
        path: File path relative to user files
        version: Version timestamp to restore (e.g., "2026-03-16T10-30-00")
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        ver_dir = _version_path(user_id, path, workspace_id)
        version_file = _validate_version(ver_dir, version)
        if version_file is None:
            return f"Invalid version identifier: {version}"

        if not version_file.exists():
            return f"Version not found: {version}"

        target = _resolve_path(path, user_id, workspace_id)

        content = version_file.read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        logger.info("version_restored", {"path": path, "version": version}, user_id=user_id)
        return f"Restored {path} to {version}"
    except Exception as e:
        logger.error(
            "versions_restore.error",
            {"path": path, "version": version, "error": str(e)},
            user_id=user_id,
        )
        return f"Error: {e}"


files_versions_restore.annotations = ToolAnnotations(title="Restore File Version", destructive=True)


@tool
def files_versions_delete(path: str, version: str | None = None, user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Delete a specific version or all versions of a file.

    Args:
        path: File path relative to user files
        version: Version timestamp to delete (omit to delete all versions)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Success or error message
    """
    try:
        ver_dir = _version_path(user_id, path, workspace_id)

        # Audit B9: validate BEFORE the existence early-return — a traversal
        # version string must be rejected even when ver_dir does not exist.
        if version:
            version_file = _validate_version(ver_dir, version)
            if version_file is None:
                return f"Invalid version identifier: {version}"

            if not ver_dir.exists() or not version_file.exists():
                return f"Version not found: {version}"
            version_file.unlink()
            logger.info("version_deleted", {"path": path, "version": version}, user_id=user_id)
            return f"Deleted version {version} of {path}"

        if not ver_dir.exists():
            return f"No versions found for: {path}"

        shutil.rmtree(ver_dir)
        logger.info("versions_deleted", {"path": path}, user_id=user_id)
        return f"Deleted all versions of {path}"
    except Exception as e:
        logger.error("versions_delete.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_versions_delete.annotations = ToolAnnotations(title="Delete File Version", destructive=True)


@tool
def files_versions_clean(user_id: str = "default_user", workspace_id: str = "personal") -> str:
    """Clean up old versions based on retention policy.

    Daily: keep all for 7 days
    Monthly: keep 1 per month for 12 months
    Yearly: keep 1 per year after that

    Args:
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Cleanup summary
    """
    try:
        ver_root = _get_version_root(user_id, workspace_id)

        if not ver_root.exists():
            return "No versions to clean"

        now = datetime.now(UTC)
        deleted_count = 0

        for file_dir in ver_root.rglob("*"):
            if not file_dir.is_dir():
                continue

            entries: list[tuple[Path, datetime]] = []
            for v in sorted(file_dir.iterdir()):
                if not v.is_file():
                    continue
                try:
                    ts = datetime.strptime(v.name, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=UTC)
                except ValueError:
                    continue
                entries.append((v, ts))
            if not entries:
                continue

            # Audit B8: the original loop appended every ascending "newer"
            # version to ``kept``, so nothing was ever deleted. Correct
            # policy: keep everything from the last 7 days, plus exactly the
            # newest version per monthly bucket (< 1 year) and per yearly
            # bucket; unlink the rest.
            keep: set[Path] = set()
            monthly: dict[str, tuple[datetime, Path]] = {}
            yearly: dict[str, tuple[datetime, Path]] = {}

            for v, ts in entries:  # ascending — name sort is chronological
                age = now - ts
                if age <= timedelta(days=7):
                    keep.add(v)
                elif age <= timedelta(days=365):
                    month_key = ts.strftime("%Y-%m")
                    current = monthly.get(month_key)
                    if current is None or ts > current[0]:
                        monthly[month_key] = (ts, v)
                else:
                    year_key = ts.strftime("%Y")
                    current = yearly.get(year_key)
                    if current is None or ts > current[0]:
                        yearly[year_key] = (ts, v)

            keep.update(p for _, p in monthly.values())
            keep.update(p for _, p in yearly.values())

            for v, _ts in entries:
                if v not in keep:
                    v.unlink()
                    deleted_count += 1

        logger.info("versions_cleaned", {"deleted": deleted_count}, user_id=user_id)
        return f"Cleaned up {deleted_count} old versions"
    except Exception as e:
        logger.error("versions_clean.error", {"error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_versions_clean.annotations = ToolAnnotations(title="Clean Old Versions", destructive=True)
