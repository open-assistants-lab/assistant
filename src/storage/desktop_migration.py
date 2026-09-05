"""Desktop one-source storage migration (desktop v0.1, Phase D1 task 4).

Promotes exactly ONE pre-DMG data source into the fresh `~/Assistant`
layout, or stops for explicit recovery when two sources coexist:

- fresh install (no sources)  -> marker written, nothing moved
- `~/Assistant/Conversation/` -> renamed to `Messages/` (default-user tree)
- `~/Assistant/Users/native_sdk_chat/` -> promoted to the default-user root
- BOTH sources                -> recovery state written + SystemExit
                              (no destructive action)

Idempotent: the completion marker (`migration-complete.json`) short-circuits
everything — data that appears after migration is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

MARKER_FILE = "migration-complete.json"
RECOVERY_FILE = "migration-recovery.json"

# These are desktop-runtime implementation directories, not legacy user-data
# sources. A legacy native_sdk_chat tree may be promoted only when every other
# root entry is one of these safe framework-owned directories.
_SAFE_ROOT_ENTRIES = {
    ".DS_Store",
    ".git",
    ".gitignore",
    ".system",
    ".versions",
    "Logs",
    "Users",
}


def _system_dir(data_root: Path, system_dir: Path | None = None) -> Path:
    # DEPLOYMENT_DATA_PATH is the .system dir; fall back to data_root/.system
    if system_dir is not None:
        return system_dir
    env = Path(os.environ.get("DEPLOYMENT_DATA_PATH", ""))
    if env.is_absolute() and env != data_root:
        return env
    return data_root / ".system"


def _conversation_source(data_root: Path) -> bool:
    return (data_root / "Conversation").is_dir()


def _legacy_tree_source(data_root: Path) -> bool:
    return (data_root / "Users" / "native_sdk_chat").is_dir()


def _write_recovery(
    system: Path,
    data_root: Path,
    message: str,
    sources: dict[str, str],
) -> None:
    """Persist an explicit recovery state and stop without moving any data."""
    state = {
        "requires_recovery": True,
        "sources": sources,
        "message": message,
        "ts": time.time(),
    }
    (system / RECOVERY_FILE).write_text(json.dumps(state, indent=2))
    print(f"Desktop migration stopped: {message} See {system / RECOVERY_FILE}", file=sys.stderr)
    raise SystemExit(1)


def _root_entries_conflict_with_legacy(data_root: Path) -> list[Path]:
    """Return root entries that make legacy-tree promotion ambiguous."""
    return [entry for entry in data_root.iterdir() if entry.name not in _SAFE_ROOT_ENTRIES]


def _legacy_target_conflicts(legacy: Path, data_root: Path) -> list[tuple[Path, Path]]:
    """Find collisions that would make a legacy promotion non-atomic."""
    conflicts: list[tuple[Path, Path]] = []
    for child in legacy.iterdir():
        name = "Messages" if child.name == "Conversation" else child.name
        target = data_root / name
        if target.exists():
            conflicts.append((child, target))
    return conflicts


def run_desktop_migration(
    data_root: Path, system_dir: Path | None = None
) -> dict[str, object]:
    """Run the one-source migration; returns a state dict.

    Raises SystemExit ONLY on the dual-tree ambiguity (explicit recovery
    stop). Never destroys data.
    """

    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    system = _system_dir(data_root, system_dir)
    system.mkdir(parents=True, exist_ok=True)
    marker = system / MARKER_FILE

    if marker.exists():
        return {"source": "already-migrated", "moved": False}

    conversation = data_root / "Conversation"
    messages = data_root / "Messages"
    legacy = data_root / "Users" / "native_sdk_chat"
    has_conv = _conversation_source(data_root)
    has_legacy = _legacy_tree_source(data_root)
    has_messages = messages.exists()

    if has_conv and has_legacy:
        _write_recovery(
            system,
            data_root,
            "both root Conversation/ and Users/native_sdk_chat/ sources exist.",
            {"conversation": str(conversation), "native_sdk_chat": str(legacy)},
        )

    if has_conv and has_messages:
        _write_recovery(
            system,
            data_root,
            "both Conversation/ and Messages/ exist; refusing to nest or merge them.",
            {"conversation": str(conversation), "messages": str(messages)},
        )

    if has_legacy:
        root_conflicts = _root_entries_conflict_with_legacy(data_root)
        target_conflicts = _legacy_target_conflicts(legacy, data_root)
        legacy_parent = legacy.parent
        unexpected_legacy_siblings = [
            entry for entry in legacy_parent.iterdir() if entry.name != legacy.name
        ]
        if root_conflicts or target_conflicts or unexpected_legacy_siblings:
            sources = {"native_sdk_chat": str(legacy)}
            sources.update({entry.name: str(entry) for entry in root_conflicts})
            sources.update(
                {
                    f"legacy/{source.name}": str(source)
                    for source, _ in target_conflicts
                }
            )
            sources.update(
                {
                    f"root/{target.name}": str(target)
                    for _, target in target_conflicts
                }
            )
            sources.update(
                {
                    f"users/{entry.name}": str(entry)
                    for entry in unexpected_legacy_siblings
                }
            )
            _write_recovery(
                system,
                data_root,
                "root-level data coexists with Users/native_sdk_chat/; refusing to merge.",
                sources,
            )

    if has_messages and not has_conv:
        _write_recovery(
            system,
            data_root,
            "Messages/ exists without a completed migration marker; explicit recovery is required.",
            {"messages": str(messages)},
        )

    moved = False
    if has_conv:
        shutil.move(str(conversation), str(messages))
        source = "conversation"
        moved = True
    elif has_legacy:
        for child in legacy.iterdir():
            name = "Messages" if child.name == "Conversation" else child.name
            shutil.move(str(child), str(data_root / name))
        legacy.rmdir()
        if not legacy.parent.iterdir():
            legacy.parent.rmdir()
        source = "native_sdk_chat"
        moved = True
    else:
        messages.mkdir()
        source = "fresh"

    marker.write_text(
        json.dumps(
            {
                "source": source,
                "moved": moved,
                "ts": time.time(),
                "migration_version": 1,
            },
            indent=2,
        )
    )
    return {"source": source, "moved": moved}


def migration_state(data_root: Path, system_dir: Path | None = None) -> dict[str, object]:
    """Migration state for the bootstrap payload."""
    system = _system_dir(Path(data_root), system_dir)
    recovery = system / RECOVERY_FILE
    if recovery.exists():
        return {"migrated": False, "requires_recovery": True}
    marker = system / MARKER_FILE
    if not marker.exists():
        return {"migrated": False, "source": "fresh"}
    state = json.loads(marker.read_text())
    return {
        "migrated": True,
        "source": state.get("source", "fresh"),
        "migration_version": state.get("migration_version", 1),
    }
