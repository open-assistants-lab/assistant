"""Profile reload API — main-agent PROFILE.md lifecycle (roadmap P0-T7 fix).

POST /profile/reload re-validates the user's main-agent PROFILE.md and, on
success, resets cached loops + detaches active WS sessions so no stale loop
serves an approved turn after a mid-session profile swap (E26 detach).
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from src.sdk import profile_loader
from src.sdk.session_worker import get_session_registry

router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("/reload")
async def reload_profile(user_id: str = Query(default="default_user")) -> JSONResponse:
    """Re-validate PROFILE.md and reset loops + detach active sessions.

    - Invalid PROFILE.md (parse/validation error) -> 400, loops untouched.
    - Valid or absent profile -> loops reset, active WS sessions cancelled,
      200 with a summary of the new state.
    """
    try:
        profile = profile_loader.load_main_agent_profile(user_id)
    except profile_loader.ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await profile_loader.revalidate_and_reset(
        user_id, registry=get_session_registry()
    )

    summary = {
        "profile_found": result["profile_found"],
        "model": profile.model if profile is not None else None,
        "persona_present": bool(profile and profile.system_prompt),
        "detached_sessions": result["detached_sessions"],
        "loops_removed": result["loops_removed"],
    }
    return JSONResponse(summary)
