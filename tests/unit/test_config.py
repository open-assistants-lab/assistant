"""Tests for config module."""

import os

import pytest


class TestConfigValidation:
    """Test configuration validation."""

    def test_agent_config_valid(self):
        """Test valid agent configuration."""
        os.environ["OLLAMA_API_KEY"] = "test-key"
        os.environ["OLLAMA_BASE_URL"] = "https://api.ollama.cloud/v1"

        from src.config.settings import AgentConfig

        config = AgentConfig(name="Test Agent", model="ollama:test-model")
        assert config.name == "Test Agent"
        assert config.model == "ollama:test-model"

    def test_agent_config_defaults(self):
        """Test agent config has defaults — shipped default is NO model (D0-5)."""
        from src.config.settings import AgentConfig

        # D0-5: no provider baked in. The dotenv loader may have set
        # AGENT_MODEL from the developer's untracked .env — default is empty
        # only with no env override; bare construction must fail fast later.
        saved_model = os.environ.pop("AGENT_MODEL", None)
        try:
            config = AgentConfig(_env_file=None)
            assert config.name == "Assistant"
            assert config.model == ""
        finally:
            if saved_model is not None:
                os.environ["AGENT_MODEL"] = saved_model


class TestObservabilityValidation:
    """OB-0 Task 1: fail-closed Langfuse host + explicit OtelConfig settings."""

    @staticmethod
    def _clean_langfuse_env(monkeypatch):
        for name in (
            "LANGFUSE_ENABLED",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "LANGFUSE_BASE_URL",
            "LANGFUSE_HOST",
        ):
            monkeypatch.delenv(name, raising=False)

    def test_disabled_langfuse_without_host_is_valid(self, monkeypatch):
        """Disabled Langfuse needs no host — validation must not raise."""
        self._clean_langfuse_env(monkeypatch)
        from src.config.settings import AppConfig, validate_observability_settings

        validate_observability_settings(AppConfig())

    def test_enabled_with_keys_and_no_host_fails_closed(self, monkeypatch):
        """Enabled + credentials + no explicit host -> startup error, never
        silently defaulting to cloud.langfuse.com."""
        self._clean_langfuse_env(monkeypatch)
        from src.config.settings import (
            AppConfig,
            LangfuseConfig,
            validate_observability_settings,
        )

        config = AppConfig(
            langfuse=LangfuseConfig(enabled=True, public_key="pk", secret_key="sk")
        )
        with pytest.raises(ValueError, match="LANGFUSE_BASE_URL"):
            validate_observability_settings(config)

    def test_enabled_with_keys_and_base_url_env_succeeds(self, monkeypatch):
        """LANGFUSE_BASE_URL (the name users copy off the Langfuse setup
        page) counts as an explicit host."""
        self._clean_langfuse_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example.com")
        from src.config.settings import (
            AppConfig,
            LangfuseConfig,
            validate_observability_settings,
        )

        config = AppConfig(
            langfuse=LangfuseConfig(enabled=True, public_key="pk", secret_key="sk")
        )
        validate_observability_settings(config)

    def test_enabled_with_keys_and_legacy_host_env_succeeds(self, monkeypatch):
        """LANGFUSE_HOST remains a valid explicit host; so does an explicitly
        configured non-default host on the settings object (yaml path)."""
        self._clean_langfuse_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.example.com")
        from src.config.settings import (
            AppConfig,
            LangfuseConfig,
            validate_observability_settings,
        )

        config = AppConfig(
            langfuse=LangfuseConfig(enabled=True, public_key="pk", secret_key="sk")
        )
        validate_observability_settings(config)

        # yaml-sync path: explicit non-default host on the config object.
        config_yaml_host = AppConfig(
            langfuse=LangfuseConfig(
                enabled=True,
                public_key="pk",
                secret_key="sk",
                host="https://langfuse.example.com",
            )
        )
        validate_observability_settings(config_yaml_host)

    def test_otel_config_defaults_to_no_export(self, monkeypatch):
        """Empty OTLP endpoint default = no export configuration (OB-0)."""
        self._clean_langfuse_env(monkeypatch)
        monkeypatch.delenv("OTEL_ENDPOINT", raising=False)
        from src.config.settings import ObservabilityConfig

        obs = ObservabilityConfig()
        assert obs.otel.endpoint == ""
        assert obs.otel.headers == {}

        monkeypatch.setenv("OTEL_ENDPOINT", "https://clickstack.example.com/v1/traces")
        assert ObservabilityConfig().otel.endpoint == "https://clickstack.example.com/v1/traces"

    def test_get_settings_fails_closed_on_oversized_langfuse_default(
        self, monkeypatch
    ):
        """Startup hook: enabled+credentials with no explicit host must fail
        get_settings(), not boot toward cloud.langfuse.com."""
        self._clean_langfuse_env(monkeypatch)
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
        import src.config.settings as settings_module

        settings_module._config = None
        try:
            with pytest.raises(ValueError, match="LANGFUSE_BASE_URL"):
                settings_module.get_settings()
        finally:
            settings_module._config = None


def test_session_log_env_override(monkeypatch):
    """D2 re-review residual: SESSION_LOG_ENABLED is the documented form.

    SessionLogConfig was the only nested config without an env_prefix, so the
    documented `SESSION_LOG_ENABLED=true` did not work (only the nested
    `SESSION_LOG__ENABLED` form did).
    """
    import src.config.settings as settings_module

    monkeypatch.setenv("SESSION_LOG_ENABLED", "true")
    settings_module._config = None
    try:
        assert settings_module.get_settings().session_log.enabled is True
    finally:
        settings_module._config = None
        monkeypatch.delenv("SESSION_LOG_ENABLED", raising=False)
