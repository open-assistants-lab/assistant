"""Provider factory — creates LLMProvider instances from config.

Uses the models.dev-powered registry to resolve provider types and base URLs
dynamically. Falls back to hardcoded defaults for well-known providers.
"""

from __future__ import annotations

import os
from typing import Any

import src.config.user_settings_store as _user_settings_store
from src.app_logging import get_logger
from src.config.user_settings import SavedUserSettings
from src.sdk.providers.anthropic import AnthropicProvider
from src.sdk.providers.base import LLMProvider
from src.sdk.providers.gemini import GeminiProvider
from src.sdk.providers.ollama import OllamaCloud
from src.sdk.providers.openai import OpenAIProvider

logger = get_logger()

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "openai-compatible": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}

_ENV_KEY_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "ollama-cloud": "OLLAMA_API_KEY",
    "agnes": "AGNES_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _resolve_provider_type(provider_id: str) -> tuple[str, str]:
    lower = provider_id.lower().strip()

    if lower == "ollama-cloud":
        return "ollama-cloud", ""
    if lower == "ollama":
        return "ollama", ""
    if lower == "anthropic":
        return "anthropic", ""
    if lower == "gemini":
        return "gemini", ""
    if lower in ("openai",):
        return "openai", ""
    if lower == "agnes":
        return "openai-compatible", "https://apihub.agnes-ai.com/v1"

    from src.sdk.registry import get_provider

    registry_provider = get_provider(provider_id)
    if registry_provider:
        provider_type = registry_provider["type"]
        if provider_type not in _PROVIDER_CLASSES:
            provider_type = "openai-compatible"
        return provider_type, registry_provider.get("base_url", "") or ""

    if lower in _PROVIDER_CLASSES:
        return lower, ""
    return "openai-compatible", ""


