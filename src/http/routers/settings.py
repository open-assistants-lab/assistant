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


@router.get("")
async def get_settings(user_id: str = Query("default_user")) -> dict[str, Any]:
    """Read current settings (default model, which providers have keys)."""
    data = _read_settings(user_id)
    from src.sdk.registry import list_providers

    providers_meta = list_providers()
    provider_status: dict[str, Any] = {}
    for p in providers_meta:
        pid = p.get("id", "")
        has_key = pid in data.get("provider_keys", {})
        has_env = _env_key_for_provider(pid) is not None
        provider_status[pid] = {
            "name": p.get("name", pid),
            "has_key": has_key or has_env,
            "key_configured_via_env": has_env,
        }
    return {
        "default_model": data.get("default_model"),
        "provider_status": provider_status,
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
                return {"valid": False, "error": f"Cannot test provider type: {provider_type}"}
        except Exception as e:
            status = getattr(e, "status_code", None) or getattr(
                getattr(e, "response", None), "status_code", None
            )
            err_body = getattr(e, "body", None) or getattr(
                getattr(e, "response", None), "text", str(e)
            )
            if status == 401:
                return {"valid": False, "error": "Invalid API key (401 Unauthorized)"}
            if status == 403:
                return {"valid": False, "error": "API key lacks permission (403 Forbidden)"}
            if status == 429:
                return {"valid": True, "warning": "Rate limited — key appears valid"}
            return {"valid": False, "error": str(err_body)[:200]}

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
    return {"status": "removed", "provider": provider}
