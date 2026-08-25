"""Unit tests for filesystem tools - full coverage for all operations and path traversal protection."""

import shutil

import pytest

TEST_USER_ID = "test_filesystem_user"


@pytest.fixture
def user_workspace(tmp_path, monkeypatch):
    """Create a temporary user workspace with mocked get_paths."""
    from src.storage.paths import DataPaths

    def mock_get_paths(user_id="default_user", workspace_id="personal"):
        return DataPaths(data_root=str(tmp_path), user_id=user_id, workspace_id=workspace_id)

    monkeypatch.setattr("src.sdk.tools_core.filesystem.get_paths", mock_get_paths)
    monkeypatch.setattr("src.sdk.tools_core.file_search.get_paths", mock_get_paths)

    workspace = DataPaths(data_root=str(tmp_path), user_id=TEST_USER_ID).workspace_files_dir()
    yield workspace


class TestFilesList:
    """Tests for files_list tool."""

    def test_files_list_empty_directory(self, user_workspace):
        """Test files_list with empty directory."""
        from src.sdk.tools_core.filesystem import files_list

        result = files_list.invoke({"path": ".", "user_id": TEST_USER_ID})
        assert "Empty directory" in result or "" == result.strip()

    def test_files_list_with_files(self, user_workspace):
        """Test files_list returns files."""
        from src.sdk.tools_core.filesystem import files_list

        (user_workspace / "test.txt").write_text("content")
        result = files_list.invoke({"path": ".", "user_id": TEST_USER_ID})
        assert "test.txt" in result

    def test_files_list_directory_not_found(self, user_workspace):
        """Test files_list handles non-existent directory."""
        from src.sdk.tools_core.filesystem import files_list

        result = files_list.invoke({"path": "nonexistent", "user_id": TEST_USER_ID})
        assert "not found" in result.lower()


class TestFilesRead:
    """Tests for files_read tool."""

    def test_files_read_nonexistent(self, user_workspace):
        """Test files_read handles non-existent file."""
        from src.sdk.tools_core.filesystem import files_read

        result = files_read.invoke({"path": "nonexistent.txt", "user_id": TEST_USER_ID})
        assert "not found" in result.lower()

    def test_files_read_success(self, user_workspace):
        """Test files_read successfully reads file."""
        from src.sdk.tools_core.filesystem import files_read

        (user_workspace / "readme.txt").write_text("Hello World")
        result = files_read.invoke({"path": "readme.txt", "user_id": TEST_USER_ID})
        assert "Hello World" in result


class TestFilesWrite:
    """Tests for files_write tool."""

    def test_files_write_success(self, user_workspace):
        """Test files_write successfully writes file."""
        from src.sdk.tools_core.filesystem import files_write

        result = files_write.invoke(
            {"path": "newfile.txt", "content": "Test content", "user_id": TEST_USER_ID}
        )
        assert "success" in result.lower() or "wrote" in result.lower()
        assert (user_workspace / "newfile.txt").read_text() == "Test content"

    def test_files_write_to_directory_fails(self, user_workspace):
        """Test files_write cannot write to directory."""
        from src.sdk.tools_core.filesystem import files_write

        subdir = user_workspace / "subdir"
        subdir.mkdir()
        result = files_write.invoke({"path": "subdir", "content": "Test", "user_id": TEST_USER_ID})
        assert "cannot write to directory" in result.lower()


class TestFilesEdit:
    """Tests for files_edit tool."""

    def test_files_edit_success(self, user_workspace):
        """Test files_edit successfully edits file."""
        from src.sdk.tools_core.filesystem import files_edit

        (user_workspace / "editme.txt").write_text("Hello World")
        result = files_edit.invoke(
            {"path": "editme.txt", "old": "World", "new": "Python", "user_id": TEST_USER_ID}
        )
        assert "edited" in result.lower()
        assert (user_workspace / "editme.txt").read_text() == "Hello Python"

    def test_files_edit_text_not_found(self, user_workspace):
        """Test files_edit handles text not found."""
        from src.sdk.tools_core.filesystem import files_edit

        (user_workspace / "editme.txt").write_text("Hello World")
        result = files_edit.invoke(
            {"path": "editme.txt", "old": "NotFound", "new": "Python", "user_id": TEST_USER_ID}
        )
        assert "not found" in result.lower()


class TestFilesDelete:
    """Tests for files_delete tool."""

    def test_files_delete_nonexistent(self, user_workspace):
        """Test files_delete handles non-existent file."""
        from src.sdk.tools_core.filesystem import files_delete

        result = files_delete.invoke({"path": "nonexistent.txt", "user_id": TEST_USER_ID})
        assert "not found" in result.lower()

    def test_files_delete_success(self, user_workspace):
        """Test files_delete successfully deletes file."""
        from src.sdk.tools_core.filesystem import files_delete

        (user_workspace / "deleteme.txt").write_text("To be deleted")
        result = files_delete.invoke({"path": "deleteme.txt", "user_id": TEST_USER_ID})
        assert "deleted" in result.lower()
        assert not (user_workspace / "deleteme.txt").exists()


