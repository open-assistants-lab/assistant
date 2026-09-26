"""Unit tests for MCP module."""

from unittest.mock import MagicMock, patch


class TestMCPConfig:
    """Tests for MCP config loading."""

    def test_http_server_headers_parsed(self):
        """Remote HTTP servers carry auth headers (e.g. ClickStack MCP bearer)."""
        from src.sdk.tools_core.mcp_config import MCPConfig

        config = MCPConfig(
            **{
                "mcpServers": {
                    "clickstack": {
                        "url": "https://clickstack.example.com/api/mcp",
                        "type": "http",
                        "headers": {"Authorization": "Bearer tok-123"},
                    }
                }
            }
        )
        server = config.mcpServers["clickstack"]
        assert server.url == "https://clickstack.example.com/api/mcp"
        assert server.headers == {"Authorization": "Bearer tok-123"}
        # stdio servers default to no headers
        from src.sdk.tools_core.mcp_config import MCPServerConfig

        assert MCPServerConfig(command="python").headers == {}

    def test_load_config_missing_file(self, tmp_path):
        """Test loading config when file doesn't exist."""
        from src.sdk.tools_core.mcp_config import load_mcp_config
        from src.storage.paths import DataPaths

        with patch("src.sdk.tools_core.mcp_config.get_paths") as mock_get_paths:
            dp = DataPaths(data_root=str(tmp_path), user_id="test_user")
            mock_get_paths.return_value = dp
            result = load_mcp_config("test_user")
            assert result is None

    def test_load_config_invalid_json(self, tmp_path):
        """Test loading config with invalid JSON."""
        from src.sdk.tools_core.mcp_config import load_mcp_config
        from src.storage.paths import DataPaths

        with patch("src.sdk.tools_core.mcp_config.get_paths") as mock_get_paths:
            dp = DataPaths(data_root=str(tmp_path), user_id="test_user")
            config_path = dp.user_mcp_config()
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("invalid json{")
            mock_get_paths.return_value = dp
            result = load_mcp_config("test_user")
            assert result is None

    def test_config_state_distinguishes_invalid_and_records_provenance(self, tmp_path):
        from src.sdk.tools_core.mcp_config import load_mcp_config_state
        from src.storage.paths import DataPaths

        with patch("src.sdk.tools_core.mcp_config.get_paths") as mock_get_paths:
            dp = DataPaths(data_root=str(tmp_path), user_id="test_user")
            config_path = dp.user_mcp_config()
            config_path.parent.mkdir(parents=True, exist_ok=True)
            mock_get_paths.return_value = dp

            config_path.write_text("not json", encoding="utf-8")
            config, state, source = load_mcp_config_state("test_user")
            assert config is None
            assert state == "invalid"
            assert source == str(config_path)

            config_path.write_text(
                '{"mcpServers": {"demo": {"command": "python"}}}', encoding="utf-8"
            )
            config, state, source = load_mcp_config_state("test_user")
            assert state == "valid"
            assert config is not None
            assert config.source_path == str(config_path)
            assert config.mcpServers["demo"].source_type == "user"
            assert config.mcpServers["demo"].enabled is True

    def test_config_mtime_missing(self, tmp_path):
        """Test mtime when config doesn't exist."""
        from src.sdk.tools_core.mcp_config import get_config_mtime
        from src.storage.paths import DataPaths

        with patch("src.sdk.tools_core.mcp_config.get_paths") as mock_get_paths:
            dp = DataPaths(data_root=str(tmp_path), user_id="test_user")
            mock_get_paths.return_value = dp
            result = get_config_mtime("test_user")
            assert result == 0.0


class TestMCPTools:
    """Tests for MCP tools (async)."""

    async def test_mcp_proxy_requires_user_id(self):
        from src.sdk.tools import ToolResult
        from src.sdk.tools_core.mcp import mcp_proxy

        result = await mcp_proxy.ainvoke({"user_id": "", "action": "search"})
        assert isinstance(result, ToolResult)
        assert result.is_error is True
        assert "user_id is required" in result.content

    @patch("src.sdk.tools_core.mcp_manager.get_mcp_manager")
    async def test_mcp_proxy_status_with_no_cached_servers(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.cached_metadata = MagicMock(return_value=[])
        mock_get_manager.return_value = mock_manager

        from src.sdk.tools_core.mcp import mcp_proxy

        result = await mcp_proxy.ainvoke({"user_id": "test_user", "action": "status"})
        assert result.is_error is False
        assert "No cached MCP servers" in result.content

    @patch("src.sdk.tools_core.mcp_manager.get_mcp_manager")
    async def test_mcp_proxy_status_lists_cached_servers(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.cached_metadata = MagicMock(
            return_value=[
                {
                    "server_name": "math",
                    "source_type": "user",
                    "tools": [{"name": "add"}, {"name": "sub"}],
                }
            ]
        )
        mock_get_manager.return_value = mock_manager

        from src.sdk.tools_core.mcp import mcp_proxy

        result = await mcp_proxy.ainvoke({"user_id": "test_user", "action": "status"})
        assert result.is_error is False
        assert "math" in result.content
        assert "2 cached tools" in result.content

    @patch("src.sdk.tools_core.mcp_manager.get_mcp_manager")
    async def test_mcp_proxy_describe_lists_tools_without_connecting(self, mock_get_manager):
        mock_manager = MagicMock()
        mock_manager.cached_metadata = MagicMock(
            return_value=[
                {
                    "server_name": "math",
                    "tools": [{"name": "add", "description": "Add numbers"}],
                }
            ]
        )
        mock_get_manager.return_value = mock_manager

        from src.sdk.tools_core.mcp import mcp_proxy

        result = await mcp_proxy.ainvoke(
            {
                "user_id": "test_user",
                "action": "describe",
                "server": "math",
                "tool": "add",
            }
        )
        assert result.is_error is False
        assert "add" in result.content
        mock_manager.initialize.assert_not_called()


class TestMCPManager:
    """Tests for MCP manager."""

    def test_compute_config_hash(self):
        """Test config hash computation."""
        from src.sdk.tools_core.mcp_config import MCPConfig, MCPServerConfig

        config = MCPConfig(mcpServers={"math": MCPServerConfig(command="python", args=["test.py"])})

        from src.sdk.tools_core.mcp_manager import MCPManager

        manager = MCPManager("test_user")
        hash1 = manager._compute_config_hash(config)
        hash2 = manager._compute_config_hash(config)

        assert hash1 == hash2
        assert len(hash1) == 32

    def test_config_changed_no_config(self, tmp_path):
        """Test config changed when no config exists."""

        with (
            patch("src.sdk.tools_core.mcp_manager.load_mcp_config") as mock_load,
            patch("src.sdk.tools_core.mcp_manager.get_config_mtime", return_value=0.0),
        ):
            mock_load.return_value = None

            from src.sdk.tools_core.mcp_manager import MCPManager

            manager = MCPManager("test_user")
            assert not manager._config_changed()
