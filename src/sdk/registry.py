"""Model registry powered by models.dev.

Fetches model/provider metadata from https://models.dev/api.json, caches locally,
and transforms into SDK ModelInfo/ModelCost types. Falls back to a built-in
curated subset if the API is unreachable.

Usage:
    from src.sdk.registry import get_model_info, list_models, get_provider

    info = get_model_info("gpt-4o")
    models = list_models(provider="openai", tool_call=True)
    provider = get_provider("anthropic")

Configuration:
    MODELS_DEV_URL: Override the API endpoint (default: https://models.dev/api.json)
    MODELS_DEV_CACHE_PATH: Local cache file (default: data/cache/models.json)
    MODELS_DEV_CACHE_TTL: Cache TTL in seconds (default: 300 = 5 minutes)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, cast
from urllib.request import Request, urlopen

from src.sdk.providers.base import ModelCost, ModelInfo
from src.storage.paths import get_paths

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://models.dev/api.json"
_DEFAULT_CACHE_TTL = 300

_PROVIDER_TYPE_MAP: dict[str, str] = {
    "@ai-sdk/openai": "openai",
    "@ai-sdk/anthropic": "anthropic",
    "@ai-sdk/google": "gemini",
    "@ai-sdk/groq": "groq",
    "@ai-sdk/openai-compatible": "openai-compatible",
}

_models_cache: dict[str, ModelInfo] | None = None
_providers_cache: dict[str, dict[str, Any]] | None = None
_last_fetch_time: float = 0.0


def _resolve_provider_type(npm: str, api: str | None) -> str:
    if npm in _PROVIDER_TYPE_MAP:
        return _PROVIDER_TYPE_MAP[npm]
    return "openai-compatible"


def _transform_model(model_id: str, model_data: dict[str, Any], provider_id: str) -> ModelInfo:
    cost_data: dict[str, Any] = model_data.get("cost", {})
    cost = ModelCost(
        input=cost_data.get("input", 0.0),
        output=cost_data.get("output", 0.0),
        reasoning=cost_data.get("reasoning"),
        cache_read=cost_data.get("cache_read"),
        cache_write=cost_data.get("cache_write"),
        input_audio=cost_data.get("input_audio"),
        output_audio=cost_data.get("output_audio"),
    )

    limit_data = model_data.get("limit", {})
    mod_data = model_data.get("modalities", {})
    raw_interleaved = model_data.get("interleaved", False)
    if isinstance(raw_interleaved, dict):
        interleaved = raw_interleaved.get("field", "")
    elif isinstance(raw_interleaved, bool):
        interleaved = raw_interleaved
    else:
        interleaved = str(raw_interleaved)

    return ModelInfo(
        id=model_id,
        name=model_data.get("name", model_id),
        provider_id=provider_id,
        family=model_data.get("family"),
        tool_call=model_data.get("tool_call", True),
        reasoning=model_data.get("reasoning", False),
        structured_output=model_data.get("structured_output", False),
        temperature=model_data.get("temperature", True),
        attachment=model_data.get("attachment", False),
        interleaved=interleaved,
        context_window=limit_data.get("context", 128000),
        input_limit=limit_data.get("input"),
        output_limit=limit_data.get("output", 4096),
        cost=cost,
        modalities_input=mod_data.get("input", ["text"]),
        modalities_output=mod_data.get("output", ["text"]),
        open_weights=model_data.get("open_weights", False),
        knowledge=model_data.get("knowledge"),
        release_date=model_data.get("release_date"),
        last_updated=model_data.get("last_updated"),
        status=model_data.get("status"),
    )


def _transform_api_data(data: dict[str, Any]) -> tuple[dict[str, ModelInfo], dict[str, dict[str, Any]]]:
    models: dict[str, ModelInfo] = {}
    providers: dict[str, dict[str, Any]] = {}

    for provider_id, provider_data in data.items():
        if not isinstance(provider_data, dict):
            continue
        if provider_id.startswith("_"):
            continue

        npm = provider_data.get("npm", "")
        api_url = provider_data.get("api")
        provider_type = _resolve_provider_type(npm, api_url)

        providers[provider_id] = {
            "name": provider_data.get("name", provider_id),
            "type": provider_type,
            "npm": npm,
            "base_url": api_url or _default_base_url(provider_id, provider_type),
            "env": provider_data.get("env", []),
            "doc_url": provider_data.get("doc", ""),
        }

        for model_id, model_data in provider_data.get("models", {}).items():
            model_info = _transform_model(model_id, model_data, provider_id)
            qualified_id = f"{provider_id}/{model_id}"
            models[qualified_id] = model_info

            # Preserve bare model lookup as a convenience alias, but never let duplicate
            # model IDs from later providers overwrite earlier provider-specific choices.
            models.setdefault(model_id, model_info)

    return models, providers


def _default_base_url(provider_id: str, provider_type: str) -> str:
    _defaults: dict[str, str] = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "ollama": "http://localhost:11434/v1",
        "ollama-cloud": "https://ollama.com/v1",
    }
    return _defaults.get(provider_id, "")


def _get_cache_path() -> Path:
    env_path = os.environ.get("MODELS_DEV_CACHE_PATH")
    if env_path:
        return Path(env_path)
    return get_paths().model_cache_path()


def _get_api_url() -> str:
    return os.environ.get("MODELS_DEV_URL", _DEFAULT_API_URL)


def _get_cache_ttl() -> int:
    try:
        return int(os.environ.get("MODELS_DEV_CACHE_TTL", str(_DEFAULT_CACHE_TTL)))
    except (ValueError, TypeError):
        return _DEFAULT_CACHE_TTL


def _load_from_cache() -> dict[str, Any] | None:
    cache_path = _get_cache_path()
    if not cache_path.exists():
        return None
    try:
        data: dict[str, Any] = cast(dict[str, Any], json.loads(cache_path.read_text()))
        if time.time() - data.get("_fetched_at", 0) < _get_cache_ttl():
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def _save_to_cache(data: dict[str, Any]) -> None:
    cache_path = _get_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache_path.write_text(json.dumps(data))
    except OSError:
        logger.warning("registry: failed to save cache to %s", cache_path)


def _fetch_api() -> dict[str, Any] | None:
    url = _get_api_url()
    try:
        request = Request(url, headers={"User-Agent": "assistant/1.0"})
        with urlopen(request, timeout=10) as resp:
            data: dict[str, Any] = cast(dict[str, Any], json.loads(resp.read()))
        data["_fetched_at"] = time.time()
        _save_to_cache(data)
        return data
    except Exception as e:
        logger.warning("registry: failed to fetch %s: %s", url, e)
        return None


def _load_builtin() -> dict[str, Any]:
    return {
        "_fetched_at": 0,
        "openai": {
            "id": "openai",
            "name": "OpenAI",
            "npm": "@ai-sdk/openai",
            "env": ["OPENAI_API_KEY"],
            "doc": "https://platform.openai.com",
            "models": {
                "gpt-4o": {
                    "id": "gpt-4o",
                    "name": "GPT-4o",
                    "family": "gpt-4o",
                    "attachment": True,
                    "reasoning": False,
                    "tool_call": True,
                    "structured_output": True,
                    "temperature": True,
                    "open_weights": False,
                    "cost": {"input": 2.5, "output": 10.0},
                    "limit": {"context": 128000, "output": 16384},
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    "release_date": "2024-05-13",
                    "last_updated": "2024-05-13",
                },
                "o3": {
                    "id": "o3",
                    "name": "o3",
                    "family": "o3",
                    "attachment": True,
                    "reasoning": True,
                    "tool_call": True,
                    "structured_output": True,
                    "temperature": False,
                    "open_weights": False,
                    "cost": {"input": 2.0, "output": 8.0, "reasoning": 8.0},
                    "limit": {"context": 200000, "output": 100000},
                    "modalities": {"input": ["text", "image"], "output": ["text"]},
                    "release_date": "2025-04-11",
                    "last_updated": "2025-04-11",
                },
            },
        },
        "anthropic": {
            "id": "anthropic",
            "name": "Anthropic",
            "npm": "@ai-sdk/anthropic",
            "env": ["ANTHROPIC_API_KEY"],
            "doc": "https://docs.anthropic.com",
            "models": {
                "claude-sonnet-4-20250514": {
                    "id": "claude-sonnet-4-20250514",
                    "name": "Claude Sonnet 4",
                    "family": "claude-sonnet",
                    "attachment": True,
                    "reasoning": True,
                    "tool_call": True,
                    "structured_output": True,
                    "temperature": True,
                    "interleaved": {"field": "reasoning_content"},
                    "open_weights": False,
                    "cost": {"input": 3.0, "output": 15.0, "reasoning": 15.0},
                    "limit": {"context": 200000, "output": 64000},
                    "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                    "release_date": "2025-05-14",
                    "last_updated": "2025-05-14",
                },
            },
        },
        "ollama": {
            "id": "ollama",
            "name": "Ollama",
            "npm": "@ai-sdk/openai-compatible",
            "api": "http://localhost:11434/v1",
            "env": [],
            "doc": "https://ollama.com",
            "models": {
                "minimax-m2.5": {
                    "id": "minimax-m2.5",
                    "name": "MiniMax M2.5",
                    "family": "minimax",
                    "attachment": False,
                    "reasoning": False,
                    "tool_call": True,
                    "structured_output": True,
                    "temperature": True,
                    "open_weights": True,
                    "cost": {"input": 0.0, "output": 0.0},
                    "limit": {"context": 1040384, "output": 16384},
                    "modalities": {"input": ["text"], "output": ["text"]},
                    "release_date": "2025-03",
                    "last_updated": "2025-03",
                },
            },
        },
        "ollama-cloud": {
            "id": "ollama-cloud",
            "name": "Ollama Cloud",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://ollama.com/v1",
            "env": ["OLLAMA_API_KEY"],
            "doc": "https://docs.ollama.com/cloud",
            "models": {
                "minimax-m2.5": {
                    "id": "minimax-m2.5",
                    "name": "minimax-m2.5",
                    "family": "minimax",
                    "attachment": False,
                    "reasoning": True,
                    "tool_call": True,
                    "structured_output": True,
                    "temperature": True,
                    "open_weights": False,
                    "cost": {"input": 0.0, "output": 0.0},
                    "limit": {"context": 204800, "output": 131072},
                    "modalities": {"input": ["text"], "output": ["text"]},
                },
            },
        },
    }


def _ensure_loaded(force: bool = False) -> None:
    global _models_cache, _providers_cache, _last_fetch_time

    if _models_cache is not None and not force:
        return

    data = _load_from_cache()
    if data is None:
        data = _fetch_api()
    if data is None:
        data = _load_builtin()
        logger.info("registry: using built-in fallback data")

    _models_cache, _providers_cache = _transform_api_data(data)
    _last_fetch_time = time.time()


def refresh() -> None:
    _ensure_loaded(force=True)


def get_model_info(model_id: str) -> ModelInfo:
    _ensure_loaded()
    assert _models_cache is not None
    if model_id in _models_cache:
        return _models_cache[model_id]
    provider_id = model_id.split("/")[0] if "/" in model_id else "unknown"
    return ModelInfo(id=model_id, name=model_id, provider_id=provider_id)


def list_models(
    provider: str | None = None,
    tool_call: bool | None = None,
    reasoning: bool | None = None,
    attachment: bool | None = None,
    open_weights: bool | None = None,
) -> list[ModelInfo]:
    _ensure_loaded()
    assert _models_cache is not None
    models = list(_models_cache.values())
    if provider:
        models = [m for m in models if m.provider_id == provider]
    if tool_call is not None:
        models = [m for m in models if m.tool_call == tool_call]
    if reasoning is not None:
        models = [m for m in models if m.reasoning == reasoning]
    if attachment is not None:
        models = [m for m in models if m.attachment == attachment]
    if open_weights is not None:
        models = [m for m in models if m.open_weights == open_weights]
    return models


def get_provider(provider_id: str) -> dict[str, Any] | None:
    _ensure_loaded()
    assert _providers_cache is not None
    return _providers_cache.get(provider_id)


def list_providers() -> list[dict[str, Any]]:
    _ensure_loaded()
    assert _providers_cache is not None
    return list(_providers_cache.values())
