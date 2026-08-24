"""Webhook, file change, and manual trigger endpoints for loop 3."""

from __future__ import annotations

import asyncio
import json
import secrets as _secrets_module
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
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


class _WebhookSecretStore:
    """Persistent per-trigger secret store (audit E24-auth).

    Backed by a single JSON file under the deployment data path.
    Migration-safe: a missing file is an empty store; an unreadable one is
    treated as empty with a warning (fail-closed for validation purposes —
    no registered secret means unregistered triggers are rejected whenever
    an API key is configured).
    """

    def __init__(self) -> None:
        self._path: Path | None = None

    def _file(self) -> Path:
        if self._path is None:
            from src.config.settings import get_settings

            self._path = (
                Path(get_settings().deployment.data_path) / "webhook_secrets.json"
            )
        return self._path

    def _load(self) -> dict[str, str]:
        try:
            return {
                str(k): str(v)
                for k, v in json.loads(self._file().read_text()).items()
            }
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(
                "webhook_secrets.unreadable", {"error": str(e)}
            )
            return {}

    def register(self, trigger_id: str) -> str:
        import os

        store = self._load()
        secret = _secrets_module.token_hex(32)
        store[trigger_id] = secret
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic + owner-only permissions: secrets at rest must not be
        # world-readable, and a partial write must never replace the store.
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(store, f, sort_keys=True)
        os.replace(tmp, path)
        logger.info(
            "webhook_secrets.registered",
            {"trigger_id": trigger_id},
        )
        return secret

    def get(self, trigger_id: str) -> str | None:
        return self._load().get(trigger_id)


_secret_store = _WebhookSecretStore()


@router.post("/webhooks/{trigger_id}/secret")
async def create_webhook_secret(trigger_id: str) -> JSONResponse:
    """Generate and store the firing secret for a webhook trigger.

    Deliberately NOT exempted from API-key auth: only authenticated owners
    may mint firing credentials. The middleware's fire-path exemption
    covers exactly ``/webhooks/{trigger_id}`` (single segment).
    """
    secret = _secret_store.register(trigger_id)
    return JSONResponse({"trigger_id": trigger_id, "secret": secret})


def _webhook_secret_authorized(trigger_id: str, request: Request) -> bool:
    """Fail-closed secret check for the fire endpoint.

    - Registered trigger: requires a timing-safe X-Webhook-Secret match.
    - Unregistered trigger with an API key configured: reject (register a
      secret first via the authenticated endpoint).
    - Unregistered trigger without API key: allowed — preserves local/
      solo-bypass behaviour that predates E24.
    """
    from src.config.settings import get_settings

    registered = _secret_store.get(trigger_id)
    provided = request.headers.get("X-Webhook-Secret", "")
    if registered is not None:
        # compare_digest raises TypeError on non-ASCII str inputs — compare
        # bytes so a crafted header gets 401, not a 500 (audit E24 fix-round).
        return bool(provided) and _secrets_module.compare_digest(
            provided.encode("utf-8"), registered.encode("utf-8")
        )
    return not bool(get_settings().auth.api_key)


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

    Auth (audit E24-auth): this route is exempt from Bearer auth by the
    middleware; instead it requires the per-trigger ``X-Webhook-Secret``
    issued by ``POST /webhooks/{trigger_id}/secret``. Unregistered
    triggers are rejected while an API key is configured (fail-closed).
    """
    if not _webhook_secret_authorized(trigger_id, request):
        raise HTTPException(
            status_code=401, detail="invalid or missing X-Webhook-Secret"
        )
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
