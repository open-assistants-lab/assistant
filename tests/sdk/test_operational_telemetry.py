"""OB-1 Task 2: operational HTTP + sandbox spans (admin destination only).

Covers:
- /health and /health/ready produce NO request span (noise filter)
- a normal HTTP request emits exactly one ``http.request`` span carrying
  ONLY allowlisted attributes (method/route/status/url) — no query strings,
  headers, or body content
- websocket connections bypass the HTTP middleware entirely (no spans;
  long-lived connections never skew latency baselines)
- NOTE on SSE/streaming: those requests DO produce spans, but the span
  measures CONNECTION LIFETIME (until the stream closes). They must be
  excluded/flagged from request-latency baselines at query time (OB-3);
  these tests only assert the WS bypass, not SSE handling
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
    obs._state["operational_telemetry_provider"] = provider
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
    """Minimal FastAPI app with the operational-telemetry middleware registered."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.http.operational_telemetry import register_operational_http_spans

    app = FastAPI()
    register_operational_http_spans(app)

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
    from src.sdk.observability import ALLOWED_OPERATIONAL_ATTRIBUTES

    _obs, recorder = obs_with_recorder
    app, client = _mini_app()

    resp = client.get("/ping", params={"q": TOPSECRET})
    assert resp.status_code == 200

    spans = _http_spans(recorder)
    assert len(spans) == 1
    span = spans[0]
    attrs = dict(span.attributes)
    # Only allowlisted attribute KEYS survive.
    assert set(attrs) <= ALLOWED_OPERATIONAL_ATTRIBUTES
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

    from src.http.operational_telemetry import register_operational_http_spans

    app = FastAPI()
    register_operational_http_spans(app)

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
    from src.sdk.observability import ALLOWED_OPERATIONAL_ATTRIBUTES
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
    assert set(attrs) <= ALLOWED_OPERATIONAL_ATTRIBUTES
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
    from src.sdk.observability import ALLOWED_OPERATIONAL_ATTRIBUTES

    assert "sandbox.command_class" in ALLOWED_OPERATIONAL_ATTRIBUTES


# --------------------------------------------------------------------------
# Trace fidelity (TRACE_VERDICT 2026-10-02): trees must not dangle, spans must
# bracket the real operation, kinds must describe the direction.
# --------------------------------------------------------------------------
class _SlowClient:
    """Stand-in provider httpx client: post() takes a real 50ms."""

    def __init__(self) -> None:
        self._http_client = self

    async def post(self, url: str, **kwargs):  # noqa: ANN001, ANN202
        import asyncio

        await asyncio.sleep(0.05)
        return type("R", (), {"status_code": 200})()

    def stream(self, method: str, url: str, **kwargs):  # noqa: ANN001, ANN202
        import asyncio

        class _S:
            async def __aenter__(self):  # noqa: ANN001, ANN202
                await asyncio.sleep(0.02)
                return type("R", (), {"status_code": 200})()

            async def __aexit__(self, *exc):  # noqa: ANN001, ANN202
                await asyncio.sleep(0.02)
                return False

        return _S()

    async def aclose(self):  # noqa: ANN202
        return None


def test_operational_span_never_dangles_under_foreign_parent(obs_with_recorder):
    """A semantic (foreign-pipeline) span as current context must never become
    the stored parent of an operational span — it is never exported by the
    operational pipeline, so the reference would dangle (verdict finding 1)."""
    import asyncio

    from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider

    obs, recorder = obs_with_recorder

    foreign_provider = SDKTracerProvider()
    foreign_tracer = foreign_provider.get_tracer("langfuse")  # semantic-like

    async def scenario():
        with foreign_tracer.start_as_current_span("semantic.root") as fs:
            with obs.operational_telemetry_span("db.query") as op_span:
                assert op_span is not None
            return fs

    fs = asyncio.run(scenario())
    raw = [s for s in recorder.ended if s.name == "db.query"]
    assert raw, "operational span was not exported"
    # At creation: inherits the semantic trace id (the join key) and the
    # natural parent, tagged so the exporter knows the parent is
    # never storable in this pipeline.
    assert raw[0].context.trace_id == fs.context.trace_id
    assert raw[0].parent.span_id == fs.context.span_id
    assert raw[0].attributes["operational.detached"] is True
    # At the destination (what the exporter stores): parent zeroed, trace
    # id preserved — no dangling reference, join key intact.
    exporter = obs.FilteringSpanExporter(delegate=None)
    stored = exporter._filtered_copy(raw[0])
    assert stored.parent.span_id == 0, "stored parent must not dangle"
    assert stored.context.trace_id == fs.context.trace_id


