"""Dev-only routes — not for production use.

Serves throwaway demo surfaces (e.g. the Gmail OAuth demo page used for the
Google verification video and manual testing).
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(prefix="/dev", tags=["dev"])

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@router.get("/gmail-demo")
async def gmail_demo() -> FileResponse:
    """Serve the Gmail OAuth demo page.

    Thin real client over /auth/login, /emails/search, /emails/sync — no mock
    data. Useful for the Google OAuth verification video and manual testing.
    """
    return FileResponse(_STATIC_DIR / "gmail-demo.html", media_type="text/html")
