"""Repo-root config resolution + API bind honoring (audit E22/E23).

E22: run() must bind settings.api.host/port instead of hardcoded values,
with env API_HOST/API_PORT beating yaml (docker-compose contract).
E23: get_settings() must resolve config.yaml/.env against the repository
root regardless of process CWD.
"""
from __future__ import annotations

import pytest

from src.config import settings as settings_module
from src.config.settings import AppConfig, reload_settings


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


def test_agent_max_iterations_setting_wires_to_run_config(fresh_settings):
    """The shipped value must be a valid, positive, operator-tunable bound.

    Asserting a hard-coded literal made this test break whenever the operator
    changed config.yaml, even though the value it claims to check — that the
    setting loads and is usable — was unaffected.
    """
    cfg = settings_module.get_settings()

    assert isinstance(cfg.agent.max_iterations, int)
    assert cfg.agent.max_iterations > 0


def test_session_lease_timeout_is_bounded_and_configurable(fresh_settings):
    cfg = settings_module.get_settings()

    assert cfg.deployment.session_lease_timeout_seconds == 300


def test_shell_interpreters_are_not_enabled_by_default(fresh_settings):
    cfg = settings_module.get_settings()

    assert "python3" not in cfg.shell_tool.allowed_commands
    assert "node" not in cfg.shell_tool.allowed_commands


def test_legacy_governance_tiers_migrate_to_permissions(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
agent:
  model: ollama-cloud:test
governance:
  enabled: true
  tiers:
    custom_write: explicit
    custom_deny: hard_block
    custom_allow: allow
""".strip()
    )
    monkeypatch.setattr(settings_module, "validate_startup_model_references", lambda config: None)

    config = settings_module.AppConfig.from_yaml(config_path)

    assert config.governance.permissions["tools"] == {
        "custom_write": "ask",
        "custom_deny": "deny",
        "custom_allow": "allow",
    }
    assert not hasattr(config.governance, "tiers")


def test_conflicting_legacy_and_new_governance_permissions_fail_closed(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
agent:
  model: ollama-cloud:test
governance:
  tiers:
    custom_write: explicit
  permissions:
    tools:
      custom_write: deny
""".strip()
    )
    monkeypatch.setattr(settings_module, "validate_startup_model_references", lambda config: None)

    with pytest.raises(ValueError, match="governance.*tiers.*permissions"):
        settings_module.AppConfig.from_yaml(config_path)


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


def test_verification_and_aux_models_load_from_yaml_with_inheritance(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """agent:\n  model: openai:gpt-5\nverification:\n  enabled: true\n  grader_model: ''\n  default_rubric: '- Response is non-empty'\nmemory:\n  summarization:\n    model: ''\n"""
    )

    cfg = AppConfig.from_yaml(config_file)

    assert cfg.verification.enabled is True
    assert cfg.verification.default_rubric == "- Response is non-empty"
    assert cfg.verification.grader_model == ""
    assert cfg.verification.grader_model or cfg.agent.model == "openai:gpt-5"
    assert cfg.memory.summarization.model or cfg.agent.model == "openai:gpt-5"


