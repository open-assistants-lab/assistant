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

# Top-level user-data dirs carried from a legacy tree into the promoted root.
_CARRIED_DIRS = ("Files", "Memory", "Skills", "Subagents", "Todos", "Contacts")


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

    has_conv = _conversation_source(data_root)
    has_legacy = _legacy_tree_source(data_root)

    if has_conv and has_legacy:
        # Explicit recovery stop: both trees exist, we refuse to choose.
        state = {
            "requires_recovery": True,
            "sources": {
                "conversation": str(data_root / "Conversation"),
                "native_sdk_chat": str(data_root / "Users" / "native_sdk_chat"),
            },
            "message": (
                "Both a root-level Conversation/ tree and a pre-DMG "
                "Users/native_sdk_chat/ tree exist. Resolve manually "
                "(keep exactly one), then remove this recovery file."
            ),
            "ts": time.time(),
        }
        (system / RECOVERY_FILE).write_text(json.dumps(state, indent=2))
        print(
            "Desktop migration stopped: two data sources found. "
            f"See {system / RECOVERY_FILE}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    moved = False
    if has_conv:
        shutil.move(str(data_root / "Conversation"), str(data_root / "Messages"))
        source = "conversation"
        moved = True
    elif has_legacy:
        legacy = data_root / "Users" / "native_sdk_chat"
        for child in legacy.iterdir():
            name = "Messages" if child.name == "Conversation" else child.name
            target = data_root / name
            if target.exists():
                # Merge: move children, not the dir (never overwrite existing).
                for sub in child.iterdir():
                    t = target / sub.name
                    if not t.exists():
                        shutil.move(str(sub), str(t))
            else:
                shutil.move(str(child), str(target))
        shutil.rmtree(legacy, ignore_errors=True)
        source = "native_sdk_chat"
        moved = True
    else:
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
    """Migration marker for the bootstrap payload."""
    system = _system_dir(Path(data_root), system_dir)
    marker = system / MARKER_FILE
    if not marker.exists():
        return {"migrated": False, "source": "fresh"}
    state = json.loads(marker.read_text())
    return {
        "migrated": True,
        "source": state.get("source", "fresh"),
        "migration_version": state.get("migration_version", 1),
    }


def _recovery_pending(data_root: Path) -> bool:
    return (_system_dir(Path(data_root)) / RECOVERY_FILE).exists()
