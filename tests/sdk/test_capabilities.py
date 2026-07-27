"""Tests for capabilities loading, merging, and defaults."""
import sqlite3
import tempfile
from pathlib import Path

import yaml

from src.sdk.capabilities import (
    _tool_default,
    load_capabilities,
    load_user_capabilities,
    merge_capabilities,
    resource_enabled,
    tool_enabled,
)


def make_caps(path: str, data: dict):
    """Helper: write capabilities.yaml to a temp path."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.dump(data))


def test_load_capabilities_returns_defaults_when_no_file():
    with tempfile.TemporaryDirectory() as d:
        caps = load_capabilities(d)
    assert caps == {"tools": {}, "skills": {}, "subagents": {}}


def test_load_capabilities_reads_yaml():
    with tempfile.TemporaryDirectory() as d:
        make_caps(f"{d}/capabilities.yaml", {
            "version": 1,
            "tools": {"files_read": True, "files_delete": False},
            "skills": {"file-management": True},
            "subagents": {},
        })
        caps = load_capabilities(d)
    assert caps["tools"]["files_read"] is True
    assert caps["tools"]["files_delete"] is False
    assert caps["skills"]["file-management"] is True


def test_merge_workspace_overrides_user():
    user = {"tools": {"files_read": True, "files_delete": False, "shell_execute": False}}
    workspace = {"tools": {"files_delete": True, "browser_open": True}}

    merged = merge_capabilities(user, workspace)
    assert merged["tools"]["files_read"] is True     # inherited
    assert merged["tools"]["files_delete"] is True   # overridden
    assert merged["tools"]["shell_execute"] is False  # inherited
    assert merged["tools"]["browser_open"] is True    # workspace-only


def test_merge_workspace_false_disables():
    user = {"tools": {"files_read": True}}
    workspace = {"tools": {"files_read": False}}
    merged = merge_capabilities(user, workspace)
    assert merged["tools"]["files_read"] is False


def test_merge_skills_and_subagents():
    user = {"tools": {}, "skills": {"agent-browser": True}, "subagents": {"researcher": True}}
    workspace = {"tools": {}, "skills": {"agent-browser": False}, "subagents": {}}
    merged = merge_capabilities(user, workspace)
    assert merged["skills"]["agent-browser"] is False
    assert merged["subagents"]["researcher"] is True


def test_tool_default_read_only():
    assert _tool_default({"read_only": True, "destructive": False}) is True


def test_tool_default_destructive():
    assert _tool_default({"read_only": False, "destructive": True}) is True


def test_tool_default_both_true_defaults_enabled():
    assert _tool_default({"read_only": True, "destructive": True}) is True


def test_tool_default_both_false():
    assert _tool_default({"read_only": False, "destructive": False}) is True


def test_tool_enabled_explicit():
    caps = {"tools": {"time_get": True, "files_delete": False}}
    assert tool_enabled(caps, "time_get", {"read_only": True, "destructive": False}) is True
    assert tool_enabled(caps, "files_delete", {"read_only": False, "destructive": True}) is False


def test_tool_enabled_missing_is_enabled_by_default():
    caps = {"tools": {}}
    assert tool_enabled(caps, "files_read", {"read_only": True, "destructive": False}) is True
    assert tool_enabled(caps, "shell_execute", {"read_only": False, "destructive": True}) is True


def test_tool_enabled_explicit_false_disables_destructive_tool():
    caps = {"tools": {"shell_execute": False}}
    assert tool_enabled(caps, "shell_execute", {"read_only": False, "destructive": True}) is False


def test_tool_enabled_invalid_configured_value_is_disabled():
    caps = {"tools": {"shell_execute": "selected", "files_delete": {"scope": "selected"}}}

    assert tool_enabled(caps, "shell_execute", {"read_only": False, "destructive": True}) is False
    assert tool_enabled(caps, "files_delete", {"read_only": False, "destructive": True}) is False


def test_resource_enabled_missing_defaults_enabled_but_invalid_configured_values_disable():
    caps = {"skills": {"good": True, "bad": "selected", "disabled": False}}

    assert resource_enabled(caps, "skills", "missing") is True
    assert resource_enabled(caps, "skills", "good") is True
    assert resource_enabled(caps, "skills", "disabled") is False
    assert resource_enabled(caps, "skills", "bad") is False


def test_load_user_capabilities_migrates_legacy_item_scopes_fail_closed(monkeypatch, tmp_path):
    db_path = tmp_path / "item_scopes.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE item_scopes ("
        "user_id TEXT, resource_type TEXT, resource_name TEXT, scope TEXT, workspace_ids TEXT)"
    )
    conn.executemany(
        "INSERT INTO item_scopes VALUES (?, ?, ?, ?, ?)",
        [
            ("alice", "tool", "safe_tool", "all", "[]"),
            ("alice", "tool", "selected_tool", "selected", '["ws"]'),
            ("alice", "skill", "disabled_skill", "none", "[]"),
            ("bob", "tool", "other_tool", "none", "[]"),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("src.sdk.capabilities.user_capabilities_root", lambda user_id: tmp_path)

    caps = load_user_capabilities("alice")
    caps_again = load_user_capabilities("alice")

    assert caps["tools"]["safe_tool"] is True
    assert caps["tools"]["selected_tool"] is False
    assert caps["skills"]["disabled_skill"] is False
    assert "other_tool" not in caps["tools"]
    assert caps_again == caps


def test_load_user_capabilities_does_not_mark_migrated_when_item_scopes_unreadable(
    monkeypatch, tmp_path
):
    root = tmp_path / "data" / "users" / "alice"
    root.mkdir(parents=True)
    (tmp_path / "data" / "item_scopes.db").write_text("not a sqlite database")

    class Settings:
        data_path = str(tmp_path / "data")

        deployment = type("Deployment", (), {"ea_root": str(tmp_path / "ea")})()

    monkeypatch.setattr("src.sdk.capabilities.get_settings", lambda: Settings())

    caps = load_user_capabilities("alice")

    assert caps == {"tools": {}, "skills": {}, "subagents": {}}
    assert not (root / ".legacy_capabilities_migrated").exists()


def test_load_user_capabilities_marks_migrated_when_item_scopes_table_missing(
    monkeypatch, tmp_path
):
    root = tmp_path / "data" / "users" / "alice"
    root.mkdir(parents=True)
    conn = sqlite3.connect(tmp_path / "data" / "item_scopes.db")
    conn.execute("CREATE TABLE other_table (id TEXT)")
    conn.commit()
    conn.close()

    class Settings:
        data_path = str(tmp_path / "data")

        deployment = type("Deployment", (), {"ea_root": str(tmp_path / "ea")})()

    monkeypatch.setattr("src.sdk.capabilities.get_settings", lambda: Settings())

    caps = load_user_capabilities("alice")

    assert caps == {"tools": {}, "skills": {}, "subagents": {}}
    assert (root / ".legacy_capabilities_migrated").exists()


def test_load_user_capabilities_migrates_legacy_capabilities_false_values(
    monkeypatch, tmp_path
):
    new_root = tmp_path / "data" / "users" / "alice"
    legacy_root = tmp_path / "ea"
    legacy_workspace = legacy_root / "Workspaces" / "sales"
    legacy_root.mkdir(parents=True)
    legacy_workspace.mkdir(parents=True)
    (legacy_root / "capabilities.yaml").write_text(
        yaml.dump(
            {
                "tools": {"legacy_disabled_tool": False, "legacy_enabled_tool": True},
                "skills": {"legacy_disabled_skill": False},
            }
        )
    )
    (legacy_workspace / "capabilities.yaml").write_text(
        yaml.dump(
            {
                "tools": {"workspace_disabled_tool": False},
                "subagents": {"workspace_disabled_agent": False},
            }
        )
    )

    class Settings:
        data_path = str(tmp_path / "data")

        deployment = type("Deployment", (), {"ea_root": str(legacy_root)})()

    monkeypatch.setattr("src.sdk.capabilities.get_settings", lambda: Settings())

    caps = load_user_capabilities("alice")
    caps_again = load_user_capabilities("alice")

    assert caps["tools"]["legacy_disabled_tool"] is False
    assert caps["tools"]["workspace_disabled_tool"] is False
    assert caps["skills"]["legacy_disabled_skill"] is False
    assert caps["subagents"]["workspace_disabled_agent"] is False
    assert "legacy_enabled_tool" not in caps["tools"]
    assert caps_again == caps
    assert (new_root / "capabilities.yaml").exists()


def test_load_user_capabilities_legacy_migration_runs_once(monkeypatch, tmp_path):
    root = tmp_path / "data" / "users" / "alice"
    legacy_root = tmp_path / "ea"
    legacy_root.mkdir(parents=True)
    (legacy_root / "capabilities.yaml").write_text(
        yaml.dump({"tools": {"legacy_disabled_tool": False}})
    )

    class Settings:
        data_path = str(tmp_path / "data")

        deployment = type("Deployment", (), {"ea_root": str(legacy_root)})()

    monkeypatch.setattr("src.sdk.capabilities.get_settings", lambda: Settings())

    caps = load_user_capabilities("alice")
    assert caps["tools"]["legacy_disabled_tool"] is False

    caps["tools"].pop("legacy_disabled_tool")
    (root / "capabilities.yaml").write_text(yaml.dump(caps))

    caps_after_remove = load_user_capabilities("alice")

    assert "legacy_disabled_tool" not in caps_after_remove["tools"]
    assert (root / ".legacy_capabilities_migrated").exists()


def test_load_user_capabilities_migrates_default_home_assistant_root(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    legacy_root = home / "Assistant"
    legacy_workspace = legacy_root / "Workspaces" / "sales"
    legacy_workspace.mkdir(parents=True)
    (legacy_root / "capabilities.yaml").write_text(
        yaml.dump({"tools": {"home_disabled_tool": False}})
    )
    (legacy_workspace / "capabilities.yaml").write_text(
        yaml.dump({"skills": {"home_disabled_skill": False}})
    )

    class Settings:
        data_path = str(tmp_path / "data")

        deployment = type("Deployment", (), {"ea_root": ""})()

    monkeypatch.setattr("src.sdk.capabilities.get_settings", lambda: Settings())
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    caps = load_user_capabilities("alice")

    assert caps["tools"]["home_disabled_tool"] is False
    assert caps["skills"]["home_disabled_skill"] is False
