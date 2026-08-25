"""Workspace compatibility integration tests.

Runtime data is user-level. workspace_id remains accepted as a compatibility
parameter, while session_id separates chat history.
"""

from __future__ import annotations

import tempfile

import pytest


def test_conversation_store_respects_explicit_base_dir():
    """Explicit base_dir still creates separate stores for tests and tools."""
    from src.storage.messages import MessageStore

    with tempfile.TemporaryDirectory() as d:
        store_a = MessageStore("test_user", base_dir=f"{d}/ws-a", workspace_id="ws-a")
        store_b = MessageStore("test_user", base_dir=f"{d}/ws-b", workspace_id="ws-b")

        store_a.add_message("user", "I live in Denver")
        store_a.add_message("assistant", "Noted, you live in Denver")

        msgs_a = store_a.get_messages(limit=100)
        msgs_b = store_b.get_messages(limit=100)

        assert len(msgs_a) == 2
        assert len(msgs_b) == 0


def test_conversation_base_dir_isolation():
    """Messages in one explicit base_dir do not appear in another."""
    from src.storage.messages import MessageStore

    with tempfile.TemporaryDirectory() as d:
        store_a = MessageStore("test_user", base_dir=f"{d}/ws-a", workspace_id="ws-a")
        store_b = MessageStore("test_user", base_dir=f"{d}/ws-b", workspace_id="ws-b")

        store_a.add_message("user", "My project is Q2 Planning")
        store_a.add_message("assistant", "Got it, project Q2 Planning")

        store_b.add_message("user", "What is my project?")

        msgs_a = store_a.get_messages(limit=100)
        msgs_b = store_b.get_messages(limit=100)

        assert any("Q2 Planning" in str(m.content) for m in msgs_a)
        assert not any("Q2 Planning" in str(m.content) for m in msgs_b)


def test_conversation_messages_dont_leak_between_explicit_base_dirs():
    """Writing to one explicit base_dir does not affect another."""
    from src.storage.messages import MessageStore

    with tempfile.TemporaryDirectory() as d:
        store_a = MessageStore("test_user", base_dir=f"{d}/ws-a", workspace_id="ws-a")
        store_b = MessageStore("test_user", base_dir=f"{d}/ws-b", workspace_id="ws-b")

        store_b.add_message("user", "I prefer dark roast coffee")

        store_a.add_message("user", "I moved to Melbourne")
        store_a.add_message("assistant", "Updated your location to Melbourne")

        msgs_b = store_b.get_messages(limit=100)
        assert len(msgs_b) == 1
        assert "dark roast" in str(msgs_b[0].content)
        assert "Melbourne" not in str(msgs_b[0].content)


def test_memory_paths_ignore_workspace_id():
    """Memory paths are user-level compatibility aliases."""
    from src.storage.paths import DataPaths

    paths_a = DataPaths(user_id="test_user", workspace_id="ws-a")
    paths_b = DataPaths(user_id="test_user", workspace_id="ws-b")

    mem_a = paths_a.workspace_memory_dir()
    mem_b = paths_b.workspace_memory_dir()

    assert mem_a == paths_a.user_memory_dir()
    assert mem_b == paths_b.user_memory_dir()
    assert mem_a == mem_b


def test_memory_stores_are_per_user(tmp_path, monkeypatch):
    """MessageStore instances are per-user, not per-workspace."""
    import src.storage.messages as messages_storage
    from src.storage.messages import get_message_store
    from src.storage.paths import DataPaths

    messages_storage._stores.clear()
    paths = DataPaths(data_root=str(tmp_path), user_id="test_user")
    monkeypatch.setattr(messages_storage, "get_paths", lambda user_id, workspace_id=None: paths)

    store_a = get_message_store("test_user", workspace_id="ws-a")
    store_b = get_message_store("test_user", workspace_id="ws-b")

    assert store_a.user_id == "test_user"
    assert store_b.user_id == "test_user"
    assert store_a is store_b
    assert store_a.workspace_id == "user"


def test_file_paths_ignore_workspace_id():
    """Workspace file paths are user-level compatibility aliases."""
    from src.storage.paths import DataPaths

    paths_a = DataPaths(user_id="test_user", workspace_id="project-alpha")
    paths_b = DataPaths(user_id="test_user", workspace_id="project-beta")

    files_a = paths_a.workspace_files_dir()
    files_b = paths_b.workspace_files_dir()

    assert files_a == paths_a.files_dir()
    assert files_b == paths_b.files_dir()
    assert files_a == files_b


