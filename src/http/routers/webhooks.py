"""Webhook and manual trigger endpoints for loop 3."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.sdk.messages import Message
from src.sdk.runner import run_sdk_agent

router = APIRouter(tags=["triggers"])


class TriggerRequest(BaseModel):
    user_id: str
    session_id: str
    message: str
    rubric: str | None = None
    model: str | None = None


class TriggerResponse(BaseModel):
    status: str
    response: str | None = None
    error: str | None = None


@router.post("/trigger", response_model=TriggerResponse)
async def manual_trigger(req: TriggerRequest) -> TriggerResponse:
    """Manually trigger an agent run (for testing/automation)."""
    messages = [Message.user(req.message)]
    try:
        result = await run_sdk_agent(
            user_id=req.user_id,
            messages=messages,
            model=req.model,
            session_id=req.session_id,
            rubric=req.rubric,
        )
        response_text = ""
        for msg in reversed(result):
            if msg.role == "assistant" and isinstance(msg.content, str):
                response_text = msg.content
                break
        return TriggerResponse(status="completed", response=response_text)
    except Exception as e:
        return TriggerResponse(status="error", error=str(e))