"""Unit tests for todos tools - full coverage for all CRUD operations."""

from unittest.mock import patch

import src.sdk.tools_core.todos_storage  # noqa: F401 - ensure submodule is importable for patches

TEST_USER_ID = "test_todos_user"


class TestTodosList:
    """Tests for todos_list tool."""

    def test_todos_list_empty(self):
        """Test todos_list with no todos."""
        from src.sdk.tools_core.todos import todos_list

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.get_todos.return_value = []
            mock_storage.todos_count.return_value = {
                "total": 0,
                "pending": 0,
                "in_progress": 0,
                "completed": 0,
            }
            result = todos_list.invoke({"user_id": TEST_USER_ID})
            assert "No todos" in result or "0 total" in result

    def test_todos_list_with_todos(self):
        """Test todos_list returns todos grouped by status."""
        from src.sdk.tools_core.todos import todos_list

        mock_todos = [
            {"id": "1", "content": "Task 1", "status": "pending"},
            {"id": "2", "content": "Task 2", "status": "in_progress"},
            {"id": "3", "content": "Task 3", "status": "completed"},
        ]
        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.get_todos.return_value = mock_todos
            mock_storage.todos_count.return_value = {
                "total": 3,
                "pending": 1,
                "in_progress": 1,
                "completed": 1,
            }
            result = todos_list.invoke({"user_id": TEST_USER_ID})
            assert "Task 1" in result
            assert "Task 2" in result


class TestTodosAdd:
    """Tests for todos_add tool."""

    def test_todos_add_requires_content(self):
        """Test todos_add requires content."""
        from src.sdk.tools_core.todos import todos_add

        result = todos_add.invoke({"user_id": TEST_USER_ID})
        assert "Error" in result

    def test_todos_add_success(self):
        """Test todos_add successfully adds todo."""
        from src.sdk.tools_core.todos import todos_add

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.add_todo.return_value = {
                "id": "new123",
                "content": "New task",
                "status": "pending",
            }
            result = todos_add.invoke({"user_id": TEST_USER_ID, "content": "New task"})
            assert "added" in result.lower() or "new123" in result

    def test_todos_add_with_priority(self):
        """Test todos_add with priority."""
        from src.sdk.tools_core.todos import todos_add

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.add_todo.return_value = {
                "id": "p123",
                "content": "High priority",
                "status": "pending",
            }
            result = todos_add.invoke(
                {"user_id": TEST_USER_ID, "content": "High priority", "priority": 5}
            )
            assert "added" in result.lower()


class TestTodosUpdate:
    """Tests for todos_update tool."""

    def test_todos_update_requires_todo_id(self):
        """Test todos_update requires todo_id."""
        from src.sdk.tools_core.todos import todos_update

        result = todos_update.invoke({"user_id": TEST_USER_ID})
        assert "Error" in result or "todo_id" in result.lower()

    def test_todos_update_success(self):
        """Test todos_update successfully updates todo."""
        from src.sdk.tools_core.todos import todos_update

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.update_todo.return_value = {"success": True}
            result = todos_update.invoke(
                {
                    "user_id": TEST_USER_ID,
                    "todo_id": "123",
                    "content": "Updated task",
                    "status": "completed",
                }
            )
            assert "updated" in result.lower() or "success" in result.lower()

    def test_todos_update_not_found(self):
        """Test todos_update handles not found."""
        from src.sdk.tools_core.todos import todos_update

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.update_todo.return_value = {"success": False, "error": "Not found"}
            result = todos_update.invoke({"user_id": TEST_USER_ID, "todo_id": "nonexistent"})
            assert "Error" in result


class TestTodosDelete:
    """Tests for todos_delete tool."""

    def test_todos_delete_requires_todo_id(self):
        """Test todos_delete requires todo_id."""
        from src.sdk.tools_core.todos import todos_delete

        result = todos_delete.invoke({"user_id": TEST_USER_ID})
        assert "Error" in result or "todo_id" in result.lower()

    def test_todos_delete_success(self):
        """Test todos_delete successfully deletes todo."""
        from src.sdk.tools_core.todos import todos_delete

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.delete_todo.return_value = {"success": True}
            result = todos_delete.invoke({"user_id": TEST_USER_ID, "todo_id": "123"})
            assert "deleted" in result.lower() or "success" in result.lower()

    def test_todos_delete_not_found(self):
        """Test todos_delete handles not found."""
        from src.sdk.tools_core.todos import todos_delete

        with patch("src.sdk.tools_core.todos_storage") as mock_storage:
            mock_storage.delete_todo.return_value = {"success": False, "error": "Not found"}
            result = todos_delete.invoke({"user_id": TEST_USER_ID, "todo_id": "nonexistent"})
            assert "Error" in result


class TestTodosStorageFunctions:
    """Tests for todos storage functions (AgentsDB-based)."""

    def test_add_todo(self):
        """Test adding a todo."""
        from src.sdk.tools_core.todos_storage import add_todo

        result = add_todo("test_user", "Test todo", status="pending", source="test")
        assert "id" in result
        assert result["content"] == "Test todo"
