"""Read-only MCP session health endpoint."""

from fastapi import APIRouter, Request

from src.http.auth import enforce_user_id
from src.sdk.tools_core.mcp_manager import get_mcp_manager
from src.storage.paths import DEFAULT_USER_ID

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/health")
async def get_mcp_health(request: Request, user_id: str = DEFAULT_USER_ID) -> dict[str, object]:
    enforce_user_id(user_id, getattr(getattr(request, "state", None), "identity", None))
    return await get_mcp_manager(user_id).health()
