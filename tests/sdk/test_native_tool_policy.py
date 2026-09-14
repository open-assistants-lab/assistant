from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import src.config.settings as settings_module
from src.config.settings import AppConfig
from src.sdk.deployment_tools import (
    filter_denied_native_tools,
    is_shipped_native_definition,
    native_tool_is_allowed,
)
from src.sdk.tools import tool
from src.sdk.tools_core.tool_reload import tool_reload
from src.sdk.tools_core.tool_search import tool_search


def _settings(mode: str, enabled: list[str] | None = None):
    return SimpleNamespace(tools=SimpleNamespace(native=SimpleNamespace(mode=mode, enabled=enabled or [])))


def test_native_policy_defaults_to_all():
    assert native_tool_is_allowed("files_read", _settings("all"))


def test_native_policy_none_excludes_every_native_tool_and_meta_tools():
    settings = _settings("none")
    assert not native_tool_is_allowed("files_read", settings)
    assert not native_tool_is_allowed("tool_search", settings)
    assert not native_tool_is_allowed("tool_reload", settings)


def test_native_policy_selected_supports_exact_and_glob_patterns():
    settings = _settings("selected", ["files_*", "time_get"])
    assert native_tool_is_allowed("files_read", settings)
    assert native_tool_is_allowed("time_get", settings)
    assert not native_tool_is_allowed("browser_open", settings)
    assert not native_tool_is_allowed("tool_search", settings)


def test_native_policy_selected_can_explicitly_include_meta_tools():
    settings = _settings("selected", ["tool_*"])
    assert native_tool_is_allowed("tool_search", settings)
    assert native_tool_is_allowed("tool_reload", settings)


@pytest.mark.parametrize(
    "yaml_content",
    ["tools:\n  native:\n    mode: all\n", None, ""],
    ids=["yaml-present", "yaml-missing", "yaml-empty"],
)
def test_native_policy_env_overrides_apply_for_every_from_yaml_path(
    tmp_path, monkeypatch, yaml_content
):
    config_path = tmp_path / "config.yaml"
    if yaml_content is not None:
        config_path.write_text(yaml_content, encoding="utf-8")
    monkeypatch.setenv("TOOLS_NATIVE__MODE", "selected")
    monkeypatch.setenv("TOOLS_NATIVE__ENABLED", '["time_get"]')

    config = AppConfig.from_yaml(config_path)

    assert config.tools.native.mode == "selected"
    assert config.tools.native.enabled == ["time_get"]


def test_native_policy_dotenv_overrides_yaml_and_process_environment_wins(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tools:\n  native:\n    mode: all\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        'TOOLS_NATIVE__MODE=selected\nTOOLS_NATIVE__ENABLED=["time_get"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("TOOLS_NATIVE__MODE", raising=False)
    monkeypatch.delenv("TOOLS_NATIVE__ENABLED", raising=False)

    dotenv_config = AppConfig.from_yaml(config_path)
    assert dotenv_config.tools.native.mode == "selected"
    assert dotenv_config.tools.native.enabled == ["time_get"]

    monkeypatch.setenv("TOOLS_NATIVE__MODE", "none")
    process_config = AppConfig.from_yaml(config_path)
    assert process_config.tools.native.mode == "none"
    assert process_config.tools.native.enabled == ["time_get"]


@pytest.mark.parametrize(
    ("mode", "enabled"),
    [
        ("unexpected", '["time_get"]'),
        ("selected", "not-json"),
        ("selected", '["time_get", 4]'),
    ],
)
def test_native_policy_invalid_env_values_fail_closed(tmp_path, monkeypatch, mode, enabled):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tools:\n  native:\n    mode: all\n", encoding="utf-8")
    monkeypatch.setenv("TOOLS_NATIVE__MODE", mode)
    monkeypatch.setenv("TOOLS_NATIVE__ENABLED", enabled)

    with pytest.raises(ValidationError):
        AppConfig.from_yaml(config_path)


def test_shipped_meta_provenance_is_identity_based_and_custom_collision_survives():
    @tool
    def custom_tool_search() -> str:
        return "custom"

    # Name collision alone does not classify a custom definition as shipped.
    custom_tool_search.name = "tool_search"
    settings = _settings("none")

    assert is_shipped_native_definition(tool_search)
    assert is_shipped_native_definition(tool_reload)
    assert not is_shipped_native_definition(custom_tool_search)
    assert filter_denied_native_tools([tool_search, custom_tool_search], settings) == [
        custom_tool_search
    ]
