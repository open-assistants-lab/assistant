"""Settings API — per-user overrides for API keys, default model, and key validation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool

from src.app_logging import get_logger
from src.config import get_settings as get_host_settings
from src.config.user_settings import (
    EffectiveUserSettings,
    FrozenJSONValue,
    GraderPromptResponse,
    ProviderStatus,
    SavedUserSettings,
    SettingsError,
    UserSettingsPatch,
    UserSettingsResponse,
    VerificationOverrides,
)
from src.config.user_settings_service import (
    SettingsResolutionError,
    build_user_settings_response,
    resolve_effective_user_settings,
    resolve_provider_statuses,
)
from src.config.user_settings_store import (
    GraderPromptUnavailableError,
    RevisionConflict,
    SettingsConfigurationError,
    SettingsWriteError,
    UserSettingsStore,
)
from src.sdk.run_models import CanonicalModel


class UpdateSettingsRequest(BaseModel):
    """Request body for PATCH /settings."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = None
    default_model: CanonicalModel | None = None
    title_model: CanonicalModel | None = None
    summarization_model: CanonicalModel | None = None
    verification: VerificationOverrides | None = None


class SetApiKeyRequest(BaseModel):
    """Request body for POST /settings/api-keys."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    api_key: str


class TestKeyRequest(BaseModel):
    """Request body for POST /settings/test-key."""
    provider: str
    api_key: str


logger = get_logger()
router = APIRouter(prefix="/settings", tags=["settings"])


def _key_test_error(status: int | None, err_body: object) -> dict[str, Any]:
    if status == 401:
        return {"valid": False, "error": "Invalid API key (401 Unauthorized)"}
    if status == 403:
        return {"valid": False, "error": "API key lacks permission (403 Forbidden)"}
    if status == 429:
        return {"valid": True, "warning": "Rate limited — key appears valid"}
    return {"valid": False, "error": str(err_body)[:200]}


async def _test_http_provider_key(prov: Any, provider: str, api_key: str) -> dict[str, Any] | None:
    client = prov.get_client() if hasattr(prov, "get_client") else None
    if client is None:
        return None
    base_url = getattr(prov, "base_url", "").rstrip("/")
    if provider == "anthropic":
        response = await client.get(f"{base_url}/v1/models")
    elif provider == "gemini":
        response = await client.get(f"{base_url}/models?key={api_key}")
    elif provider == "ollama-cloud":
        response = await client.get(f"{base_url}/api/tags")
    else:
        return None

    status = getattr(response, "status_code", None)
    if status is not None and 200 <= status < 300:
        return {"valid": True}
    return _key_test_error(status, getattr(response, "text", response))


def _setup_provider_key_test(provider: str, api_key: str) -> tuple[Any, str]:
    """Resolve metadata and construct a provider outside the event loop."""
    from src.sdk.providers.factory import create_provider
    from src.sdk.registry import get_provider as get_provider_meta

    meta = get_provider_meta(provider)
    provider_type = meta.get("type", "openai-compatible") if meta else "openai-compatible"
    base_url = meta.get("base_url", "") if meta else ""
    if (
        base_url
        and provider_type in ("openai", "openai-compatible")
        and not base_url.rstrip("/").endswith("/v1")
    ):
        base_url = base_url.rstrip("/") + "/v1"
    return create_provider(provider, api_key=api_key, base_url=base_url or None), provider_type


def _get_settings_store(user_id: str) -> UserSettingsStore:
    """Create a settings store with the host's legacy rubric fallback."""
    host = get_host_settings()
    return UserSettingsStore(
        user_id,
        legacy_default_rubric=host.verification.default_rubric,
    )


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id, reason="settings_changed")


