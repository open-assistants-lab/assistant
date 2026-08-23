# ruff: noqa: E402
import asyncio
import json
import os
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # noqa: E402

import mistune  # noqa: E402
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import src.config.user_settings_store as _user_settings_store
import src.sdk.context_measurement as _context_measurement
import src.sdk.run_models as _run_models
from src.app_logging import get_logger
from src.config import get_settings
from src.http.auth import require_auth
from src.http.conversation_persistence import (
    persist_assistant_message,
    persist_reasoning_message,
    persist_tool_message,
)
from src.http.models import MessageRequest, MessageResponse, VerificationVerdict
from src.http.stream_adapter import adapt_stream_chunk
from src.sdk.messages import Message, ToolCall
from src.sdk.run_models import display_model_name
from src.sdk.run_service import RunService
from src.sdk.runner import (
    _messages_from_conversation,
    get_sdk_loop,
    reset_sdk_loop,
    run_sdk_agent_stream,
)
from src.sdk.session_worker import SessionBusyError, SessionWorkerRegistry
from src.storage.messages import get_message_store

_pending_interrupts: dict[str, dict[str, Any]] = {}
_cancel_flags: dict[str, bool] = {}
_active_streams: dict[str, asyncio.Event] = {}
_session_registry = SessionWorkerRegistry()

router = APIRouter(tags=["conversation"])
logger = get_logger()


def _stream_key(user_id: str, session_id: str | None) -> str:
    """Composite key for per-session stream tracking (enables concurrent sessions per user)."""
    return f"{user_id}:{session_id or 'default'}"


def _normalized_session_id(session_id: str | None) -> str:
    """Return the nonempty session boundary used for scoped history reads."""
    return session_id.strip() if session_id and session_id.strip() else "default"


