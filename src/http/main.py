"""HTTP server for Assistant."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.config.settings import REPO_ROOT
from src.http.routers import (
    audit_router,
    capabilities,
    contacts_router,
    conversation_router,
    email_router,
    health_router,
    improvements_router,
    memories_router,
    profile_router,
    scheduler_router,
    skills_router,
    subagents_router,
    todos_router,
    tools_router,
    user_prompt_router,
    webhooks_router,
    workspace_router,
    workspaces_router,
)
from src.http.routers.connectors import router as connectors_router
from src.http.routers.dev import router as dev_router
from src.http.routers.settings import router as settings_router
from src.http.routers.v1 import include_v1_aliases
from src.http.routers.ws import router as ws_router
from src.storage.paths import DEFAULT_USER_ID

load_dotenv(REPO_ROOT / ".env")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager — SDK runtime."""
    try:
        from src.subagent.scheduler import get_scheduler

        get_scheduler()
    except Exception:
        pass

    # Start companion scheduler if enabled
    try:
        from src.app_logging import get_logger
        from src.config import get_settings
        settings = get_settings()
        if getattr(settings.companion, "enabled", False):
            pass  # Companion scheduler disabled

        # Start connectkit token refresh background task
        _token_refresh_task: asyncio.Task[Any] | None = None

        # Register default trigger handler for loop 3 (event-driven)
        try:
            from src.sdk.loops.events import default_trigger_handler, get_trigger_registry
            registry = get_trigger_registry()
            for trigger_type in ("cron", "webhook", "file_change", "manual", "rerun"):
                registry.register(trigger_type, default_trigger_handler)
            get_logger().info("trigger_registry.handlers_registered", {"types": ["cron", "webhook", "file_change", "manual"]})
        except Exception as e:
            get_logger().warning("trigger_registry.register_failed", {"error": str(e)})

        async def _refresh_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(300)  # every 5 minutes
                    from connectkit.bridge import ConnectKitBridge
                    bridge = ConnectKitBridge("system")
                    await bridge.refresh_all()
                except Exception as e:
                    from src.app_logging import get_logger as _gl

                    _gl().warning(
                        "connectkit.refresh_failed",
                        {"error": str(e), "error_type": type(e).__name__},
                    )

        try:
            loop = asyncio.get_event_loop()
            _token_refresh_task = loop.create_task(_refresh_loop())
        except Exception:
            pass
    except Exception:
        pass

    print("HTTP server ready (SDK runtime)")
    yield

    # Only companion cleanup: token refresh task
    try:
        from src.app_logging import get_logger

        if _token_refresh_task is not None:
            _token_refresh_task.cancel()
            get_logger().info("scheduler.stopped", {}, user_id="system")
    except Exception:
        pass

    # Close cached provider HTTP clients (audit S3) so sockets are released
    # deterministically instead of waiting on GC.
    try:
        from src.sdk.providers.factory import close_all_providers

        await close_all_providers()
    except Exception:
        pass


def _oauth_login_error(service: str, config_fn: Any) -> str | None:
    """Return an error message if the connector cannot authorize, else None.

    The OAuth router builds the provider authorize URL from the connector's
    client_id; an unconfigured connector (no vault token, no
    DEFAULT_GWS_CLIENT_ID) would produce a broken URL with an empty
    client_id. The /auth/login guard calls this before allowing the redirect.
    """
    try:
        cfg = config_fn(service)
    except Exception:
        return f"Connector '{service}' is not configured"
    if not cfg.get("client_id"):
        return (
            f"Connector '{service}' is not configured (missing client_id). "
            "Configure credentials in Settings → Tools before connecting."
        )
    return None


app = FastAPI(
    title="Assistant",
    description="HTTP API for Assistant",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (audit B17): wildcard origins combined with allow_credentials lets any
# site ride authenticated sessions. Trust only explicitly configured origins;
# with none configured, stay permissive but credential-free.
_trusted_origins = [
    o.strip()
    for o in get_settings().auth.cors_origins.split(",")
    if o.strip()
]
if _trusted_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_trusted_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )


_PUBLIC_PATHS = {
    "/health",
    "/health/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    # Browser-initiated OAuth redirects carry no Bearer token (audit E24).
    # Exact paths only — the in-app connector guard still rejects
    # unconfigured services with 400.
    "/auth/login",
    "/auth/callback",
    # Dev demo page — static HTML, no Bearer token from the browser.
    "/dev/gmail-demo",
}


def _is_webhook_fire_path(path: str) -> bool:
    """True for POST /webhooks/{trigger_id} — the fire endpoint only.

    Audit E24: external webhook callers have no Bearer token; this path is
    exempt from API-key auth and instead enforces a per-trigger secret in
    the router (X-Webhook-Secret). Subpaths such as /webhooks/{id}/secret
    (secret registration) deliberately keep Bearer auth.
    """
    parts = [p for p in path.split("/") if p]
    return len(parts) == 2 and parts[0] == "webhooks"


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next: Any) -> Any:
    """Apply API-key auth consistently across HTTP routes.

    Roadmap P0-T1: the IdentityResolver seam is the single enforcement
    point — the middleware resolves the request and 401s when the resolver
    returns None. Default resolver = SharedSecretResolver (behavior
    identical to the pre-seam inline verify_key flow).
    """
    if request.url.path in _PUBLIC_PATHS or _is_webhook_fire_path(request.url.path):
        return await call_next(request)

    import inspect

    from src.http.auth import get_resolver

    result = get_resolver().resolve(request)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return JSONResponse({"detail": "Invalid API key"}, status_code=401)

    # P0-T2: stash the resolved identity so routers can enforce user_id
    # without resolving twice.
    request.state.identity = result

    return await call_next(request)

