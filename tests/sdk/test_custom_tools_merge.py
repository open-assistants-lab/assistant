"""Shared vs per-user TOOL.md merge (deployment-shared Tools dir)."""


from src.sdk.tools_custom import get_custom_tools


def _write_tool(tools_dir, name, description):
    d = tools_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "TOOL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        f'command: echo "{name}"\n---\nbody\n',
        encoding="utf-8",
    )


def test_shared_tool_visible_to_every_user(tmp_path, monkeypatch):
    import src.storage.paths as paths_mod

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(paths_mod.DataPaths, "root", property(lambda self: tmp_path / "data"), raising=False)

    _write_tool(tmp_path / "data" / "Tools", "shared_tool", "shared")
    import src.storage.paths as pm
    dp = pm.DataPaths(user_id="alice")
    print("DEBUG root:", dp.root, "| shared exists:", dp.workspace_tools_dir().exists(), "| user exists:", dp.user_tools_dir().exists(), "| alice file:", (dp.user_tools_dir() / "snooze_check" / "TOOL.md").exists())
    import src.storage.paths as pm2
    dp2 = pm2.DataPaths(user_id="alice")
    print("DEBUG2 root:", dp2.root, "| shared:", dp2.workspace_tools_dir().exists(), "| user dir exists:", dp2.user_tools_dir().exists(), "| file:", (dp2.user_tools_dir() / "snooze_check" / "TOOL.md").exists())
    alice = get_custom_tools(user_id="alice")
    bob = get_custom_tools(user_id="bob")
    assert [t.name for t in alice] == ["shared_tool"]
    assert [t.name for t in bob] == ["shared_tool"]


def test_user_tool_overrides_shared_same_name(tmp_path, monkeypatch):
    """Per-user TOOL.md overrides the deployment-shared same-name tool."""
    import src.storage.paths as paths_mod

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        paths_mod.DataPaths, "root", property(lambda self: tmp_path / "data")
    )

    root_tools = tmp_path / "data" / "Tools"
    _write_tool(root_tools, "snooze_check", "SHARED version")
    _write_tool(
        tmp_path / "data" / "Users" / "alice" / "Tools",
        "snooze_check",
        "ALICE version",
    )

    alice = get_custom_tools(user_id="alice")
    bob = get_custom_tools(user_id="bob")
    alice_d = {t.name: t.description for t in alice}
    bob_d = {t.name: t.description for t in bob}
    assert alice_d["snooze_check"] == "ALICE version"   # user override wins
    assert bob_d["snooze_check"] == "SHARED version"    # bob sees shared