@pytest.mark.asyncio
async def test_subagents_are_user_level_across_workspaces():
    """Subagents created with one workspace_id are visible with another."""
    import tempfile
    from unittest.mock import patch

    from agentprofile.models import AgentProfile

    from src.sdk.coordinator import SubagentCoordinator
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        mock_a = DataPaths(data_root=d, user_id="test_user", workspace_id="ws-a")
        mock_b = DataPaths(data_root=d, user_id="test_user", workspace_id="ws-b")

        mock_a.subagents_dir = mock_a.workspace_subagents_dir
        mock_b.subagents_dir = mock_b.workspace_subagents_dir

        def _make_path(user_id=None, team_id=None, workspace_id=None):
            if workspace_id == "ws-a":
                return mock_a
            if workspace_id == "ws-b":
                return mock_b
            return DataPaths(data_root=d, user_id=user_id, workspace_id=workspace_id)

        with patch("src.storage.paths.get_paths", side_effect=_make_path):
            coord_a = SubagentCoordinator("test_user", workspace_id="ws-a")
            coord_b = SubagentCoordinator("test_user", workspace_id="ws-b")

            profile = AgentProfile(
                name="writer",
                description="Report writer for project alpha",
                tools=["time_get"],
            )
            await coord_a.create(profile)

            defs_a = await coord_a.list_defs()
            defs_b = await coord_b.list_defs()

            assert any(d.name == "writer" for d in defs_a), "writer should appear in ws-a"
            assert any(d.name == "writer" for d in defs_b), "writer should be user-level"


@pytest.mark.asyncio
async def test_same_name_subagent_across_workspaces_updates_user_level_definition():
    """Same subagent name across workspace_ids refers to one user-level definition."""
    import tempfile
    from unittest.mock import patch

    from agentprofile.models import AgentProfile

    from src.sdk.coordinator import SubagentCoordinator
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        mock_a = DataPaths(data_root=d, user_id="test_user", workspace_id="ws-a")
        mock_b = DataPaths(data_root=d, user_id="test_user", workspace_id="ws-b")

        mock_a.subagents_dir = mock_a.workspace_subagents_dir
        mock_b.subagents_dir = mock_b.workspace_subagents_dir

        def _make_path(user_id=None, team_id=None, workspace_id=None):
            if workspace_id == "ws-a":
                return mock_a
            if workspace_id == "ws-b":
                return mock_b
            return DataPaths(data_root=d, user_id=user_id, workspace_id=workspace_id)

        with patch("src.storage.paths.get_paths", side_effect=_make_path):
            coord_a = SubagentCoordinator("test_user", workspace_id="ws-a")
            coord_b = SubagentCoordinator("test_user", workspace_id="ws-b")

            ad_a = AgentProfile(
                name="researcher",
                description="Research for project alpha",
                model="ollama:minimax-m2.5",
                tools=["time_get", "memory_search"],
            )
            ad_b = AgentProfile(
                name="researcher",
                description="Research for project beta",
                model="anthropic:claude-sonnet-4-20250514",
                tools=["time_get"],
            )

            await coord_a.create(ad_a)
            await coord_b.create(ad_b)

            loaded_a = coord_a.load_def("researcher")
            loaded_b = coord_b.load_def("researcher")

            assert loaded_a is not None
            assert loaded_b is not None
            assert loaded_a.description == loaded_b.description == "Research for project beta"
            assert loaded_a.model == loaded_b.model == "anthropic:claude-sonnet-4-20250514"
            assert "memory_search" not in (loaded_a.tools or [])
            assert "memory_search" not in (loaded_b.tools or [])


@pytest.mark.asyncio
async def test_subagent_delete_through_one_workspace_removes_user_level_definition():
    """Deleting a subagent path through one workspace_id removes the shared definition."""
    import tempfile
    from unittest.mock import patch

    from agentprofile.models import AgentProfile

    from src.sdk.coordinator import SubagentCoordinator
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        mock_a = DataPaths(data_root=d, user_id="test_user", workspace_id="ws-a")
        mock_b = DataPaths(data_root=d, user_id="test_user", workspace_id="ws-b")

        mock_a.subagents_dir = mock_a.workspace_subagents_dir
        mock_b.subagents_dir = mock_b.workspace_subagents_dir

        def _make_path(user_id=None, team_id=None, workspace_id=None):
            if workspace_id == "ws-a":
                return mock_a
            if workspace_id == "ws-b":
                return mock_b
            return DataPaths(data_root=d, user_id=user_id, workspace_id=workspace_id)

        with patch("src.storage.paths.get_paths", side_effect=_make_path):
            coord_a = SubagentCoordinator("test_user", workspace_id="ws-a")
            coord_b = SubagentCoordinator("test_user", workspace_id="ws-b")

            agent = AgentProfile(name="shared", description="Exists in both", tools=["time_get"])
            await coord_a.create(agent)
            await coord_b.create(agent)

            # Both coordinators point at the same user-level subagent directory.
            import shutil
            shutil.rmtree(coord_a.base_path / "shared")

            assert coord_a.load_def("shared") is None
            assert coord_b.load_def("shared") is None


def test_get_paths_workspace_helpers_default_to_user_level():
    """Calling DataPaths without workspace_id still returns user-level aliases."""
    from src.storage.paths import DataPaths

    dp = DataPaths(user_id="test_user")
    files = dp.workspace_files_dir()
    assert files == dp.files_dir()
    assert dp.workspace_memory_dir() == dp.user_memory_dir()
    assert dp.workspace_skills_dir() == dp.user_skills_dir()
    assert dp.workspace_subagents_dir() == dp.user_subagents_dir()
    assert dp.versions_dir() == dp.user_dir / ".versions"
    assert dp.workspace_conversation_path() == dp.conversation_dir() / "app.db"
