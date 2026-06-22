"""Tests for DataPaths path restructuring."""

from src.storage.paths import DataPaths


def test_root_defaults_to_home_ea():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.root) == "/tmp/ea-test-root"


def test_user_skills_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_skills_dir()) == "/tmp/ea-test-root/Skills"


def test_user_subagents_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_subagents_dir()) == "/tmp/ea-test-root/Subagents"


def test_user_prompt_path():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_prompt_path()) == "/tmp/ea-test-root/AGENTS.md"


def test_email_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.email_dir()) == "/tmp/ea-test-root/Email"


def test_email_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.email_db()) == "/tmp/ea-test-root/Email/emails.db"


def test_gmail_cache_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.gmail_cache_dir()) == "/tmp/ea-test-root/Email/gmail_cache"


def test_contacts_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.contacts_dir()) == "/tmp/ea-test-root/Contacts"


def test_contacts_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.contacts_db()) == "/tmp/ea-test-root/Contacts/contacts.db"


def test_todos_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.todos_dir()) == "/tmp/ea-test-root/Todos"


def test_todos_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.todos_db()) == "/tmp/ea-test-root/Todos/todos.db"


def test_conversation_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.conversation_dir()) == "/tmp/ea-test-root/Conversation"


def test_conversation_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.conversation_db()) == "/tmp/ea-test-root/Conversation/messages.db"


def test_user_memory_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_memory_dir()) == "/tmp/ea-test-root/Memory/global"


def test_user_apps_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_apps_dir()) == "/tmp/ea-test-root/Apps"


def test_user_mcp_config():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.user_mcp_config()) == "/tmp/ea-test-root/.mcp.json"


def test_research_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.research_dir()) == "/tmp/ea-test-root/Research/tester"


def test_companion_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.companion_dir()) == "/tmp/ea-test-root/Companion"


def test_companion_notifications_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.companion_notifications_db()) == "/tmp/ea-test-root/Companion/notifications.db"


def test_companion_memory_db():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    assert str(dp.companion_memory_db()) == "/tmp/ea-test-root/Companion/memory.db"


def test_deprecated_skills_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.skills_dir()
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Skills"


def test_deprecated_global_subagents_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.global_subagents_dir()
        assert len(w) == 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Subagents"


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
    assert str(result) == "/tmp/ea-test-root/Skills"


def test_deprecated_subagents_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.subagents_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Subagents"


def test_deprecated_agent_defs_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.agent_defs_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Subagents/agent_defs"


def test_deprecated_global_memory_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.global_memory_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Memory/global"


def test_deprecated_memory_dir_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.memory_dir()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/Memory/global"


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
    assert str(result) == "/tmp/ea-test-root/Email/gmail_cache"


def test_deprecated_mcp_config_path_warns():
    import warnings
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = dp.mcp_config_path()
        assert len(w) >= 1
        assert "deprecated" in str(w[0].message).lower()
    assert str(result) == "/tmp/ea-test-root/.mcp.json"


def test_versions_dir():
    dp = DataPaths(user_id="tester", ea_root="/tmp/ea-test-root")
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
    assert str(result) == "/tmp/ea-test-root/Apps"
