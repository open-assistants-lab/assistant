"""Version-2 authority consumes the verified authentication seam, never payload IDs."""

import inspect

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from src.http import auth
from src.http.auth.resolver import UserIdentity
from src.sdk.authority_models import Actor
from src.storage.instance_state import InstanceState


async def resolve_actor(request: Request, state: InstanceState) -> Actor:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, UserIdentity):
        raise HTTPException(status_code=401, detail="individual authentication required")
    try:
        return await state.authorize(identity, state.instance_id)
    except PermissionError as exc:
        # Do not publish private subject/key/company membership details.
        raise HTTPException(status_code=403, detail="instance access denied") from exc


class InstanceAuthorityGate:
    """Fail-closed ingress while the breaking contract is staged.

    A trusted launcher binds server.state.instance_state. Until a handler is
    explicitly qualified by the version-2 contract, it cannot enter legacy route
    exemptions, default-user resolution, localhost bypass or unbound callbacks.
    This is ingress control, not the yet-unqualified OS execution boundary.
    """

    def __init__(self, app: ASGIApp, *, server: FastAPI) -> None:
        self.app = app
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = getattr(self.server.state, "instance_state", None)
        if state is None or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not isinstance(state, InstanceState):
            await self._refuse(scope, receive, send, 503)
            return
        if scope["type"] == "http" and scope["path"] in ("/health", "/health/ready"):
            status = 200 if scope["path"] == "/health" else 503
            await JSONResponse(
                {"status": "initializing", "contract_version": 2}, status_code=status
            )(scope, receive, send)
            return
        # Existing resolvers only inspect connection properties; they never read
        # the body. A copied scope avoids mutating WebSocket protocol semantics.
        request = Request({**scope, "type": "http"}, receive=receive)
        try:
            identity = auth.get_resolver().resolve(request)
            if inspect.isawaitable(identity):
                identity = await identity
            request.state.identity = identity
            actor = await resolve_actor(request, state)
        except HTTPException as exc:
            await self._refuse(scope, receive, send, exc.status_code)
            return
        except Exception:
            # A broken/missing auth store or resolver never enables old bypass.
            await self._refuse(scope, receive, send, 503)
            return
        scope.setdefault("state", {})["actor"] = actor
        scope["state"]["identity"] = identity
        for route in self.server.routes:
            match, _ = route.matches(scope)
            endpoint = getattr(route, "endpoint", None)
            if match is Match.FULL and getattr(endpoint, "instance_authority", False):
                await self.app(scope, receive, send)
                return
        await self._refuse(scope, receive, send, 503)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        if scope["type"] == "websocket":
            code = {401: 4401, 403: 4403}.get(status, 1013)
            await send({"type": "websocket.close", "code": code})
        else:
            detail = (
                "individual instance authentication required"
                if status in (401, 403)
                else "instance contract not qualified"
            )
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
