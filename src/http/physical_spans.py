"""OB-1 Task 2: physical HTTP request spans (admin destination only).

A pure-ASGI middleware so an unconfigured deployment pays one dict lookup
per request and nothing more. Behavior:

- only ``http`` scopes are instrumented; websocket connections bypass the
  middleware entirely (long-lived connections never enter latency pools)
- **SSE/streaming responses ARE instrumented, and their spans measure
  CONNECTION LIFETIME** (span ends when the stream closes, not when the
  first byte is sent). Such spans must be EXCLUDED — or explicitly
  flagged — from request-latency baseline calculations (R-PERF-2 §noise:
  "long-lived WS/SSE connection spans … excluded from p95 latency
  baselines"); exclusion itself is applied at query/alert time (OB-3)
- health probes (``/health``, ``/health/ready``) are excluded (R-PERF-2
  noise filter: ~17k spans/day otherwise)
- attributes are restricted to the physical allowlist: method, route,
  status code, path, scheme. Query strings, headers, and bodies are never
  read — content cannot leak through a path that never touches it.
- when no OB-0 provider is configured (``physical_active()`` false), the
  middleware short-circuits: zero spans, zero tracer lookups.
"""

from __future__ import annotations

from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.sdk.observability import physical_active, physical_span

# Must mirror the public health routes registered in src/http/main.py.
HEALTH_PATHS = frozenset({"/health", "/health/ready"})


class PhysicalSpanMiddleware:
    """Emit one ``http.request`` span per (non-health) HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "") in HEALTH_PATHS:
            await self._app(scope, receive, send)
            return

        if not physical_active():
            # Unconfigured deployment: zero observability cost.
            await self._app(scope, receive, send)
            return

        attributes: dict[str, Any] = {
            "http.request.method": scope.get("method", ""),
            "url.path": scope.get("path", ""),
            "url.scheme": scope.get("scheme", "http"),
        }
        with physical_span("http.request", **attributes) as span:
            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start" and span is not None:
                    span.set_attribute(
                        "http.response.status_code", message["status"]
                    )
                await send(message)

            await self._app(scope, receive, send_wrapper)
            # The router records the matched route on the scope during
            # dispatch; attach it while the span is still open. A 404 (no
            # route) simply omits the attribute — url.path still identifies
            # the request.
            if span is not None:
                route = scope.get("route")
                route_path = getattr(route, "path", None)
                if route_path:
                    span.set_attribute("http.route", route_path)


def register_physical_http_spans(app: Any) -> None:
    """Attach the physical-span middleware to a FastAPI/Starlette app."""
    app.add_middleware(PhysicalSpanMiddleware)