def create_provider(
    provider_type: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    resolved_type, registry_url = _resolve_provider_type(provider_type)

    if resolved_type == "ollama":
        env_base_url = os.environ.get("OLLAMA_LOCAL_BASE_URL", "")
        resolved_url = base_url or env_base_url or registry_url or "http://localhost:11434/v1"
        return OpenAIProvider(base_url=resolved_url, model=model or "minimax-m2.5")

    if resolved_type == "ollama-cloud":
        resolved_key = api_key or os.environ.get("OLLAMA_API_KEY", "")
        resolved_url = base_url or os.environ.get("OLLAMA_BASE_URL", "") or "https://ollama.com"
        return OllamaCloud(
            base_url=resolved_url,
            model=model or "minimax-m2.5",
            api_key=resolved_key,
        )

    if resolved_type == "anthropic":
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        resolved_url = base_url or "https://api.anthropic.com"
        return AnthropicProvider(
            api_key=resolved_key, model=model or "claude-sonnet-4-20250514", base_url=resolved_url
        )

    if resolved_type == "gemini":
        resolved_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        return GeminiProvider(api_key=resolved_key, model=model or "gemini-2.5-flash")

    if resolved_type in ("openai", "openai-compatible"):
        env_key = _ENV_KEY_MAP.get(provider_type, "")
        resolved_key = api_key or (os.environ.get(env_key, "") if env_key else "")
        default_url = registry_url or _default_base_url(provider_type)
        resolved_url = base_url or default_url
        return OpenAIProvider(
            api_key=resolved_key or "unused",
            base_url=resolved_url,
            model=model or _default_model(provider_type),
            provider_id=provider_type,
            default_provider_options=_default_provider_options(provider_type),
            timeout=300.0 if provider_type == "agnes" else 120.0,
        )

    raise ValueError(f"Unknown provider type: {provider_type}")


def _registry_ref_parts(model_ref: str) -> tuple[str, str, str] | None:
    if ":" in model_ref:
        provider_id, model_name = model_ref.split(":", 1)
        provider_id = provider_id.strip()
        model_name = model_name.strip()
        if not provider_id or not model_name:
            return None
        return provider_id, model_name, f"{provider_id}/{model_name}"

    if "/" in model_ref:
        provider_id, model_name = model_ref.split("/", 1)
        provider_id = provider_id.strip()
        model_name = model_name.strip()
        if not provider_id or not model_name:
            return None
        return provider_id, model_name, model_ref

    return None


def create_provider_from_registry_model(
    model_ref: str, api_key: str | None = None
) -> LLMProvider | None:
    """Create a provider from an exact models.dev provider/model reference."""
    parts = _registry_ref_parts(model_ref)
    if parts is None:
        return None

    provider_id, model_name, registry_ref = parts
    from src.sdk.registry import get_model_info, get_provider

    provider_info = get_provider(provider_id)
    model_info = get_model_info(registry_ref)
    if not provider_info or model_info.provider_id != provider_id:
        return None

    base_url = provider_info.get("base_url") or None
    env_keys = provider_info.get("env") or []
    resolved_key = api_key
    for env_key in env_keys:
        if not resolved_key and os.environ.get(env_key):
            resolved_key = os.environ[env_key]
            break

    if provider_id == "ollama-cloud" and base_url and base_url.rstrip("/").endswith("/v1"):
        base_url = base_url.rstrip("/")[:-3]

    return create_provider(provider_id, model=model_name, api_key=resolved_key, base_url=base_url)


def _default_base_url(provider_id: str) -> str:
    from src.sdk.registry import get_provider

    p = get_provider(provider_id)
    if p and p.get("base_url"):
        return str(p["base_url"])

    _fallback: dict[str, str] = {
        "agnes": "https://apihub.agnes-ai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "together": "https://api.together.xyz/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "lmstudio": "http://localhost:1234/v1",
        "llamacpp": "http://localhost:8080/v1",
    }
    return _fallback.get(provider_id, "https://api.openai.com/v1")


def _default_model(provider_id: str) -> str:
    if provider_id == "agnes":
        return "agnes-2.0-flash"
    return "gpt-4o"


def _default_provider_options(provider_id: str) -> dict[str, Any]:
    if provider_id == "agnes":
        return {"chat_template_kwargs": {"enable_thinking": True}}
    return {}


def _load_user_settings(user_id: str) -> SavedUserSettings | None:
    """Load one canonical settings snapshot, falling back safely on known failures."""
    try:
        return _user_settings_store.UserSettingsStore(user_id).load()
    except (_user_settings_store.UserSettingsStoreError, ValueError) as exc:
        logger.warning(
            "provider.user_settings_load_failed",
            {"error_type": type(exc).__name__},
            user_id=user_id,
        )
        return None


def _load_stored_key(provider_type: str, user_id: str = "default_user") -> str | None:
    """Check the canonical per-user settings store for a provider API key."""
    saved = _load_user_settings(user_id)
    return saved.provider_keys.get(provider_type) if saved is not None else None


def _load_stored_default_model(user_id: str = "default_user") -> str | None:
    """Check the canonical per-user settings store for a default model override."""
    saved = _load_user_settings(user_id)
    return saved.default_model if saved is not None else None


def create_model_from_config(
    config_model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    user_id: str = "default_user",
) -> LLMProvider:
    from src.config import get_settings

    settings = get_settings()
    saved = _load_user_settings(user_id)
    model_str = config_model or (saved.default_model if saved else None) or settings.agent.model

    provider_type, model_name = _parse_model_string(model_str)

    resolved_key = None
    if provider_keys:
        resolved_key = provider_keys.get(provider_type) or provider_keys.get(provider_type.lower(), "")
        if not resolved_key:
            resolved_key = None

    if not resolved_key:
        resolved_key = saved.provider_keys.get(provider_type) if saved else None

    registry_provider = create_provider_from_registry_model(model_str, api_key=resolved_key)
    if registry_provider is not None:
        if not resolved_key:
            provider = registry_provider
        elif hasattr(registry_provider, "_api_key"):
            setattr(registry_provider, "_api_key", resolved_key)
            provider = registry_provider
        else:
            registry_type = getattr(registry_provider, "provider_type", "openai-compatible")
            base_url = getattr(registry_provider, "base_url", None)
            provider = create_provider(
                registry_type,
                model=model_name,
                api_key=resolved_key,
                base_url=base_url,
            )
    else:
        provider = create_provider(provider_type, model=model_name, api_key=resolved_key)

    # Wrap with Langfuse if enabled
    lf_settings = get_settings()
    if (
        lf_settings.langfuse.enabled
        and lf_settings.langfuse.public_key
        and lf_settings.langfuse.secret_key
    ):
        from src.sdk.langfuse_tracer import LangfuseTracer
        if not LangfuseTracer.is_enabled():
            LangfuseTracer.init(
                public_key=lf_settings.langfuse.public_key,
                secret_key=lf_settings.langfuse.secret_key,
                host=lf_settings.langfuse.host,
            )
        if LangfuseTracer.is_enabled():
            provider = LangfuseTracer.wrap_provider(provider)

    return provider


def _parse_model_string(model_str: str) -> tuple[str, str]:
    if ":" in model_str:
        provider, model_name = model_str.split(":", 1)
        return provider.strip(), model_name.strip()
    if "/" in model_str:
        provider, model_name = model_str.split("/", 1)
        return provider.strip(), model_name.strip()
    return "ollama", model_str.strip()
