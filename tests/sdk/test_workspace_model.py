"""The documented workspace model: storage is user-global, not per-workspace.

#47 recorded that `workspace_models.py` promises an isolation boundary the code
does not provide. The decision is that storage is **user-global by design**, and
the promise was the defect. This test makes the decision executable: if someone
later implements real per-workspace storage, this test fails and forces the
docstrings to be updated deliberately, in the same commit.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

# Accessors whose `workspace_` prefix historically implied per-workspace
# storage. They all resolve under the user directory.
USER_GLOBAL_ACCESSORS = (
    "workspace_files_dir",
    "workspace_memory_dir",
    "workspace_skills_dir",
    "workspace_subagents_dir",
    "workspace_conversation_path",
    "workspace_cache",
)


@pytest.fixture
def two_workspaces():
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        a = DataPaths(data_root=d, user_id="same_user", workspace_id="project_a")
        b = DataPaths(data_root=d, user_id="same_user", workspace_id="project_b")
        assert a.workspace_id != b.workspace_id, "fixture must use distinct workspaces"
        yield a, b


@pytest.mark.parametrize("accessor", USER_GLOBAL_ACCESSORS)
def test_storage_accessors_are_user_global(two_workspaces, accessor: str) -> None:
    a, b = two_workspaces
    assert getattr(a, accessor)() == getattr(b, accessor)()


@pytest.mark.parametrize("accessor", USER_GLOBAL_ACCESSORS)
def test_storage_accessors_are_scoped_by_user_not_workspace(accessor: str) -> None:
    """Different users must NOT collide — user is the real boundary."""
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        one = DataPaths(data_root=d, user_id="user_one", workspace_id="project_a")
        two = DataPaths(data_root=d, user_id="user_two", workspace_id="project_a")
        assert getattr(one, accessor)() != getattr(two, accessor)()


def test_different_users_sharing_a_workspace_are_still_isolated() -> None:
    """The boundary that actually holds: same workspace, different users."""
    from src.storage.paths import DataPaths

    with tempfile.TemporaryDirectory() as d:
        one = DataPaths(data_root=d, user_id="u1", workspace_id="shared_ws")
        two = DataPaths(data_root=d, user_id="u2", workspace_id="shared_ws")
        assert one.workspace_files_dir() != two.workspace_files_dir()
        assert one.user_subagents_dir() != two.user_subagents_dir()


def test_memory_and_conversation_share_one_store_per_user() -> None:
    """Memory is a lens over the message history, not a separate store.

    `_store_key` omits workspace_id and `MessageStore.__init__` discards it, so
    a per-project memory boundary is not possible without splitting the store.
    """
    from src.storage.messages import USER_LEVEL_CONTEXT, get_message_store

    with tempfile.TemporaryDirectory() as d:
        import src.storage.messages as messages_module

        messages_module._stores.clear()
        original = messages_module.MessageStore

        class _Recorder:
            def __init__(self, user_id, workspace_id="personal", **_kwargs):
                self.user_id = user_id
                self.workspace_id = USER_LEVEL_CONTEXT
                self.requested_workspace_id = workspace_id

        messages_module.MessageStore = _Recorder  # type: ignore[misc]
        try:
            store = get_message_store("u1", workspace_id="project_a")
            assert store.workspace_id == USER_LEVEL_CONTEXT
            assert store.requested_workspace_id == "project_a"
        finally:
            messages_module.MessageStore = original  # type: ignore[misc]
            messages_module._stores.clear()
        assert Path(d).exists()


def test_the_model_docstring_makes_no_isolation_claim() -> None:
    """Guard the promise itself: the module must not claim isolation."""
    import inspect

    from src.sdk import workspace_models

    doc = inspect.getdoc(workspace_models) or ""
    lowered = doc.casefold()
    assert "isolated project container" not in lowered
    # It should positively state the real model.
    assert "user-global" in lowered or "user global" in lowered


def test_path_accessors_document_that_they_are_user_global() -> None:
    """Each misleadingly-named accessor must say what it actually returns."""
    from src.storage.paths import DataPaths

    for accessor in USER_GLOBAL_ACCESSORS:
        doc = inspect.getdoc(getattr(DataPaths, accessor)) or ""
        assert doc, f"{accessor} has no docstring"
        assert "user-global" in doc.casefold() or "user global" in doc.casefold(), (
            f"{accessor} does not document that its path is user-global"
        )