def sse(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE event string with a canonical envelope.

    `data` is placed under the "data" key. Use sse_raw() instead when `data`
    is already a full RunEvent dump (which carries its own type+data), to avoid
    double-nesting the payload.
    """
    return f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"


def sse_raw(event_dump: dict[str, Any]) -> str:
    """Format an SSE event from a raw RunEvent dump.

    A RunEvent dump already carries "type" and "data" at its top level, so it
    is serialized directly — matching the WebSocket path and the canonical
    contract clients expect: {"type": ..., "data": {payload}}.
    """
    return f"data: {json.dumps(event_dump)}\n\n"

# ── Canvas HTML fence block parser ──────────────────────────────────────────

_CANVAS_FENCE = re.compile(
    r"```html:(canvas|skill-form|subagent-form)\s*\n(.*?)```",
    re.DOTALL,
)

_CANVAS_SCHEMAS: dict[str, list[str]] = {
    "skill-form": ["name", "description", "content"],
    "subagent-form": ["name", "description", "model", "system_prompt"],
    "canvas": [],
    "editor": ["filePath", "content"],
}

# ── Editor fence block parser ──────────────────────────────────────────────

_EDITOR_FENCE = re.compile(
    r"```html:editor\s*\nfilePath:\s*(.+?)\n---\n(.*?)```",
    re.DOTALL,
)

_EDITOR_TEMPLATE_PATH = Path(__file__).parent.parent / "static" / "editor.html"
_EDITOR_TEMPLATE: str | None = None


def _load_editor_template() -> str:
    global _EDITOR_TEMPLATE
    if _EDITOR_TEMPLATE is None:
        with open(_EDITOR_TEMPLATE_PATH) as f:
            _EDITOR_TEMPLATE = f.read()
    return _EDITOR_TEMPLATE


def _render_editor_surface(file_path: str, content: str) -> str:
    template = _load_editor_template()
    escaped_path = json.dumps(file_path)
    escaped_content = json.dumps(mistune.html(content))
    html = template.replace("__FILE_PATH__", escaped_path)
    html = html.replace("__INITIAL_HTML__", escaped_content)
    return html


def _extract_editor(text: str) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for i, match in enumerate(_EDITOR_FENCE.finditer(text)):
        file_path = match.group(1).strip()
        content = match.group(2).strip()
        if not content or not file_path:
            continue
        html = _render_editor_surface(file_path, content)
        surfaces.append({
            "surface_id": f"editor-{i}",
            "action": "create",
            "html": html,
            "surface_type": "editor",
            "file_path": file_path,
        })
    return surfaces


def _extract_canvas(text: str, surface_id_prefix: str = "canvas") -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for i, match in enumerate(_CANVAS_FENCE.finditer(text)):
        surface_type = match.group(1)
        html = match.group(2).strip()
        if not html:
            continue
        surfaces.append({
            "surface_id": f"{surface_id_prefix}-{i}",
            "action": "create",
            "html": html,
            "surface_type": surface_type,
        })
    return surfaces


def _extract_surfaces(text: str) -> list[dict[str, Any]]:
    """Extract all surface blocks (canvas, skill-form, subagent-form, editor)."""
    return _extract_canvas(text) + _extract_editor(text)


def _strip_canvas_fences(text: str) -> str:
    """Remove ```html:canvas/skill-form/subagent-form/editor ... ``` fence blocks from text."""
    text = _CANVAS_FENCE.sub("", text)
    text = _EDITOR_FENCE.sub("", text)
    return text.strip()


def _persist_tool_messages(
    conversation: Any, tool_events: list[dict[str, Any]], session_id: str
) -> None:
    seen_call_ids: set[str] = set()
    for event in tool_events:
        output = event.get("output")
        call_id = event.get("call_id") or event.get("tool_call_id") or ""
        if event.get("stage") != "end" or not output:
            continue
        if call_id in seen_call_ids:
            continue
        seen_call_ids.add(call_id)
        persist_tool_message(
            conversation,
            str(output),
            session_id=session_id,
            tool_name=event.get("tool") or event.get("tool_name") or "unknown",
            tool_call_id=call_id,
        )


def _persist_collected_stream_state(
    conversation: Any,
    *,
    session_id: str,
    ai_content_parts: list[str],
    reasoning_parts: list[str],
    tool_metadata_list: list[dict[str, Any]],
    tool_results: dict[str, str],
) -> None:
    for tm in tool_metadata_list:
        call_id = tm.get("tool_call_id", "")
        output = tool_results.get(call_id, "")
        if output:
            persist_tool_message(
                conversation,
                output,
                session_id=session_id,
                tool_name=tm.get("tool_name", "unknown"),
                tool_call_id=call_id,
            )
    if reasoning_parts:
        persist_reasoning_message(conversation, "".join(reasoning_parts), session_id=session_id)
    response = "".join(ai_content_parts).strip()
    if response:
        persist_assistant_message(
            conversation, response, metadata={"stream": True}, session_id=session_id
        )


@router.get("/context-info")
def get_context_info(
    user_id: str = "default_user",
    session_id: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Estimate persisted session history without running or mutating the agent."""
    settings = get_settings()
    if model is not None:
        try:
            model_str = _run_models.normalize_canonical_model(model)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid model identifier") from exc
    else:
        saved_model = None
        try:
            saved_model = _user_settings_store.UserSettingsStore(user_id).load().default_model
        except (_user_settings_store.UserSettingsStoreError, OSError, ValueError) as exc:
            logger.warning(
                "context_info.user_settings_load_failed",
                {"error_type": type(exc).__name__},
                user_id=user_id,
            )
        if saved_model:
            model_str = saved_model
        else:
            host_model = settings.agent.model.strip()
            if ":" not in host_model:
                if "/" in host_model:
                    provider, model_id = host_model.split("/", 1)
                    host_model = f"{provider}:{model_id}"
                else:
                    host_model = f"ollama:{host_model}"
            try:
                model_str = _run_models.normalize_canonical_model(host_model)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid host model identifier") from exc

    context_window = _context_measurement.resolve_context_window(model_str)
    conversation = get_message_store(user_id)
    sid = _normalized_session_id(session_id)
    recent = conversation.get_messages_with_summary(session_id=sid, limit=100)
    sdk_msgs = _messages_from_conversation(recent)
    current_tokens = _context_measurement.estimate_message_tokens(sdk_msgs)

    sum_config = settings.memory.summarization
    trigger = sum_config.get_trigger()
    trigger_tokens = None
    if trigger and isinstance(trigger, tuple) and trigger[0] == "tokens":
        trigger_tokens = trigger[1]

    return {
        "model": model_str,
        "context_window": context_window,
        "current_tokens": current_tokens,
        "summarization_threshold": trigger_tokens,
        "summarization_enabled": sum_config.enabled,
        "context_percentage": current_tokens / context_window * 100 if context_window else None,
        "source": "history_estimate",
        "freshness": "stale",
        "estimated": True,
    }


@router.get("/conversation")
async def get_conversation(
    user_id: str = "default_user", limit: int = 100, session_id: str | None = None
) -> dict[str, Any]:
    """Get conversation history, optionally filtered by session_id."""
    conversation = get_message_store(user_id)
    if session_id:
        messages = conversation.get_messages_by_session_id(session_id, limit)
    else:
        messages = conversation.get_messages_by_session_id("default", limit)

    return {
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "source": m.metadata.get("source") if m.metadata else None,
                "timestamp": m.ts.isoformat() if m.ts else None,
                "metadata": m.metadata,
            }
            for m in messages
        ]
    }


