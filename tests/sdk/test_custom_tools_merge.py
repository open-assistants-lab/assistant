"""Shared vs per-user TOOL.md merge (deployment-shared Tools dir)."""

import inspect

from jsonschema import Draft7Validator

from src.sdk.tool_index import ToolIndex
from src.sdk.tools_custom import get_custom_tools, scan_tools_dir


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


def test_all_multi_parameter_tool_md_tools_bind_and_index(tmp_path):
    """Generic ``**kwargs`` hid named inputs; YAML also coerced string true/false enums."""
    specs = {
        "ch_query": {"sql": "string"},
        "member_lookup": {"query": "string"},
        "snooze_check": {"store": "string"},
        "snooze_confirm": {"pending_id": "string"},
        "snooze_set": {"store": "string", "snooze": "string", "requested_by": "string"},
        "jobs_add": {"job_type": "string", "store": "string", "at": "string", "requested_by": "string"},
        "jobs_cancel": {"job_id": "string"},
        "changes_log": {"user": "string"},
    }
    tools_dir = tmp_path / "Tools"
    for name, properties in specs.items():
        required = list(properties)[:-1] if len(properties) > 1 else list(properties)
        props = "\n".join(f"    {key}: {{type: {kind}}}" for key, kind in properties.items())
        command_args = " ".join(f"--{key}={{{{{key}}}}}" for key in properties)
        tool_dir = tools_dir / name
        tool_dir.mkdir(parents=True)
        (tool_dir / "TOOL.md").write_text(
            f"---\nname: {name}\ndescription: fixture {name}\ncommand: echo {command_args}\n"
            f"parameters:\n  type: object\n  properties:\n{props}\n  required: {required}\n"
            "annotations:\n  read_only: true\n---\nfixture\n",
            encoding="utf-8",
        )
        if name == "snooze_set":
            path = tool_dir / "TOOL.md"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "    snooze: {type: string}",
                    "    snooze: {type: string, enum: [true, false]}",
                ),
                encoding="utf-8",
            )

    tools = scan_tools_dir(tools_dir)
    assert {tool.name for tool in tools} == set(specs)
    index = ToolIndex(tmp_path / "index")
    for tool in tools:
        Draft7Validator.check_schema(tool.parameters)
        if tool.name == "snooze_set":
            assert tool.parameters["properties"]["snooze"]["enum"] == ["true", "false"]
        assert tool.function is not None
        assert set(inspect.signature(tool.function).parameters) == set(specs[tool.name])
        tool.function(**{key: "true" if key == "snooze" else "x" for key in specs[tool.name]})
        index.index_tool(tool, tool_type="custom")
    assert set(index.list_all_names()) == set(specs)
