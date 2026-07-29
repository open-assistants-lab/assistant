"""Webhook, file change, and manual trigger endpoints for loop 3."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.app_logging import get_logger
from src.sdk.loops.events import AgentEvent, get_trigger_registry
from src.sdk.messages import Message
from src.sdk.runner import run_sdk_agent

router = APIRouter(tags=["triggers"])
logger = get_logger()


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


class WebhookResponse(BaseModel):
    status: str
    trigger_id: str
    response: str | None = None
    error: str | None = None


# -- Manual trigger --


@router.post("/trigger", response_model=TriggerResponse)
async def manual_trigger(req: TriggerRequest) -> TriggerResponse:
    """Manually trigger an agent run (for testing/automation)."""
    event = AgentEvent(
        trigger_type="manual",
        trigger_id=str(uuid.uuid4()),
        user_id=req.user_id,
        session_id=req.session_id,
        message=req.message,
        rubric=req.rubric,
        model=req.model,
    )
    try:
        registry = get_trigger_registry()
        await registry.fire(event)
        return TriggerResponse(status="completed")
    except KeyError:
        # No handler — run directly
        messages = [Message.user(req.message)]
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


# -- Webhook trigger --


@router.post("/webhooks/{trigger_id}", response_model=WebhookResponse)
async def webhook_trigger(trigger_id: str, request: Request) -> WebhookResponse:
    """External webhook trigger. Accepts any JSON body.

    The body should contain:
    - user_id (required)
    - message (required)
    - session_id (optional, defaults to webhook_{trigger_id})
    - rubric (optional)
    - model (optional)

    Any other fields are passed as event metadata.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    user_id = body.get("user_id")
    message = body.get("message")
    if not user_id or not message:
        return WebhookResponse(
            status="error",
            trigger_id=trigger_id,
            error="user_id and message are required",
        )

    session_id = body.get("session_id", f"webhook_{trigger_id}")
    rubric = body.get("rubric")
    model = body.get("model")
    metadata = {k: v for k, v in body.items() if k not in {"user_id", "message", "session_id", "rubric", "model"}}

    event = AgentEvent(
        trigger_type="webhook",
        trigger_id=trigger_id,
        user_id=user_id,
        session_id=session_id,
        message=message,
        rubric=rubric,
        model=model,
        metadata=metadata,
    )
    try:
        registry = get_trigger_registry()
        await registry.fire(event)
        return WebhookResponse(status="completed", trigger_id=trigger_id)
    except Exception as e:
        return WebhookResponse(status="error", trigger_id=trigger_id, error=str(e))


# -- File change trigger --


class FileChangeWatcher:
    """Watches a directory for file changes and emits AgentEvent."""

    def __init__(
        self,
        user_id: str,
        watch_dir: str | Path,
        message_template: str = "A file was changed: {filename}",
        session_id: str | None = None,
        model: str | None = None,
        rubric: str | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.user_id = user_id
        self.watch_dir = Path(watch_dir)
        self.message_template = message_template
        self.session_id = session_id or f"filewatch_{user_id}"
        self.model = model
        self.rubric = rubric
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self._last_mtimes: dict[str, float] = {}

    async def start(self) -> None:
        self._stopped = False
        self._snapshot()
        self._task = asyncio.create_task(self._watch())
        logger.info("file_change_watcher.started", {"user_id": self.user_id, "dir": str(self.watch_dir)}, user_id=self.user_id)

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("file_change_watcher.stopped", {"user_id": self.user_id}, user_id=self.user_id)

    def _snapshot(self) -> None:
        """Take initial snapshot of file modification times."""
        if not self.watch_dir.exists():
            return
        for f in self.watch_dir.rglob("*"):
            if f.is_file():
                try:
                    self._last_mtimes[str(f)] = f.stat().st_mtime
                except OSError:
                    pass

    async def _watch(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self.poll_interval)
            if not self.watch_dir.exists():
                continue
            for f in self.watch_dir.rglob("*"):
                if not f.is_file():
                    continue
                key = str(f)
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if key not in self._last_mtimes:
                    # New file
                    self._last_mtimes[key] = mtime
                    await self._fire_event(f.name, "created")
                elif mtime > self._last_mtimes[key]:
                    self._last_mtimes[key] = mtime
                    await self._fire_event(f.name, "modified")

    async def _fire_event(self, filename: str, change_type: str) -> None:
        message = self.message_template.format(filename=filename)
        event = AgentEvent(
            trigger_type="file_change",
            trigger_id=f"filewatch_{self.user_id}",
            user_id=self.user_id,
            session_id=self.session_id,
            message=message,
            rubric=self.rubric,
            model=self.model,
            metadata={"filename": filename, "change_type": change_type, "dir": str(self.watch_dir)},
        )
        try:
            registry = get_trigger_registry()
            await registry.fire(event)
        except Exception as e:
            logger.warning("file_change_watcher.fire_failed", {"error": str(e), "filename": filename}, user_id=self.user_id)