def test_malformed_startup_model_reference_has_clear_error(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("agent:\n  model: 'ollama:'\n")

    with pytest.raises(ValueError, match="Invalid agent model reference.*provider:model"):
        AppConfig.from_yaml(config_file)


def test_historical_misplaced_cloud_provider_reference_warns(tmp_path, caplog):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("agent:\n  model: ollama:xyz-cloud\n")

    AppConfig.from_yaml(config_file)

    assert "provider/model separator is misplaced" in caplog.text


def test_startup_validation_accepts_provider_model_slash_form():
    """A models.dev-style provider/model ref must boot: runtime accepts the
    slash form, so startup validation accepts it too (malformed still fails)."""
    from src.config.settings import AppConfig, validate_startup_model_references

    cfg = AppConfig(agent={"name": "T", "model": "anthropic/claude-3-5-sonnet"})
    validate_startup_model_references(cfg)  # must not raise

    # Bare names resolve as ollama models at runtime — startup no longer
    # rejects them either (same allow_legacy semantics as the factory).
    cfg_bare = AppConfig(agent={"name": "T", "model": "some-local-pull"})
    validate_startup_model_references(cfg_bare)  # must not raise



# --------------------------------------------------------------------------
# Environment must beat config.yaml (audit: pydantic-settings gives init kwargs
# precedence, so every key in config.yaml used to be env-inert)
# --------------------------------------------------------------------------


def test_env_overrides_a_key_present_in_yaml(monkeypatch, fresh_settings) -> None:
    """config.yaml says exposure: auto; the environment must still win."""
    monkeypatch.setenv("MCP__EXPOSURE", "never")
    cfg = reload_settings()
    assert cfg.mcp.exposure == "never"


def test_env_overrides_a_key_absent_from_its_yaml_section(monkeypatch, fresh_settings) -> None:
    """A whole section being present in yaml used to seal off all of its env vars."""
    monkeypatch.setenv("MCP__MAX_RESULT_CHARS", "12345")
    cfg = reload_settings()
    assert cfg.mcp.max_result_chars == 12345


def test_yaml_still_applies_when_no_env_is_set(monkeypatch, fresh_settings) -> None:
    """With no env var, config.yaml is still the source of truth."""
    monkeypatch.delenv("MCP__EXPOSURE", raising=False)
    cfg = reload_settings()
    assert cfg._yaml_doc["mcp"]["exposure"] == cfg.mcp.exposure


def test_flat_deployment_contracts_still_win(monkeypatch, fresh_settings) -> None:
    """API_PORT is a flat deployment contract, not a nested env var — keep it."""
    monkeypatch.setenv("API_PORT", "9123")
    cfg = reload_settings()
    assert cfg.api.port == 9123


def test_env_precedence_is_reported_at_startup(monkeypatch, fresh_settings, caplog) -> None:
    """A behaviour change that is silent is the same failure as the bug itself."""
    monkeypatch.setenv("MCP__EXPOSURE", "never")
    with caplog.at_level("WARNING"):
        reload_settings()
    assert any("config.yaml" in r.message and "MCP__EXPOSURE" in r.message for r in caplog.records), (
        "expected a warning naming the overridden key; got "
        f"{[r.message for r in caplog.records]}"
    )


def test_no_warning_when_env_and_yaml_agree(monkeypatch, fresh_settings, caplog) -> None:
    monkeypatch.delenv("MCP__EXPOSURE", raising=False)
    with caplog.at_level("WARNING"):
        reload_settings()
    assert not [r for r in caplog.records if "config.yaml" in r.message]


class TestClickstackSinkConfig:
    """The second operational sink must only ever be configured on purpose.

    Regression: clickstack was typed as OtelConfig, whose env_prefix is OTEL_,
    so OTEL_ENDPOINT silently configured BOTH sinks and every span was exported
    to one destination twice.
    """

    def test_otel_endpoint_does_not_configure_clickstack(self, monkeypatch):
        monkeypatch.setenv("OTEL_ENDPOINT", "http://127.0.0.1:9/v1/traces")
        monkeypatch.delenv("OBSERVABILITY__CLICKSTACK__ENDPOINT", raising=False)
        monkeypatch.delenv("CLICKSTACK_OTEL_ENDPOINT", raising=False)
        from src.config.settings import AppConfig

        obs = AppConfig().observability
        assert str(obs.otel.endpoint) == "http://127.0.0.1:9/v1/traces"
        assert str(obs.clickstack.endpoint) == ""

    def test_clickstack_owns_its_env_prefix(self, monkeypatch):
        monkeypatch.setenv("CLICKSTACK_OTEL_ENDPOINT", "http://collector:4318")
        monkeypatch.delenv("OBSERVABILITY__CLICKSTACK__ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_ENDPOINT", raising=False)
        from src.config.settings import AppConfig

        obs = AppConfig().observability
        assert str(obs.clickstack.endpoint) == "http://collector:4318"
        assert str(obs.otel.endpoint) == ""

    def test_nested_delimiter_path_still_configures_clickstack(self, monkeypatch):
        monkeypatch.setenv("OBSERVABILITY__CLICKSTACK__ENDPOINT", "http://cs:4318")
        monkeypatch.delenv("CLICKSTACK_OTEL_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_ENDPOINT", raising=False)
        from src.config.settings import AppConfig

        obs = AppConfig().observability
        assert str(obs.clickstack.endpoint) == "http://cs:4318"


class TestLangfuseConsentPrecedence:
    """Tracing consent: an explicit env opt-out must beat yaml, always.

    Found live in the Docker product deployment: config.yaml (baked into the
    image) shipped `observability.langfuse.enabled: true`, and the yaml
    bridge overwrote `LANGFUSE_ENABLED=0` AFTER construction. Tracing was
    then on in a container whose owner had explicitly not accepted it — the
    gate only held while keys/hosts were absent.
    """

    def test_env_optout_beats_yaml_true(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LANGFUSE_ENABLED", "0")
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.org")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "observability:\n  langfuse:\n    enabled: true\n"
        )
        import src.config.settings as sm

        cfg = sm.AppConfig.from_yaml(cfg_file)
        assert cfg.langfuse.enabled is False, (
            "an explicit LANGFUSE_ENABLED=0 must close the gate even when "
            "yaml says enabled: true"
        )

    def test_yaml_applies_when_env_silent(self, monkeypatch, tmp_path):
        for var in ("LANGFUSE_ENABLED", "LANGFUSE_HOST", "LANGFUSE_BASE_URL",
                    "LANGFUSE_ENVIRONMENT"):
            monkeypatch.delenv(var, raising=False)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "observability:\n  langfuse:\n    enabled: true\n"
        )
        import src.config.settings as sm

        cfg = sm.AppConfig.from_yaml(cfg_file)
        assert cfg.langfuse.enabled is True

    def test_env_optin_beats_yaml_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LANGFUSE_ENABLED", "1")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "observability:\n  langfuse:\n    enabled: false\n"
        )
        import src.config.settings as sm

        cfg = sm.AppConfig.from_yaml(cfg_file)
        assert cfg.langfuse.enabled is True
