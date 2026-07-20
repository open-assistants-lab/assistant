import asyncio
import json
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import mistune
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.app_logging import get_logger, timer
from src.http.auth import require_auth
from src.http.models import MessageRequest, MessageResponse
from src.sdk.messages import Message
from src.sdk.runner import (
    _messages_from_conversation,
    get_sdk_loop,
    reset_sdk_loop,
    run_sdk_agent,
    run_sdk_agent_stream,
)
from src.storage.messages import get_message_store

_pending_approvals: dict[str, dict[str, Any]] = {}
_pending_interrupts: dict[str, dict[str, Any]] = {}
_cancel_flags: dict[str, bool] = {}
_active_streams: dict[str, asyncio.Event] = {}

router = APIRouter(tags=["conversation"])
logger = get_logger()


def _stream_key(user_id: str, session_id: str | None) -> str:
    """Composite key for per-session stream tracking (enables concurrent sessions per user)."""
    return f"{user_id}:{session_id or 'default'}"


def sse(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE event string."""
    return f"data: {json.dumps({'type': event_type, 'data': data})}\n\n"

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


def _persist_tool_messages(conversation: Any, tool_events: list[dict[str, Any]]) -> None:
    for event in tool_events:
        output = event.get("output")
        if event.get("stage") != "end" or not output:
            continue
        conversation.add_message(
            "tool",
            str(output),
            metadata={
                "tool_name": event.get("tool") or event.get("tool_name") or "unknown",
                "tool_call_id": event.get("call_id") or event.get("tool_call_id") or "",
            },
        )


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
                "timestamp": m.ts.isoformat() if m.ts else None,
                "metadata": m.metadata,
            }
            for m in messages
        ]
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
    ]
}


def _stored_provider_key(user_id: str, provider: str) -> str | None:
    try:
        from src.sdk.providers.factory import _load_stored_key

        return _load_stored_key(provider, user_id)
    except Exception:
        return None


def _provider_key_source(provider: str, user_id: str) -> str | None:
    if _stored_provider_key(user_id, provider):
        return "user"
    env_map = {
        "agnes": "AGNES_API_KEY",
        "ollama-cloud": "OLLAMA_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_key = env_map.get(provider)
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
    for provider in ("agnes", "ollama-cloud", "anthropic", "openai"):
        if _provider_key_source(provider, user_id):
            configured.append(provider)

    models = []
    for provider in configured:
        provider_models = _STATIC_MODELS.get(provider)
        if provider_models is None:
            provider_models = [
                {
                    "id": f"{provider}:{m.id}",
                    "name": m.name,
                    "provider": provider,
                    "provider_display": _PROVIDER_DISPLAY.get(provider, provider.title()),
                }
                for m in list_models(provider=provider)
            ]
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


async def _summarize_title(user_msg: str, assistant_msg: str) -> str | None:
    """Generate a 3-5 word title via a simple provider.chat() call."""
    from src.config import get_settings
    from src.sdk.messages import Message
    from src.sdk.providers.factory import create_model_from_config

    settings = get_settings()
    model = settings.agent.title_model or settings.agent.model
    provider = create_model_from_config(model)

    prompt = (
        "Summarize the following conversation in 3-5 words. "
        "Use a short noun phrase. No punctuation at the end. No quotes.\n\n"
        f"User: {user_msg}\n"
        f"Assistant: {assistant_msg}\n\n"
        "Title:"
    )

    try:
        response = await provider.chat(
            messages=[Message.user(prompt)],
            tools=None,
            provider_options={"agnes": {"chat_template_kwargs": {"enable_thinking": False}}},
            max_tokens=20,
            temperature=0.3,
        )
        content_preview = response.content[:80] if len(response.content) > 80 else response.content
        logger.info("title_gen_response", {"content": content_preview})
        title = response.content.strip().strip('"').strip("'").strip()
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

    title = await _summarize_title(user_msg, assistant_msg)
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
        msg_content = req.message.strip()

        if user_id in _pending_approvals and msg_content.lower() in ("approve", "reject", "edit"):
            pending = _pending_approvals.pop(user_id)
            tool_name = pending["tool_name"]

            if msg_content.lower() == "reject":
                return MessageResponse(response=f"{tool_name} rejected.")

            tool_args = pending.get("tool_args", {})
            if "user_id" not in tool_args:
                tool_args["user_id"] = user_id

            return MessageResponse(response=f"{tool_name} approved (execution pending).")

        conversation = get_message_store(user_id)
        session_id = req.session_id or "default"
        conversation.add_message("user", req.message, metadata={}, session_id=session_id)

        recent_messages = conversation.get_messages_by_session_id(session_id, 50)
        sdk_messages = _messages_from_conversation(recent_messages)

        logger = get_logger()
        verbose_data: dict[str, Any] | None = None
        tool_events: list[dict[str, Any]] = []
        ai_content_parts: list[str] = []

        with timer(
            "agent",
            {"message": msg_content, "user_id": user_id, "verbose": req.verbose},
            channel="http",
        ):
            if req.verbose:
                async for chunk in run_sdk_agent_stream(
                    user_id=user_id,
                    messages=sdk_messages,
                    model=req.model,
                    provider_keys=req.provider_keys,
                ):
                    if chunk.type == "ai_token" and chunk.content:
                        ai_content_parts.append(chunk.content)
                    elif (
                        chunk.canonical_type == "text_delta"
                        and chunk.type != "ai_token"
                        and chunk.content
                    ):
                        ai_content_parts.append(chunk.content)
                    elif chunk.type == "tool_start" and chunk.tool:
                        tool_events.append(
                            {"tool": chunk.tool, "stage": "start", "call_id": chunk.call_id}
                        )
                    elif chunk.type == "tool_input_start" and chunk.tool:
                        tool_events.append(
                            {"tool": chunk.tool, "stage": "start", "call_id": chunk.call_id}
                        )
                    elif chunk.type == "tool_end" and chunk.tool:
                        tool_events.append(
                            {
                                "tool": chunk.tool,
                                "stage": "end",
                                "call_id": chunk.call_id,
                                "output": (chunk.result_preview or "")[:2000],
                            }
                        )
                    elif chunk.type == "tool_result" and chunk.tool:
                        tool_events.append(
                            {
                                "tool": chunk.tool,
                                "stage": "end",
                                "call_id": chunk.call_id,
                                "output": (chunk.result_preview or "")[:2000],
                            }
                        )

                verbose_data = {"tool_events": tool_events}

                response = ""
                if tool_events:
                    tool_outputs = [
                        t["output"]
                        for t in tool_events
                        if t.get("stage") == "end" and t.get("output")
                    ]
                    if tool_outputs:
                        response = "\n".join(tool_outputs)
                if not response and ai_content_parts:
                    response = "".join(ai_content_parts)
                if not response:
                    response = "Task completed."

                _persist_tool_messages(conversation, tool_events)
            else:
                result_messages = await run_sdk_agent(
                    user_id=user_id, messages=sdk_messages,
                    model=req.model, provider_keys=req.provider_keys,
                )

                tool_contents = []
                for m in result_messages:
                    if m.role == "tool" and m.content:
                        content = m.content if isinstance(m.content, str) else str(m.content)
                        tool_contents.append(content)
                        conversation.add_message(
                            "tool",
                            content,
                            metadata={"tool_name": m.name or "unknown"},
                        )

                response = ""
                last_ai = None
                for m in reversed(result_messages):
                    if m.role == "assistant" and m.content:
                        last_ai = m
                        break

                if (
                    last_ai
                    and last_ai.content
                    and (
                        last_ai.content
                        if isinstance(last_ai.content, str)
                        else str(last_ai.content)
                    ).strip()
                ):
                    response = (
                        last_ai.content
                        if isinstance(last_ai.content, str)
                        else str(last_ai.content)
                    )
                elif tool_contents:
                    response = "\n".join(tool_contents)

                if not response:
                    response = "Task completed."

        canvas_blocks = _extract_surfaces(response)
        response = _strip_canvas_fences(response)

        tool_calls_list = None
        if req.verbose:
            seen_call_ids: set[str] = set()
            tool_calls_list = []
            for t in tool_events:
                call_id = t.get("call_id", "")
                tool_name = t.get("tool", "")
                if tool_name and call_id not in seen_call_ids:
                    seen_call_ids.add(call_id)
                    tool_calls_list.append({"name": tool_name, "tool_call_id": call_id})

        assistant_metadata: dict[str, Any] = {}
        if verbose_data and verbose_data.get("tool_events"):
            assistant_metadata["tool_events"] = verbose_data["tool_events"]
        conversation.add_message("assistant", response, metadata=assistant_metadata, session_id=session_id)

        logger.info(
            "agent.response",
            {"response": response[:80], "verbose": req.verbose},
            user_id=user_id,
            channel="http",
        )

        if verbose_data is None:
            verbose_data = {}
        verbose_data["canvas_blocks"] = canvas_blocks

        return MessageResponse(
            response=response,
            verbose_data=verbose_data,
            tool_calls=tool_calls_list,
        )

    except Exception as e:
        import traceback

        traceback.print_exc()
        return MessageResponse(response="", error=str(e))


@router.post("/message/stream")
async def message_stream(req: MessageRequest, _: None = Depends(require_auth)) -> StreamingResponse:
    """Send a message and stream response using SSE (SDK-powered)."""
    try:
        user_id = req.user_id or "default_user"
        session_id = req.session_id or "default"
        skey = _stream_key(user_id, session_id)

        conversation = get_message_store(user_id)
        conversation.add_message("user", req.message, metadata={}, session_id=session_id)

        # Set up cancellation for this session's stream
        _cancel_flags[skey] = False
        cancel_event = asyncio.Event()
        _active_streams[skey] = cancel_event

        recent_messages = conversation.get_messages_by_session_id(session_id, 50)
        sdk_messages = _messages_from_conversation(recent_messages)

        logger = get_logger()

        async def generate() -> AsyncGenerator[str, None]:
            ai_content_parts: list[str] = []
            tool_metadata_list: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            response = ""

            try:
                async for chunk in run_sdk_agent_stream(
                    user_id=user_id,
                    messages=sdk_messages,
                    model=req.model,
                    provider_keys=req.provider_keys,
                    cancel_event=cancel_event,
                    session_id=session_id,
                ):
                    # Check cancel flag between chunks (fast path)
                    if _cancel_flags.get(skey, False) or cancel_event.is_set():
                        yield sse("cancelled", {"content": "Cancelled"})
                        break
                    canonical = chunk.canonical_type

                    if canonical == "text_delta" and chunk.type != "ai_token" and chunk.content:
                        ai_content_parts.append(chunk.content)
                        yield sse("messages", {"content": chunk.content})

                    elif canonical == "tool_input_start" and chunk.tool:
                        tool_metadata_list.append(
                            {"tool_name": chunk.tool, "tool_call_id": chunk.call_id or ""}
                        )
                        yield sse("tool_start", {
                            "tool": chunk.tool,
                            "call_id": chunk.call_id or "",
                            "args": chunk.args or {},
                        })

                    elif canonical == "tool_result" and chunk.tool:
                        output = (chunk.result_preview or "")[:500]
                        if output:
                            tool_results.append(
                                {"tool_call_id": chunk.call_id or "", "output": output}
                            )
                            yield sse("tool_result", {
                                "tool": chunk.tool,
                                "call_id": chunk.call_id or "",
                                "result": output,
                            })

                    elif canonical == "reasoning_delta" and chunk.content:
                        yield sse("reasoning", {"content": chunk.content})

                    elif canonical == "reasoning_start":
                        pass  # reasoning_start just signals a new reasoning block; the native app creates a bubble on first reasoning delta

                    elif chunk.type == "interrupt":
                        _pending_interrupts[skey] = {
                            "tool": chunk.tool,
                            "call_id": chunk.call_id,
                            "args": chunk.args or {},
                        }
                        yield sse("interrupt", {
                            "tool": chunk.tool,
                            "call_id": chunk.call_id,
                            "args": chunk.args,
                        })

                    elif chunk.type == "error":
                        yield sse("error", {"content": chunk.content})

                    elif chunk.type == "done" and chunk.content:
                        # Only emit done content as messages if no streaming text was received
                        if not ai_content_parts:
                            ai_content_parts.append(chunk.content)
                            yield sse("messages", {"content": chunk.content})

                response = "".join(ai_content_parts) if ai_content_parts else ""
                if not response and tool_results:
                    response = "\n".join(result["output"] for result in tool_results)
                if not response:
                    response = "Task completed."

                # Store messages immediately — before any yields
                # (client may disconnect, preventing post-yield storage)
                result_by_call_id = {
                    result["tool_call_id"]: result["output"] for result in tool_results
                }
                for tm in tool_metadata_list:
                    output = result_by_call_id.get(tm.get("tool_call_id", ""), "")
                    conversation.add_message("tool", output, metadata=tm, session_id=session_id)

                conversation.add_message("assistant", response.strip(), metadata={"stream": True}, session_id=session_id)
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

            except GeneratorExit:
                # Client disconnected — still store what we have
                response = "".join(ai_content_parts) if ai_content_parts else ""
                if response:
                    conversation.add_message("assistant", response.strip(), metadata={"stream": True}, session_id=session_id)
                    logger.info(
                        "agent.response_stored_disconnect", {"response": response[:80], "session_id": session_id}, user_id=user_id, channel="http"
                    )
                raise
            except asyncio.CancelledError:
                # asyncio cancellation — still store what we have
                response = "".join(ai_content_parts) if ai_content_parts else ""
                if response:
                    conversation.add_message("assistant", response.strip(), metadata={"stream": True}, session_id=session_id)
                    logger.info(
                        "agent.response_stored_cancelled", {"response": response[:80], "session_id": session_id}, user_id=user_id, channel="http"
                    )
                raise
            finally:
                # Clean up cancel tracking
                _active_streams.pop(skey, None)
                _cancel_flags.pop(skey, None)

        return StreamingResponse(generate(), media_type="text/event-stream")

    except Exception as e:
        skey = _stream_key(req.user_id or "default_user", req.session_id)
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


@router.post("/message/approve")
async def approve_tool(req: ApproveRequest, _: None = Depends(require_auth)) -> StreamingResponse:
    """Approve a pending tool call (HITL) and resume the agent as an SSE stream.

    Mirrors the WebSocket retry loop: pops the pending interrupt, marks the tool
    as approved on the cached loop, then re-runs the agent with an approval
    instruction appended. The response is a text/event-stream identical in shape
    to POST /message/stream so existing SSE clients can consume it unchanged.
    """
    session_id = req.session_id or "default"
    skey = _stream_key(req.user_id, session_id)
    pending = _pending_interrupts.pop(skey, None)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending tool call to approve")
    tool_name = pending.get("tool", "unknown")

    loop = await get_sdk_loop(req.user_id, model=req.model, provider_keys=req.provider_keys, session_id=session_id)
    loop._approved_tool_names.add(tool_name)

    conversation = get_message_store(req.user_id)
    cancel_event = asyncio.Event()
    _cancel_flags[skey] = False
    _active_streams[skey] = cancel_event

    async def generate() -> AsyncGenerator[str, None]:
        ai_content_parts: list[str] = []
        try:
            recent = conversation.get_messages_by_session_id(session_id, 50)
            retry_msgs = _messages_from_conversation(recent)
            retry_msgs.append(Message.user(f"approve: please proceed with {tool_name}"))

            async for chunk in run_sdk_agent_stream(
                user_id=req.user_id,
                messages=retry_msgs,
                model=req.model,
                provider_keys=req.provider_keys,
                cancel_event=cancel_event,
                session_id=session_id,
            ):
                if _cancel_flags.get(skey, False) or cancel_event.is_set():
                    yield sse("cancelled", {"content": "Cancelled"})
                    break
                canonical = chunk.canonical_type

                if canonical == "text_delta" and chunk.type != "ai_token" and chunk.content:
                    ai_content_parts.append(chunk.content)
                    yield sse("messages", {"content": chunk.content})

                elif canonical == "tool_input_start" and chunk.tool:
                    yield sse("tool_start", {
                        "tool": chunk.tool,
                        "call_id": chunk.call_id or "",
                        "args": chunk.args or {},
                    })

                elif canonical == "tool_result" and chunk.tool:
                    output = (chunk.result_preview or "")[:500]
                    if output:
                        yield sse("tool_result", {
                            "tool": chunk.tool,
                            "call_id": chunk.call_id or "",
                            "result": output,
                        })

                elif canonical == "reasoning_delta" and chunk.content:
                    yield sse("reasoning", {"content": chunk.content})

                elif canonical == "reasoning_start":
                    pass

                elif chunk.type == "interrupt":
                    _pending_interrupts[skey] = {
                        "tool": chunk.tool,
                        "call_id": chunk.call_id,
                        "args": chunk.args or {},
                    }
                    yield sse("interrupt", {
                        "tool": chunk.tool,
                        "call_id": chunk.call_id,
                        "args": chunk.args,
                    })

                elif chunk.type == "error":
                    yield sse("error", {"content": chunk.content})

                elif chunk.type == "done" and chunk.content:
                    if not ai_content_parts:
                        ai_content_parts.append(chunk.content)
                        yield sse("messages", {"content": chunk.content})

            response = "".join(ai_content_parts) if ai_content_parts else ""
            if response:
                conversation.add_message(
                    "assistant", response, metadata={"stream": True}, session_id=session_id
                )
        except Exception as e:
            logger.error("approve_stream_error", {"error": str(e)}, user_id=req.user_id, channel="http")
            yield sse("error", {"content": str(e)})
        finally:
            _cancel_flags.pop(skey, None)
            _active_streams.pop(skey, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/message/reject")
async def reject_tool(req: RejectRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Reject a pending tool call (HITL)."""
    skey = _stream_key(req.user_id, req.session_id or req.call_id or None)
    pending = _pending_interrupts.pop(skey, None)
    if not pending:
        # Fallback: try user_id-only key for backward compat
        pending = _pending_interrupts.pop(req.user_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending tool call to reject")
    return {"status": "rejected", "tool": pending.get("tool", "unknown")}


@router.post("/message/cancel")
async def cancel_message(req: CancelRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Cancel the current agent execution for a session."""
    skey = _stream_key(req.user_id, req.session_id)
    _cancel_flags[skey] = True
    # Signal the active stream to break
    event = _active_streams.get(skey)
    if event:
        event.set()
    reset_sdk_loop(req.user_id, session_id=req.session_id)
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
