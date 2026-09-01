"""/v1 route aliasing (roadmap P0-T5).

Alias the stable public surface under a /v1 prefix WITHOUT redirects: the
same handlers, same middleware (auth is path-agnostic), same behavior —
old paths keep working forever. Partners and the TS SDK target /v1;
existing clients keep /.

Implementation: re-include the selected routers with `prefix="/v1"`. For
routers that already carry their own prefix (e.g. tools -> /tools), the
FastAPI prefix is prepended, yielding /v1/tools/...; for prefix-less
routers (conversation, ws) it yields /v1/message, /v1/ws/conversation, ...
"""

from fastapi import FastAPI

# Routers whose full public surface is aliased under /v1. Order mirrors
# the unprefixed includes in main.py for readability.
from src.http.routers import capabilities as _capabilities
from src.http.routers import conversation as _conversation
from src.http.routers import skills as _skills
from src.http.routers import subagents as _subagents
from src.http.routers import tools as _tools
from src.http.routers.audit import router as _audit_router
from src.http.routers.mcp import router as _mcp_router
from src.http.routers.billing import router as _billing_router
from src.http.routers.usage import router as _usage_router
from src.http.routers.ws import router as _ws_router


def include_v1_aliases(app: FastAPI) -> None:
    """Mount /v1 aliases for the core endpoints on `app`."""
    app.include_router(_audit_router, prefix="/v1")
    app.include_router(_usage_router, prefix="/v1")
    app.include_router(_billing_router, prefix="/v1")
    from src.http.routers.governance import router as _gov_router
    app.include_router(_gov_router, prefix="/v1")
    app.include_router(_mcp_router, prefix="/v1")
    app.include_router(_conversation.router, prefix="/v1")
    app.include_router(_skills.router, prefix="/v1")
    app.include_router(_subagents.router, prefix="/v1")
    app.include_router(_tools.router, prefix="/v1")
    app.include_router(_capabilities.router, prefix="/v1")
    app.include_router(_ws_router, prefix="/v1")