def test_operational_spans_nest_under_operational_parent(obs_with_recorder):
    """Within the operational pipeline the tree must form: a span opened
    inside another operational span is its child (verdict finding 1)."""
    import asyncio

    obs, recorder = obs_with_recorder

    async def scenario():
        with obs.operational_telemetry_span("http.request") as root:
            with obs.operational_telemetry_span("db.query") as child:
                assert child is not None
            return root

    root = asyncio.run(scenario())
    child = [s for s in recorder.ended if s.name == "db.query"]
    assert child and child[0].parent.span_id == root.context.span_id


def test_http_client_span_brackets_the_real_operation(obs_with_recorder):
    """Post-hoc spans (real duration in an attribute, µs span lifetime) made
    waterfalls lie. The span must bracket the call (verdict finding 2)."""
    import asyncio

    obs, recorder = obs_with_recorder
    provider = type("P", (), {})()
    provider._http_client = _SlowClient()
    obs.instrument_provider_http(provider)

    async def scenario():
        return await provider._http_client.post("https://api.example.com/v1/x")

    asyncio.run(scenario())
    spans = [s for s in recorder.ended if s.name == "http.client"]
    assert spans, "no http.client span exported"
    dur_ms = (spans[0].end_time - spans[0].start_time) / 1e6
    assert dur_ms >= 45.0, f"http.client span lasted {dur_ms:.3f}ms — post-hoc, not bracketing"
    assert spans[0].attributes["duration_ms"] >= 45.0
    assert spans[0].attributes["http.response.status_code"] == 200


def test_http_client_stream_span_brackets_lifecycle(obs_with_recorder):
    """The stream adapter must open on __aenter__ and close on __aexit__ so
    the span covers connect→close, not the emit call afterwards."""
    import asyncio

    obs, recorder = obs_with_recorder
    provider = type("P", (), {})()
    provider._http_client = _SlowClient()
    obs.instrument_provider_http(provider)

    async def scenario():
        async with provider._http_client.stream("POST", "https://api.example.com/v1/x"):
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    spans = [s for s in recorder.ended if s.name == "http.client"]
    assert spans, "no stream http.client span exported"
    dur_ms = (spans[0].end_time - spans[0].start_time) / 1e6
    assert dur_ms >= 45.0, f"stream span lasted {dur_ms:.3f}ms — emitted post-hoc"


def test_db_query_span_brackets_the_real_operation(obs_with_recorder):
    """Same finding 2 for db.query: the span must wrap execute(), not record
    it afterwards."""
    import time as _time

    obs, recorder = obs_with_recorder

    class _SlowConn:
        def execute(self, sql, *args):  # noqa: ANN001, ANN202
            _time.sleep(0.03)
            return []

    proxy = obs.instrument_sqlite_connection(_SlowConn())
    proxy.execute("select 1")
    spans = [s for s in recorder.ended if s.name == "db.query"]
    assert spans, "no db.query span exported"
    dur_ms = (spans[0].end_time - spans[0].start_time) / 1e6
    assert dur_ms >= 25.0, f"db.query span lasted {dur_ms:.3f}ms — post-hoc"


def test_span_kinds_describe_direction(obs_with_recorder):
    """SERVER for inbound, CLIENT for outbound (verdict observation: all
    spans were SpanKind.Internal)."""
    from opentelemetry.trace import SpanKind

    obs, recorder = obs_with_recorder
    app, client = _mini_app()

    @app.get("/kinds")
    async def kinds() -> dict[str, str]:
        provider = type("P", (), {})()
        provider._http_client = _SlowClient()
        obs.instrument_provider_http(provider)
        await provider._http_client.post("https://api.example.com/v1/y")
        return {"ok": "1"}

    client.get("/kinds")
    server_spans = [s for s in recorder.ended if s.name == "http.request"]
    client_spans = [s for s in recorder.ended if s.name == "http.client"]
    assert server_spans and server_spans[0].kind == SpanKind.SERVER
    assert client_spans and client_spans[0].kind == SpanKind.CLIENT


def test_operational_span_error_sets_error_status(obs_with_recorder):
    """Failure paths must surface: status ERROR (the filter strips the
    description, not the code)."""
    import asyncio

    obs, recorder = obs_with_recorder

    async def scenario():
        try:
            with obs.operational_telemetry_span("db.query"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

    asyncio.run(scenario())
    spans = [s for s in recorder.ended if s.name == "db.query"]
    assert spans
    assert spans[0].status.status_code.name == "ERROR"


def test_operational_resource_carries_service_version(obs_with_recorder):
    """Verdict observation: no service version for attribution. The resource
    must carry service.version (allowlisted) when the package reports one."""
    obs, _ = obs_with_recorder
    version = obs._release_version()
    if not version:
        import pytest

        pytest.skip("no installed package version in this environment")
    resource = obs._operational_resource()
    assert dict(resource.attributes).get("service.version") == version
    exporter = obs.FilteringSpanExporter(delegate=None)
    filtered = exporter._filtered_resource(resource)
    assert dict(filtered.attributes).get("service.version") == version
