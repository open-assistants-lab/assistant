"""MCP configuration models."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.storage.paths import get_paths


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    command: str | None = Field(
        default=None, description="Command to run (e.g., 'uvx', 'python', '/path/to/server')"
    )
    args: list[str] = Field(default_factory=list, description="Arguments to pass to command")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    url: str | None = Field(default=None, description="URL for HTTP transport servers")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP headers for remote servers (e.g. Authorization: Bearer ...)",
    )
    transport: str = Field(default="stdio", description="Transport type: 'stdio' or 'http'")
    source_path: str = Field(default="", exclude=True)
    source_type: Literal["user", "project", "runtime"] = Field(default="user", exclude=True)
    disabled: bool = Field(default=False, exclude=True)


class MCPConfig(BaseModel):
    """MCP configuration for a user."""

    model_config = {"extra": "ignore", "populate_by_name": True}

    mcpServers: dict[str, MCPServerConfig] = Field(default_factory=dict)  # noqa: N815
    source_path: str = Field(default="", exclude=True)
    source_type: Literal["user", "project", "runtime"] = Field(default="user", exclude=True)


def load_mcp_config_state(user_id: str) -> tuple[MCPConfig | None, str, str]:
    """Return (config, state, source_path) distinguishing missing from invalid."""
    config_path = get_paths(user_id).user_mcp_config()
    if not config_path.exists():
        return None, "missing", str(config_path)

    import json

    try:
        data = json.loads(config_path.read_text())
        config = MCPConfig(**data)
    except Exception:
        return None, "invalid", str(config_path)
    config.source_path = str(config_path)
    config.source_type = "user"
    for server in config.mcpServers.values():
        server.source_path = str(config_path)
        server.source_type = "user"
    return config, "valid", str(config_path)


def load_mcp_config(user_id: str) -> MCPConfig | None:
    """Load MCP configuration from user's .mcp.json file."""
    config, _state, _source_path = load_mcp_config_state(user_id)
    return config


def get_config_path(user_id: str) -> Path:
    """Get path to user's MCP config file."""
    return get_paths(user_id).user_mcp_config()


def get_config_mtime(user_id: str) -> float:
    """Get modification time of config file."""
    config_path = get_config_path(user_id)
    if config_path.exists():
        return config_path.stat().st_mtime
    return 0.0