class TestFilesMkdir:
    """Tests for files_mkdir tool."""

    def test_files_mkdir_success(self, user_workspace):
        """Test files_mkdir successfully creates directory."""
        from src.sdk.tools_core.filesystem import files_mkdir

        result = files_mkdir.invoke({"path": "newdir", "user_id": TEST_USER_ID})
        assert "created" in result.lower() or "success" in result.lower()
        assert (user_workspace / "newdir").is_dir()

    def test_files_mkdir_already_exists(self, user_workspace):
        """Test files_mkdir handles existing directory."""
        from src.sdk.tools_core.filesystem import files_mkdir

        (user_workspace / "existing").mkdir()
        result = files_mkdir.invoke({"path": "existing", "user_id": TEST_USER_ID})
        assert "already exists" in result.lower()


class TestFilesRename:
    """Tests for files_rename tool."""

    def test_files_rename_success(self, user_workspace):
        """Test files_rename successfully renames file."""
        from src.sdk.tools_core.filesystem import files_rename

        (user_workspace / "oldname.txt").write_text("Content")
        result = files_rename.invoke(
            {"path": "oldname.txt", "new_name": "newname.txt", "user_id": TEST_USER_ID}
        )
        assert "renamed" in result.lower()
        assert (user_workspace / "newname.txt").exists()
        assert not (user_workspace / "oldname.txt").exists()

    def test_files_rename_nonexistent(self, user_workspace):
        """Test files_rename handles non-existent file."""
        from src.sdk.tools_core.filesystem import files_rename

        result = files_rename.invoke(
            {"path": "nonexistent.txt", "new_name": "new.txt", "user_id": TEST_USER_ID}
        )
        assert "not found" in result.lower()


class TestPathTraversalProtection:
    """Tests for path traversal protection."""

    def test_path_traversal_absolute_path_rejected(self, user_workspace):
        """Test that absolute paths are rejected."""
        from src.sdk.tools_core.filesystem import files_write

        result = files_write.invoke(
            {"path": "/etc/passwd", "content": "hacked", "user_id": TEST_USER_ID}
        )
        assert "relative paths only" in result.lower() or "error" in result.lower()

    def test_path_traversal_parent_directory_rejected(self, user_workspace):
        """Test that paths attempting to escape user directory are rejected."""
        from src.sdk.tools_core.filesystem import files_write

        result = files_write.invoke(
            {"path": "../etc/passwd", "content": "hacked", "user_id": TEST_USER_ID}
        )
        assert (
            "outside user directory" in result.lower()
            or "error" in result.lower()
            or "relative paths only" in result.lower()
        )

    def test_path_traversal_absolute_with_slash_rejected(self, user_workspace):
        """Test that paths starting with / are rejected."""
        from src.sdk.tools_core.filesystem import files_read

        result = files_read.invoke({"path": "/tmp/secret.txt", "user_id": TEST_USER_ID})
        assert "relative paths only" in result.lower() or "error" in result.lower()

    def test_path_traversal_sibling_directory_blocked(self, user_workspace):
        """Test that paths to sibling directories are blocked."""
        from src.sdk.tools_core.filesystem import files_read

        other_dir = user_workspace.parent / "other_user"
        other_dir.mkdir(parents=True, exist_ok=True)
        (other_dir / "secret.txt").write_text("secret")

        result = files_read.invoke({"path": "../other_user/secret.txt", "user_id": TEST_USER_ID})
        assert "outside user directory" in result.lower() or "error" in result.lower()

        shutil.rmtree(other_dir.parent)


class TestFilesGlobSearch:
    """Tests for files_glob_search tool."""

    def test_files_glob_search_python_files(self, user_workspace):
        """Test files_glob_search finds Python files."""
        from src.sdk.tools_core.file_search import files_glob_search

        (user_workspace / "test.py").write_text("print('hello')")
        (user_workspace / "main.py").write_text("print('main')")
        (user_workspace / "readme.txt").write_text("readme")

        result = files_glob_search.invoke({"pattern": "*.py", "path": ".", "user_id": TEST_USER_ID})
        assert "test.py" in result
        assert "main.py" in result

    def test_files_glob_search_no_matches(self, user_workspace):
        """Test files_glob_search with no matches."""
        from src.sdk.tools_core.file_search import files_glob_search

        (user_workspace / "readme.txt").write_text("readme")

        result = files_glob_search.invoke(
            {"pattern": "*.xyz", "path": ".", "user_id": TEST_USER_ID}
        )
        assert "no files" in result.lower() or result.strip() == ""


class TestFilesGrepSearch:
    """Tests for files_grep_search tool."""

    def test_files_grep_search_finds_content(self, user_workspace):
        """Test files_grep_search finds content in files."""
        from src.sdk.tools_core.file_search import files_grep_search

        (user_workspace / "todo.txt").write_text("TODO: fix bug")
        (user_workspace / "notes.txt").write_text("Meeting notes")

        result = files_grep_search.invoke({"pattern": "TODO", "path": ".", "user_id": TEST_USER_ID})
        assert "todo.txt" in result
        assert "TODO" in result

    def test_files_grep_search_no_matches(self, user_workspace):
        """Test files_grep_search with no matches."""
        from src.sdk.tools_core.file_search import files_grep_search

        (user_workspace / "notes.txt").write_text("Just notes")

        result = files_grep_search.invoke(
            {"pattern": "NONEXISTENT", "path": ".", "user_id": TEST_USER_ID}
        )
        assert "no matches" in result.lower() or result.strip() == ""
