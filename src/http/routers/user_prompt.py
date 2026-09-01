# mypy: disable-error-code="assignment"
"""User prompt API endpoints."""

from json import JSONDecodeError
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from src.config import get_settings
from src.config.user_settings import (
    FrozenJSONValue,
    GraderPromptResponse,
    GraderPromptUpdate,
    RevisionRequest,
    SettingsError,
)
from src.config.user_settings_store import (
    GraderPromptMutation,
    RevisionConflict,
    SettingsConfigurationError,
    SettingsWriteError,
    UserSettingsStore,
)
from src.http.auth import resolve_user_id
from src.sdk.user_prompt import load_user_prompt, save_user_prompt
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/user", tags=["user"])


class UserPromptResponse(BaseModel):
    prompt: str


class UserPromptRequest(BaseModel):
    prompt: str


def _get_grader_prompt_store(user_id: str) -> UserSettingsStore:
    """Create a prompt store with the host's legacy rubric fallback."""
    return UserSettingsStore(
        user_id,
        legacy_default_rubric=get_settings().verification.default_rubric,
    )


def _save_grader_prompt_sync(
    user_id: str, request: GraderPromptUpdate
) -> GraderPromptMutation:
    return _get_grader_prompt_store(user_id).save_grader_prompt(request)


def _reset_grader_prompt_sync(
    user_id: str, request: RevisionRequest
) -> GraderPromptMutation:
    return _get_grader_prompt_store(user_id).reset_grader_prompt(request)


def _grader_prompt_conflict_details_sync(
    user_id: str, expected: int, actual: int
) -> dict[str, object]:
    details: dict[str, object] = {"expected": expected, "actual": actual}
    try:
        details["latest"] = _get_grader_prompt_store(user_id).load_grader_prompt().model_dump(
            mode="json"
        )
    except Exception:
        details["latest_error"] = "configuration_error"
    return details


def _reset_grader_prompt_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id, reason="grader_prompt_changed")


def _grader_prompt_error(
    status_code: int,
    code: Literal["revision_conflict", "validation_error", "configuration_error"],
    message: str,
    details: FrozenJSONValue,
) -> JSONResponse:
    error = SettingsError(code=code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


def _grader_prompt_configuration_failure() -> JSONResponse:
    return _grader_prompt_error(
        500,
        "configuration_error",
        "Unable to process user settings",
        {},
    )


@router.get("/prompt", response_model=UserPromptResponse)
async def get_user_prompt(user_id: str =  DEFAULT_USER_ID, request: Request = None) -> UserPromptResponse:
    """Get the user's custom prompt."""
    user_id = resolve_user_id(request, user_id)
    prompt = load_user_prompt(user_id)
    return UserPromptResponse(prompt=prompt)


@router.put("/prompt", response_model=UserPromptResponse)
async def set_user_prompt(req: UserPromptRequest, user_id: str =  DEFAULT_USER_ID, request: Request = None) -> UserPromptResponse:
    """Set the user's custom prompt."""
    user_id = resolve_user_id(request, user_id)
    save_user_prompt(user_id, req.prompt)
    return UserPromptResponse(prompt=req.prompt)


@router.get("/grader-prompt", response_model=GraderPromptResponse)
def get_grader_prompt(
    user_id: str = Query(DEFAULT_USER_ID),
    request: Request = None,
) -> GraderPromptResponse | JSONResponse:
    """Get the user's revisioned grader prompt."""
    user_id = resolve_user_id(request, user_id)
    try:
        return _get_grader_prompt_store(user_id).load_grader_prompt()
    except ValueError:
        return _grader_prompt_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _grader_prompt_configuration_failure()


@router.put("/grader-prompt", response_model=GraderPromptResponse)
async def set_grader_prompt(
    request: Request,
    user_id: str = Query(DEFAULT_USER_ID),
) -> GraderPromptResponse | JSONResponse:
    """Replace the user's grader prompt when its revision is current."""
    user_id = resolve_user_id(request, user_id)
    try:
        payload = await request.json()
        update = GraderPromptUpdate.model_validate(payload)
        mutation = await run_in_threadpool(_save_grader_prompt_sync, user_id, update)
        if mutation.changed:
            _reset_grader_prompt_loops(user_id)
        return mutation.response
    except RevisionConflict as exc:
        details = await run_in_threadpool(
            _grader_prompt_conflict_details_sync, user_id, exc.expected, exc.actual
        )
        return _grader_prompt_error(
            409,
            "revision_conflict",
            "Settings revision conflict",
            details,
        )
    except (JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError):
        return _grader_prompt_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _grader_prompt_configuration_failure()


@router.post("/grader-prompt/reset", response_model=GraderPromptResponse)
async def reset_grader_prompt(
    request: Request,
    user_id: str = Query(DEFAULT_USER_ID),
) -> GraderPromptResponse | JSONResponse:
    """Restore the packaged grader prompt when its revision is current."""
    user_id = resolve_user_id(request, user_id)
    try:
        payload = await request.json()
        revision = RevisionRequest.model_validate(payload)
        mutation = await run_in_threadpool(_reset_grader_prompt_sync, user_id, revision)
        if mutation.changed:
            _reset_grader_prompt_loops(user_id)
        return mutation.response
    except RevisionConflict as exc:
        details = await run_in_threadpool(
            _grader_prompt_conflict_details_sync, user_id, exc.expected, exc.actual
        )
        return _grader_prompt_error(
            409,
            "revision_conflict",
            "Settings revision conflict",
            details,
        )
    except (JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError):
        return _grader_prompt_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _grader_prompt_configuration_failure()
