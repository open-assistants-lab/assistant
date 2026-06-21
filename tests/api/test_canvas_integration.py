"""Integration tests for Canvas 2.0 end-to-end with real agent loop.

Exercises run_sdk_agent_stream → AgentLoop with MockProvider (no real LLM).
Covers: session isolation, canvas blocks, diff cards, suggestions,
directives, combined fences, and full co-edit flow.
"""

import pytest

from src.http.suggestion_store import clear as clear_suggestions
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message, StreamChunk
from src.sdk.providers.base import LLMProvider, ModelCost, ModelInfo


class MockProvider(LLMProvider):
    """Predictable mock provider for agent loop integration tests."""

    def __init__(self, responses: list[Message] | None = None):
        self.responses = responses or []
        self._call_count = 0

    async def chat(self, messages, tools=None, model=None, **kwargs):
        if self._call_count < len(self.responses):
            response = self.responses[self._call_count]
            self._call_count += 1
            return response
        return Message.assistant(content="No more responses")

    async def chat_stream_impl(self, messages, tools, model, **kwargs):
        if self.responses:
            idx = min(self._call_count, len(self.responses) - 1)
            resp = self.responses[idx]
            self._call_count += 1
            if resp.content:
                yield StreamChunk.text_delta(content=resp.content)
            if resp.tool_calls:
                for tc in resp.tool_calls:
                    yield StreamChunk.tool_input_start(
                        tool=tc.name, call_id=tc.id, args=tc.arguments
                    )
                    yield StreamChunk.tool_input_end(tool=tc.name, call_id=tc.id)
            yield StreamChunk.done(content=resp.content or "")
        else:
            yield StreamChunk.done(content="")

    def chat_stream(self, messages, tools=None, model=None, **kwargs):
        return self.chat_stream_impl(messages, tools, model, **kwargs)

    def count_tokens(self, text: str, model: str | None = None) -> int:
        return max(1, len(text) // 4)

    def get_model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            name=model or "mock",
            provider="mock",
            input_price=0,
            output_price=0,
            context_length=8192,
        )

    def get_model_cost(self, model: str) -> ModelCost:
        return ModelCost(input_price_per_million=0, output_price_per_million=0)

    @property
    def provider_id(self) -> str:
        return "mock"


def _make_mock_run_stream(response: str):
    """Create a run_sdk_agent_stream replacement that runs AgentLoop with MockProvider.

    Returns an async generator function that creates a real AgentLoop with
    MockProvider and runs loop.run_stream(messages).
    """

    async def _run(user_id, messages, **kwargs):
        provider = MockProvider(responses=[Message.assistant(content=response)])
        loop = AgentLoop(
            provider=provider,
            tools=[],
            system_prompt="You are a test assistant.",
            middlewares=[],
            run_config=RunConfig(max_llm_calls=5),
        )
        async for chunk in loop.run_stream(messages):
            yield chunk

    return _run


@pytest.fixture(autouse=True)
def clear_state():
    """Clear suggestion store before each test."""
    clear_suggestions()
    yield


def _collect_events(ws, timeout: float = 5.0) -> list[dict]:
    """Collect all WS events until done or error."""
    events = []
    while True:
        msg = ws.receive_json()
        events.append(msg)
        if msg["type"] in ("done", "error"):
            break
    return events


TARGET = "src.http.routers.ws.run_sdk_agent_stream"


class TestWidgetServing:
    """Widget endpoints return valid HTML."""

    def test_email_widget(self, client):
        r = client.get("/widget/email")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "<html" in r.text.lower()

    def test_todos_widget(self, client):
        r = client.get("/widget/todos")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_contacts_widget(self, client):
        r = client.get("/widget/contacts")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_files_widget(self, client):
        r = client.get("/widget/files")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_invalid_widget_404(self, client):
        r = client.get("/widget/nonexistent")
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()