def _settings_error(
    status_code: int,
    code: Literal["revision_conflict", "validation_error", "configuration_error"],
    message: str,
    details: FrozenJSONValue,
) -> JSONResponse:
    error = SettingsError(code=code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


_KNOWN_PROVIDERS = [
    {"id": "agnes", "name": "Agnes"},
    {"id": "openai", "name": "OpenAI"},
    {"id": "anthropic", "name": "Anthropic"},
    {"id": "gemini", "name": "Google Gemini"},
    {"id": "ollama-cloud", "name": "Ollama Cloud"},
    {"id": "groq", "name": "Groq"},
    {"id": "deepseek", "name": "DeepSeek"},
    {"id": "together", "name": "Together AI"},
    {"id": "openrouter", "name": "OpenRouter"},
]

_STATIC_MODELS = {
    "agnes": [
        {
            "id": "agnes:agnes-2.0-flash",
            "name": "Agnes 2.0 Flash",
            "provider": "agnes",
            "provider_display": "Agnes",
        },
        {
            "id": "agnes:agnes-2.0-pro",
            "name": "Agnes 2.0 Pro",
            "provider": "agnes",
            "provider_display": "Agnes",
        },
    ],
    "anthropic": [
        {
            "id": "anthropic:claude-sonnet-4-5",
            "name": "Claude Sonnet 4.5",
            "provider": "anthropic",
            "provider_display": "Anthropic",
        }
    ],
    "openai": [
        {
            "id": "openai:gpt-4.1",
            "name": "GPT-4.1",
            "provider": "openai",
            "provider_display": "OpenAI",
        }
    ],
    "gemini": [
        {
            "id": "gemini:gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "provider": "gemini",
            "provider_display": "Google Gemini",
        }
    ],
    "ollama-cloud": [
        {
            "id": "ollama-cloud:deepseek-v4-flash:0731",
            "name": "DeepSeek V4 Flash 0731",
            "provider": "ollama-cloud",
            "provider_display": "Ollama Cloud",
        }
    ],
}


def _catalog_providers() -> list[dict[str, Any]]:
    providers_by_id = {provider["id"]: dict(provider) for provider in _KNOWN_PROVIDERS}
    try:
        from src.sdk.registry import list_providers

        for provider in list_providers():
            provider_id = provider.get("id")
            if not provider_id:
                continue
            providers_by_id[str(provider_id)] = {
                "id": str(provider_id),
                "name": str(provider.get("name") or provider_id),
                "env": provider.get("env", []),
                "type": provider.get("type", "openai-compatible"),
            }
    except Exception:
        logger.warning("settings.catalog_providers_failed", {})
        pass
    return sorted(providers_by_id.values(), key=lambda provider: provider["name"].lower())


def _provider_models(provider_id: str, provider_name: str) -> list[dict[str, str]]:
    static_models = _STATIC_MODELS.get(provider_id)
    if static_models is not None:
        return sorted(static_models, key=lambda model: model["name"].lower())

    try:
        from src.sdk.registry import list_models

        models = [
            {
                "id": f"{provider_id}:{model.id}",
                "name": model.name,
                "provider": provider_id,
                "provider_display": provider_name,
            }
            for model in list_models(provider=provider_id)
        ]
    except Exception:
        logger.warning("settings.provider_models_failed", {"provider_id": provider_id})
        models = []

    deduped: dict[str, dict[str, str]] = {}
    for model in sorted(models, key=lambda item: (item["name"].lower(), item["id"].lower())):
        deduped.setdefault(model["name"].lower(), model)
    return list(deduped.values())


def _catalog_snapshot() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    providers = _catalog_providers()
    models = {
        str(provider["id"]): _provider_models(str(provider["id"]), str(provider["name"]))
        for provider in providers
    }
    return providers, models


@dataclass(frozen=True)
class _SettingsPreflight:
    saved: SavedUserSettings
    providers: tuple[Mapping[str, object], ...]
    models: Mapping[str, tuple[Mapping[str, str], ...]]
    provider_status: Mapping[str, ProviderStatus]
    effective: EffectiveUserSettings
    prompt: GraderPromptResponse | None
    environ: Mapping[str, str]

    def response(self, saved: SavedUserSettings | None = None) -> UserSettingsResponse:
        return build_user_settings_response(
            self.saved if saved is None else saved,
            self.effective,
            self.provider_status,
        )


def _preflight_settings(
    store: UserSettingsStore,
    saved: SavedUserSettings | None = None,
) -> _SettingsPreflight:
    resolved_saved = store.load() if saved is None else saved
    raw_providers, raw_models = _catalog_snapshot()
    providers = tuple(
        MappingProxyType(
            {
                **provider,
                "env": (
                    tuple(provider.get("env", []))
                    if isinstance(provider.get("env", []), (list, tuple))
                    else provider.get("env")
                ),
            }
        )
        for provider in raw_providers
    )
    models = MappingProxyType(
        {
            provider_id: tuple(MappingProxyType(dict(model)) for model in provider_models)
            for provider_id, provider_models in raw_models.items()
        }
    )
    environ = MappingProxyType(dict(os.environ))
    provider_status = resolve_provider_statuses(resolved_saved, providers, environ)
    available_providers = frozenset(models)
    available_models = frozenset(
        model["id"] for provider_models in models.values() for model in provider_models
    )
    try:
        prompt = store.load_grader_prompt()
    except GraderPromptUnavailableError:
        prompt = None

    host = get_host_settings()
    # Validate host defaults even when a user override would otherwise bypass them.
    resolve_effective_user_settings(
        saved=SavedUserSettings(),
        prompt=prompt,
        host_default_model=host.agent.model,
        host_title_model=host.agent.title_model,
        host_summarization_model=host.memory.summarization.model,
        host_verification_enabled=host.verification.enabled,
        host_grader_model=host.verification.grader_model,
        host_max_attempts=host.verification.max_iterations,
        provider_status=provider_status,
        model_available=available_models.__contains__,
        provider_available=available_providers.__contains__,
    )
    effective = resolve_effective_user_settings(
        saved=resolved_saved,
        prompt=prompt,
        host_default_model=host.agent.model,
        host_title_model=host.agent.title_model,
        host_summarization_model=host.memory.summarization.model,
        host_verification_enabled=host.verification.enabled,
        host_grader_model=host.verification.grader_model,
        host_max_attempts=host.verification.max_iterations,
        provider_status=provider_status,
        model_available=available_models.__contains__,
        provider_available=available_providers.__contains__,
    )
    return _SettingsPreflight(
        saved=resolved_saved,
        providers=providers,
        models=models,
        provider_status=provider_status,
        effective=effective,
        prompt=prompt,
        environ=environ,
    )


def _preview_patch(current: SavedUserSettings, patch: UserSettingsPatch) -> SavedUserSettings:
    payload = current.model_dump(mode="json")
    if "default_model" in patch.model_fields_set:
        payload["default_model"] = patch.default_model
    if "title_model" in patch.model_fields_set:
        payload["title_model"] = patch.title_model
    if "summarization_model" in patch.model_fields_set:
        payload["summarization_model"] = patch.summarization_model
    if "verification" in patch.model_fields_set:
        if patch.verification is None:
            payload["verification"] = VerificationOverrides().model_dump(mode="json")
        else:
            verification = current.verification.model_dump(mode="json")
            for field in patch.verification.model_fields_set:
                verification[field] = getattr(patch.verification, field)
            payload["verification"] = verification

    candidate = SavedUserSettings.model_validate(payload)
    if candidate != current:
        payload["revision"] = current.revision + 1
        candidate = SavedUserSettings.model_validate(payload)
    return candidate


def _configuration_failure() -> JSONResponse:
    return _settings_error(
        500,
        "configuration_error",
        "Unable to process user settings",
        {},
    )


@router.get("", response_model=UserSettingsResponse)
def get_settings(user_id: str = Query("default_user")) -> UserSettingsResponse | JSONResponse:
    """Read canonical saved and effective settings without exposing credentials."""
    try:
        return _preflight_settings(_get_settings_store(user_id)).response()
    except ValueError:
        return _settings_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError, SettingsResolutionError):
        return _configuration_failure()


