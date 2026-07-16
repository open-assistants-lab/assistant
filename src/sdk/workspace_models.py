"""Workspace model — multi-project isolation for EA.

Each workspace is a named, isolated project container with its own
conversation history, memory, files, subagents, and custom AI instructions.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Workspace:
    id: str
    name: str
    description: str = ""
    prompt: str = ""
    model_override: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_name(cls, name: str) -> Workspace:
        clean_name = name.strip()
        ws_id = re.sub(r"[^a-z0-9]+", "-", clean_name.lower()).strip("-")
        now = datetime.now(UTC).isoformat()
        return cls(id=ws_id, name=clean_name, created_at=now, updated_at=now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "model_override": self.model_override,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Workspace:
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            description=d.get("description", ""),
            prompt=d.get("prompt", d.get("custom_instructions", "")),
            model_override=d.get("model_override"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str) -> Workspace:
        return cls.from_dict(json.loads(s))


WORKSPACE_DEFAULT = Workspace(
    id="personal",
    name="Personal",
    description="Default workspace",
    created_at="",
    updated_at="",
)


def _config_path(workspace_id: str, base_path: Path) -> Path:
    return base_path / workspace_id / "workspace.yaml"


def save_workspace(ws: Workspace, base_path: Path | None = None) -> None:
    if base_path is None:
        base_path = _default_workspaces_dir()
    cfg = _config_path(ws.id, base_path)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    ws.updated_at = datetime.now(UTC).isoformat()
    cfg.write_text(yaml.dump(ws.to_dict()), encoding="utf-8")


def load_workspace(workspace_id: str, base_path: Path | None = None) -> Workspace | None:
    if base_path is None:
        base_path = _default_workspaces_dir()
    cfg = _config_path(workspace_id, base_path)
    if not cfg.exists():
        return None
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    if data is None:
        return None
    return Workspace.from_dict(data)


def list_workspaces(base_path: Path | None = None) -> list[Workspace]:
    if base_path is None:
        base_path = _default_workspaces_dir()
    if not base_path.exists():
        return []
    results = []
    for item in sorted(base_path.iterdir()):
        if item.is_dir():
            cfg = item / "workspace.yaml"
            if cfg.exists():
                data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
                if data:
                    results.append(Workspace.from_dict(data))
    return results


def delete_workspace(workspace_id: str, base_path: Path | None = None) -> None:
    if base_path is None:
        base_path = _default_workspaces_dir()
    ws_dir = base_path / workspace_id
    if ws_dir.exists():
        shutil.rmtree(ws_dir)


def _default_workspaces_dir() -> Path:
    from src.storage.paths import DataPaths

    return DataPaths().root / "Workspaces"