@router.get("/conversation/turns")
async def get_conversation_turns(
    user_id: str = "default_user",
    session_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Get conversation turns grouped by run_id."""
    conversation = get_message_store(user_id)
    sid = session_id or "default"
    turns, next_cursor = conversation.get_turns(sid, limit=limit, cursor=cursor)
    return {
        "turns": [
            {
                "run_id": t["run_id"],
                "metadata": t["metadata"],
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "source": m.metadata.get("source") if m.metadata else None,
                        "timestamp": m.ts.isoformat() if m.ts else None,
                        "metadata": m.metadata,
                    }
                    for m in t["messages"]
                ],
            }
            for t in turns
        ],
        "next_cursor": next_cursor,
    }


@router.get("/conversation/sessions")
async def list_sessions(user_id: str = "default_user") -> dict[str, Any]:
    """List all chat sessions with titles derived from first user message."""
    conversation = get_message_store(user_id)
    sessions = conversation.get_sessions()
    return {"sessions": sessions}


@router.delete("/conversation/session")
async def delete_session(user_id: str = "default_user", session_id: str = "") -> dict[str, Any]:
    """Delete all messages in a specific chat session."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    conversation = get_message_store(user_id)
    conversation.delete_session(session_id)
    reset_sdk_loop(user_id, session_id=session_id)
    return {"status": "deleted", "session_id": session_id}


_PROVIDER_DISPLAY = {
    "agnes": "Agnes",
    "ollama-cloud": "Ollama Cloud",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
}

_STATIC_MODELS = {
    "agnes": [
        {
            "id": "agnes:agnes-2.0-flash",
            "name": "Agnes 2.0 Flash",
            "provider": "agnes",
            "provider_display": "Agnes",
        }
    ],
    "ollama-cloud": [
        {
            "id": "ollama-cloud:deepseek-v4-flash:0731",
            "name": "DeepSeek V4 Flash 0731",
            "provider": "ollama-cloud",
            "provider_display": "Ollama Cloud",
        }
    ],
}


def _stored_provider_key(user_id: str, provider: str) -> str | None:
    try:
        from src.sdk.providers.factory import _load_stored_key

        return _load_stored_key(provider, user_id)
    except Exception:
        return None


_PROVIDER_ENV_KEYS = {
    "agnes": "AGNES_API_KEY",
    "ollama-cloud": "OLLAMA_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_ALL_KNOWN_PROVIDER_IDS = list(_PROVIDER_ENV_KEYS.keys())


def _provider_key_source(provider: str, user_id: str) -> str | None:
    if _stored_provider_key(user_id, provider):
        return "user"
    env_key = _PROVIDER_ENV_KEYS.get(provider)
    if env_key and os.environ.get(env_key):
        return "hosted" if provider == "agnes" else "env"
    if provider == "ollama-cloud" and os.environ.get("OLLAMA_BASE_URL"):
        return "env"
    return None


@router.get("/models")
async def list_available_models(
    user_id: str = "default_user", _: None = Depends(require_auth)
) -> dict[str, list[dict[str, str]]]:
    """List available models from providers with configured API keys."""
    from src.sdk.registry import list_models

    configured = []
    # Check all known providers, not just a hardcoded subset
    for provider_id in _ALL_KNOWN_PROVIDER_IDS:
        if _provider_key_source(provider_id, user_id):
            configured.append(provider_id)

    models = []
    for provider in configured:
        # Static seed is a fallback, NOT authoritative — merge with the
        # registry (models.dev) so providers like ollama-cloud expose their
        # full catalog (the seed held only one model).
        static_models = _STATIC_MODELS.get(provider) or []
        try:
            registry_models = [
                {
                    "id": f"{provider}:{m.id}",
                    "name": display_model_name(m.id, m.name),
                    "provider": provider,
                    "provider_display": _PROVIDER_DISPLAY.get(provider, provider.title()),
                }
                for m in list_models(provider=provider)
            ]
        except Exception:
            registry_models = []
        by_id: dict[str, dict[str, str]] = {}
        for m in registry_models + static_models:
            by_id.setdefault(m["id"], m)
        provider_models = list(by_id.values()) if registry_models else static_models
        key_source = _provider_key_source(provider, user_id) or "unknown"
        for m in provider_models:
            models.append({
                **m,
                "key_source": key_source,
                "billing_mode": key_source if key_source in {"hosted", "user", "env"} else "unknown",
            })
    return {"models": models}


class TitleRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str


async def _summarize_title(
    user_msg: str,
    assistant_msg: str,
    user_id: str = "default_user",
    existing_titles: list[str] | None = None,
) -> str | None:
    """Generate a 3-5 word title via a simple provider.chat() call.

    When existing_titles is provided, the prompt asks the model to avoid
    reusing any of them so the sidebar never shows duplicate chat titles.
    """
    from src.sdk.providers.factory import get_cached_model_provider

    settings = get_settings()
    # User-configured title model wins over the host config; falls back to
    # the agent model when neither is set.
    from src.config.user_settings_service import load_saved_user_settings

    saved = load_saved_user_settings(user_id)
    model = (
        saved.title_model
        if saved is not None and saved.title_model
        else (settings.agent.title_model or settings.agent.model)
    )
    provider = get_cached_model_provider(model, user_id=user_id)

    prompt = (
        "Summarize the following conversation in 3-5 words. "
        "Use a short noun phrase. No punctuation at the end. No quotes."
    )
    if existing_titles:
        prompt += (
            " Do NOT reuse any of these existing titles: "
            + ", ".join(existing_titles)
            + "."
        )
    prompt += f"\n\nUser: {user_msg}\nAssistant: {assistant_msg}\n\nTitle:"

    try:
        response = await provider.chat(
            messages=[Message.user(prompt)],
            tools=None,
            provider_options={
                # Reserved key: names the Langfuse generation (the provider
                # ignores it).
                "langfuse": {"name": "title_generation"},
                # A title needs no reasoning — disable thinking so the token
                # budget goes to the title, not a thinking chain.
                "ollama-cloud": {"think": False},
                "agnes": {"chat_template_kwargs": {"enable_thinking": False}},
            },
            max_tokens=100,
            temperature=0.3,
        )
        content_preview = response.content[:80] if len(response.content) > 80 else response.content
        logger.info("title_gen_response", {"content": content_preview})
        raw_title = response.content if isinstance(response.content, str) else str(response.content)
        title = raw_title.strip().strip('"').strip("'").strip()
        # Strip trailing punctuation (.,;:!?。)
        while title and title[-1] in ".。,;:!?,;:!?":
            title = title[:-1].strip()
        if len(title) > 40:
            title = title[:40]
        return title or None
    except Exception as e:
        logger.warning("title_gen_error", {"error": str(e), "model": model})
        return None


@router.post("/conversation/title")
async def generate_title(req: TitleRequest, _: None = Depends(require_auth)) -> dict[str, str]:
    """Generate a short title for a chat session."""
    conversation = get_message_store(req.user_id)

    # Idempotent: a session that already has a stored title keeps it — the
    # LLM is only consulted once per session, and a lost response never
    # clobbers an existing title.
    stored_title = conversation.get_session_title(req.session_id)
    if stored_title:
        return {"title": stored_title, "session_id": req.session_id}

    messages = conversation.get_messages_by_session_id(req.session_id, limit=50)
    if len(messages) < 2:
        raise HTTPException(status_code=400, detail="Need at least user + assistant message")

    # Find first user message and first assistant message
    user_msg = ""
    assistant_msg = ""
    for msg in messages:
        if not user_msg and msg.role == "user":
            user_msg = msg.content
        elif not assistant_msg and msg.role == "assistant":
            assistant_msg = msg.content
        if user_msg and assistant_msg:
            break

    if not user_msg:
        raise HTTPException(status_code=400, detail="No user message found")
    if len(user_msg) < 5:
        raise HTTPException(status_code=400, detail="First message too short to summarize")
    if not assistant_msg.strip():
        raise HTTPException(status_code=400, detail="Empty assistant response")

    assistant_msg = assistant_msg[:500]

    # Collect other sessions' titles so the LLM avoids duplicate titles.
    existing_titles = [
        s["title"]
        for s in conversation.get_sessions()
        if s["session_id"] != req.session_id and s.get("title")
    ]

    title = await _summarize_title(
        user_msg, assistant_msg, user_id=req.user_id, existing_titles=existing_titles
    )
    if not title:
        raise HTTPException(status_code=500, detail="Title generation failed")

    conversation.update_session_title(req.session_id, title)
    return {"title": title, "session_id": req.session_id}


@router.delete("/conversation")
async def clear_conversation(user_id: str = "default_user") -> dict[str, Any]:
    """Clear conversation history."""
    conversation = get_message_store(user_id)
    conversation.clear()
    return {"status": "cleared", "user_id": user_id}


@router.post("/message", response_model=MessageResponse)
async def handle_message(req: MessageRequest, _: None = Depends(require_auth)) -> MessageResponse:
    """Send a message to the agent (SDK-powered)."""
    try:
        user_id = req.user_id or "default_user"

        conversation = get_message_store(user_id)
        session_id = _normalized_session_id(req.session_id)

        try:
            run_service = RunService(user_id, _session_registry, conversation)
            result = await run_service.execute(
                session_id=session_id,
                prompt=req.message,
                model=req.model,
                provider_keys=req.provider_keys,
                rubric=req.verification.rubric if req.verification else None,
                mode=req.verification.mode if req.verification else None,
            )
        except SessionBusyError:
            return MessageResponse(response="", error="Session already has an active run")

        response = result.response
        reasoning_text = None
        usage_data = None
        if result.usage.agent.available:
            usage_data = {
                "input_tokens": result.usage.agent.input_tokens,
                "output_tokens": result.usage.agent.output_tokens,
                "reasoning_tokens": result.usage.agent.reasoning_tokens,
            }

        verification_verdict = None
        if result.verification.availability.value == "on":
            latest = result.verification.evaluations[-1] if result.verification.evaluations else None
            # A skipped run (C11 auto mode) has no evaluations but still
            # reports its status so clients can distinguish skip from off.
            verification_verdict = VerificationVerdict(
                status=result.verification.status.value,
                iterations=result.verification.attempts,
                attempts=result.verification.attempts,
                max_attempts=result.verification.max_attempts,
                explanation=latest.explanation if latest else "",
                criteria=(
                    [{"name": c.name, "passed": c.passed, "gap": c.gap} for c in latest.criteria]
                    if latest
                    else []
                ),
                evaluations=[
                    {
                        "attempt": e.attempt,
                        "result": e.result.value,
                        "explanation": e.explanation,
                        "criteria": [{"name": c.name, "passed": c.passed, "gap": c.gap} for c in e.criteria],
                    }
                    for e in result.verification.evaluations
                ],
            )

        canvas_blocks = _extract_surfaces(response)
        response = _strip_canvas_fences(response)

        logger.info(
            "agent.response",
            {"response": response[:80], "verbose": req.verbose},
            user_id=user_id,
            channel="http",
        )

        return MessageResponse(
            response=response,
            reasoning=reasoning_text,
            verbose_data={"canvas_blocks": canvas_blocks},
            tool_calls=result.tool_calls if result.tool_calls else None,
            verification=verification_verdict,
            usage=usage_data,
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return MessageResponse(response="", error=str(e))


_HEARTBEAT_PING = ": ping\n\n"


async def _sse_with_heartbeat(
    event_iter: AsyncGenerator[Any, None] | AsyncIterator[Any], interval: float = 15.0
) -> AsyncGenerator[Any, None]:
    """Interleave SSE keepalive comments while the upstream run is working.

    Provider calls and tool executions can legitimately stay silent for
    minutes, so the stream must emit a liveness signal that does not depend
    on run progress. The native client treats the gap between received
    lines as its stream-liveness clock.

    Yields the upstream events unchanged plus `_HEARTBEAT_PING` (a str)
    between them while the run is still in flight.
    """
    # Single outstanding `__anext__` + a fresh timer per iteration, with the
    # finished task discarded each round. This preserves the original
    # design's exact laziness: the upstream generator's mid-stream side
    # effects (e.g. setting a cancel flag between yields) are observed by
    # the consumer at the same point as direct iteration, because the next
    # item is only requested after the current one is yielded. The original
    # implementation instead accumulated every finished task into a
    # `pending` set that was never pruned, so `asyncio.wait(...)` returned
    # immediately forever — an unbounded ping burst.
    #
    # The brief's queue+pump alternative was rejected after verification:
    # with a bounded queue the pump's `finally: await queue.put(_SENTINEL)`
    # deadlocks when the consumer is cancelled while the queue is full (no
    # one drains it, so the sentinel put blocks forever). The single-
    # outstanding-`__anext__` design cannot run ahead of the consumer, so
    # no such buffered-sentinel state exists. (pinned by
    # test_heartbeat_cancellation_is_not_swallowed)
    next_task: asyncio.Task[Any] = asyncio.ensure_future(event_iter.__anext__())
    timer: asyncio.Task[Any] | None = None
    try:
        while True:
            timer = asyncio.ensure_future(asyncio.sleep(interval))
            done, _ = await asyncio.wait(
                {next_task, timer}, return_when=asyncio.FIRST_COMPLETED
            )
            if next_task in done:
                timer.cancel()
                try:
                    item = next_task.result()
                except StopAsyncIteration:
                    return
                yield item
                next_task = asyncio.ensure_future(event_iter.__anext__())
            else:
                yield _HEARTBEAT_PING
    finally:
        if next_task is not None:
            next_task.cancel()
            try:
                await next_task
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
        if timer is not None:
            timer.cancel()


@router.post("/message/stream")
async def message_stream(req: MessageRequest, _: None = Depends(require_auth)) -> StreamingResponse:
    """Send a message and stream response using SSE (SDK-powered)."""
    try:
        user_id = req.user_id or "default_user"
        session_id = _normalized_session_id(req.session_id)
        skey = _stream_key(user_id, session_id)
        cancel_event: asyncio.Event | None = None

        conversation = get_message_store(user_id)

        # Audit B12: probe BEFORE mutating cancel/slot dicts. A request that
        # is about to fail session-busy must not clobber the live stream's
        # registration (old code wiped the flag, failed busy in the lazy
        # acquire, then popped A's slot in its finally). The lazy acquire
        # inside execute_stream remains as the authoritative backstop.
        run_service = RunService(user_id, _session_registry, conversation)
        if run_service.probe_session_busy(session_id):
            async def busy_stream() -> AsyncGenerator[str, None]:
                yield sse("error", {"code": "session_busy", "message": "Session already has an active run"})
            return StreamingResponse(busy_stream(), media_type="text/event-stream")

        # Set up cancellation for this session's stream
        _cancel_flags[skey] = False
        cancel_event = asyncio.Event()
        _active_streams[skey] = cancel_event

        async def generate() -> AsyncGenerator[str, None]:
            ai_content_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_metadata_list: list[dict[str, Any]] = []
            tool_results: dict[str, str] = {}
            response = ""
            aborted = False
            run_failed = False
            persisted = False

            try:
                async for event in _sse_with_heartbeat(
                    run_service.execute_stream(
                        session_id=session_id,
                        prompt=req.message,
                        model=req.model,
                        provider_keys=req.provider_keys,
                        rubric=req.verification.rubric if req.verification else None,
                        mode=req.verification.mode if req.verification else None,
                    )
                ):
                    if isinstance(event, str):
                        # Heartbeat keepalive comment — not a run event.
                        yield event
                        continue

                    if _cancel_flags.get(skey, False) or cancel_event.is_set():
                        _persist_collected_stream_state(
                            conversation,
                            session_id=session_id,
                            ai_content_parts=ai_content_parts,
                            reasoning_parts=reasoning_parts,
                            tool_metadata_list=tool_metadata_list,
                            tool_results=tool_results,
                        )
                        persisted = True
                        aborted = True
                        yield sse("cancelled", {"content": "Cancelled"})
                        break

                    event_type = event.type
                    data = event.model_dump(mode="json")
                    event_data = data.get("data", {})

                    if event_type == "text_delta":
                        delta = event_data.get("delta", "")
                        if delta:
                            ai_content_parts.append(delta)
                            yield sse_raw(data)

                    elif event_type == "reasoning_delta":
                        delta = event_data.get("delta", "")
                        if delta:
                            reasoning_parts.append(delta)
                            yield sse_raw(data)

                    elif event_type == "tool_input_start":
                        tool_metadata_list.append(
                            {"tool_name": event_data.get("name", ""), "tool_call_id": event_data.get("tool_call_id", "")}
                        )
                        yield sse_raw(data)

                    elif event_type == "tool_result":
                        output = event_data.get("content", "")
                        if output:
                            tool_results[event_data.get("tool_call_id", "")] = str(output)[:500]
                        yield sse_raw(data)

                    elif event_type == "interrupt":
                        _pending_interrupts[skey] = {
                            "tool": event_data.get("tool", ""),
                            "call_id": event_data.get("call_id", ""),
                            "args": event_data.get("args", {}),
                            "model": req.model,
                            "provider_keys": req.provider_keys,
                            "session_id": session_id,
                        }
                        yield sse_raw(data)
                        _persist_collected_stream_state(
                            conversation,
                            session_id=session_id,
                            ai_content_parts=ai_content_parts,
                            reasoning_parts=reasoning_parts,
                            tool_metadata_list=tool_metadata_list,
                            tool_results=tool_results,
                        )
                        persisted = True
                        aborted = True
                        break

                    elif event_type == "error":
                        _persist_collected_stream_state(
                            conversation,
                            session_id=session_id,
                            ai_content_parts=ai_content_parts,
                            reasoning_parts=reasoning_parts,
                            tool_metadata_list=tool_metadata_list,
                            tool_results=tool_results,
                        )
                        persisted = True
                        aborted = True
                        yield sse_raw(data)
                        break

                    elif event_type == "rubric_evaluation_start":
                        yield sse_raw(data)

                    elif event_type == "rubric_evaluation_end":
                        yield sse_raw(data)

                    elif event_type == "usage":
                        yield sse_raw(data)

                    elif event_type == "response_revision_start":
                        yield sse_raw(data)

                    elif event_type == "context_compressed":
                        yield sse_raw(data)

                    elif event_type == "done":
                        result = event_data.get("result", {})
                        run_failed = result.get("status") == "failed"
                        response = result.get("response", "")
                        if not ai_content_parts and response:
                            ai_content_parts.append(response)
                        yield sse_raw(data)

                if aborted:
                    return

                if run_failed:
                    # B11: RunService.persist_run already persisted this run's
                    # partial state WITH run_id. The legacy fallback collector
                    # here wrote a second, run_id-less copy of the same rows
                    # (duplicate tool/reasoning/assistant rows in turns). Do
                    # nothing — the done event above already reached the client.
                    return

                response = "".join(ai_content_parts) if ai_content_parts else ""
                if not response and tool_results:
                    response = "\n".join(tool_results.values())
                if not response:
                    response = "Task completed."

                # Tool messages, reasoning and the final answer are all
                # persisted by RunService.persist_run (tools as audit records
                # with run_id + tool_name metadata, reasoning as a pre-message,
                # the answer as the run's final message). Persisting the tools
                # again here would duplicate them without run_id — the router
                # only persists collected state on failure/cancel paths.
                persisted = True
                logger.info(
                    "agent.response_stored", {"response": response[:80], "session_id": session_id, "user_id": user_id}, user_id=user_id, channel="http"
                )

                canvas_blocks = _extract_surfaces(response)
                for surface in canvas_blocks:
                    try:
                        yield sse("canvas_update", surface)
                    except Exception:
                        break
                response = _strip_canvas_fences(response)

            except SessionBusyError:
                yield sse("error", {"code": "session_busy", "message": "Session already has an active run"})
            except GeneratorExit:
                if not persisted:
                    _persist_collected_stream_state(
                        conversation,
                        session_id=session_id,
                        ai_content_parts=ai_content_parts,
                        reasoning_parts=reasoning_parts,
                        tool_metadata_list=tool_metadata_list,
                        tool_results=tool_results,
                    )
                response = "".join(ai_content_parts) if ai_content_parts else ""
                if response:
                    logger.info(
                        "agent.response_stored_disconnect", {"response": response[:80], "session_id": session_id}, user_id=user_id, channel="http"
                    )
                raise
            except asyncio.CancelledError:
                if not persisted:
                    _persist_collected_stream_state(
                        conversation,
                        session_id=session_id,
                        ai_content_parts=ai_content_parts,
                        reasoning_parts=reasoning_parts,
                        tool_metadata_list=tool_metadata_list,
                        tool_results=tool_results,
                    )
                response = "".join(ai_content_parts) if ai_content_parts else ""
                if response:
                    logger.info(
                        "agent.response_stored_cancelled", {"response": response[:80], "session_id": session_id}, user_id=user_id, channel="http"
                    )
                raise
            finally:
                # Identity-checked pop (audit B12): only clean up if the slot
                # still holds THIS request's event — never a concurrent owner's.
                if _active_streams.get(skey) is cancel_event:
                    _active_streams.pop(skey, None)
                    _cancel_flags.pop(skey, None)

        return StreamingResponse(generate(), media_type="text/event-stream")

    except Exception as e:
        skey = _stream_key(req.user_id or "default_user", req.session_id)
        if cancel_event is not None and _active_streams.get(skey) is cancel_event:
            _active_streams.pop(skey, None)
            _cancel_flags.pop(skey, None)
        raise HTTPException(status_code=500, detail=str(e))


class ApproveRequest(BaseModel):
    user_id: str = "default_user"
    call_id: str = ""
    session_id: str | None = None
    model: str | None = None
    provider_keys: dict[str, str] | None = None


class RejectRequest(BaseModel):
    user_id: str = "default_user"
    call_id: str = ""
    session_id: str | None = None
    reason: str = ""


class CancelRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str | None = None


async def _execute_approved_tool(
    loop: Any,
    tool_name: str,
    args: dict[str, Any],
    call_id: str,
) -> list[Any]:
    """Execute an approved tool via the loop and build the continuation messages.

    The approve flow used to re-run the agent with an instruction like
    "approve: please proceed with files_write" and rely on the model
    re-proposing the EXACT approved call — but each generation produces
    fresh call ids/args, so the loop's (name, args) approval match never
    fired and every re-run interrupted again: an approve loop. Executing
    the pending call directly puts the actual outcome in the model's
    context, so the agent continues from the real result instead of
    re-proposing the same tool.
    """
    from src.sdk.loop import AgentLoop  # noqa: F401  (type hint only)
    from src.sdk.messages import ToolCall

    result = await loop._execute_tool(
        ToolCall(id=call_id, name=tool_name, arguments=args)
    )
    return [
        Message.assistant(
            content="",
            tool_calls=[ToolCall(id=call_id, name=tool_name, arguments=args)],
        ),
        Message.tool_result(
            tool_call_id=call_id, content=result.content, name=tool_name
        ),
        Message.user(
            f"continue: the approved {tool_name} call above has been executed"
        ),
    ]


@router.post("/message/approve")
async def approve_tool(req: ApproveRequest, _: None = Depends(require_auth)) -> StreamingResponse:
    """Approve a pending tool call (HITL) and resume the agent as an SSE stream.

    Mirrors the WebSocket retry loop: pops the pending interrupt, marks the tool
    as approved on the cached loop, then re-runs the agent with an approval
    instruction appended. The response is a text/event-stream identical in shape
    to POST /message/stream so existing SSE clients can consume it unchanged.
    """
    session_id = _normalized_session_id(req.session_id)
    skey = _stream_key(req.user_id, session_id)
    pending = _pending_interrupts.pop(skey, None)
    if not pending and req.session_id is None:
        prefix = f"{req.user_id}:"
        matches = [key for key in _pending_interrupts if key.startswith(prefix)]
        if len(matches) == 1:
            skey = matches[0]
            pending = _pending_interrupts.pop(skey)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending tool call to approve")
    if req.call_id and pending.get("call_id") and pending.get("call_id") != req.call_id:
        _pending_interrupts[skey] = pending
        raise HTTPException(status_code=409, detail="Pending tool call does not match call_id")
    tool_name = pending.get("tool", "unknown")
    session_id = _normalized_session_id(pending.get("session_id") or session_id)
    skey = _stream_key(req.user_id, session_id)
    model = pending.get("model")
    provider_keys = pending.get("provider_keys")

    loop = await get_sdk_loop(
        req.user_id,
        model=model,
        provider_keys=provider_keys,
        session_id=session_id,
    )
    loop.approve_tool_call(
        ToolCall(id=pending.get("call_id") or req.call_id, name=tool_name, arguments=pending.get("args") or {})
    )

    conversation = get_message_store(req.user_id)

    # Audit B12: same probe-before-mutate contract as /message/stream — the
    # approve path had the identical clobber pattern (mutate dicts, fail busy,
    # pop A's slot in its own finally).
    if _session_registry.holds(f"{req.user_id}::{session_id}"):
        async def busy_stream() -> AsyncGenerator[str, None]:
            yield sse("error", {"code": "session_busy", "message": "Session already has an active run"})
        return StreamingResponse(busy_stream(), media_type="text/event-stream")

    cancel_event = asyncio.Event()
    _cancel_flags[skey] = False
    _active_streams[skey] = cancel_event

    async def generate() -> AsyncGenerator[str, None]:
        ai_content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_metadata_list: list[dict[str, Any]] = []
        tool_results: dict[str, str] = {}
        aborted = False
        persisted = False
        seen_canonical_text: set[str] = set()
        seen_canonical_reasoning: set[str] = set()
        seen_canonical_tool_starts: set[str] = set()
        try:
            recent = conversation.get_messages_with_summary(session_id=session_id, limit=50)
            retry_msgs = _messages_from_conversation(recent)
            # Execute the approved tool directly — see _execute_approved_tool.
            retry_msgs.extend(
                await _execute_approved_tool(
                    loop,
                    tool_name,
                    pending.get("args") or {},
                    pending.get("call_id") or f"call_{secrets.token_hex(4)}",
                )
            )

            async for chunk in run_sdk_agent_stream(
                user_id=req.user_id,
                messages=retry_msgs,
                model=model,
                provider_keys=provider_keys,
                cancel_event=cancel_event,
                session_id=session_id,
            ):
                if _cancel_flags.get(skey, False) or cancel_event.is_set():
                    _persist_collected_stream_state(
                        conversation,
                        session_id=session_id,
                        ai_content_parts=ai_content_parts,
                        reasoning_parts=reasoning_parts,
                        tool_metadata_list=tool_metadata_list,
                        tool_results=tool_results,
                    )
                    persisted = True
                    aborted = True
                    yield sse("cancelled", {"content": "Cancelled"})
                    break
                event = adapt_stream_chunk(chunk)
                is_compat_alias = chunk.type != event.kind

                if event.kind == "text_delta" and event.content:
                    if is_compat_alias and event.content in seen_canonical_text:
                        continue
                    ai_content_parts.append(event.content)
                    yield sse("text_delta", {"delta": event.content})
                    if not is_compat_alias:
                        seen_canonical_text.add(event.content)

                elif event.kind == "tool_input_start" and event.tool:
                    call_id = event.call_id or ""
                    if is_compat_alias and call_id in seen_canonical_tool_starts:
                        continue
                    if not is_compat_alias:
                        seen_canonical_tool_starts.add(call_id)
                    tool_metadata_list.append(
                        {"tool_name": event.tool, "tool_call_id": call_id}
                    )
                    yield sse("tool_input_start", {
                        "name": event.tool,
                        "tool_call_id": call_id,
                        "args": event.args or {},
                    })

                elif event.kind == "tool_result" and event.tool:
                    output = (event.result_preview or "")[:500]
                    if output:
                        tool_results[event.call_id or ""] = output
                        yield sse("tool_result", {
                            "name": event.tool,
                            "tool_call_id": event.call_id or "",
                            "content": output,
                        })

                elif event.kind == "reasoning_delta" and event.content:
                    if is_compat_alias and event.content in seen_canonical_reasoning:
                        continue
                    reasoning_parts.append(event.content)
                    yield sse("reasoning_delta", {"delta": event.content})
                    if not is_compat_alias:
                        seen_canonical_reasoning.add(event.content)

                elif event.kind == "reasoning_start":
                    pass

                elif event.kind == "interrupt":
                    _pending_interrupts[skey] = {
                        "tool": event.tool,
                        "call_id": event.call_id,
                        "args": event.args or {},
                        "model": model,
                        "provider_keys": provider_keys,
                        "session_id": session_id,
                    }
                    yield sse("interrupt", {
                        "tool": event.tool,
                        "call_id": event.call_id,
                        "args": event.args,
                    })
                    _persist_collected_stream_state(
                        conversation,
                        session_id=session_id,
                        ai_content_parts=ai_content_parts,
                        reasoning_parts=reasoning_parts,
                        tool_metadata_list=tool_metadata_list,
                        tool_results=tool_results,
                    )
                    persisted = True
                    aborted = True
                    break

                elif event.kind == "error":
                    _persist_collected_stream_state(
                        conversation,
                        session_id=session_id,
                        ai_content_parts=ai_content_parts,
                        reasoning_parts=reasoning_parts,
                        tool_metadata_list=tool_metadata_list,
                        tool_results=tool_results,
                    )
                    persisted = True
                    aborted = True
                    yield sse("error", {"code": "error", "message": event.content})
                    break

                elif event.kind == "done" and event.content:
                    if not ai_content_parts:
                        ai_content_parts.append(event.content)
                        yield sse("done", {"result": {"response": event.content}})

            response = "".join(ai_content_parts) if ai_content_parts else ""
            if aborted:
                return
            for tm in tool_metadata_list:
                call_id = tm.get("tool_call_id", "")
                output = tool_results.get(call_id, "")
                if output:
                    persist_tool_message(
                        conversation,
                        output,
                        session_id=session_id,
                        tool_name=tm.get("tool_name", "unknown"),
                        tool_call_id=call_id,
                    )
            if reasoning_parts:
                persist_reasoning_message(conversation, "".join(reasoning_parts), session_id=session_id)
            if response:
                persist_assistant_message(
                    conversation, response, metadata={"stream": True}, session_id=session_id
                )
                persisted = True
        except GeneratorExit:
            if not persisted:
                _persist_collected_stream_state(
                    conversation,
                    session_id=session_id,
                    ai_content_parts=ai_content_parts,
                    reasoning_parts=reasoning_parts,
                    tool_metadata_list=tool_metadata_list,
                    tool_results=tool_results,
                )
            raise
        except asyncio.CancelledError:
            if not persisted:
                _persist_collected_stream_state(
                    conversation,
                    session_id=session_id,
                    ai_content_parts=ai_content_parts,
                    reasoning_parts=reasoning_parts,
                    tool_metadata_list=tool_metadata_list,
                    tool_results=tool_results,
                )
            raise
        except Exception as e:
            logger.error("approve_stream_error", {"error": str(e)}, user_id=req.user_id, channel="http")
            yield sse("error", {"content": str(e)})
        finally:
            # Identity-checked pop (audit B12): never evict a concurrent owner.
            if _active_streams.get(skey) is cancel_event:
                _cancel_flags.pop(skey, None)
                _active_streams.pop(skey, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/message/reject")
async def reject_tool(req: RejectRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Reject a pending tool call (HITL)."""
    skey = _stream_key(req.user_id, req.session_id or "default")
    pending = _pending_interrupts.pop(skey, None)
    if not pending:
        # Fallback: try user_id-only key for backward compat
        pending = _pending_interrupts.pop(req.user_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending tool call to reject")
    if req.call_id and pending.get("call_id") and pending.get("call_id") != req.call_id:
        _pending_interrupts[skey] = pending
        raise HTTPException(status_code=409, detail="Pending tool call does not match call_id")
    return {"status": "rejected", "tool": pending.get("tool", "unknown")}


@router.post("/message/cancel")
async def cancel_message(req: CancelRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Cancel the current agent execution for a session."""
    session_id = req.session_id or "default"
    skey = _stream_key(req.user_id, session_id)
    _cancel_flags[skey] = True
    # Signal the active stream to break
    event = _active_streams.get(skey)
    if event:
        event.set()
    reset_sdk_loop(req.user_id, session_id=session_id)
    return {"status": "cancelled"}


class ConversationImportRequest(BaseModel):
    user_id: str = "default_user"
    messages: list[dict[str, Any]]  # [{"role": "user", "content": "..."}, ...]


@router.post("/conversation/import")
async def import_conversation(req: ConversationImportRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Bulk-import conversation history without triggering the agent loop.

    Used by evaluation frameworks (LongMemEval) to pre-load session data
    before asking a single question. Each message is added to the
    conversation store but NOT sent to the agent.
    """
    from src.storage.messages import get_message_store

    conversation = get_message_store(req.user_id)
    for msg in req.messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content.strip():
            meta = msg.get("metadata")
            conversation.add_message(role, content, metadata=meta)
    return {"imported": len(req.messages)}
