"""Tests for workspace model, storage, and scoping."""
import tempfile
from pathlib import Path

from src.sdk.workspace_models import WORKSPACE_DEFAULT, Workspace


class TestWorkspaceModel:
    """Workspace data model."""

    def test_default_workspace_has_id(self):
        assert WORKSPACE_DEFAULT.id == "personal"

    def test_default_workspace_has_name(self):
        assert WORKSPACE_DEFAULT.name == "Personal"

    def test_create_workspace_with_all_fields(self):
        ws = Workspace(
            id="q2-planning",
            name="Q2 Planning",
            description="Q2 product launch",
            prompt="Respond as a PM. Use AEST.",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        assert ws.id == "q2-planning"
        assert ws.name == "Q2 Planning"
        assert ws.description == "Q2 product launch"
        assert ws.prompt == "Respond as a PM. Use AEST."

    def test_workspace_id_from_name(self):
        """Test that workspace IDs are derived from names."""
        names_and_ids = [
            ("Q2 Planning", "q2-planning"),
            ("Home Renovation", "home-renovation"),
            ("Personal", "personal"),
            ("My Project!", "my-project"),
            ("  Spaces  ", "spaces"),
        ]
        for name, expected_id in names_and_ids:
            ws = Workspace.from_name(name)
            assert ws.id == expected_id, f"Expected '{expected_id}' from '{name}'"
            assert ws.name == name.strip()

    def test_workspace_to_dict(self):
        ws = Workspace(
            id="test", name="Test", description="desc",
            prompt="ci", created_at="a", updated_at="b",
            model_override="ollama:minimax-m2.7",
        )
        d = ws.to_dict()
        assert d["id"] == "test"
        assert d["name"] == "Test"
        assert d["description"] == "desc"
        assert d["prompt"] == "ci"
        assert d["model_override"] == "ollama:minimax-m2.7"

    def test_workspace_from_dict(self):
        d = {
            "id": "test", "name": "Test", "description": "d",
            "prompt": "c", "created_at": "a", "updated_at": "b",
            "model_override": "deepseek:deepseek-v4-flash",
        }
        ws = Workspace.from_dict(d)
        assert ws.id == "test"
        assert ws.name == "Test"
        assert ws.model_override == "deepseek:deepseek-v4-flash"

    def test_workspace_from_dict_defaults_model_override_to_none(self):
        ws = Workspace.from_dict({"id": "test", "name": "Test"})

        assert ws.model_override is None

    def test_workspace_json_roundtrip(self):
        ws = Workspace(
            id="test", name="Test", description="desc",
            prompt="ci", created_at="2026-01-01", updated_at="2026-01-01",
        )
        json_str = ws.to_json()
        ws2 = Workspace.from_json(json_str)
        assert ws2.id == ws.id
        assert ws2.name == ws.name


class TestWorkspaceStorage:
    """Workspace persistence (YAML file per workspace)."""

    def test_save_and_load_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.sdk.workspace_models import load_workspace, save_workspace

            ws = Workspace(
                id="test", name="Test", description="d",
                prompt="c", created_at="a", updated_at="b",
                model_override="ollama:minimax-m2.7",
            )
            save_workspace(ws, base_path=Path(tmpdir))

            loaded = load_workspace("test", base_path=Path(tmpdir))
            assert loaded is not None
            assert loaded.id == "test"
            assert loaded.name == "Test"
            assert loaded.model_override == "ollama:minimax-m2.7"

    def test_load_nonexistent_workspace_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.sdk.workspace_models import load_workspace
            assert load_workspace("nonexistent", base_path=Path(tmpdir)) is None

    def test_list_workspaces(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.sdk.workspace_models import list_workspaces, save_workspace

            ws1 = Workspace.from_name("Project A")
            ws2 = Workspace.from_name("Project B")
            save_workspace(ws1, base_path=Path(tmpdir))
            save_workspace(ws2, base_path=Path(tmpdir))

            workspaces = list_workspaces(base_path=Path(tmpdir))
            names = [w.name for w in workspaces]
            assert "Project A" in names
            assert "Project B" in names
            assert len(workspaces) >= 2

    def test_delete_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from src.sdk.workspace_models import delete_workspace, load_workspace, save_workspace

            ws = Workspace.from_name("DeleteMe")
            save_workspace(ws, base_path=Path(tmpdir))
            assert load_workspace("deleteme", base_path=Path(tmpdir)) is not None

            delete_workspace("deleteme", base_path=Path(tmpdir))
            assert load_workspace("deleteme", base_path=Path(tmpdir)) is None


class TestWorkspaceDataPaths:
    """Workspace-scoped paths via DataPaths."""

    def test_workspace_files_dir_default(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="personal")
        d = dp.workspace_files_dir()
        assert d.name == "Files"
        assert d == dp.files_dir()

    def test_workspace_memory_dir(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="q2-planning")
        d = dp.workspace_memory_dir()
        assert d == dp.user_memory_dir()

    def test_workspace_conversation_path(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="test")
        p = dp.workspace_conversation_path()
        assert p == dp.conversation_dir() / "app.db"

    def test_workspace_subagents_dir(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="test")
        d = dp.workspace_subagents_dir()
        assert d.name == "Subagents"

    def test_workspace_skills_dir(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="test")
        d = dp.workspace_skills_dir()
        assert d.name == "Skills"

    def test_global_memory_dir(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(user_id="test_user")
        d = dp.global_memory_dir()
        assert "Memory" in str(d)
        assert "global" in str(d)

    def test_global_skills_dir(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(user_id="test_user")
        d = dp.global_skills_dir()
        assert "Skills" in str(d)

    def test_global_subagents_dir(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(user_id="test_user", ea_root="/tmp/ea-test-root")
        d = dp.global_subagents_dir()
        assert "Subagents" in str(d)

    def test_workspace_dir_is_backward_compat(self):
        """workspace_dir() should delegate to workspace_files_dir()."""
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="test")
        assert dp.workspace_dir() == dp.workspace_files_dir()

    def test_user_prompt_path(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(user_id="test_user", ea_root="/tmp/ea-test-root")
        d = dp.user_prompt_path()
        assert "AGENTS.md" in str(d)


class TestWorkspaceTools:
    """Agent-facing workspace tools."""

    @staticmethod
    def _invoke(tool_def, **kwargs):
        """Call a @tool-decorated function via its invoke method."""
        return tool_def.invoke(kwargs)

    def test_workspace_create_returns_string(self):
        from src.sdk.tools_core.workspace import workspace_create
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock as mock
            with mock.patch("src.sdk.workspace_models._default_workspaces_dir", return_value=Path(tmpdir)):
                result = self._invoke(workspace_create,
                    name="Test Project",
                    description="A test workspace",
                    instructions="Be helpful",
                )
                assert "Test Project" in result or "test-project" in result
                assert "legacy organizational" in result.lower()
                assert "user-level" in result.lower()
                assert "session-separated" in result.lower()
                assert "own files" not in workspace_create.description.lower()
                assert "Files:" not in result

    def test_workspace_list_returns_names(self):
        from src.sdk.tools_core.workspace import workspace_create, workspace_list
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock as mock
            with mock.patch("src.sdk.workspace_models._default_workspaces_dir", return_value=Path(tmpdir)):
                self._invoke(workspace_create, name="Alpha")
                self._invoke(workspace_create, name="Beta")
                result = self._invoke(workspace_list)
                assert "Alpha" in result
                assert "Beta" in result
                assert "legacy organizational" in result.lower()
                assert "user-level" in result.lower()

    def test_workspace_switch_updates_current(self):
        from src.sdk.tools_core.workspace import (
            _get_current_workspace,
            workspace_create,
            workspace_switch,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock as mock
            with mock.patch("src.sdk.workspace_models._default_workspaces_dir", return_value=Path(tmpdir)):
                self._invoke(workspace_create, name="Project X")
                self._invoke(workspace_switch, name="Project X", user_id="test_user")
                assert _get_current_workspace("test_user") == "project-x"

    def test_workspace_current_shows_info(self):
        from src.sdk.tools_core.workspace import (
            workspace_create,
            workspace_current,
            workspace_switch,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock as mock
            with mock.patch("src.sdk.workspace_models._default_workspaces_dir", return_value=Path(tmpdir)):
                self._invoke(workspace_create, name="Current Test")
                self._invoke(workspace_switch, name="Current Test", user_id="u1")
                result = self._invoke(workspace_current, user_id="u1")
                assert "Current Test" in result
                assert "legacy organizational" in result.lower()
                assert "user-level" in result.lower()

    def test_workspace_delete_removes(self):
        from src.sdk.tools_core.workspace import (
            workspace_create,
            workspace_delete,
            workspace_list,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock as mock
            with mock.patch("src.sdk.workspace_models._default_workspaces_dir", return_value=Path(tmpdir)):
                self._invoke(workspace_create, name="DeleteMe")
                assert "DeleteMe" in self._invoke(workspace_list)
                result = self._invoke(workspace_delete, name="DeleteMe")
                assert "DeleteMe" not in self._invoke(workspace_list)
                assert "metadata" in result.lower()
                assert "user-level data was not deleted" in result.lower()
                assert "all its data" not in workspace_delete.description.lower()

    def test_workspace_switch_to_nonexistent_errors(self):
        from src.sdk.tools_core.workspace import workspace_switch
        result = self._invoke(workspace_switch, name="NonExistent", user_id="u1")
        assert "not found" in result.lower() or "error" in result.lower()

    def test_workspace_create_duplicate_updates(self):
        from src.sdk.tools_core.workspace import workspace_create, workspace_list
        with tempfile.TemporaryDirectory() as tmpdir:
            import unittest.mock as mock
            with mock.patch("src.sdk.workspace_models._default_workspaces_dir", return_value=Path(tmpdir)):
                self._invoke(workspace_create, name="Dup", description="first")
                self._invoke(workspace_create, name="Dup", description="second")
                result = self._invoke(workspace_list)
                count = result.count("Dup")
                assert count == 1
