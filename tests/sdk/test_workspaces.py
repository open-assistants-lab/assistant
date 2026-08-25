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
        dp = DataPaths(user_id="test_user", data_root="/tmp/ea-test-root")
        d = dp.global_subagents_dir()
        assert "Subagents" in str(d)

    def test_workspace_dir_is_backward_compat(self):
        """workspace_dir() should delegate to workspace_files_dir()."""
        from src.storage.paths import DataPaths
        dp = DataPaths(workspace_id="test")
        assert dp.workspace_dir() == dp.workspace_files_dir()

    def test_user_prompt_path(self):
        from src.storage.paths import DataPaths
        dp = DataPaths(user_id="test_user", data_root="/tmp/ea-test-root")
        d = dp.user_prompt_path()
        assert "AGENTS.md" in str(d)


