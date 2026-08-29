"""Human review queue for auto-drafted skills."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from src.app_logging import get_logger
from src.http.auth import enforce_user_id
from src.skills.registry import get_skill_registry
from src.storage.paths import DEFAULT_USER_ID, get_paths

router = APIRouter(prefix="/review", tags=["review"])
_NAME_RE = re.compile(r"^[a-z0-9-]+$")


class ReviseDraftRequest(BaseModel):
    """Replacement content supplied by a human reviewer."""

    content: str


def _validate_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid draft name")


def _review_dir(user_id: str) -> Path:
    """Return the private, user-scoped review outcome directory."""
    path = get_paths(user_id).user_dir / "private" / "review"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _outcome_path(user_id: str, name: str) -> Path:
    return _review_dir(user_id) / f"{name}.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_outcome(user_id: str, name: str, status: str) -> dict[str, Any]:
    outcome = {
        "name": name,
        "status": status,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    _outcome_path(user_id, name).write_text(json.dumps(outcome), encoding="utf-8")
    return outcome


def _authorize(request: Request, user_id: str) -> None:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))


@router.get("/drafts")
async def list_drafts(
    request: Request,
    user_id: str = Query(DEFAULT_USER_ID),
) -> dict[str, list[dict[str, Any]]]:
    """List pending drafts including their complete draft metadata."""
    _authorize(request, user_id)
    registry = get_skill_registry(user_id)
    drafts: list[dict[str, Any]] = []
    for item in registry.list_skill_drafts():
        name = str(item["name"])
        meta = _read_json(registry.drafts_dir / name / ".draft-meta.json")
        drafts.append({**item, "metadata": meta})
    return {"drafts": drafts}


@router.post("/drafts/{name}/approve")
async def approve_draft(
    name: str,
    request: Request,
    user_id: str = Query(DEFAULT_USER_ID),
) -> dict[str, Any]:
    """Promote a pending draft to a live skill."""
    _validate_name(name)
    _authorize(request, user_id)
    registry = get_skill_registry(user_id)
    meta = _read_json(registry.drafts_dir / name / ".draft-meta.json")
    try:
        path = registry.approve_skill_draft(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    status = "approved_with_edit" if meta.get("revised") else "approved"
    outcome = _write_outcome(user_id, name, status)
    get_logger().info("skill_review.approved", {"name": name, "status": status}, user_id=user_id)
    return {**outcome, "path": str(path)}


@router.post("/drafts/{name}/revise")
async def revise_draft(
    name: str,
    body: ReviseDraftRequest,
    request: Request,
    user_id: str = Query(DEFAULT_USER_ID),
) -> dict[str, Any]:
    """Replace draft content and retain provenance for later approval."""
    _validate_name(name)
    _authorize(request, user_id)
    registry = get_skill_registry(user_id)
    meta_path = registry.drafts_dir / name / ".draft-meta.json"
    if registry.get_skill_draft(name) is None:
        raise HTTPException(status_code=404, detail=f"no draft named {name!r}")
    meta = _read_json(meta_path)
    registry.put_skill_draft(name, body.content, source=str(meta.get("source", "")))
    meta = _read_json(meta_path)
    meta.update({"revised": True, "revised_at": datetime.now(UTC).isoformat()})
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    get_logger().info("skill_review.revised", {"name": name}, user_id=user_id)
    return {"name": name, "status": "pending", "metadata": meta}


@router.post("/drafts/{name}/flag")
async def flag_draft(
    name: str,
    request: Request,
    user_id: str = Query(DEFAULT_USER_ID),
) -> dict[str, Any]:
    """Remove a draft from the queue and record a flagged outcome."""
    _validate_name(name)
    _authorize(request, user_id)
    registry = get_skill_registry(user_id)
    if registry.get_skill_draft(name) is None:
        raise HTTPException(status_code=404, detail=f"no draft named {name!r}")
    registry.reject_skill_draft(name)
    outcome = _write_outcome(user_id, name, "flagged")
    get_logger().info("skill_review.flagged", {"name": name}, user_id=user_id)
    return outcome
