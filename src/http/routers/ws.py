"""WebSocket conversation endpoint for bidirectional agent communication.

This replaces the SSE /message/stream endpoint with a proper WebSocket
that supports:
- Streaming AI tokens
- Tool call events
- Human-in-the-loop interrupts
- Cancel/approve/reject
- Middleware events (verbose mode)

Uses the SDK AgentLoop for all agent execution.
"""

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.app_logging import get_logger
from src.config.settings import get_settings
from src.http.auth import verify_key
from src.http.routers.conversation import _extract_surfaces, _strip_canvas_fences
from src.http.ws_protocol import (
    ApproveMessage,
    AuthMessage,
    AuthOkMessage,
    CancelMessage,
    CanvasUpdateMessage,
    DoneMessage,
    EditAndApproveMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
    RejectMessage,
    parse_client_message,
)
from src.sdk.messages import Message
from src.sdk.runner import (
    _messages_from_conversation,
    get_sdk_loop,
    run_sdk_agent_stream,
)
from src.storage.messages import get_message_store

logger = get_logger()

router = APIRouter(tags=["websocket"])


async def _run_agent_stream(
    websocket: WebSocket,
    user_id: str,
    sdk_messages: list[Message],
    conversation: Any,
    session_id: str = "",
    pending_ref: list[Any] | None = None,
    workspace_id: str = "personal",
    model: str | None = None,
    provider_keys: dict[str, str] | None = None,
) -> None:
    """Run the agent streaming loop and handle all chunk types."""
    import uuid as _uuid

    def _with_workspace(payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "workspace_id": workspace_id}

    ai_content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_metadata_list: list[dict[str, Any]] = []
    tool_results: dict[str, str] = {}
    skill_load_names: dict[str, str] = {}

    try:
        async for chunk in run_sdk_agent_stream(
            user_id=user_id,
            messages=sdk_messages,
            workspace_id=workspace_id,
            model=model,
            provider_keys=provider_keys,
        ):
            canonical = chunk.canonical_type
            is_compat_alias = chunk.type != canonical

            if canonical == "text_delta" and chunk.content and not is_compat_alias:
                ai_content_parts.append(chunk.content)
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "text_start" and not is_compat_alias:
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "text_end" and not is_compat_alias:
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "tool_input_start" and not is_compat_alias:
                tool_name = chunk.tool or "unknown"
                call_id = chunk.call_id or str(_uuid.uuid4())[:8]
                tool_metadata_list.append(
                    {"tool_name": tool_name, "tool_call_id": call_id}
                )
                if tool_name == "skills_load":
                    skill_load_names[call_id] = (chunk.args or {}).get("name", "unknown")
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "tool_input_delta":
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "tool_input_end":
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "reasoning_start" and not is_compat_alias:
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "reasoning_delta" and not is_compat_alias:
                reasoning_parts.append(chunk.content or "")
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "reasoning_end" and not is_compat_alias:
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif canonical == "tool_result" or chunk.type == "tool_result":
                tool_name = chunk.tool or "unknown"
                call_id = chunk.call_id or "unknown"
                result_preview = chunk.result_preview or ""
                tool_results[call_id] = result_preview[:500]
                from src.http.ws_protocol import SkillsLoadMessage, ToolResultMessage

                await websocket.send_json(
                    ToolResultMessage(
                        tool=tool_name,
                        call_id=call_id,
                        result_preview=result_preview[:500],
                    ).model_dump() | {"workspace_id": workspace_id}
                )

                if tool_name == "skills_load":
                    skill_name = skill_load_names.pop(call_id, "unknown")
                    await websocket.send_json(
                        SkillsLoadMessage(name=skill_name).model_dump()
                        | {"workspace_id": workspace_id}
                    )

            elif chunk.type == "interrupt":
                if pending_ref is not None:
                    pending_ref[0] = {
                        "tool": chunk.tool or "unknown",
                        "call_id": chunk.call_id or "unknown",
                        "args": chunk.args or {},
                    }
                await websocket.send_json(_with_workspace(chunk.to_ws_message()))

            elif chunk.type == "done":
                response = "".join(ai_content_parts)

                canvas_blocks = _extract_surfaces(response)
                for surface in canvas_blocks:
                    await _handle_canvas_update(
                        websocket,
                        surface_id=surface["surface_id"],
                        action="create",
                        html=surface["html"],
                        workspace_id=workspace_id,
                    )

                response = _strip_canvas_fences(response)

                reasoning_content = "".join(reasoning_parts) if reasoning_parts else None

                for tm in tool_metadata_list:
                    call_id = tm.get("tool_call_id", "")
                    result_content = tool_results.get(call_id, "")
                    conversation.add_message(
                        "tool", result_content, metadata={**tm, "workspace_id": workspace_id}
                    )

                if reasoning_content:
                    conversation.add_message(
                        "reasoning",
                        reasoning_content,
                        metadata={"session_id": session_id, "workspace_id": workspace_id},
                    )

                msg_id = conversation.add_message(
                    "assistant",
                    response,
                    metadata={
                        "stream": True,
                        "session_id": session_id,
                        "workspace_id": workspace_id,
                    },
                )

                await websocket.send_json(
                    DoneMessage(
                        response=response,
                        message_id=str(msg_id),
                        tool_calls=[
                            {"tool": tm["tool_name"], "call_id": tm["tool_call_id"]}
                            for tm in tool_metadata_list
                        ],
                    ).model_dump() | {"workspace_id": workspace_id}
                )

            elif chunk.type == "error":
                await websocket.send_json(
                    ErrorMessage(message=str(chunk.content), code="AGENT_ERROR").model_dump()
                    | {"workspace_id": workspace_id}
                )

    except Exception as e:
        logger.error(
            "ws.sdk_agent_error",
            {"error": str(e), "error_type": type(e).__name__},
            user_id=user_id,
            channel="ws",
        )
        await websocket.send_json(
            ErrorMessage(message=str(e), code="AGENT_ERROR").model_dump()
        )


