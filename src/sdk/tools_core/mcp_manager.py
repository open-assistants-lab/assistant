"""MCP Manager - Stateful MCP server management with lazy start and idle timeout.

Uses the native `mcp` Python SDK.
Supports both stdio and streamable HTTP transports.
"""

import asyncio
import hashlib
import os
import time
from contextlib import AsyncExitStack
from typing import Any

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.tools_core.mcp_config import (
    MCPServerConfig,
    get_config_mtime,
    load_mcp_config,
)

logger = get_logger()

_MCP_MANAGERS: dict[str, "MCPManager"] = {}


class MCPServerConnection:
    """Holds a connected MCP server with its session and tools."""

    def __init__(self, server_name: str, session: Any, exit_stack: AsyncExitStack) -> None:
        self.server_name = server_name
        self.session = session
        self.exit_stack = exit_stack
        self.tools: list[Any] = []

    async def aclose(self) -> None:
        await self.exit_stack.aclose()


class MCPManager:
    """Manages MCP servers for a user with lazy start and idle timeout."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self._connections: dict[str, MCPServerConnection] = {}
        self._config_mtime: float = 0.0
        self._config_hash: str = ""
        self._lock = asyncio.Lock()
        self._last_used: float = time.time()
        self._idle_task: asyncio.Task[Any] | None = None

    def _get_idle_timeout(self) -> int:
        try:
            settings = get_settings()
            return settings.mcp.idle_timeout_minutes * 60
        except Exception:
            return 30 * 60

    def _is_enabled(self) -> bool:
        try:
            settings = get_settings()
            return settings.mcp.enabled
        except Exception:
            return True

    async def _start_idle_monitor(self) -> None:
        if self._idle_task is not None:
            return

        async def _monitor() -> None:
            while True:
                await asyncio.sleep(60)
                if not self._connections:
                    continue

                idle_time = time.time() - self._last_used
                timeout = self._get_idle_timeout()

                if idle_time > timeout:
                    logger.info(
                        "mcp.idle_timeout",
                        {
                            "user_id": self.user_id,
                            "idle_seconds": int(idle_time),
                            "timeout": timeout,
                        },
                    )
                    await self._stop_all()

        self._idle_task = asyncio.create_task(_monitor())

    async def _ensure_started(self) -> None:
        if not self._is_enabled():
            return

        if self._connections:
            return

        config = load_mcp_config(self.user_id)
        if not config:
            logger.info("mcp.no_config", {"user_id": self.user_id})
            return

        self._config_mtime = get_config_mtime(self.user_id)
        self._config_hash = self._compute_config_hash(config)

        for server_name, server_config in config.mcpServers.items():
            await self._start_server(server_name, server_config)

        await self._start_idle_monitor()

    def _compute_config_hash(self, config: Any) -> str:
        data = config.model_dump_json()
        return hashlib.md5(data.encode()).hexdigest()

    async def _start_server(self, server_name: str, server_config: MCPServerConfig) -> None:
        try:
            logger.info(
                "mcp.starting_server", {"server": server_name, "command": server_config.command}
            )
            conn = await self._create_connection(server_name, server_config)
            self._connections[server_name] = conn

            result = await conn.session.list_tools()
            conn.tools = result.tools

            logger.info("mcp.server_started", {"server": server_name, "tools": len(conn.tools)})
        except Exception as e:
            logger.error(
                "mcp.server_error",
                {"server": server_name, "error": str(e), "error_type": type(e).__name__},
            )

    async def _create_connection(
        self, server_name: str, server_config: MCPServerConfig
    ) -> MCPServerConnection:
        """Create MCP client connection using the native mcp SDK."""
        from mcp import ClientSession

        exit_stack = AsyncExitStack()

        if server_config.transport == "http" or server_config.url:
            from mcp.client.streamable_http import streamablehttp_client

            url = server_config.url or "http://localhost:8000/mcp"

            read_stream, write_stream, _ = await exit_stack.enter_async_context(
                streamablehttp_client(url)
            )
            session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        else:
            from mcp.client.stdio import stdio_client

            env = dict(os.environ)
            env.update(server_config.env)

            from mcp import StdioServerParameters

            params = StdioServerParameters(
                command=server_config.command or "",
                args=server_config.args,
                env=env if env != dict(os.environ) else None,
            )

            read_stream, write_stream = await exit_stack.enter_async_context(stdio_client(params))
            session = await exit_stack.enter_async_context(ClientSession(read_stream, write_stream))

        await session.initialize()

        return MCPServerConnection(server_name, session, exit_stack)

    def _config_changed(self) -> bool:
        current_mtime = get_config_mtime(self.user_id)
        if current_mtime == self._config_mtime:
            return False

        config = load_mcp_config(self.user_id)
        if config and self._compute_config_hash(config) != self._config_hash:
            return True

        return False

    async def snapshot_connections(self) -> dict[str, MCPServerConnection]:
        """Return a copy of active connections taken under the lock."""
        async with self._lock:
            return dict(self._connections)

    async def get_connection(self, server_name: str) -> MCPServerConnection | None:
        """Return a single connection under the lock, or None."""
        async with self._lock:
            return self._connections.get(server_name)

    async def get_tools(self, server_name: str | None = None) -> list[Any]:
        """Get tools from MCP servers (lazy start on first call)."""
        if not self._is_enabled():
            return []

        async with self._lock:
            await self._ensure_started()

        # Release lock for config check + restart (prevents deadlock with _stop_all)
        if self._config_changed():
            await self._restart_all()

        self._last_used = time.time()

        async with self._lock:
            if not self._connections:
                return []

            if server_name:
                conn = self._connections.get(server_name)
                return conn.tools if conn else []

            all_tools = []
            for conn in self._connections.values():
                all_tools.extend(conn.tools)
            return all_tools

    async def list_servers(self) -> dict[str, Any]:
        """List configured MCP servers and their status."""
        config = load_mcp_config(self.user_id)
        servers = {}

        if config:
            for name, cfg in config.mcpServers.items():
                conn = self._connections.get(name)
                servers[name] = {
                    "command": cfg.command,
                    "args": cfg.args,
                    "transport": cfg.transport,
                    "running": conn is not None,
                    "tool_count": len(conn.tools) if conn else 0,
                }

        return servers

    async def reload(self) -> str:
        """Reload all MCP servers."""
        await self._restart_all()
        return "MCP servers reloaded"

    async def _restart_all(self) -> None:
        """Stop all servers and restart from config."""
        logger.info("mcp.reloading", {"user_id": self.user_id})
        await self._stop_all()

        config = load_mcp_config(self.user_id)
        if config:
            self._config_mtime = get_config_mtime(self.user_id)
            self._config_hash = self._compute_config_hash(config)

            for server_name, server_config in config.mcpServers.items():
                await self._start_server(server_name, server_config)

    async def _stop_all(self) -> None:
        async with self._lock:
            names = list(self._connections.keys())
        for name in names:
            await self._stop_server(name)
        async with self._lock:
            self._connections.clear()

    async def _stop_server(self, server_name: str) -> None:
        if server_name in self._connections:
            try:
                await self._connections[server_name].aclose()
            except Exception:
                pass
            del self._connections[server_name]

        logger.info("mcp.server_stopped", {"server": server_name})

    async def cleanup(self) -> None:
        """Clean up all MCP resources."""
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None
        await self._stop_all()

    async def initialize(self) -> None:
        """Public initialize method (compatibility with old API)."""
        await self._ensure_started()


def get_mcp_manager(user_id: str) -> MCPManager:
    """Get or create MCP manager for a user."""
    if user_id not in _MCP_MANAGERS:
        _MCP_MANAGERS[user_id] = MCPManager(user_id)
    return _MCP_MANAGERS[user_id]
