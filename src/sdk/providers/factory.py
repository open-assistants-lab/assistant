"""Provider factory — creates LLMProvider instances from config.

Uses the models.dev-powered registry to resolve provider types and base URLs
dynamically. Falls back to hardcoded defaults for well-known providers.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import threading
import weakref
from collections import OrderedDict
from collections.abc import Mapping
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

# ---------------------------------------------------------------------------
# Keyed provider cache (audit S3)
#
# Providers used to be rebuilt on nearly every request — each construction
# creates a fresh httpx/AsyncOpenAI client with a cold TLS pool, defeating
# connection reuse. The cache below reuses one provider per (effective
# constructor inputs) so repeated calls share the underlying client.
#
# Scoping: httpx clients are bound to the event loop that created them, so
# the cache is keyed per running loop (WeakKeyDictionary — entries vanish
# with their loop). A thread-id fallback covers sync callers that run with
# no running loop (unit tests, rare sync paths).
# ---------------------------------------------------------------------------

_PROVIDER_CACHE_LIMIT = 64

# {event loop -> OrderedDict[(key tuple) -> LLMProvider]}
_loop_provider_caches: weakref.WeakKeyDictionary[Any, OrderedDict[tuple[Any, ...], LLMProvider]] = (
    weakref.WeakKeyDictionary()
)
# {thread id -> OrderedDict[(key tuple) -> LLMProvider]} — sync/no-loop fallback.
_thread_provider_caches: dict[int, OrderedDict[tuple[Any, ...], LLMProvider]] = {}
_provider_cache_lock = threading.Lock()
# Providers evicted from the cache but still potentially referenced by live
# AgentLoops and concurrent streams. They are deliberately NOT closed at
# eviction (closing would abort in-flight requests on a shared client);
# close_all_providers() closes them once, at shutdown.
_evicted_providers: set[LLMProvider] = set()



def _freeze(value: Any) -> Any:
    """Normalize kwargs into a deterministic, hashable structure."""
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _provider_cache_key(
    provider_type: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    kwargs: dict[str, Any],
) -> tuple[Any, ...]:
    """Hash every constructor-affecting input into one stable key.

    Distinct effective inputs (provider type, model, API key, base URL,
    extra kwargs such as timeouts/options) must never collide; identical
    inputs must hash identically. The API key is hashed so plaintext keys
    never live in the cache key.
    """
    api_key_hash = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:12]
    return (
        provider_type.lower().strip(),
        model,
        api_key_hash,
        base_url,
        _freeze(kwargs),
    )


def _get_caches() -> tuple[Any, OrderedDict[tuple[Any, ...], LLMProvider]]:
    """Return (owner, cache) for the current loop or (loop-less) thread."""
    try:
        owner: Any = asyncio.get_running_loop()
    except RuntimeError:
        owner = threading.current_thread().ident
        with _provider_cache_lock:
            cache = _thread_provider_caches.get(owner)
            if cache is None:
                cache = OrderedDict()
                _thread_provider_caches[owner] = cache
        return owner, cache
    cache = _loop_provider_caches.get(owner)
    if cache is None:
        cache = OrderedDict()
        _loop_provider_caches[owner] = cache
    return owner, cache


def _evict_overflow(owner: Any, cache: OrderedDict[tuple[Any, ...], LLMProvider]) -> None:
    """Evict LRU entries past the cap WITHOUT closing them.

    An evicted provider may still be referenced by live AgentLoops (runner's
    _loop_cache) and concurrent streams; closing its client here would abort
    their in-flight requests. Eviction only drops the cache entry and moves
    the provider to _evicted_providers, which close_all_providers() closes
    exactly once at shutdown.
    """
    while len(cache) > _PROVIDER_CACHE_LIMIT:
        _, evicted = cache.popitem(last=False)
        _evicted_providers.add(evicted)


async def close_all_providers() -> None:
    """Close every cached and evicted provider's HTTP client; clear caches.

    Called from the HTTP server shutdown (main.py lifespan) so sockets are
    released deterministically instead of waiting on GC. This is the ONLY
    place cached/evicted clients are closed — eviction (past the LRU cap)
    never closes a client that live references may still be using.
    """
    caches = list(_loop_provider_caches.values())
    with _provider_cache_lock:
        caches += list(_thread_provider_caches.values())
        _thread_provider_caches.clear()
    _loop_provider_caches.clear()
    providers: list[LLMProvider] = [p for cache in caches for p in cache.values()]
    providers += list(_evicted_providers)
    _evicted_providers.clear()
    for provider in providers:
        close = getattr(provider, "aclose", None)
        if close is None:
            continue
        try:
            await close()
        except Exception as exc:  # noqa: BLE001 — shutdown must never raise
            logger.warning(
                "provider.close_failed",
                {"error_type": type(exc).__name__},
                user_id="system",
            )
    for cache in caches:
        cache.clear()


def get_cached_provider(
    provider_type: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs: Any,
) -> LLMProvider:
    """Return a provider for the effective constructor inputs, reusing a
    cached instance per event loop when the inputs match (audit S3).

    The cache key covers every constructor-affecting input (provider type,
    model, hashed API key, base URL, extra kwargs) so distinct effective
    inputs never collide and identical ones share one HTTP client. The
    cache is per event loop because httpx/AsyncOpenAI clients are bound to
    their creating loop. Works in sync callers too (loop-less fallback).
    """
    owner, cache = _get_caches()
    key = _provider_cache_key(provider_type, model, api_key, base_url, kwargs)
    provider = cache.get(key)
    if provider is not None:
        cache.move_to_end(key)
        return provider
    provider = create_provider(
        provider_type, model=model, api_key=api_key, base_url=base_url, **kwargs
    )
    cache[key] = provider
    cache.move_to_end(key)
    _evict_overflow(owner, cache)
    return provider


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
        return OpenAIProvider(
            base_url=resolved_url,
            model=model or "minimax-m2.5",
            provider_id="ollama",
        )

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


def _resolve_registry_provider(
    model_ref: str, api_key: str | None = None
) -> tuple[str, str, str | None, str | None] | None:
    """Resolve a models.dev ref to effective constructor inputs.

    Returns (provider_id, model_name, base_url, resolved_key) when the ref
    resolves to a known registry provider, else None. Shared by the uncached
    constructor and the keyed cache so the cache key sees the same effective
    base_url/key the constructor would use.
    """
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

    return provider_id, model_name, base_url, resolved_key


def create_provider_from_registry_model(
    model_ref: str, api_key: str | None = None
) -> LLMProvider | None:
    """Create a provider from an exact models.dev provider/model reference."""
    resolved = _resolve_registry_provider(model_ref, api_key)
    if resolved is None:
        return None

    provider_id, model_name, base_url, resolved_key = resolved
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
    except (_user_settings_store.UserSettingsStoreError, OSError, ValueError) as exc:
        logger.warning(
            "provider.user_settings_load_failed",
            {"error_type": type(exc).__name__},
            user_id=user_id,
        )
        return None


def _provider_key(keys: Mapping[str, str] | None, provider_type: str) -> str | None:
    if not keys:
        return None
    value = keys.get(provider_type)
    if value:
        return value
    return next(
        (value for provider, value in keys.items() if provider.lower() == provider_type and value),
        None,
    )


def _load_stored_key(provider_type: str, user_id: str = "default_user") -> str | None:
    """Check the canonical per-user settings store for a provider API key."""
    saved = _load_user_settings(user_id)
    return _provider_key(saved.provider_keys, provider_type.lower()) if saved is not None else None


def _load_stored_default_model(user_id: str = "default_user") -> str | None:
    """Check the canonical per-user settings store for a default model override."""
    saved = _load_user_settings(user_id)
    return saved.default_model if saved is not None else None


def _resolve_config_model_inputs(
    config_model: str | None,
    provider_keys: dict[str, str] | None,
    user_id: str,
) -> tuple[str, str, str, str | None]:
    """Resolve the effective (provider_type, model_name, normalized_model_ref, resolved_key).

    Shared by the uncached create_model_from_config and the cached
    get_cached_model_provider so their resolution can never drift. Each
    caller then performs its own registry step: the uncached path calls
    create_provider_from_registry_model (patchable), the cached path calls
    _resolve_registry_provider to key the cache.
    """
    from src.config import get_settings

    settings = get_settings()
    saved: SavedUserSettings | None = None
    settings_loaded = False
    model_str = config_model
    if not model_str:
        saved = _load_user_settings(user_id)
        settings_loaded = True
        model_str = (saved.default_model if saved else None) or settings.agent.model

    if not model_str:
        raise ValueError(
            "No model configured. Set agent.model in config.yaml (deployment "
            "default), add 'model:' to the user's PROFILE.md (primary agent "
            "configuration), or pass model/provider_keys per request."
        )

    provider_type, model_name = _parse_model_string(model_str)
    normalized_model_ref = _normalized_model_ref(model_str, provider_type, model_name)

    resolved_key = _provider_key(provider_keys, provider_type)
    if not resolved_key:
        if not settings_loaded:
            saved = _load_user_settings(user_id)
            settings_loaded = True
        resolved_key = _provider_key(saved.provider_keys, provider_type) if saved else None

    return provider_type, model_name, normalized_model_ref, resolved_key


def _maybe_wrap_langfuse(provider: LLMProvider) -> LLMProvider:
    """Apply the Langfuse tracing wrapper when configured (shared by both
    constructor paths so cached and uncached providers behave identically).

    Idempotent: LangfuseTracer.wrap_provider MUTATES the provider in place
    (assigns provider.chat/chat_stream) and has no guard of its own, so the
    cached path would otherwise stack a new traced_chat closure on every
    call — one nested generation span per layer per request. The first wrap
    stamps `_langfuse_wrapped`; later calls return the provider untouched.
    """
    if getattr(provider, "_langfuse_wrapped", False):
        return provider
    from src.config import get_settings

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
            provider._langfuse_wrapped = True  # type: ignore[attr-defined]
    return provider


def create_model_from_config(
    config_model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    user_id: str = "default_user",
) -> LLMProvider:
    """Create a provider from model config — uncached.

    Request paths should prefer get_cached_model_provider() so identical
    effective inputs reuse one HTTP client (audit S3). This uncached form
    is retained for short-lived consumers (rubric graders) that close their
    provider explicitly.
    """
    provider_type, model_name, normalized_model_ref, resolved_key = _resolve_config_model_inputs(
        config_model, provider_keys, user_id
    )

    registry_provider = create_provider_from_registry_model(
        normalized_model_ref, api_key=resolved_key
    )
    if registry_provider is not None:
        provider = registry_provider
    else:
        provider = create_provider(provider_type, model=model_name, api_key=resolved_key)

    return _maybe_wrap_langfuse(provider)


def get_cached_model_provider(
    config_model: str | None = None,
    provider_keys: dict[str, str] | None = None,
    user_id: str = "default_user",
) -> LLMProvider:
    """Model-config resolver backed by the keyed provider cache (audit S3).

    Resolves the same effective inputs create_model_from_config would and
    routes construction through get_cached_provider, so repeated calls with
    the same model/key/base_url share one underlying HTTP client. The
    Langfuse wrapper (when enabled) is applied AFTER the cache lookup: the
    wrapper is thin and stateless, and keeping the raw provider cached
    preserves client reuse.
    """
    provider_type, model_name, normalized_model_ref, resolved_key = _resolve_config_model_inputs(
        config_model, provider_keys, user_id
    )
    registry = _resolve_registry_provider(normalized_model_ref, api_key=resolved_key)
    if registry is not None:
        registry_provider_type, registry_model_name, base_url, registry_key = registry
        provider = get_cached_provider(
            registry_provider_type,
            model=registry_model_name,
            api_key=registry_key,
            base_url=base_url,
        )
    else:
        provider = get_cached_provider(provider_type, model=model_name, api_key=resolved_key)
    return _maybe_wrap_langfuse(provider)



def _parse_model_string(model_str: str) -> tuple[str, str]:
    if ":" in model_str:
        provider, model_name = model_str.split(":", 1)
        return provider.strip().lower(), model_name.strip()
    if "/" in model_str:
        provider, model_name = model_str.split("/", 1)
        return provider.strip().lower(), model_name.strip()
    return "ollama", model_str.strip()


def _normalized_model_ref(model_str: str, provider_type: str, model_name: str) -> str:
    if ":" in model_str:
        return f"{provider_type}:{model_name}"
    if "/" in model_str:
        return f"{provider_type}/{model_name}"
    return model_name