@router.get("/model-catalog", response_model=None)
def model_catalog(
    user_id: str = Query("default_user"),
    max_models_per_provider: int | None = None,
    max_providers: int | None = None,
) -> dict[str, Any] | JSONResponse:
    """Return the Settings provider-grouped model catalog."""
    try:
        preflight = _preflight_settings(_get_settings_store(user_id))
    except ValueError:
        return _settings_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError, SettingsResolutionError):
        return _configuration_failure()

    providers: list[dict[str, Any]] = []
    for provider in preflight.providers:
        provider_id = str(provider["id"])
        provider_name = str(provider["name"])
        status = preflight.provider_status[provider_id]
        key_source = status.key_source
        all_provider_models = preflight.models[provider_id]
        provider_model_count = len(all_provider_models)
        shown_models = (
            all_provider_models[:max_models_per_provider]
            if max_models_per_provider is not None
            else all_provider_models
        )
        provider_models = [
            {**model, "key_source": key_source}
            for model in shown_models
        ]
        providers.append(
            {
                "id": provider_id,
                "name": provider_name,
                "key_source": key_source,
                "has_key": key_source != "none",
                "total_models": provider_model_count,
                "models": provider_models,
            }
        )

    providers.sort(key=lambda p: (p["key_source"] == "none", p["name"].lower()))
    total_providers = len(providers)
    shown_providers = providers[:max_providers] if max_providers is not None else providers
    return {
        "revision": preflight.saved.revision,
        "default_model": preflight.effective.default_model,
        "grader_model": preflight.effective.verification.grader_model,
        "title_model": preflight.effective.title_model,
        "summarization_model": preflight.effective.summarization_model,
        "total_providers": total_providers,
        "providers": shown_providers,
    }


