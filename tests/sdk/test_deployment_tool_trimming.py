"""Deployment-wide native tool trimming (#16)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.settings import AppConfig, ToolsConfig
from src.sdk.tools import ToolDefinition


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=name, parameters={})


def test_tools_disabled_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("TOOLS_DISABLED", raising=False)
    assert ToolsConfig(_env_file=None).disabled == []


def test_tools_disabled_accepts_yaml_style_list_and_env_json(monkeypatch):
    assert AppConfig(tools={"disabled": ["app_*", "browser_open"]}).tools.disabled == [
        "app_*",
        "browser_open",
    ]
    monkeypatch.setenv("TOOLS_DISABLED", '["files_*", "shell_execute"]')
    assert ToolsConfig(_env_file=None).disabled == ["files_*", "shell_execute"]


def test_from_yaml_tools_disabled_env_overrides_yaml_and_unset_preserves_yaml(
    tmp_path, monkeypatch
):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("tools:\n  disabled: [app_*]\n", encoding="utf-8")

    monkeypatch.setenv("TOOLS_DISABLED", '["files_*", "shell_execute"]')
    assert AppConfig.from_yaml(config_file).tools.disabled == ["files_*", "shell_execute"]

    monkeypatch.delenv("TOOLS_DISABLED")
    assert AppConfig.from_yaml(config_file).tools.disabled == ["app_*"]


def test_native_filter_supports_exact_and_case_sensitive_globs():
    from src.sdk.runner import filter_deployment_disabled_native_tools

    tools = [_tool("app_list"), _tool("browser_open"), _tool("Browser_open"), _tool("time_get")]
    retained = filter_deployment_disabled_native_tools(tools, ["app_*", "browser_open"])
    assert [tool.name for tool in retained] == ["Browser_open", "time_get"]


def test_native_filter_empty_patterns_retains_all_tools():
    from src.sdk.runner import filter_deployment_disabled_native_tools

    tools = [_tool("app_list"), _tool("time_get")]
    assert filter_deployment_disabled_native_tools(tools, []) == tools


def test_current_catalog_filters_only_native_tools(monkeypatch):
    import src.sdk.runner as runner

    native = _tool("files_read")
    custom = _tool("files_custom")
    mcp = _tool("mcp__crm__files_read")
    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(tools=SimpleNamespace(disabled=["files_*"])),
    )
    monkeypatch.setattr(runner, "get_native_tools", lambda: [native])
    monkeypatch.setattr("src.sdk.tools_custom.get_custom_tools", lambda *args: [custom])

    loop = SimpleNamespace(
        user_id="u",
        workspace_id="personal",
        _mcp_bridge=SimpleNamespace(get_tool_definitions=lambda: [mcp]),
    )
    names = [tool.name for tool in runner._current_tool_catalog(loop)]
    assert "files_read" not in names
    assert "files_custom" in names
    assert "mcp__crm__files_read" in names


@pytest.mark.asyncio
async def test_deployment_disable_is_a_hard_ceiling_before_capabilities(monkeypatch):
    """An enabled user capability cannot restore a deployment-disabled native tool."""
    import src.sdk.runner as runner

    native = _tool("files_read")
    settings = MagicMock()
    settings.tools.disabled = ["files_*"]
    settings.memory.summarization.enabled = False
    settings.verification.enabled = False
    settings.langfuse.enabled = False
    settings.agent.model = ""
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(runner, "get_native_tools", lambda: [native])
    monkeypatch.setattr(runner, "_load_user_capabilities", lambda user_id: {"tools": {"files_read": True}})
    monkeypatch.setattr(runner, "_resource_enabled", lambda *args: True)
    monkeypatch.setattr(runner, "_seed_default_workspace", lambda: None)
    monkeypatch.setattr(runner, "_get_system_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        "src.config.user_settings_service.load_saved_user_settings", lambda user_id: None
    )
    monkeypatch.setattr(
        "src.sdk.tool_index.get_or_create_index", lambda *args, **kwargs: (MagicMock(count=lambda: 0), lambda: None)
    )
    provider = MagicMock(provider_id="test", model="test")
    monkeypatch.setattr(runner, "get_cached_model_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr("src.sdk.tools_custom.get_custom_tools", lambda *args: [])

    loop = await runner.create_sdk_loop(user_id="test-user")
    assert "files_read" not in loop._registry.list_names()


def test_refresh_unregisters_deployment_disabled_native_tool_but_keeps_custom_and_mcp(
    monkeypatch,
):
    """Refresh applies the deployment ceiling to an already-live loop only."""
    import src.sdk.runner as runner
    from src.sdk.loop import AgentLoop

    native = _tool("files_read")
    custom = _tool("files_custom")
    mcp = _tool("mcp__crm__files_read")
    loop = AgentLoop(
        provider=SimpleNamespace(provider_id="test", model="test"),
        tools=[native, custom, mcp],
    )
    loop.user_id = "u"
    loop.workspace_id = "personal"
    loop._mcp_bridge = SimpleNamespace(get_tool_definitions=lambda: [mcp])

    monkeypatch.setattr(
        runner,
        "get_settings",
        lambda: SimpleNamespace(tools=SimpleNamespace(disabled=["files_*"])),
    )
    monkeypatch.setattr(runner, "get_native_tools", lambda: [native])
    monkeypatch.setattr("src.sdk.tools_custom.get_custom_tools", lambda *args: [custom])
    monkeypatch.setattr(runner, "_load_user_capabilities", lambda user_id: {})
    monkeypatch.setattr(runner, "_resource_enabled", lambda *args: True)
    monkeypatch.setattr(runner, "_live_user_loops", lambda user_id: [loop])

    assert runner.refresh_user_tool_registries("u") == 1
    assert "files_read" not in loop._registry.list_names()
    assert "files_custom" in loop._registry.list_names()
    assert "mcp__crm__files_read" in loop._registry.list_names()