async def _handle_canvas_update(
    websocket: WebSocket,
    surface_id: str,
    action: Literal["create", "update", "destroy"],
    html: str = "",
    workspace_id: str = "personal",
) -> None:
    """Broadcast a canvas_update event to the connected WebSocket client."""
    await websocket.send_json(
        CanvasUpdateMessage(
            surface_id=surface_id,
            action=action,
            html=html,
        ).model_dump()
        | {"workspace_id": workspace_id}
    )


async def _handle_canvas_update_from_preview(
    call_id: str,
    result_preview: str,
    workspace_id: str,
    websocket: WebSocket,
    tool_responses: dict[str, Any],
) -> None:
    """Extract HTML from canvas_paint result and broadcast as canvas_update."""
    html = tool_responses.pop(call_id, result_preview)
    if not html or len(html) < 10:
        return
    await _handle_canvas_update(
        websocket,
        surface_id=f"canvas-{abs(hash(html)) % 100000}",
        action="create",
        html=html,
        workspace_id=workspace_id,
    )


@router.websocket("/ws/conversation")
async def ws_conversation(websocket: WebSocket) -> None:
    """WebSocket endpoint for bidirectional agent conversation.

    Protocol:
    - Client sends JSON messages with a 'type' field
    - Server streams back JSON messages with a 'type' field
    - See src/http/ws_protocol.py for all message types

    Client → Server:
        user_message: Send a chat message
        approve: Approve a pending tool call (HITL)
        reject: Reject a pending tool call (HITL)
        edit_and_approve: Edit tool args and approve (HITL)
        cancel: Cancel ongoing agent execution
        ping: Heartbeat

    Server → Client:
        ai_token: Streaming text token
        tool_start: Tool call started
        tool_end: Tool call completed
        interrupt: Agent requests human approval
        middleware: Middleware event (verbose mode)
        reasoning: Thinking token (reasoning models)
        done: Agent execution complete
        error: Error occurred
        pong: Heartbeat response
    """
    await websocket.accept()

    # ── API key auth (first message after connect) ─────────────────────────
    settings = get_settings()
    needs_auth = bool(settings.auth.api_key)

    # Check if this is a localhost WebSocket (bypass solo auth)
    if needs_auth and settings.auth.solo_bypass:
        client = websocket.client if hasattr(websocket, "client") else None
        if client and client.host in ("127.0.0.1", "::1", "localhost"):
            needs_auth = False

    if needs_auth:
        raw = await websocket.receive_text()
        try:
            data = json.loads(raw)
            auth_msg = AuthMessage.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            await websocket.send_json(
                ErrorMessage(message="Authentication required", code="AUTH_FAILED").model_dump()
            )
            await websocket.close()
            return

        if not verify_key(auth_msg.api_key):
            await websocket.send_json(
                ErrorMessage(message="Invalid API key", code="AUTH_FAILED").model_dump()
            )
            await websocket.close()
            return

        await websocket.send_json(AuthOkMessage().model_dump())
    # ── End auth ──────────────────────────────────────────────────────────

    session_id = str(uuid.uuid4())[:8]
    user_id = "default_user"
    workspace_id = "personal"
    verbose = False
    current_model: str | None = None
    current_provider_keys: dict[str, str] | None = None
    pending_container: list[Any] = [None]

    try:
        while True:
            try:
                raw_data = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_json(
                    ErrorMessage(message="Invalid JSON", code="PARSE_ERROR").model_dump()
                )
                continue

            msg = parse_client_message(data)

            if msg is None:
                await websocket.send_json(
                    ErrorMessage(
                        message=f"Unknown message type: {data.get('type', 'missing')}",
                        code="UNKNOWN_TYPE",
                    ).model_dump()
                )
                continue

            if isinstance(msg, PingMessage):
                await websocket.send_json(PongMessage().model_dump())
                continue

            if isinstance(msg, ApproveMessage):
                if pending_container[0]:
                    tool_name = pending_container[0].get("tool", "unknown")
                    loop = await get_sdk_loop(
                        user_id,
                        workspace_id,
                        model=current_model,
                        provider_keys=current_provider_keys,
                    )
                    loop._approved_tool_names.add(tool_name)
                    pending_container[0] = None
                    conversation = get_message_store(user_id, workspace_id)
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(50, workspace_id=workspace_id)
                    )
                    retry_msgs.append(Message.user(f"approve: please proceed with {tool_name}"))
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=current_model, provider_keys=current_provider_keys,
                    )
                continue

            if isinstance(msg, RejectMessage):
                if pending_container[0]:
                    await websocket.send_json(
                        DoneMessage(
                            response=f"Rejected: {pending_container[0].get('tool', 'unknown')}"
                        ).model_dump()
                    )
                    pending_container[0] = None
                else:
                    await websocket.send_json(
                        ErrorMessage(
                            message="No pending tool call to reject",
                            code="NO_PENDING_INTERRUPT",
                        ).model_dump()
                    )
                continue

            if isinstance(msg, EditAndApproveMessage):
                if pending_container[0]:
                    tool_name = pending_container[0].get("tool", "unknown")
                    loop = await get_sdk_loop(
                        user_id,
                        workspace_id,
                        model=current_model,
                        provider_keys=current_provider_keys,
                    )
                    loop._approved_tool_names.add(tool_name)
                    pending_container[0] = None
                    conversation = get_message_store(user_id, workspace_id)
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(50, workspace_id=workspace_id)
                    )
                    retry_msgs.append(Message.user(f"approved: proceed with {tool_name} with edited args: {msg.edited_args}"))
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=current_model, provider_keys=current_provider_keys,
                    )
                else:
                    await websocket.send_json(
                        ErrorMessage(
                            message="No pending tool call to edit",
                            code="NO_PENDING_INTERRUPT",
                        ).model_dump()
                    )
                continue

            if isinstance(msg, CancelMessage):
                await websocket.send_json(DoneMessage(response="Cancelled").model_dump())
                break

            user_id = getattr(msg, "user_id", user_id) or user_id
            workspace_id = getattr(msg, "workspace_id", workspace_id) or workspace_id
            verbose = getattr(msg, "verbose", verbose)
            msg_model: str | None = getattr(msg, "model", None)
            msg_provider_keys: dict[str, str] | None = getattr(msg, "provider_keys", None)
            current_model = msg_model
            current_provider_keys = msg_provider_keys

            if not hasattr(msg, "content"):
                continue

            content = msg.content
            conversation = get_message_store(user_id, workspace_id)

            # If user types "approve" while a tool is pending, trigger retry
            if pending_container[0] and content.strip().lower() in ("approve", "yes", "accept"):
                tool_name = pending_container[0].get("tool", "unknown")
                loop = await get_sdk_loop(user_id, workspace_id, model=msg_model, provider_keys=msg_provider_keys)
                loop._approved_tool_names.add(tool_name)
                pending_container[0] = None
                # Fall through — the message is added below once

            import time
            t0 = time.monotonic()

            t1 = time.monotonic()

            conversation.add_message("user", content, metadata={"workspace_id": workspace_id})
            t2 = time.monotonic()

            recent_messages = conversation.get_messages_with_summary(50, workspace_id=workspace_id)
            t3 = time.monotonic()

            sdk_messages = _messages_from_conversation(recent_messages)

            t4 = time.monotonic()
            logger.info(
                "ws.pre_loop_timing",
                {
                    "get_store": f"{t1 - t0:.3f}s",
                    "add_msg": f"{t2 - t1:.3f}s",
                    "get_msgs": f"{t3 - t2:.3f}s",
                    "convert": f"{t4 - t3:.3f}s",
                    "total": f"{t4 - t0:.3f}s",
                    "user_id": user_id,
                },
                user_id=user_id,
                channel="ws",
            )

            await _run_agent_stream(
                websocket, user_id, sdk_messages, conversation, session_id,
                pending_ref=pending_container, workspace_id=workspace_id,
                model=msg_model, provider_keys=msg_provider_keys,
            )
            # After stream finishes: if a tool was interrupted, wait for approval
            while pending_container[0] is not None:
                tool_name = pending_container[0].get("tool", "unknown")
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=300)
                except (TimeoutError, WebSocketDisconnect):
                    pending_container[0] = None
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type", "")
                content = data.get("content", "")
                is_approve = msg_type in ("approve_tool", "approve") or (
                    msg_type == "user_message"
                    and content.strip().lower() in ("approve", "approved", "yes", "accept")
                )
                is_reject = msg_type in ("reject_tool", "reject") or (
                    msg_type == "user_message"
                    and content.strip().lower() in ("reject", "rejected", "no", "deny")
                )
                if is_approve:
                    loop = await get_sdk_loop(user_id, workspace_id, model=msg_model, provider_keys=msg_provider_keys)
                    loop._approved_tool_names.add(tool_name)
                    pending_container[0] = None
                    # Retry with approval context
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(50, workspace_id=workspace_id)
                    )
                    retry_msgs.append(Message.user(f"approve: please proceed with {tool_name}"))
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=msg_model, provider_keys=msg_provider_keys,
                    )
                elif is_reject:
                    pending_container[0] = None
                    break

    except WebSocketDisconnect:
        logger.info(
            "ws.disconnected",
            {"session_id": session_id, "user_id": user_id},
            user_id=user_id,
            channel="ws",
        )
    except Exception as e:
        logger.error(
            "ws.error", {"error": str(e), "session_id": session_id}, user_id=user_id, channel="ws"
        )
        try:
            await websocket.send_json(
                ErrorMessage(message=str(e), code="WEBSOCKET_ERROR").model_dump()
            )
        except Exception:
            pass