app.include_router(health_router)
app.include_router(audit_router)
app.include_router(scheduler_router)
app.include_router(conversation_router)
app.include_router(email_router)
app.include_router(memories_router)
app.include_router(user_prompt_router)
app.include_router(contacts_router)
app.include_router(todos_router)
# email_router already included above
app.include_router(workspace_router)
app.include_router(workspaces_router)
app.include_router(skills_router)
app.include_router(subagents_router)
app.include_router(tools_router)
app.include_router(capabilities.router)
app.include_router(ws_router)
app.include_router(settings_router)
app.include_router(webhooks_router)
app.include_router(improvements_router)
app.include_router(profile_router)

# ConnectKit OAuth + catalog routers (safe if connectkit not installed)
try:
    from connectkit.bridge import ConnectKitBridge, _default_spec_dir
    from connectkit.oauth import create_oauth_router
    from connectkit.spec import ConnectorSpec

    def _vault_factory(user_id: str) -> Any:
        # Audit E24 fix-round: /auth/login is PUBLIC, so its client-supplied
        # user_id has no authority — honouring it would let an attacker
        # plant their provider token into an arbitrary user's credential
        # vault (login-CSRF). This deployment model is one owner per process
        # (container-per-user), so every OAuth-router vault operation binds
        # to the deployment owner regardless of query parameters.
        bridge = ConnectKitBridge(DEFAULT_USER_ID)
        return bridge.vault

    # Load specs once — shared between config provider and oauth router
    _oauth_specs = ConnectorSpec.from_yaml_dir(_default_spec_dir())

    import os

    def _oauth_config(service: str) -> dict[str, Any]:
        bridge = ConnectKitBridge("")
        token = bridge.vault.get_token(service) or {}
        result = {
            "client_id": token.get("client_id", ""),
            "client_secret": token.get("client_secret", ""),
        }
        if not result["client_id"]:
            result["client_id"] = os.environ.get("DEFAULT_GWS_CLIENT_ID", "")
        if not result["client_secret"]:
            result["client_secret"] = os.environ.get("DEFAULT_GWS_CLIENT_SECRET", "")
        return result

    from src.config import get_settings as _get_settings

    # Default the OAuth redirect base to the configured bind port —
    # get_settings() applies API_HOST/API_PORT env over yaml (audit E22), so
    # this tracks whichever source is authoritative. Deployments behind a
    # public URL must set API_PUBLIC_URL explicitly.
    _api_settings = _get_settings().api
    _oauth_base_url = _api_settings.public_url or f"http://localhost:{_api_settings.port}"

    @app.middleware("http")
    async def _guard_oauth_login(request: Request, call_next: Any) -> Any:
        """Reject /auth/login for connectors that have no client_id configured.

        Without this, the OAuth router happily redirects to the provider with
        an empty client_id, producing a broken authorize URL (observed when an
        agent hit /auth/login for an unconfigured connector).

        INTERIM: the durable fix lives upstream in the ConnectKit repo
        (connectkit/oauth.py — `_build_authorize_url` and the callback raise
        400 on missing client_id). Remove this guard when the app's
        connectkit pin bumps past 0.1.4.
        """
        if request.url.path == "/auth/login":
            error = _oauth_login_error(
                request.query_params.get("service", ""), _oauth_config
            )
            if error is not None:
                return JSONResponse(status_code=400, content={"detail": error})

        return await call_next(request)

    oauth_router = create_oauth_router(
        specs=_oauth_specs,
        vault_factory=_vault_factory,
        config=_oauth_config,
        base_url=_oauth_base_url,
    )
    app.include_router(oauth_router)
    print(f"Included oauth_router: {[r.path for r in oauth_router.routes]}")
except Exception:
    import traceback
    traceback.print_exc()

app.include_router(connectors_router)
app.include_router(dev_router)

# P0-T5: /v1 aliases for the stable partner surface (same handlers, no
# redirect; auth middleware is path-agnostic and applies identically).
include_v1_aliases(app)


def run() -> None:
    """Run the HTTP server.

    Binds settings.api.host/port (audit E22): docker-compose sets
    API_PORT/API_HOST env which beats yaml; local dev uses the yaml value
    (8080 — the native-app contract)."""
    import uvicorn

    from src.config import get_settings

    cfg = get_settings()
    print(f"Starting assistant HTTP API on {cfg.api.host}:{cfg.api.port}")
    uvicorn.run(app, host=cfg.api.host, port=cfg.api.port)


if __name__ == "__main__":
    run()