@router.patch("", response_model=UserSettingsResponse)
def update_settings(
    body: Any = Body(...),
    user_id: str = Query("default_user"),
) -> UserSettingsResponse | JSONResponse:
    """Apply a revisioned settings patch, retaining omitted legacy revision support."""
    try:
        request = UpdateSettingsRequest.model_validate(body)
        store = _get_settings_store(user_id)
        current = store.load()
        expected_revision = (
            request.expected_revision
            if request.expected_revision is not None
            else current.revision
        )
        patch_payload: dict[str, Any] = {"expected_revision": expected_revision}
        for field in ("default_model", "title_model", "summarization_model", "verification"):
            if field in request.model_fields_set:
                patch_payload[field] = getattr(request, field)
        patch = UserSettingsPatch.model_validate(patch_payload)
        if patch.expected_revision != current.revision:
            raise RevisionConflict(patch.expected_revision, current.revision)
        preflight = _preflight_settings(store, _preview_patch(current, patch))
        mutation = store.patch(patch)
        if mutation.changed:
            _reset_user_loops(user_id)
        return preflight.response(mutation.settings)
    except RevisionConflict as exc:
        try:
            latest = _preflight_settings(store).response().model_dump(mode="json")
        except (SettingsConfigurationError, SettingsWriteError, SettingsResolutionError):
            return _configuration_failure()
        return _settings_error(
            409,
            "revision_conflict",
            "Settings revision conflict",
            {"expected": exc.expected, "actual": exc.actual, "latest": latest},
        )
    except (ValidationError, ValueError):
        return _settings_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError, SettingsResolutionError):
        return _configuration_failure()


@router.get("/api-keys", response_model=None)
def list_api_keys(user_id: str = Query("default_user")) -> dict[str, bool] | JSONResponse:
    """List which providers have stored API keys (without revealing keys)."""
    try:
        saved = _get_settings_store(user_id).load()
        return {provider: True for provider in saved.provider_keys}
    except ValueError:
        return _settings_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _configuration_failure()


@router.post("/api-keys", response_model=None)
def set_api_key(
    body: Any = Body(...),
    user_id: str = Query("default_user"),
) -> dict[str, str | int] | JSONResponse:
    """Store an API key for a provider."""
    try:
        request = SetApiKeyRequest.model_validate(body)
        mutation = _get_settings_store(user_id).set_provider_key(
            request.provider, request.api_key
        )
        if mutation.changed:
            _reset_user_loops(user_id)
        return {
            "status": "stored",
            "provider": request.provider,
            "revision": mutation.settings.revision,
        }
    except (ValidationError, ValueError):
        return _settings_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _configuration_failure()


@router.post("/test-key")
async def test_api_key(body: TestKeyRequest) -> dict[str, Any]:
    """Test whether an API key is valid for the given provider.

    Makes a minimal API call (list models) to verify the key works.
    Returns success/failure with an error message on failure.
    """
    provider = body.provider
    api_key = body.api_key

    if not api_key:
        return {"valid": False, "error": "API key is empty"}

    try:
        prov, provider_type = await run_in_threadpool(
            _setup_provider_key_test, provider, api_key
        )
        try:
            if hasattr(prov, "_client"):
                await prov._client.models.list()
            else:
                http_result = await _test_http_provider_key(prov, provider, api_key)
                if http_result is None:
                    return {"valid": False, "error": f"Cannot test provider type: {provider_type}"}
                return http_result
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )
            err_body = getattr(e, "body", None) or getattr(
                getattr(e, "response", None), "text", str(e)
            )
            return _key_test_error(status, err_body)

        return {"valid": True}

    except Exception as e:
        logger.warning("test-key failed", {"provider": provider, "error": str(e)})
        return {"valid": False, "error": f"Could not test key: {e}"}


@router.delete("/api-keys/{provider}", response_model=None)
def delete_api_key(
    provider: str,
    user_id: str = Query("default_user"),
) -> dict[str, str | int] | JSONResponse:
    """Remove a stored API key for a provider."""
    try:
        mutation = _get_settings_store(user_id).delete_provider_key(provider)
        if mutation.changed:
            _reset_user_loops(user_id)
        return {
            "status": "removed",
            "provider": provider,
            "revision": mutation.settings.revision,
        }
    except ValueError:
        return _settings_error(422, "validation_error", "Invalid settings request", {})
    except (SettingsConfigurationError, SettingsWriteError):
        return _configuration_failure()
