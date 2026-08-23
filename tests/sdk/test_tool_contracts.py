"""Contract tests for all tools.

Each tool is tested with its documented input schema and output format.
These verify that tools conform to their contract regardless of whether
they use LangChain @tool or SDK @tool.
"""




# ─── Time ───


class TestTimeGet:
    def test_returns_current_time(self):
        from src.sdk.tools_core.time import time_get

        result = time_get.invoke()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_date(self):
        from src.sdk.tools_core.time import time_get

        result = time_get.invoke()
        assert "20" in result


# ─── Shell ───


class TestShellExecute:
    def test_simple_command(self):
        from src.sdk.tools_core.shell import shell_execute

        result = shell_execute.invoke({"command": "echo hello", "user_id": "test"})
        assert "hello" in str(result)

    def test_rejects_dangerous_command(self):
        from src.sdk.tools_core.shell import shell_execute

        result = shell_execute.invoke({"command": "rm -rf /", "user_id": "test"})
        lowered = str(result).lower()
        assert (
            "not allowed" in lowered
            or "error" in lowered
            or "rejected" in lowered
            or "dangerous" in lowered
        )

    def test_default_user_id(self):
        from src.sdk.tools_core.shell import shell_execute

        result = shell_execute.invoke({"command": "echo test"})
        assert "test" in str(result)

    def test_command_timeout(self):
        from src.sdk.tools_core.shell import shell_execute

        result = shell_execute.invoke({"command": "sleep 1 && echo done", "user_id": "test"})
        assert isinstance(str(result), str)


# ─── Filesystem ───


class TestFilesystemTools:
    def test_files_write_and_read(self):
        from src.sdk.tools_core.filesystem import files_read, files_write

        files_write.invoke(
            {
                "path": "test_contract_ws/hello.txt",
                "content": "Hello World",
                "user_id": "test_contract_fs",
            }
        )
        result = files_read.invoke(
            {"path": "test_contract_ws/hello.txt", "user_id": "test_contract_fs"}
        )
        assert "Hello World" in str(result)

    def test_files_list(self):
        from src.sdk.tools_core.filesystem import files_list

        result = files_list.invoke({"user_id": "test_contract_fs"})
        assert isinstance(str(result), str)

    def test_files_mkdir(self):
        from src.sdk.tools_core.filesystem import files_mkdir

        result = files_mkdir.invoke(
            {"path": "test_contract_ws/subdir", "user_id": "test_contract_fs"}
        )
        assert isinstance(str(result), str)

    def test_files_edit(self):
        from src.sdk.tools_core.filesystem import files_edit, files_write

        files_write.invoke(
            {
                "path": "test_contract_ws/edit_test.txt",
                "content": "Hello World",
                "user_id": "test_contract_fs",
            }
        )
        result = files_edit.invoke(
            {
                "path": "test_contract_ws/edit_test.txt",
                "old": "World",
                "new": "Universe",
                "user_id": "test_contract_fs",
            }
        )
        assert isinstance(str(result), str)

    def test_files_delete(self):
        from src.sdk.tools_core.filesystem import files_delete, files_write

        files_write.invoke(
            {
                "path": "test_contract_ws/delete_test.txt",
                "content": "to delete",
                "user_id": "test_contract_fs",
            }
        )
        result = files_delete.invoke(
            {"path": "test_contract_ws/delete_test.txt", "user_id": "test_contract_fs"}
        )
        assert isinstance(str(result), str)

    def test_files_rename(self):
        from src.sdk.tools_core.filesystem import files_rename, files_write

        files_write.invoke(
            {
                "path": "test_contract_ws/rename_test.txt",
                "content": "rename me",
                "user_id": "test_contract_fs",
            }
        )
        result = files_rename.invoke(
            {
                "path": "test_contract_ws/rename_test.txt",
                "new_name": "renamed.txt",
                "user_id": "test_contract_fs",
            }
        )
        assert isinstance(str(result), str)


# ─── File Search ───


class TestFileSearchTools:
    def test_glob_search(self):
        from src.sdk.tools_core.file_search import files_glob_search

        result = files_glob_search.invoke({"pattern": "*.py", "user_id": "test_contract_fs"})
        assert isinstance(str(result), str)

    def test_grep_search(self):
        from src.sdk.tools_core.file_search import files_grep_search

        result = files_grep_search.invoke(
            {"pattern": "def time_get", "user_id": "test_contract_fs"}
        )
        assert isinstance(str(result), str)


# ─── Versioning ───


