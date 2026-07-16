"""Tests for config module."""

import os


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
        """Test agent config has defaults."""
        from src.config.settings import AgentConfig

        config = AgentConfig()
        assert config.name == "Assistant"
        assert config.model == "ollama:minimax-m2.5"
