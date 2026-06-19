"""Widget HTML templates for Canvas 2.0."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["widgets"])

WIDGETS_DIR = Path(__file__).parent.parent / "static" / "widgets"

AVAILABLE_WIDGETS = {
    "email": "email.html",
    "todos": "todos.html",
    "contacts": "contacts.html",
    "files": "files.html",
}


@router.get("/widget/{widget_type}", response_class=HTMLResponse)
async def get_widget(widget_type: str):
    widget_type = widget_type.lower()
    if widget_type not in AVAILABLE_WIDGETS:
        raise HTTPException(status_code=404, detail=f"Widget '{widget_type}' not found")

    file_path = WIDGETS_DIR / AVAILABLE_WIDGETS[widget_type]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Widget template not found")

    html = file_path.read_text(encoding="utf-8")
    return HTMLResponse(content=html)
