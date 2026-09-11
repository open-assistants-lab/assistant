"""MCP configuration models."""

from pathlib import Path

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


class MCPConfig(BaseModel):
    """MCP configuration for a user."""

    model_config = {"extra": "ignore", "populate_by_name": True}

    mcpServers: dict[str, MCPServerConfig] = Field(default_factory=dict)  # noqa: N815


def load_mcp_config(user_id: str) -> MCPConfig | None:
    """Load MCP configuration from user's .mcp.json file."""
    config_path = get_paths(user_id).user_mcp_config()
    if not config_path.exists():
        return None

    import json

    try:
        data = json.loads(config_path.read_text())
        return MCPConfig(**data)
    except Exception:
        return None


def get_config_path(user_id: str) -> Path:
    """Get path to user's MCP config file."""
    return get_paths(user_id).user_mcp_config()


def get_config_mtime(user_id: str) -> float:
    """Get modification time of config file."""
    config_path = get_config_path(user_id)
    if config_path.exists():
        return config_path.stat().st_mtime
    return 0.0