class TestEditorEndpoint:
    """GET /editor serves ProseMirror editor."""

    def test_editor_returns_html(self, client):
        r = client.get("/editor", params={"file_path": "test.py", "content": "hello"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_editor_empty_params(self, client):
        r = client.get("/editor")
        assert r.status_code == 200


class TestSessionManagement:
    """WS session_id flows through agent loop into message store."""

    def test_session_id_persists(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream("reply to session test"),
        )

        with client.websocket_connect(
            "/ws/conversation?session_id=integ-session-1"
        ) as ws:
            ws.send_json({
                "type": "user_message",
                "content": "hello from session test",
                "user_id": test_user_id,
            })
            _collect_events(ws)

        r = client.get(
            "/conversation",
            params={"user_id": test_user_id, "limit": 50},
        )
        assert r.status_code == 200
        messages = r.json()["messages"]
        contents = [m["content"] for m in messages]
        assert "hello from session test" in contents
        assert "reply to session test" in contents


class TestCanvasBlocks:
    """Canvas HTML fences via real agent loop."""

    def test_canvas_block_emits_update(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                'Here is a canvas:\n```html:canvas\n<h1>Hello</h1>\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "show canvas",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        canvas_events = [e for e in events if e["type"] == "canvas_update"]
        assert len(canvas_events) >= 1
        assert canvas_events[0]["action"] == "create"

    def test_multiple_canvas_blocks(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                'First:\n```html:canvas\n<p>A</p>\n```\n\n'
                'Second:\n```html:canvas\n<p>B</p>\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "two canvases",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        canvas_events = [e for e in events if e["type"] == "canvas_update"]
        assert len(canvas_events) == 2

    def test_editor_fence(self, client, monkeypatch, test_user_id):
        """html:editor fence emits canvas_update with editor surface."""
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '```html:editor\nfilePath:test.py\n---\nprint("hello")\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "open editor",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        canvas_events = [e for e in events if e["type"] == "canvas_update"]
        assert len(canvas_events) >= 1
        assert any("editor" in e.get("surface_id", "") for e in canvas_events)


class TestDiffCards:
    """Diff fences via real agent loop."""

    def test_diff_emits_card(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                'Change:\n```diff:src/main.py\n-old\n+new\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "update main.py",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        diff_events = [e for e in events if e["type"] == "diff_card"]
        assert len(diff_events) == 1
        assert diff_events[0]["file_path"] == "src/main.py"

    def test_multiple_diff_blocks(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                'File1:\n```diff:a.py\n-a\n+b\n```\n\n'
                'File2:\n```diff:b.py\n-c\n+d\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "update two files",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        diff_events = [e for e in events if e["type"] == "diff_card"]
        assert len(diff_events) == 2
        assert diff_events[0]["file_path"] == "a.py"
        assert diff_events[1]["file_path"] == "b.py"


class TestSuggestions:
    """Suggestion fences via real agent loop."""

    def test_suggestion_emits_push(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '```suggestion\nfilePath:src/main.py\n---\nnew content\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "suggest edit",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        sug_events = [e for e in events if e["type"] == "suggestion_push"]
        assert len(sug_events) == 1
        assert sug_events[0]["file_path"] == "src/main.py"


class TestDirectives:
    """Agent directives via real agent loop."""

    def test_switch_tab(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream("Switching [SWITCH_TAB:email] now"),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "switch to email",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        tab_events = [e for e in events if e["type"] == "tab_update"]
        assert len(tab_events) == 1
        assert tab_events[0]["tab"] == "email"

        done_event = [e for e in events if e["type"] == "done"][0]
        assert "[SWITCH_TAB:" not in done_event.get("response", "")

    def test_widget_refresh_param(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream("Refresh [WIDGET_REFRESH:todos]"),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "refresh todos",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        refresh_events = [e for e in events if e["type"] == "widget_refresh"]
        assert len(refresh_events) == 1
        assert refresh_events[0]["tab"] == "todos"

    def test_widget_refresh_all(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream("Refresh all [WIDGET_REFRESH]"),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "refresh all",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        refresh_events = [e for e in events if e["type"] == "widget_refresh"]
        assert len(refresh_events) == 1
        assert refresh_events[0]["tab"] == "all"

    def test_new_session(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream("New [NEW_SESSION] created"),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "new session plz",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        session_events = [e for e in events if e["type"] == "new_session"]
        assert len(session_events) == 1

    def test_open_editor(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream("Open [OPEN_EDITOR:src/app.py] now"),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "open editor",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        editor_events = [e for e in events if e["type"] == "open_editor"]
        assert len(editor_events) == 1
        assert editor_events[0]["file_path"] == "src/app.py"
        assert editor_events[0]["tab"] == "notes"

    def test_multiple_directives_stripped(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                "Go to [SWITCH_TAB:files] and refresh [WIDGET_REFRESH:files]"
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "files tab",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        types = [e["type"] for e in events]
        assert types.count("tab_update") == 1
        assert types.count("widget_refresh") == 1

        done_event = [e for e in events if e["type"] == "done"][0]
        response = done_event.get("response", "")
        assert "[SWITCH_TAB:" not in response
        assert "[WIDGET_REFRESH:" not in response


class TestCombinedFences:
    """Multiple fence types in one response via real agent loop."""

    def test_diff_and_suggestion_together(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                'Diff:\n```diff:src/main.py\n-old\n+new\n```\n'
                'Suggestion:\n```suggestion\nfilePath:src/other.py\n---\nother content\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "edit files",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        assert len([e for e in events if e["type"] == "diff_card"]) == 1
        assert len([e for e in events if e["type"] == "suggestion_push"]) == 1

    def test_all_fence_types(self, client, monkeypatch, test_user_id):
        """Canvas + diff + suggestion + directives all in one response."""
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '[SWITCH_TAB:email]\n'
                '```html:canvas\n<p>Canvas</p>\n```\n'
                '```diff:src/main.py\n-old\n+new\n```\n'
                '```suggestion\nfilePath:src/other.py\n---\ncontent\n```'
            ),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "do everything",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

        types = [e["type"] for e in events]
        assert "tab_update" in types
        assert "canvas_update" in types
        assert "diff_card" in types
        assert "suggestion_push" in types
        assert "done" in types

        done_event = [e for e in events if e["type"] == "done"][0]
        response = done_event.get("response", "")
        assert "[SWITCH_TAB:" not in response
        assert "```" not in response


class TestCoEditFlow:
    """Full co-edit: agent proposes → user accepts → file written."""

    def test_accept_diff_card_writes_file(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '```diff:test_coedit_integ.txt\n line1\n-old_line\n+new_line\n line3\n```'
            ),
        )

        target = self._workspace_file(test_user_id, "test_coedit_integ.txt")
        target.write_text("line1\nold_line\nline3")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "edit file",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

            diff_events = [e for e in events if e["type"] == "diff_card"]
            assert len(diff_events) == 1
            sug_id = diff_events[0]["suggestion_id"]
            fpath = diff_events[0]["file_path"]

            ws.send_json({
                "type": "diff_card_accept",
                "suggestion_id": sug_id,
                "file_path": fpath,
                "user_id": test_user_id,
            })
            ack = ws.receive_json()

        assert ack["type"] == "widget_refresh"
        assert ack["tab"] == "files"
        assert target.read_text() == "line1\nnew_line\nline3"

    def test_reject_diff_card(self, client, monkeypatch, test_user_id):
        from src.http.suggestion_store import get_suggestion

        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream('```diff:test_reject.txt\n-old\n+new\n```'),
        )

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "suggest diff",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

            diff_events = [e for e in events if e["type"] == "diff_card"]
            assert len(diff_events) == 1
            sug_id = diff_events[0]["suggestion_id"]
            assert get_suggestion(sug_id) is not None

            ws.send_json({
                "type": "diff_card_reject",
                "suggestion_id": sug_id,
                "file_path": "test_reject.txt",
                "user_id": test_user_id,
            })

        assert get_suggestion(sug_id) is None

    def _workspace_file(self, test_user_id, filename):
        """Get path within user files dir."""
        from src.storage.paths import get_paths
        paths = get_paths(test_user_id)
        wf_dir = paths.user_files_dir()
        wf_dir.mkdir(parents=True, exist_ok=True)
        return wf_dir / filename

    def test_accept_suggestion_writes_file(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '```suggestion\nfilePath:test_sug_integ.txt\n---\nnew file content\n```'
            ),
        )

        target = self._workspace_file(test_user_id, "test_sug_integ.txt")
        target.write_text("old content")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "suggest",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

            sug_events = [e for e in events if e["type"] == "suggestion_push"]
            assert len(sug_events) == 1
            sug_id = sug_events[0]["suggestion_id"]
            fpath = sug_events[0]["file_path"]

            ws.send_json({
                "type": "suggestion_accept",
                "suggestion_id": sug_id,
                "file_path": fpath,
                "user_id": test_user_id,
            })
            ack = ws.receive_json()

        assert ack["type"] == "widget_refresh"
        assert ack["tab"] == "files"
        assert target.read_text() == "new file content"

    def test_accept_all_suggestions(self, client, monkeypatch, test_user_id):
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '```suggestion\nfilePath:test_accept_all.txt\n---\nfinal version\n```'
            ),
        )

        from src.http.suggestion_store import store_suggestion

        store_suggestion("preload", "earlier version", "test_accept_all.txt")
        target = self._workspace_file(test_user_id, "test_accept_all.txt")
        target.write_text("initial")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "edit",
                "user_id": test_user_id,
            })
            _collect_events(ws)

            ws.send_json({
                "type": "suggestion_accept_all",
                "file_path": "test_accept_all.txt",
                "user_id": test_user_id,
            })
            ack = ws.receive_json()

        assert ack["type"] == "widget_refresh"
        assert ack["tab"] == "files"
        assert target.read_text() == "final version"

    def test_diff_card_context_mismatch(self, client, monkeypatch, test_user_id):
        """Diff card accept fails gracefully when file was edited."""
        monkeypatch.setattr(
            TARGET,
            _make_mock_run_stream(
                '```diff:test_mismatch.txt\n line1\n-old_line\n+new_line\n line3\n```'
            ),
        )

        target = self._workspace_file(test_user_id, "test_mismatch.txt")
        target.write_text("line1\nEDITED_line\nline3")

        with client.websocket_connect("/ws/conversation") as ws:
            ws.send_json({
                "type": "user_message", "content": "edit file",
                "user_id": test_user_id,
            })
            events = _collect_events(ws)

            diff_events = [e for e in events if e["type"] == "diff_card"]
            assert len(diff_events) == 1
            sug_id = diff_events[0]["suggestion_id"]

            ws.send_json({
                "type": "diff_card_accept",
                "suggestion_id": sug_id,
                "file_path": "test_mismatch.txt",
                "user_id": test_user_id,
            })
            error = ws.receive_json()

        assert error["type"] == "diff_card_error"
        assert error["code"] == "DIFF_CONFLICT"
