"""Repo-root config resolution + API bind honoring (audit E22/E23).

E22: run() must bind settings.api.host/port instead of hardcoded values,
with env API_HOST/API_PORT beating yaml (docker-compose contract).
E23: get_settings() must resolve config.yaml/.env against the repository
root regardless of process CWD.
"""
from __future__ import annotations

import pytest

from src.config import settings as settings_module
from src.config.settings import reload_settings


@pytest.fixture
def fresh_settings():
    reload_settings()
    yield
    reload_settings()


def test_yaml_loaded_from_repo_root_regardless_of_cwd(tmp_path, monkeypatch, fresh_settings):
    """Launching from a foreign CWD must still find repo-root config.yaml."""
    monkeypatch.chdir(tmp_path)  # empty dir — relative lookup would miss
    cfg = reload_settings()
    assert "agent-browser" in cfg.shell_tool.allowed_commands


def test_env_api_port_beats_yaml(monkeypatch, fresh_settings):
    """Deployment (compose API_PORT=8000) must win over yaml api.port."""
    monkeypatch.setenv("API_PORT", "8123")
    cfg = reload_settings()
    assert cfg.api.port == 8123


def test_env_api_host_beats_yaml(monkeypatch, fresh_settings):
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    cfg = reload_settings()
    assert cfg.api.host == "127.0.0.1"


def test_run_binds_settings_host_port(monkeypatch, fresh_settings):
    from src.http import main as http_main

    captured: dict = {}

    def fake_uvicorn_run(app, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)
    http_main.run()

    cfg = settings_module.get_settings()
    assert captured["host"] == cfg.api.host
    assert captured["port"] == cfg.api.port


def test_oauth_fallback_uses_configured_port(monkeypatch):
    """The OAuth redirect base must follow the configured bind port (audit
    E22 fix-round: the old literal 'http://localhost:8080' went stale the
    moment API_PORT became authoritative under env-beats-yaml)."""
    import importlib

    import src.http.main as http_main
    from src.config.settings import reload_settings

    monkeypatch.setenv("API_PORT", "9466")
    monkeypatch.delenv("API_PUBLIC_URL", raising=False)
    reload_settings()
    try:
        importlib.reload(http_main)
        assert http_main._oauth_base_url == "http://localhost:9466"
    finally:
        reload_settings()
        importlib.reload(http_main)


def test_api_config_default_port_matches_native_contract():
    """With no config.yaml at all, the default port must be the native-app
    contract (8080), not the docker bind (8000)."""
    assert settings_module.ApiConfig().port == 8080
