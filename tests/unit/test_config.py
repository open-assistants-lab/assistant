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
