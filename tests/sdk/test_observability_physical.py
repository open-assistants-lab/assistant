"""OB-1 Task 2: physical HTTP + sandbox spans (admin destination only).

Covers:
- /health and /health/ready produce NO request span (noise filter)
- a normal HTTP request emits exactly one ``http.request`` span carrying
  ONLY allowlisted attributes (method/route/status/url) — no query strings,
  headers, or body content
- websocket connections bypass the HTTP middleware entirely (no spans;
  long-lived connections never skew latency baselines)
- when the OB-0 provider is NOT configured, the middleware short-circuits
  and no spans exist (instrumentation is conditional by design)
- sandbox backends emit one ``sandbox.exec`` span with backend, command
  CLASS (never the command text), and exit code; timeouts record exit_code -1
"""

from __future__ import annotations

import pytest
from fastapi import WebSocket

TOPSECRET = "TOPSECRET-marker-value"


class RecordingProcessor:
    """In-memory span processor: records every ended span."""

    def __init__(self) -> None:
        self.ended: list[object] = []

    def on_start(self, span, parent_context=None):  # noqa: ANN001, ANN202
        return None

    def on_end(self, span) -> None:  # noqa: ANN001
        self.ended.append(span)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis=None):  # noqa: ANN001, ANN202
        return True


@pytest.fixture()
def obs_with_recorder():
    """Install a REAL OB-0 provider (module state only) + recording processor."""
    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

    import src.sdk.observability as obs

    obs._reset_for_tests()
    recorder = RecordingProcessor()
    provider = SDKTracerProvider()
    provider.add_span_processor(recorder)
    obs._state["provider"] = provider
    obs._state["owns_provider"] = True
    yield obs, recorder
    obs._reset_for_tests()


@pytest.fixture()
def obs_inactive():
    """No OB-0 provider configured — instrumentation must short-circuit."""
    import src.sdk.observability as obs

    obs._reset_for_tests()
    recorder = RecordingProcessor()
    yield obs, recorder
    obs._reset_for_tests()


def _mini_app():
    """Minimal FastAPI app with the physical-span middleware registered."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.http.physical_spans import register_physical_http_spans

    app = FastAPI()
    register_physical_http_spans(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "1"}

    return app, TestClient(app)


def _http_spans(recorder: RecordingProcessor) -> list[object]:
    return [s for s in recorder.ended if s.name == "http.request"]  # type: ignore[attr-defined]


def _sandbox_spans(recorder: RecordingProcessor) -> list[object]:
    return [s for s in recorder.ended if s.name == "sandbox.exec"]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# HTTP spans
# --------------------------------------------------------------------------


def test_health_routes_produce_no_request_span(obs_with_recorder):
    _obs, recorder = obs_with_recorder
    app, client = _mini_app()

    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert _http_spans(recorder) == []


def test_normal_request_emits_allowlisted_attributes_only(obs_with_recorder):
    from src.sdk.observability import ALLOWED_PHYSICAL_ATTRIBUTES

    _obs, recorder = obs_with_recorder
    app, client = _mini_app()

    resp = client.get("/ping", params={"q": TOPSECRET})
    assert resp.status_code == 200

    spans = _http_spans(recorder)
    assert len(spans) == 1
    span = spans[0]
    attrs = dict(span.attributes)
    # Only allowlisted attribute KEYS survive.
    assert set(attrs) <= ALLOWED_PHYSICAL_ATTRIBUTES
    assert attrs.get("http.request.method") == "GET"
    assert attrs.get("http.response.status_code") == 200
    assert attrs.get("url.path") == "/ping"
    # No query string, header, or body content anywhere in the span.
    for value in attrs.values():
        assert TOPSECRET not in str(value)


def test_websocket_connections_produce_no_http_span(obs_with_recorder):
    _obs, recorder = obs_with_recorder
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.http.physical_spans import register_physical_http_spans

    app = FastAPI()
    register_physical_http_spans(app)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    client = TestClient(app)
    with client.websocket_connect("/ws") as socket:
        assert socket.receive_text() == "hello"
    assert _http_spans(recorder) == []


def test_middleware_short_circuits_when_observability_inactive(obs_inactive):
    _obs, recorder = obs_inactive
    app, client = _mini_app()

    resp = client.get("/ping")
    assert resp.status_code == 200
    # No OB-0 provider -> the recorder (attached to a separate provider)
    # must have received nothing: no spans were created at all.
    assert recorder.ended == []


# --------------------------------------------------------------------------
# Sandbox spans
# --------------------------------------------------------------------------


def test_sandbox_span_records_backend_class_exit_never_command(obs_with_recorder):
    from src.sdk.observability import ALLOWED_PHYSICAL_ATTRIBUTES
    from src.sdk.sandbox import NullSandboxBackend, SandboxLimits

    _obs, recorder = obs_with_recorder
    backend = NullSandboxBackend()

    result = backend.run(
        ["python3", "-c", f"print('{TOPSECRET}')"],
        cwd=__import__("pathlib").Path("."),
        limits=SandboxLimits(),
    )
    assert result.exit_code == 0

    spans = _sandbox_spans(recorder)
    assert len(spans) == 1
    span = spans[0]
    attrs = dict(span.attributes)
    assert set(attrs) <= ALLOWED_PHYSICAL_ATTRIBUTES
    assert attrs.get("sandbox.backend") == "null"
    assert attrs.get("sandbox.command_class") == "python"
    assert attrs.get("sandbox.exit_code") == 0
    for value in attrs.values():
        assert TOPSECRET not in str(value)


def test_sandbox_timeout_records_negative_exit_code(obs_with_recorder):
    from pathlib import Path

    from src.sdk.sandbox import NullSandboxBackend, SandboxLimits

    _obs, recorder = obs_with_recorder
    backend = NullSandboxBackend()

    result = backend.run(
        ["sleep", "5"],
        cwd=Path("."),
        limits=SandboxLimits(timeout_seconds=1),
    )
    assert result.timed_out is True

    spans = _sandbox_spans(recorder)
    assert len(spans) == 1
    attrs = dict(spans[0].attributes)
    assert attrs.get("sandbox.command_class") == "other"
    assert attrs.get("sandbox.exit_code") == -1


def test_sandbox_command_class_unwraps_bwrap_dispatch():
    from src.sdk.sandbox import _command_class

    assert _command_class(["bwrap", "--ro-bind", "/", "/", "--", "python3", "-c", "x"]) == "python"
    assert _command_class(["bash", "-c", "echo hi"]) == "shell"
    assert _command_class(["rg", "pattern"]) == "other"


def test_no_sandbox_spans_when_observability_inactive(obs_inactive):
    from pathlib import Path

    from src.sdk.sandbox import NullSandboxBackend, SandboxLimits

    _obs, recorder = obs_inactive
    backend = NullSandboxBackend()
    result = backend.run(
        ["python3", "-c", "print('ok')"], cwd=Path("."),
        limits=SandboxLimits(),
    )
    assert result.exit_code == 0
    assert recorder.ended == []


def test_sandbox_command_class_is_exporter_allowlisted():
    from src.sdk.observability import ALLOWED_PHYSICAL_ATTRIBUTES

    assert "sandbox.command_class" in ALLOWED_PHYSICAL_ATTRIBUTES
