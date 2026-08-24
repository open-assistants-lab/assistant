"""Tests for models.dev registry integration."""

from unittest.mock import patch

import pytest

from src.sdk.registry import (
    _transform_api_data,
    _transform_model,
    get_model_info,
    get_provider,
    list_models,
    list_providers,
    refresh,
)

SAMPLE_API_DATA = {
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
                "cost": {"input": 2.5, "output": 10.0, "cache_read": 1.25},
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
                "cost": {
                    "input": 3.0,
                    "output": 15.0,
                    "reasoning": 15.0,
                    "cache_read": 0.3,
                    "cache_write": 3.75,
                },
                "limit": {"context": 200000, "output": 64000},
                "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                "release_date": "2025-05-14",
                "last_updated": "2025-05-14",
            },
        },
    },
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "npm": "@ai-sdk/openai-compatible",
        "api": "https://api.deepseek.com",
        "env": ["DEEPSEEK_API_KEY"],
        "doc": "https://platform.deepseek.com",
        "models": {
            "deepseek-chat": {
                "id": "deepseek-chat",
                "name": "DeepSeek V3",
                "family": "deepseek",
                "attachment": False,
                "reasoning": False,
                "tool_call": True,
                "structured_output": True,
                "temperature": True,
                "open_weights": True,
                "cost": {"input": 0.27, "output": 1.1, "cache_read": 0.07},
                "limit": {"context": 131072, "output": 8192},
                "modalities": {"input": ["text"], "output": ["text"]},
                "release_date": "2024-12-26",
                "last_updated": "2024-12-26",
            },
        },
    },
    "another-openai-compatible": {
        "id": "another-openai-compatible",
        "name": "Another OpenAI Compatible",
        "npm": "@ai-sdk/openai-compatible",
        "api": "https://api.example.com/v1",
        "env": ["EXAMPLE_API_KEY"],
        "models": {
            "gpt-4o": {
                "id": "gpt-4o",
                "name": "GPT-4o via Example",
                "reasoning": True,
                "limit": {"context": 64000, "output": 4096},
            }
        },
    },
    "ollama-cloud": {
        "id": "ollama-cloud",
        "name": "Ollama Cloud",
        "npm": "@ai-sdk/openai-compatible",
        "api": "https://ollama.com/v1",
        "env": ["OLLAMA_API_KEY"],
        "models": {
            "minimax-m2.5": {
                "id": "minimax-m2.5",
                "name": "minimax-m2.5",
                "reasoning": True,
                "limit": {"context": 204800, "output": 131072},
            }
        },
    },
}


class TestTransformModel:
    def test_basic_model(self):
        m = _transform_model("gpt-4o", SAMPLE_API_DATA["openai"]["models"]["gpt-4o"], "openai")
        assert m.id == "gpt-4o"
        assert m.name == "GPT-4o"
        assert m.provider_id == "openai"
        assert m.family == "gpt-4o"
        assert m.tool_call is True
        assert m.reasoning is False
        assert m.structured_output is True
        assert m.temperature is True
        assert m.attachment is True
        assert m.context_window == 128000
        assert m.output_limit == 16384
        assert m.open_weights is False
        assert m.release_date == "2024-05-13"

    def test_reasoning_model(self):
        m = _transform_model("o3", SAMPLE_API_DATA["openai"]["models"]["o3"], "openai")
        assert m.reasoning is True
        assert m.temperature is False
        assert m.cost is not None
        assert m.cost.reasoning == 8.0

    def test_interleaved_model(self):
        data = SAMPLE_API_DATA["anthropic"]["models"]["claude-sonnet-4-20250514"]
        m = _transform_model("claude-sonnet-4-20250514", data, "anthropic")
        assert m.interleaved == "reasoning_content"
        assert m.cost is not None
        assert m.cost.cache_read == 0.3
        assert m.cost.cache_write == 3.75

    def test_openai_compatible_provider(self):
        data = SAMPLE_API_DATA["deepseek"]["models"]["deepseek-chat"]
        m = _transform_model("deepseek-chat", data, "deepseek")
        assert m.provider_id == "deepseek"
        assert m.open_weights is True

    def test_modalities(self):
        m = _transform_model("gpt-4o", SAMPLE_API_DATA["openai"]["models"]["gpt-4o"], "openai")
        assert "image" in m.modalities_input
        assert "text" in m.modalities_output

    def test_cache_costs(self):
        data = SAMPLE_API_DATA["anthropic"]["models"]["claude-sonnet-4-20250514"]
        m = _transform_model("claude-sonnet-4-20250514", data, "anthropic")
        assert m.cost is not None
        assert m.cost.input == 3.0
        assert m.cost.output == 15.0
        assert m.cost.reasoning == 15.0
        assert m.cost.cache_read == 0.3
        assert m.cost.cache_write == 3.75

    def test_default_values(self):
        minimal = {"id": "test-model", "name": "Test"}
        m = _transform_model("test-model", minimal, "test")
        assert m.tool_call is True
        assert m.reasoning is False
        assert m.temperature is True
        assert m.attachment is False
        assert m.context_window == 128000
        assert m.output_limit == 4096
        assert m.cost is not None
        assert m.cost.input == 0.0

    def test_input_limit(self):
        data = {
            "id": "x",
            "name": "X",
            "limit": {"context": 200000, "input": 272000, "output": 128000},
        }
        m = _transform_model("x", data, "test")
        assert m.input_limit == 272000
        assert m.output_limit == 128000


