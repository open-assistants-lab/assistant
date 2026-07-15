import json
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

router = APIRouter(tags=["conversation"])
logger = get_logger()

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
    all_messages = conversation.get_recent_messages(10000)

    sessions: dict[str, str] = {}
    for msg in all_messages:
        if msg.role == "user":
            # Extract session_id from metadata or use "default"
            sid = "default"
            if msg.metadata and "session_id" in msg.metadata:
                sid = msg.metadata["session_id"]
            if sid not in sessions:
                title = msg.content[:60] if len(msg.content) > 60 else msg.content
                sessions[sid] = title

    return {
        "sessions": [
            {"session_id": sid, "title": title}
            for sid, title in sessions.items()
        ]
    }


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

        conversation = get_message_store(user_id)
        conversation.add_message("user", req.message, metadata={}, session_id=session_id)

        recent_messages = conversation.get_messages_by_session_id(session_id, 50)
        sdk_messages = _messages_from_conversation(recent_messages)

        logger = get_logger()

        async def generate() -> AsyncGenerator[str, None]:
            ai_content_parts: list[str] = []
            tool_metadata_list: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []

            async for chunk in run_sdk_agent_stream(
                user_id=user_id,
                messages=sdk_messages,
                model=req.model,
                provider_keys=req.provider_keys,
            ):
                canonical = chunk.canonical_type

                if canonical == "text_delta" and chunk.type != "ai_token" and chunk.content:
                    ai_content_parts.append(chunk.content)
                    yield f"data: {json.dumps({'type': 'messages', 'data': {'content': chunk.content}})}\n\n"

                elif canonical == "tool_input_start" and chunk.tool:
                    tool_metadata_list.append(
                        {"tool_name": chunk.tool, "tool_call_id": chunk.call_id or ""}
                    )
                    yield f"data: {json.dumps({'type': 'updates', 'data': {'content': f'Using tool: {chunk.tool}'}})}\n\n"

                elif canonical == "tool_result" and chunk.tool:
                    output = (chunk.result_preview or "")[:500]
                    if output:
                        tool_results.append(
                            {"tool_call_id": chunk.call_id or "", "output": output}
                        )
                        yield f"data: {json.dumps({'type': 'updates', 'data': {'content': output}})}\n\n"

                elif canonical == "reasoning_delta" and chunk.content:
                    yield f"data: {json.dumps({'type': 'messages', 'data': {'content': f'[Reasoning] {chunk.content}'}})}\n\n"

                elif canonical == "reasoning_start":
                    yield f"data: {json.dumps({'type': 'updates', 'data': {'content': '[Thinking...]'}})}\n\n"

                elif chunk.type == "interrupt":
                    _pending_interrupts[user_id] = {
                        "tool": chunk.tool,
                        "call_id": chunk.call_id,
                        "args": chunk.args or {},
                    }
                    yield f"data: {json.dumps({'type': 'interrupt', 'data': {'tool': chunk.tool, 'call_id': chunk.call_id, 'args': chunk.args}})}\n\n"

                elif chunk.type == "error":
                    yield f"data: {json.dumps({'type': 'error', 'data': {'content': chunk.content}})}\n\n"

            response = "".join(ai_content_parts) if ai_content_parts else ""
            if not response and tool_results:
                response = "\n".join(result["output"] for result in tool_results)
            if not response:
                response = "Task completed."

            canvas_blocks = _extract_surfaces(response)
            for surface in canvas_blocks:
                yield f"data: {json.dumps({'type': 'canvas_update', 'data': surface})}\n\n"
            response = _strip_canvas_fences(response)

            result_by_call_id = {
                result["tool_call_id"]: result["output"] for result in tool_results
            }
            for tm in tool_metadata_list:
                output = result_by_call_id.get(tm.get("tool_call_id", ""), "")
                conversation.add_message("tool", output, metadata=tm, session_id=session_id)

            conversation.add_message("assistant", response, metadata={"stream": True}, session_id=session_id)
            logger.info(
                "agent.response", {"response": response[:80]}, user_id=user_id, channel="http"
            )

        return StreamingResponse(generate(), media_type="text/event-stream")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ApproveRequest(BaseModel):
    user_id: str = "default_user"
    call_id: str = ""


class RejectRequest(BaseModel):
    user_id: str = "default_user"
    call_id: str = ""
    reason: str = ""


class CancelRequest(BaseModel):
    user_id: str = "default_user"


@router.post("/message/approve")
async def approve_tool(req: ApproveRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Approve a pending tool call (HITL)."""
    pending = _pending_interrupts.pop(req.user_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending tool call to approve")
    tool_name = pending.get("tool", "unknown")
    loop = await get_sdk_loop(req.user_id)
    loop._approved_tool_names.add(tool_name)
    return {"status": "approved", "tool": tool_name}


@router.post("/message/reject")
async def reject_tool(req: RejectRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Reject a pending tool call (HITL)."""
    pending = _pending_interrupts.pop(req.user_id, None)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending tool call to reject")
    return {"status": "rejected", "tool": pending.get("tool", "unknown")}


@router.post("/message/cancel")
async def cancel_message(req: CancelRequest, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Cancel the current agent execution."""
    reset_sdk_loop(req.user_id)
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