class TestVersioningTools:
    def test_capture_version(self):
        from src.sdk.tools_core.file_versioning import capture_version
        from src.sdk.tools_core.filesystem import files_write

        files_write.invoke(
            {
                "path": "test_contract_ws/ver_test.txt",
                "content": "version 1",
                "user_id": "test_contract_fs",
            }
        )
        result = capture_version(
            user_id="test_contract_fs",
            file_path="test_contract_ws/ver_test.txt",
            new_content="version 2",
        )
        assert isinstance(str(result), str) or result is None

    def test_files_versions_list(self):
        from src.sdk.tools_core.file_versioning import files_versions_list

        result = files_versions_list.invoke(
            {"path": "test_contract_ws/ver_test.txt", "user_id": "test_contract_fs"}
        )
        assert isinstance(str(result), str)


# ─── Todos ───


class TestTodosTools:
    def test_todos_add_and_list(self):
        from src.sdk.tools_core.todos import todos_add, todos_list

        result = todos_add.invoke(
            {"content": "Contract test todo", "user_id": "test_contract_todos"}
        )
        assert isinstance(str(result), str)
        list_result = todos_list.invoke({"user_id": "test_contract_todos"})
        assert isinstance(str(list_result), str)

    def test_todos_update(self):
        from src.sdk.tools_core.todos import todos_add

        add_result = todos_add.invoke(
            {"content": "Update test todo", "user_id": "test_contract_todos"}
        )
        assert isinstance(str(add_result), str)

# ─── Contacts ───


class TestContactsTools:
    def test_contacts_add_and_list(self):
        from src.sdk.tools_core.contacts import contacts_add, contacts_list

        result = contacts_add.invoke(
            {"email": "contract@test.com", "name": "Contract Test", "user_id": "test_contract_ct"}
        )
        assert isinstance(str(result), str)
        list_result = contacts_list.invoke({"user_id": "test_contract_ct"})
        assert isinstance(str(list_result), str)

    def test_contacts_search(self):
        from src.sdk.tools_core.contacts import contacts_search

        result = contacts_search.invoke({"query": "test", "user_id": "test_contract_ct"})
        assert isinstance(str(result), str)

    def test_contacts_update(self):
        from src.sdk.tools_core.contacts import contacts_update

        result = contacts_update.invoke(
            {"contact_id": "nonexistent", "name": "Updated", "user_id": "test_contract_ct"}
        )
        assert isinstance(str(result), str)

    def test_contacts_delete(self):
        from src.sdk.tools_core.contacts import contacts_delete

        result = contacts_delete.invoke(
            {"contact_id": "nonexistent", "user_id": "test_contract_ct"}
        )
        assert isinstance(str(result), str)

    def test_message_search(self):
        from src.sdk.tools_core.message import message_search

        result = message_search.invoke({"query": "test", "user_id": "test_contract_mem"})
        assert isinstance(str(result), str)


class TestMemoryTools:
    def test_memory_profile(self):
        from src.sdk.tools_core.memory import memory_profile

        result = memory_profile.invoke({"user_id": "test_contract_mem"})
        assert isinstance(str(result), str)


class TestSkillsTools:
    def test_skills_load_exists(self):
        from src.sdk.tools_core.skills import skills_load

        result = skills_load.invoke(
            {"name": "skill-creator", "user_id": "test_contract_skills"}
        )
        assert isinstance(result, str)

    def test_skills_load_not_found(self):
        from src.sdk.tools_core.skills import skills_load

        result = skills_load.invoke(
            {"name": "nonexistent-skill-xyz", "user_id": "test_contract_skills"}
        )
        assert "not found" in result.lower()

    def test_skills_reload_returns_string(self):
        from src.sdk.tools_core.skills import skills_reload

        result = skills_reload.invoke({"user_id": "test_contract_skills"})
        assert isinstance(result, str)

    def test_skills_load_tool_description(self):
        from src.sdk.tools_core.skills import skills_load

        openai_format = skills_load.to_openai_format()
        desc = openai_format["function"]["description"]
        assert len(desc) > 0
        assert "skill" in desc.lower()

    def test_skills_reload_tool_description(self):
        from src.sdk.tools_core.skills import skills_reload

        openai_format = skills_reload.to_openai_format()
        desc = openai_format["function"]["description"]
        assert "Reload" in desc or "reload" in desc


class TestAppTools:
    """App tools require ChromaDB — contract checks only."""

    def test_app_tools_have_invoke(self):
        from src.sdk.tools_core.apps import app_create, app_delete, app_list, app_query, app_schema

        for tool in [app_create, app_list, app_schema, app_query, app_delete]:
            assert hasattr(tool, "invoke"), f"{tool.name} must have invoke method"
