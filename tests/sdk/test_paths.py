"""Tests for DataPaths path restructuring."""

from __future__ import annotations

from src.storage.paths import DataPaths

USER_ROOT = "/tmp/ea-test-root/Users/tester"


def test_root_defaults_to_home_ea():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.root) == "/tmp/ea-test-root"


def test_user_skills_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_skills_dir()) == f"{USER_ROOT}/Skills"


def test_user_subagents_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_subagents_dir()) == f"{USER_ROOT}/Subagents"


def test_user_prompt_path():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_prompt_path()) == f"{USER_ROOT}/AGENTS.md"


def test_email_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.email_dir()) == f"{USER_ROOT}/Email"


def test_email_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.email_db()) == f"{USER_ROOT}/Email/emails.db"


def test_gmail_cache_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.gmail_cache_dir()) == f"{USER_ROOT}/Email/gmail_cache"


def test_contacts_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.contacts_dir()) == f"{USER_ROOT}/Contacts"


def test_contacts_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.contacts_db()) == f"{USER_ROOT}/Contacts/contacts.db"


def test_todos_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.todos_dir()) == f"{USER_ROOT}/Todos"


def test_todos_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.todos_db()) == f"{USER_ROOT}/Todos/todos.db"


def test_conversation_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.conversation_dir()) == f"{USER_ROOT}/Conversation"


def test_conversation_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.conversation_db()) == f"{USER_ROOT}/Conversation/messages.db"


def test_user_memory_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_memory_dir()) == f"{USER_ROOT}/Memory/global"


def test_user_apps_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_apps_dir()) == f"{USER_ROOT}/Apps"


def test_user_mcp_config():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_mcp_config()) == f"{USER_ROOT}/.mcp.json"


def test_research_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.research_dir()) == f"{USER_ROOT}/Research/testws"


def test_scheduler_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.scheduler_dir()) == f"{USER_ROOT}/Scheduler"


def test_scheduler_notifications_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.scheduler_notifications_db()) == f"{USER_ROOT}/Scheduler/notifications.db"


def test_scheduler_memory_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.scheduler_memory_db()) == f"{USER_ROOT}/Scheduler/memory.db"


def test_companion_dir_backward_compat():
    """Old companion_dir() should still work as alias."""
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert dp.companion_dir() == dp.scheduler_dir()
    assert dp.companion_notifications_db() == dp.scheduler_notifications_db()
    assert dp.companion_memory_db() == dp.scheduler_memory_db()


def test_workspace_skills_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_skills_dir()) == f"{USER_ROOT}/Skills"


def test_workspace_subagents_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_subagents_dir()) == f"{USER_ROOT}/Subagents"


def test_workspace_files_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_files_dir()) == f"{USER_ROOT}/Files"


def test_workspace_memory_dir_uppercase():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_memory_dir()) == f"{USER_ROOT}/Memory/global"


def test_workspace_conversation_path():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    assert str(dp.workspace_conversation_path()) == f"{USER_ROOT}/Conversation/app.db"


def test_deprecated_skills_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.skills_dir()
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Skills"


def test_deprecated_global_subagents_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.global_subagents_dir()
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Subagents"


def test_model_cache_path():
    dp = DataPaths(user_id="tester", data_path="/tmp/ea-test-data")
    assert "cache" in str(dp.model_cache_path())


def test_work_queue_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    path = dp.work_queue_db()
    assert "Subagents" in str(path)
    assert path.name == "work_queue.db"


def test_deprecated_global_skills_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.global_skills_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Skills"


def test_deprecated_subagents_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.subagents_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Subagents"


def test_deprecated_agent_defs_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.agent_defs_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Subagents/agent_defs"


def test_deprecated_global_memory_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.global_memory_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Memory/global"


def test_deprecated_memory_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.memory_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Memory/global"


def test_deprecated_user_config_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        dp.user_config_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()


def test_deprecated_gmail_cache_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.gmail_cache()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Email/gmail_cache"


def test_deprecated_mcp_config_path_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.mcp_config_path()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/.mcp.json"


def test_deprecated_workspace_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.workspace_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Files"


def test_workspace_cache():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    result = dp.workspace_cache()
    assert ".file_cache.json" in str(result)


def test_versions_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root", workspace_id="testws")
    result = dp.versions_dir()
    assert ".versions" in str(result)


def test_team_root_none_in_solo():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert dp.team_root is None


def test_deprecated_apps_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.apps_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == f"{USER_ROOT}/Apps"
