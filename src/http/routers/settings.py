"""Settings API — per-user overrides for API keys, default model, and key validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.app_logging import get_logger


class UpdateSettingsRequest(BaseModel):
    """Request body for PATCH /settings."""
    default_model: str | None = None


class SetApiKeyRequest(BaseModel):
    """Request body for POST /settings/api-keys."""
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
    if not hasattr(prov, "_get_client"):
        return None

    base_url = getattr(prov, "base_url", "").rstrip("/")
    client = prov._get_client()
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


def _settings_path(user_id: str) -> Path:
    from src.config.settings import get_settings

    root = get_settings().data_path or "data"
    return Path(f"{root}/users/{user_id}/settings.json")


def _read_settings(user_id: str) -> dict[str, Any]:
    path = _settings_path(user_id)
    if path.exists():
        return cast(dict[str, Any], json.loads(path.read_text()))
    return {"provider_keys": {}, "default_model": None}


def _write_settings(user_id: str, data: dict[str, Any]) -> None:
    path = _settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _reset_user_loops(user_id: str) -> None:
    from src.sdk.runner import reset_user_sdk_loops

    reset_user_sdk_loops(user_id, reason="settings_changed")


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
}


def _provider_name(provider_id: str) -> str:
    for provider in _catalog_providers():
        if provider["id"] == provider_id:
            return provider["name"]
    return provider_id.title()


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
        pass
    return sorted(providers_by_id.values(), key=lambda provider: provider["name"].lower())


def _registry_env_key_for_provider(provider_id: str) -> str | None:
    try:
        from src.sdk.registry import get_provider

        provider = get_provider(provider_id)
        if not provider:
            return None
        for env_key in provider.get("env", []):
            if os.environ.get(str(env_key)):
                return str(env_key)
    except Exception:
        return None
    return None


def _provider_key_source(provider_id: str, user_id: str, data: dict[str, Any] | None = None) -> str:
    settings = data if data is not None else _read_settings(user_id)
    if provider_id in settings.get("provider_keys", {}):
        return "user"

    if _env_key_for_provider(provider_id) is not None or _registry_env_key_for_provider(provider_id):
        return "hosted" if provider_id == "agnes" else "env"

    return "none"


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
        models = []

    deduped: dict[str, dict[str, str]] = {}
    for model in sorted(models, key=lambda item: (item["name"].lower(), item["id"].lower())):
        deduped.setdefault(model["name"].lower(), model)
    return list(deduped.values())


@router.get("")
async def get_settings(user_id: str = Query("default_user")) -> dict[str, Any]:
    """Read current settings (default model, which providers have keys)."""
    data = _read_settings(user_id)

    provider_status: dict[str, Any] = {}
    for p in _catalog_providers():
        pid = p["id"]
        key_source = _provider_key_source(pid, user_id, data)
        provider_status[pid] = {
            "name": p["name"],
            "has_key": key_source != "none",
            "key_configured_via_env": key_source == "env",
            "key_source": key_source,
        }
    return {
        "default_model": data.get("default_model"),
        "provider_status": provider_status,
    }


@router.get("/model-catalog")
async def model_catalog(
    user_id: str = Query("default_user"),
    max_models_per_provider: int | None = None,
    max_providers: int | None = None,
) -> dict[str, Any]:
    """Return the Settings provider-grouped model catalog."""
    data = _read_settings(user_id)
    providers = []

    for provider in _catalog_providers():
        provider_id = provider["id"]
        provider_name = provider["name"]
        key_source = _provider_key_source(provider_id, user_id, data)
        all_provider_models = _provider_models(provider_id, provider_name)
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
        "default_model": data.get("default_model"),
        "total_providers": total_providers,
        "providers": shown_providers,
    }


def _env_key_for_provider(provider_id: str) -> str | None:
    mapping = {
        "agnes": "AGNES_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "ollama": "OLLAMA_API_KEY",
        "ollama-cloud": "OLLAMA_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "together": "TOGETHER_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    env_var = mapping.get(provider_id)
    if env_var:
        return os.environ.get(env_var)
    return None


@router.patch("")
async def update_settings(
    body: UpdateSettingsRequest,
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    """Update settings (default_model, etc.)."""
    data = _read_settings(user_id)
    if body.default_model is not None:
        data["default_model"] = body.default_model
    _write_settings(user_id, data)
    _reset_user_loops(user_id)
    return {"status": "updated"}


@router.get("/api-keys")
async def list_api_keys(user_id: str = Query("default_user")) -> dict[str, Any]:
    """List which providers have stored API keys (without revealing keys)."""
    data = _read_settings(user_id)
    keys = data.get("provider_keys", {})
    return {pid: bool(val) for pid, val in keys.items()}


@router.post("/api-keys")
async def set_api_key(
    body: SetApiKeyRequest,
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    """Store an API key for a provider."""
    data = _read_settings(user_id)
    data.setdefault("provider_keys", {})[body.provider] = body.api_key
    _write_settings(user_id, data)
    _reset_user_loops(user_id)
    return {"status": "stored", "provider": body.provider}


@router.post("/test-key")
async def test_api_key(body: TestKeyRequest) -> dict[str, Any]:
    """Test whether an API key is valid for the given provider.

    Makes a minimal API call (list models) to verify the key works.
    Returns success/failure with an error message on failure.
    """
    from src.sdk.providers.factory import create_provider
    from src.sdk.registry import get_provider as get_provider_meta

    provider = body.provider
    api_key = body.api_key

    if not api_key:
        return {"valid": False, "error": "API key is empty"}

    try:
        meta = get_provider_meta(provider)
        provider_type = meta.get("type", "openai-compatible") if meta else "openai-compatible"
        base_url = meta.get("base_url", "") if meta else ""

        if base_url and provider_type in ("openai", "openai-compatible") and not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"

        prov = create_provider(provider, api_key=api_key, base_url=base_url or None)
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


@router.delete("/api-keys/{provider}")
async def delete_api_key(
    provider: str,
    user_id: str = Query("default_user"),
) -> dict[str, Any]:
    """Remove a stored API key for a provider."""
    data = _read_settings(user_id)
    data.get("provider_keys", {}).pop(provider, None)
    _write_settings(user_id, data)
    _reset_user_loops(user_id)
    return {"status": "removed", "provider": provider}
