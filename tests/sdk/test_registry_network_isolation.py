"""Issue #15 follow-up: the test process must never fetch models.dev.

The root tests/conftest.py installs a ``_fetch_api`` stub at the module
boundary (a cache-TTL knob cannot cover a cold cache, which still calls
_fetch_api). These tests assert the stub is installed by default and that a
cold-cache refresh performs no HTTP call — the registry must fall back to
its built-in subset.
"""

from __future__ import annotations


def test_default_registry_fetch_stub_installed():
    import src.sdk.registry as registry

    stub = registry._fetch_api
    assert getattr(stub, "__test_stub__", False) is True


def test_cold_refresh_makes_no_http_call(monkeypatch, tmp_path):
    import src.sdk.registry as registry

    def _forbidden_urlopen(*args, **kwargs):
        raise AssertionError("tests must not perform a models.dev HTTP fetch")

    monkeypatch.setattr(registry, "urlopen", _forbidden_urlopen)
    monkeypatch.setattr(
        registry, "_get_cache_path", lambda: tmp_path / "models.json"
    )

    saved = (
        registry._models_cache,
        registry._providers_cache,
        registry._last_fetch_time,
    )
    try:
        # Cold cache: nothing in memory, empty cache dir → the registry must
        # survive via its built-in subset WITHOUT touching the network. Any
        # fetch attempt raises through _forbidden_urlopen.
        registry._models_cache = None
        registry._providers_cache = None
        registry._last_fetch_time = 0.0
        registry._ensure_loaded(force=True)
        assert registry.list_models() is not None
    finally:
        (
            registry._models_cache,
            registry._providers_cache,
            registry._last_fetch_time,
        ) = saved
