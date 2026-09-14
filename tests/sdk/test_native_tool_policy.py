from types import SimpleNamespace

from src.sdk.deployment_tools import native_tool_is_allowed


def _settings(mode: str, enabled: list[str] | None = None):
    return SimpleNamespace(tools=SimpleNamespace(native=SimpleNamespace(mode=mode, enabled=enabled or [])))


def test_native_policy_defaults_to_all():
    assert native_tool_is_allowed("files_read", _settings("all"))


def test_native_policy_none_excludes_every_native_tool():
    assert not native_tool_is_allowed("files_read", _settings("none"))


def test_native_policy_selected_supports_exact_and_glob_patterns():
    settings = _settings("selected", ["files_*", "time_get"])
    assert native_tool_is_allowed("files_read", settings)
    assert native_tool_is_allowed("time_get", settings)
    assert not native_tool_is_allowed("browser_open", settings)
