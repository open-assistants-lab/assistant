"""Tests for WebSocket suggestion and diff card accept/reject handlers."""

from src.http.suggestion_store import clear, get_suggestion, store_suggestion
from src.storage.paths import get_paths


class TestSuggestionAccept:
    def test_suggestion_accept_writes_file(self, client, test_user_id):
        clear()
        store_suggestion("sug_1", "hello world", "/test_sug.txt")
        paths = get_paths(test_user_id, workspace_id="personal")
        target_file = paths.workspace_files_dir() / "test_sug.txt"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("old content")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "suggestion_accept",
                "suggestion_id": "sug_1",
                "file_path": "test_sug.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "widget_refresh"
        assert target_file.read_text() == "hello world"
        assert get_suggestion("sug_1") is None

    def test_suggestion_accept_missing_suggestion(self, client, test_user_id):
        clear()

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "suggestion_accept",
                "suggestion_id": "nonexistent",
                "file_path": "test.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "diff_card_error"
        assert msg["code"] == "FILE_NOT_FOUND"
        assert "Suggestion not found" in msg["message"]

    def test_suggestion_accept_outside_ea_root(self, client, test_user_id):
        clear()
        store_suggestion("sug_2", "content", "/etc/passwd")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "suggestion_accept",
                "suggestion_id": "sug_2",
                "file_path": "/etc/passwd",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "diff_card_error"
        assert msg["code"] == "FILE_NOT_FOUND"
        assert get_suggestion("sug_2") is None


class TestSuggestionReject:
    def test_suggestion_reject_removes_suggestion(self, client, test_user_id):
        clear()
        store_suggestion("sug_1", "content", "/test.txt")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "suggestion_reject",
                "suggestion_id": "sug_1",
                "file_path": "/test.txt",
                "user_id": test_user_id,
            })
            # Server sends no response — send ping to get pong back
            ws.send_json({"type": "ping"})
            msg = ws.receive_json()

        assert msg["type"] == "pong"
        assert get_suggestion("sug_1") is None


class TestSuggestionAcceptAll:
    def test_suggestion_accept_all_writes_file(self, client, test_user_id):
        clear()
        store_suggestion("sug_a", "version_a", "test_multi.txt")
        store_suggestion("sug_b", "version_b", "test_multi.txt")
        paths = get_paths(test_user_id, workspace_id="personal")
        target_file = paths.workspace_files_dir() / "test_multi.txt"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("original")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "suggestion_accept_all",
                "file_path": "test_multi.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "widget_refresh"
        assert target_file.read_text() == "version_b"
        assert get_suggestion("sug_a") is None
        assert get_suggestion("sug_b") is None

    def test_suggestion_accept_all_outside_root(self, client, test_user_id):
        clear()
        store_suggestion("sug_a", "content", "/etc/hosts")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "suggestion_accept_all",
                "file_path": "/etc/hosts",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "diff_card_error"
        assert msg["code"] == "FILE_NOT_FOUND"


class TestDiffCardAccept:
    def test_diff_card_accept_applies_diff(self, client, test_user_id):
        clear()
        diff_content = " line1\n-old_line\n+new_line\n line3"
        store_suggestion("diff_1", diff_content, "/test_diff.txt")
        paths = get_paths(test_user_id, workspace_id="personal")
        target_file = paths.workspace_files_dir() / "test_diff.txt"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("line1\nold_line\nline3")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "diff_card_accept",
                "suggestion_id": "diff_1",
                "file_path": "test_diff.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "widget_refresh"
        assert target_file.read_text() == "line1\nnew_line\nline3"
        assert get_suggestion("diff_1") is None

    def test_diff_card_accept_context_mismatch(self, client, test_user_id):
        clear()
        diff_content = " line1\n-old_line\n+new_line\n line3"
        store_suggestion("diff_2", diff_content, "/test_mismatch.txt")
        paths = get_paths(test_user_id, workspace_id="personal")
        target_file = paths.workspace_files_dir() / "test_mismatch.txt"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("line1\nEDITED_line\nline3")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "diff_card_accept",
                "suggestion_id": "diff_2",
                "file_path": "test_mismatch.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "diff_card_error"
        assert msg["code"] == "DIFF_CONFLICT"
        assert get_suggestion("diff_2") is None

    def test_diff_card_accept_missing_file(self, client, test_user_id):
        clear()
        store_suggestion("diff_3", "content", "/nonexistent.txt")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "diff_card_accept",
                "suggestion_id": "diff_3",
                "file_path": "nonexistent.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "diff_card_error"
        assert msg["code"] == "FILE_NOT_FOUND"
        assert get_suggestion("diff_3") is None

    def test_diff_card_accept_missing_suggestion(self, client, test_user_id):
        clear()

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "diff_card_accept",
                "suggestion_id": "nonexistent",
                "file_path": "test.txt",
                "user_id": test_user_id,
            })
            msg = ws.receive_json()

        assert msg["type"] == "diff_card_error"
        assert msg["code"] == "FILE_NOT_FOUND"


class TestDiffCardReject:
    def test_diff_card_reject_removes_suggestion(self, client, test_user_id):
        clear()
        store_suggestion("diff_1", "content", "/test.txt")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "diff_card_reject",
                "suggestion_id": "diff_1",
                "file_path": "/test.txt",
                "user_id": test_user_id,
            })
            ws.send_json({"type": "ping"})
            msg = ws.receive_json()

        assert msg["type"] == "pong"
        assert get_suggestion("diff_1") is None
