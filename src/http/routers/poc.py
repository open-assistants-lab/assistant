"""POC route for canvas 2.0 — tabbed workspace with focal point tracking."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["poc"])

_HERE = Path(__file__).resolve().parent
_STATIC = _HERE.parent / "static"
_POC_HTML = _STATIC / "poc.html"


@router.get("/poc", response_class=HTMLResponse)
async def get_poc() -> str:
    """Serve the canvas 2.0 POC page."""
    if _POC_HTML.exists():
        return _POC_HTML.read_text(encoding="utf-8")
    return "<h1>POC page not found</h1>"