class TestTransformApiData:
    def test_providers_transformed(self):
        models, providers = _transform_api_data(SAMPLE_API_DATA)
        assert "openai" in providers
        assert "anthropic" in providers
        assert "deepseek" in providers

    def test_provider_fields(self):
        _, providers = _transform_api_data(SAMPLE_API_DATA)
        openai = providers["openai"]
        assert openai["name"] == "OpenAI"
        assert openai["type"] == "openai"
        assert openai["env"] == ["OPENAI_API_KEY"]

    def test_deepseek_is_openai_compatible(self):
        _, providers = _transform_api_data(SAMPLE_API_DATA)
        deepseek = providers["deepseek"]
        assert deepseek["type"] == "openai-compatible"
        assert deepseek["base_url"] == "https://api.deepseek.com"

    def test_models_transformed(self):
        models, _ = _transform_api_data(SAMPLE_API_DATA)
        assert "gpt-4o" in models
        assert "openai/gpt-4o" in models
        assert "another-openai-compatible/gpt-4o" in models
        assert "ollama-cloud/minimax-m2.5" in models
        assert "o3" in models
        assert "claude-sonnet-4-20250514" in models
        assert "deepseek-chat" in models

    def test_model_count(self):
        models, _ = _transform_api_data(SAMPLE_API_DATA)
        # 6 provider/model entries plus 5 bare aliases; duplicate bare gpt-4o does not overwrite.
        assert len(models) == 11

    def test_duplicate_bare_model_does_not_overwrite_first_provider(self):
        models, _ = _transform_api_data(SAMPLE_API_DATA)
        assert models["gpt-4o"].provider_id == "openai"
        assert models["another-openai-compatible/gpt-4o"].provider_id == "another-openai-compatible"


class TestRegistryWithMockData:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import src.sdk.registry as reg

        cache_path = tmp_path / "models.json"
        reg._models_cache = None
        reg._providers_cache = None
        reg._last_fetch_time = 0.0

        with patch.object(reg, "_fetch_api", return_value=SAMPLE_API_DATA):
            with patch.object(reg, "_get_cache_path", return_value=cache_path):
                refresh()

    def test_get_model_info(self):
        info = get_model_info("gpt-4o")
        assert info.name == "GPT-4o"
        assert info.provider_id == "openai"

    def test_get_qualified_model_info(self):
        info = get_model_info("another-openai-compatible/gpt-4o")
        assert info.name == "GPT-4o via Example"
        assert info.provider_id == "another-openai-compatible"

    def test_get_model_info_unknown(self):
        info = get_model_info("unknown-model")
        assert info.id == "unknown-model"
        assert info.provider_id == "unknown"

    def test_list_models_filter_provider(self):
        models = list_models(provider="openai")
        assert all(m.provider_id == "openai" for m in models)
        assert len(models) == 4

    def test_list_models_filter_reasoning(self):
        models = list_models(reasoning=True)
        assert len(models) == 7

    def test_list_models_filter_tool_call(self):
        models = list_models(tool_call=True)
        assert all(m.tool_call for m in models)

    def test_list_models_filter_open_weights(self):
        models = list_models(open_weights=True)
        assert any(m.id == "deepseek-chat" for m in models)

    def test_get_provider(self):
        p = get_provider("openai")
        assert p is not None
        assert p["name"] == "OpenAI"
        assert p["type"] == "openai"

    def test_get_provider_unknown(self):
        assert get_provider("nonexistent") is None

    def test_list_providers(self):
        providers = list_providers()
        assert len(providers) >= 3
        names = [p["name"] for p in providers]
        assert "OpenAI" in names


