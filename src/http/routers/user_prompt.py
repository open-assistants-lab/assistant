"""User prompt API endpoints."""

from typing import Any, Literal

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from src.config import get_settings
from src.config.user_settings import (
    FrozenJSONValue,
    GraderPromptResponse,
    GraderPromptUpdate,
    RevisionRequest,
    SettingsError,
)
from src.config.user_settings_store import (
    RevisionConflict,
    SettingsConfigurationError,
    SettingsWriteError,
    UserSettingsStore,
)
from src.sdk.user_prompt import load_user_prompt, save_user_prompt

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
async def get_user_prompt(user_id: str = "default_user") -> UserPromptResponse:
    """Get the user's custom prompt."""
    prompt = load_user_prompt(user_id)
    return UserPromptResponse(prompt=prompt)


@router.put("/prompt", response_model=UserPromptResponse)
async def set_user_prompt(req: UserPromptRequest, user_id: str = "default_user") -> UserPromptResponse:
    """Set the user's custom prompt."""
    save_user_prompt(user_id, req.prompt)
    return UserPromptResponse(prompt=req.prompt)


@router.get("/grader-prompt", response_model=GraderPromptResponse)
def get_grader_prompt(
    user_id: str = Query("default_user"),
) -> GraderPromptResponse | JSONResponse:
    """Get the user's revisioned grader prompt."""
    try:
        return _get_grader_prompt_store(user_id).load_grader_prompt()
    except ValueError:
        return _grader_prompt_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _grader_prompt_configuration_failure()


@router.put("/grader-prompt", response_model=GraderPromptResponse)
def set_grader_prompt(
    body: Any = Body(...),
    user_id: str = Query("default_user"),
) -> GraderPromptResponse | JSONResponse:
    """Replace the user's grader prompt when its revision is current."""
    try:
        request = GraderPromptUpdate.model_validate(body)
        mutation = _get_grader_prompt_store(user_id).save_grader_prompt(request)
        if mutation.changed:
            _reset_grader_prompt_loops(user_id)
        return mutation.response
    except RevisionConflict as exc:
        return _grader_prompt_error(
            409,
            "revision_conflict",
            "Settings revision conflict",
            {"expected": exc.expected, "actual": exc.actual},
        )
    except (ValidationError, ValueError):
        return _grader_prompt_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _grader_prompt_configuration_failure()


@router.post("/grader-prompt/reset", response_model=GraderPromptResponse)
def reset_grader_prompt(
    body: Any = Body(...),
    user_id: str = Query("default_user"),
) -> GraderPromptResponse | JSONResponse:
    """Restore the packaged grader prompt when its revision is current."""
    try:
        request = RevisionRequest.model_validate(body)
        mutation = _get_grader_prompt_store(user_id).reset_grader_prompt(request)
        if mutation.changed:
            _reset_grader_prompt_loops(user_id)
        return mutation.response
    except RevisionConflict as exc:
        return _grader_prompt_error(
            409,
            "revision_conflict",
            "Settings revision conflict",
            {"expected": exc.expected, "actual": exc.actual},
        )
    except (ValidationError, ValueError):
        return _grader_prompt_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _grader_prompt_configuration_failure()
