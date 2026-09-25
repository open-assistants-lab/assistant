"""Minimal real stdio MCP server used by lifecycle tests."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("assistant-test-fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Return the supplied text."""
    return text


if __name__ == "__main__":
    mcp.run()