class TestRegistryTTL:
    """Audit B13: in-process cache TTL is honored and stale disk data beats the builtin."""

    @staticmethod
    def _big_cache_data(n_models: int = 150) -> dict:
        return {
            "_fetched_at": 0,  # replaced by callers with an actual timestamp
            "test-provider": {
                "id": "test-provider",
                "name": "Test Provider",
                "npm": "@ai-sdk/openai-compatible",
                "models": {
                    f"m{i}": {"id": f"m{i}", "name": f"Model {i}"} for i in range(n_models)
                },
            },
        }

    def test_expired_cache_plus_fetch_failure_falls_back_to_stale_cache(self, tmp_path, monkeypatch):
        """Expired disk cache + failed fetch must use the stale cache, not the 4-model builtin."""
        import json
        import time

        import src.sdk.registry as reg

        reg._models_cache = None
        reg._providers_cache = None
        reg._last_fetch_time = 0.0

        cache_path = tmp_path / "models.json"
        data = self._big_cache_data()
        data["_fetched_at"] = time.time() - reg.REGISTRY_TTL_SECONDS - 60  # expired
        cache_path.write_text(json.dumps(data))

        monkeypatch.setattr(reg, "_get_cache_path", lambda: cache_path)
        monkeypatch.setattr(reg, "_fetch_api", lambda: None)

        reg._ensure_loaded(force=True)

        assert reg._models_cache is not None
        assert len(reg._models_cache) > 100  # stale cache, NOT the 4-model builtin
        assert "test-provider/m0" in reg._models_cache
        assert "test-provider/m149" in reg._models_cache

    def test_ttl_expiry_triggers_refresh(self, tmp_path, monkeypatch):
        """An expired in-process cache must re-fetch on the next call."""
        import time

        import src.sdk.registry as reg

        reg._models_cache = None
        reg._providers_cache = None
        reg._last_fetch_time = 0.0

        cache_path = tmp_path / "models.json"
        monkeypatch.setattr(reg, "_get_cache_path", lambda: cache_path)
        monkeypatch.setattr(reg, "_get_cache_ttl", lambda: 0)  # everything is always expired

        calls = {"n": 0}

        def fake_fetch() -> dict:
            calls["n"] += 1
            return {
                "_fetched_at": time.time(),
                "openai": {
                    "id": "openai",
                    "name": "OpenAI",
                    "models": {
                        f"v{calls['n']}-model": {
                            "id": f"v{calls['n']}-model",
                            "name": f"V{calls['n']}",
                        }
                    },
                },
            }

        monkeypatch.setattr(reg, "_fetch_api", fake_fetch)

        reg._ensure_loaded()
        first = reg._models_cache
        assert first is not None

        reg._ensure_loaded()

        assert calls["n"] == 2, "expired TTL must trigger a re-fetch"
        assert reg._models_cache is not first

    def test_refresh_failure_keeps_in_memory_cache(self, tmp_path, monkeypatch):
        """A failed refresh with no disk cache must keep in-memory data, not downgrade to builtin."""
        import time

        import src.sdk.registry as reg

        reg._models_cache = None
        reg._providers_cache = None
        reg._last_fetch_time = 0.0

        cache_path = tmp_path / "models.json"
        monkeypatch.setattr(reg, "_get_cache_path", lambda: cache_path)
        monkeypatch.setattr(reg, "_get_cache_ttl", lambda: 0)

        fetch_calls = {"n": 0}

        def failing_fetch():
            fetch_calls["n"] += 1
            return None

        # First load: fresh fetch succeeds.
        def ok_fetch() -> dict:
            fetch_calls["n"] += 1
            return dict(SAMPLE_API_DATA, _fetched_at=time.time())

        monkeypatch.setattr(reg, "_fetch_api", ok_fetch)
        reg._ensure_loaded()
        first = reg._models_cache
        assert first is not None and len(first) > 4

        # Now: cache is expired (ttl=0), fetch fails. The patched fetch never
        # writes the disk cache, so there is no stale disk copy either.
        monkeypatch.setattr(reg, "_fetch_api", failing_fetch)

        reg._ensure_loaded()

        assert fetch_calls["n"] == 2, "expired TTL must have attempted a re-fetch"
        assert reg._models_cache is first, "must keep the existing in-memory cache"
        assert len(reg._models_cache) > 4, "must NOT downgrade to the 4-model builtin"

    def test_concurrent_first_callers_single_flight(self, tmp_path, monkeypatch):
        """Concurrent first callers must perform exactly one fetch (thread-safe single-flight)."""
        import threading
        import time

        import src.sdk.registry as reg

        reg._models_cache = None
        reg._providers_cache = None
        reg._last_fetch_time = 0.0

        cache_path = tmp_path / "models.json"
        monkeypatch.setattr(reg, "_get_cache_path", lambda: cache_path)

        fetch_calls = {"n": 0}

        def slow_fetch() -> dict:
            fetch_calls["n"] += 1
            time.sleep(0.05)
            return dict(SAMPLE_API_DATA, _fetched_at=time.time())

        monkeypatch.setattr(reg, "_fetch_api", slow_fetch)

        results: list = []
        errors: list = []

        def worker() -> None:
            try:
                reg._ensure_loaded()
                results.append(reg._models_cache)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 8
        assert fetch_calls["n"] == 1, "single-flight: exactly one fetch across all callers"
        assert all(r is results[0] for r in results)
