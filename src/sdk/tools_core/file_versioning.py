"""File versioning system — SDK-native implementation."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import get_paths

logger = get_logger()


def _get_version_root(user_id: str) -> Path:
    return get_paths(user_id).versions_dir()


def _resolve_path(path: str | None, user_id: str) -> Path:
    root_path = get_paths(user_id).user_files_dir().resolve()

    if path is None:
        return root_path

    if path.startswith("/"):
        raise ValueError(f"Use relative paths only. Path: {path}")

    resolved = (root_path / path).resolve()
    if not resolved.is_relative_to(root_path):
        raise ValueError(f"Path outside user directory: {path}")

    return resolved


def _version_path(user_id: str, file_path: str) -> Path:
    root = _get_version_root(user_id)
    return root / file_path


def capture_version(user_id: str, file_path: str, new_content: str) -> str | None:
    try:
        target = _resolve_path(file_path, user_id)

        if not target.exists() or not target.is_file():
            return None

        current_content = target.read_text(encoding="utf-8")
        if current_content == new_content:
            return None

        ver_dir = _version_path(user_id, file_path)
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
def files_versions_list(path: str, user_id: str = "default_user") -> str:
    """List all versions of a file.

    Args:
        path: File path relative to user files
        user_id: User identifier

    Returns:
        List of versions with timestamps
    """
    try:
        ver_dir = _version_path(user_id, path)

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
def files_versions_restore(path: str, version: str, user_id: str = "default_user") -> str:
    """Restore a file to a specific version.

    Args:
        path: File path relative to user files
        version: Version timestamp to restore (e.g., "2026-03-16T10-30-00")
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        ver_dir = _version_path(user_id, path)
        version_file = ver_dir / version

        if not version_file.exists():
            return f"Version not found: {version}"

        target = _resolve_path(path, user_id)

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
def files_versions_delete(path: str, version: str | None = None, user_id: str = "default_user") -> str:
    """Delete a specific version or all versions of a file.

    Args:
        path: File path relative to user files
        version: Version timestamp to delete (omit to delete all versions)
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        ver_dir = _version_path(user_id, path)

        if not ver_dir.exists():
            return f"No versions found for: {path}"

        if version:
            version_file = ver_dir / version
            if not version_file.exists():
                return f"Version not found: {version}"
            version_file.unlink()
            logger.info("version_deleted", {"path": path, "version": version}, user_id=user_id)
            return f"Deleted version {version} of {path}"
        else:
            shutil.rmtree(ver_dir)
            logger.info("versions_deleted", {"path": path}, user_id=user_id)
            return f"Deleted all versions of {path}"
    except Exception as e:
        logger.error("versions_delete.error", {"path": path, "error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_versions_delete.annotations = ToolAnnotations(title="Delete File Version", destructive=True)


@tool
def files_versions_clean(user_id: str = "default_user") -> str:
    """Clean up old versions based on retention policy.

    Daily: keep all for 7 days
    Monthly: keep 1 per month for 12 months
    Yearly: keep 1 per year after that

    Args:
        user_id: User identifier

    Returns:
        Cleanup summary
    """
    try:
        ver_root = _get_version_root(user_id)

        if not ver_root.exists():
            return "No versions to clean"

        now = datetime.now(UTC)
        deleted_count = 0

        for file_dir in ver_root.rglob("*"):
            if not file_dir.is_dir():
                continue

            versions = sorted(file_dir.iterdir())
            if not versions:
                continue

            kept = []
            monthly_versions: dict[str, datetime] = {}
            yearly_versions: dict[str, datetime] = {}

            for v in versions:
                try:
                    ts = datetime.strptime(v.name, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=UTC)
                    age = now - ts

                    if age <= timedelta(days=7):
                        kept.append(v)
                    elif age <= timedelta(days=365):
                        month_key = ts.strftime("%Y-%m")
                        if month_key not in monthly_versions or ts > monthly_versions[month_key]:
                            if month_key in monthly_versions:
                                pass
                            monthly_versions[month_key] = ts
                            kept.append(v)
                    else:
                        year_key = ts.strftime("%Y")
                        if year_key not in yearly_versions or ts > yearly_versions[year_key]:
                            if year_key in yearly_versions:
                                pass
                            yearly_versions[year_key] = ts
                            kept.append(v)

                    if v not in kept:
                        v.unlink()
                        deleted_count += 1
                except ValueError:
                    continue

        logger.info("versions_cleaned", {"deleted": deleted_count}, user_id=user_id)
        return f"Cleaned up {deleted_count} old versions"
    except Exception as e:
        logger.error("versions_clean.error", {"error": str(e)}, user_id=user_id)
        return f"Error: {e}"


files_versions_clean.annotations = ToolAnnotations(title="Clean Old Versions", destructive=True)
