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
import secrets
import uuid
from typing import Any, Literal, cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.app_logging import get_logger
from src.config.settings import get_settings
from src.http.auth import verify_key
from src.http.routers.conversation import (
    _execute_approved_tool,
    _extract_surfaces,
    _persist_collected_stream_state,
    _strip_canvas_fences,
)
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
    SteerAckMessage,
    SteerMessage,
    parse_client_message,
)
from src.sdk.messages import Message, ToolCall
from src.sdk.run_service import RunService
from src.sdk.runner import (
    _messages_from_conversation,
    get_sdk_loop,
)
from src.sdk.session_worker import SessionBusyError, get_session_registry
from src.storage.messages import aget_message_store

logger = get_logger()

router = APIRouter(tags=["websocket"])

_session_registry = get_session_registry()


def _persist_ws_conversation_message(
    conversation: Any, role: str, content: str, session_id: str, metadata: dict[str, Any] | None = None
) -> str:
    return cast(str, conversation.add_message(role, content, metadata=metadata or {}, session_id=session_id))


def _resolve_ws_session_id(msg: Any, fallback: str) -> str:
    session_id = getattr(msg, "session_id", None) or fallback
    return session_id.strip() if session_id.strip() else "default"


def _pending_runtime_context(
    pending: dict[str, Any],
    session_id: str,
    model: str | None,
    provider_keys: dict[str, str] | None,
) -> tuple[str, str | None, dict[str, str] | None]:
    run_session_id = pending.get("session_id") or session_id
    return (
        run_session_id.strip() if run_session_id.strip() else "default",
        pending.get("model"),
        pending.get("provider_keys"),
    )


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
    cancel_event: asyncio.Event | None = None,
    rubric: str | None = None,
    stream_loop_out: dict[str, Any] | None = None,
) -> None:
    """Run the agent streaming loop and handle all chunk types.

    ``stream_loop_out`` (audit E25): when provided, the live loop is written
    into the dict via execute_stream's on_stream_end callback so the caller
    can run follow-up steers after RunService unregisters the loop.
    """
    import uuid as _uuid

    def _with_workspace(payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "workspace_id": workspace_id}

    ai_content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_metadata_list: list[dict[str, Any]] = []
    tool_results: dict[str, str] = {}
    emitted_tool_results: set[str] = set()
    skill_load_names: dict[str, str] = {}
    persisted = False

    run_service = RunService(user_id, _session_registry, conversation)

    try:
        async for event in run_service.execute_stream(
            session_id=session_id,
            prompt=str(sdk_messages[-1].content) if sdk_messages else "",
            model=model,
            provider_keys=provider_keys,
            on_stream_end=(
                (lambda lp: stream_loop_out.__setitem__("loop", lp))
                if stream_loop_out is not None
                else None
            ),
        ):
            if cancel_event is not None and cancel_event.is_set():
                if not persisted:
                    _persist_collected_stream_state(
                        conversation,
                        session_id=session_id,
                        ai_content_parts=ai_content_parts,
                        reasoning_parts=reasoning_parts,
                        tool_metadata_list=tool_metadata_list,
                        tool_results=tool_results,
                        run_id=event.run_id,
                    )
                    persisted = True
                break

            event_type = event.type
            data = event.model_dump(mode="json")
            event_data = data.get("data", {})

            if event_type == "text_delta":
                delta = event_data.get("delta", "")
                if delta:
                    ai_content_parts.append(delta)
                    await websocket.send_json(_with_workspace(data))

            elif event_type == "reasoning_delta":
                delta = event_data.get("delta", "")
                if delta:
                    reasoning_parts.append(delta)
                    await websocket.send_json(_with_workspace(data))

            elif event_type == "tool_input_start":
                tool_name = event_data.get("name", "unknown")
                call_id = event_data.get("tool_call_id", str(_uuid.uuid4())[:8])
                tool_metadata_list.append(
                    {"tool_name": tool_name, "tool_call_id": call_id}
                )
                if tool_name == "skills_load":
                    skill_load_names[call_id] = (event_data.get("args", {}) or {}).get("name", "unknown")
                await websocket.send_json(_with_workspace(data))

            elif event_type == "tool_input_delta":
                await websocket.send_json(_with_workspace(data))

            elif event_type == "tool_input_end":
                await websocket.send_json(_with_workspace(data))

            elif event_type == "reasoning_start":
                await websocket.send_json(_with_workspace(data))

            elif event_type == "reasoning_end":
                await websocket.send_json(_with_workspace(data))

            elif event_type == "text_start":
                await websocket.send_json(_with_workspace(data))

            elif event_type == "text_end":
                await websocket.send_json(_with_workspace(data))

            elif event_type == "tool_result":
                tool_name = event_data.get("name", "unknown")
                call_id = event_data.get("tool_call_id", "unknown")
                content = event_data.get("content", "")
                tool_results[call_id] = str(content)[:500]
                if call_id in emitted_tool_results:
                    continue
                emitted_tool_results.add(call_id)
                # Canonical envelope (E-streaming): forward the RunEvent
                # envelope as-is; ToolResultData carries name/tool_call_id/
                # status/content, so no flat re-wrap is needed.
                await websocket.send_json(_with_workspace(data))

                if tool_name == "skills_load":
                    skill_name = skill_load_names.pop(call_id, "unknown")
                    await websocket.send_json(
                        {"type": "skills_load", "data": {"name": skill_name}, "workspace_id": workspace_id}
                    )

            elif event_type == "usage":
                # Forward usage events (canonical envelope) so WS clients
                # see token accounting like SSE clients do.
                await websocket.send_json(_with_workspace(data))

            elif event_type == "interrupt":
                if pending_ref is not None:
                    pending_ref[0] = {
                        "tool": event_data.get("tool", "unknown"),
                        "call_id": event_data.get("call_id", "unknown"),
                        "args": event_data.get("args", {}),
                        "model": model,
                        "provider_keys": provider_keys,
                        "session_id": session_id,
                    }
                await websocket.send_json(_with_workspace(data))
                _persist_collected_stream_state(
                    conversation,
                    session_id=session_id,
                    ai_content_parts=ai_content_parts,
                    reasoning_parts=reasoning_parts,
                    tool_metadata_list=tool_metadata_list,
                    tool_results=tool_results,
                    run_id=event.run_id,
                )
                persisted = True
                break

            elif event_type == "done":
                result = event_data.get("result", {})
                if result.get("status") == "failed":
                    # B11: RunService.persist_run already persisted this run's
                    # partial state WITH run_id — the fallback collector here
                    # wrote a duplicate, run_id-less copy. Do nothing.
                    await websocket.send_json(
                        ErrorMessage(message="Agent run failed", code="AGENT_ERROR").model_dump()
                        | {"workspace_id": workspace_id}
                    )
                    break
                response = result.get("response", "")
                if not ai_content_parts and response:
                    ai_content_parts.append(response)
                # The streamed deltas are the source of truth for what was
                # rendered; fall back to the done result only when nothing
                # streamed (mirrors the conversation router's post-loop join).
                response = "".join(ai_content_parts) if ai_content_parts else response

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

                # Tool messages and reasoning are persisted by
                # RunService.persist_run (tools as audit records with run_id +
                # tool_name metadata, reasoning as a pre-message) — persisting
                # them again here would duplicate them without run_id. The WS
                # path only persists collected state on failure/cancel paths.
                msg_id = result.get("final_message_id") or ""
                persisted = True

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

            elif event_type == "error":
                _persist_collected_stream_state(
                    conversation,
                    session_id=session_id,
                    ai_content_parts=ai_content_parts,
                    reasoning_parts=reasoning_parts,
                    tool_metadata_list=tool_metadata_list,
                    tool_results=tool_results,
                    run_id=event.run_id,
                )
                persisted = True
                await websocket.send_json(
                    ErrorMessage(message=str(event_data.get("message", "")), code="AGENT_ERROR").model_dump()
                    | {"workspace_id": workspace_id}
                )
                break

            elif event_type == "rubric_evaluation_start":
                await websocket.send_json(
                    {"type": "rubric_evaluation_start", "data": event_data}
                    | {"workspace_id": workspace_id}
                )

            elif event_type == "rubric_evaluation_end":
                await websocket.send_json(
                    {"type": "rubric_evaluation_end", "data": event_data}
                    | {"workspace_id": workspace_id}
                )

    except SessionBusyError:
        await websocket.send_json(
            ErrorMessage(message="Session already has an active run", code="SESSION_BUSY").model_dump()
        )
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
        if not persisted:
            _persist_collected_stream_state(
                conversation,
                session_id=session_id,
                ai_content_parts=ai_content_parts,
                reasoning_parts=reasoning_parts,
                tool_metadata_list=tool_metadata_list,
                tool_results=tool_results,
            )
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

    # Persistent reader (audit P6 part B): ONE task owns the socket and feeds
    # an asyncio.Queue. Per-pass receive tasks raced the stream and cancelled a
    # pending frame at stream end, losing it (a frame popped from the socket
    # then discarded). The queue preserves every frame; consumers cancel a
    # queue.get() safely because the frame stays queued.
    control_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _ws_reader() -> None:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                await control_queue.put(None)
                return
            await control_queue.put(raw)

    reader_task = asyncio.create_task(_ws_reader())

    try:
        while True:
            raw_data = await control_queue.get()
            if raw_data is None:
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
                    if msg.call_id != pending_container[0].get("call_id"):
                        await websocket.send_json(
                            ErrorMessage(
                                message="Pending tool call does not match call_id",
                                code="CALL_ID_MISMATCH",
                            ).model_dump()
                        )
                        continue
                    tool_name = pending_container[0].get("tool", "unknown")
                    run_session_id, run_model, run_provider_keys = _pending_runtime_context(
                        pending_container[0], session_id, current_model, current_provider_keys
                    )
                    loop = await get_sdk_loop(
                        user_id,
                        workspace_id,
                        session_id=run_session_id,
                        model=run_model,
                        provider_keys=run_provider_keys,
                    )
                    loop.approve_tool_call(
                        ToolCall(
                            id=pending_container[0].get("call_id") or msg.call_id,
                            name=tool_name,
                            arguments=pending_container[0].get("args") or {},
                        )
                    )
                    approved_args = pending_container[0].get("args") or {}
                    approved_call_id = pending_container[0].get("call_id") or msg.call_id
                    pending_container[0] = None
                    conversation = await aget_message_store(user_id, workspace_id)
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(
                            session_id=run_session_id, limit=50
                        )
                    )
                    # Execute the approved tool directly — see
                    # conversation._execute_approved_tool (re-proposals never
                    # match the approved call, so the old instruction-only
                    # flow looped on HITL interrupts).
                    retry_msgs.extend(
                        await _execute_approved_tool(
                            loop,
                            tool_name,
                            approved_args,
                            approved_call_id or f"call_{secrets.token_hex(4)}",
                        )
                    )
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, run_session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=run_model, provider_keys=run_provider_keys,
                    )
                else:
                    await websocket.send_json(
                        ErrorMessage(
                            message="No pending tool call to approve",
                            code="NO_PENDING_INTERRUPT",
                        ).model_dump()
                    )
                continue

            if isinstance(msg, RejectMessage):
                if pending_container[0]:
                    if msg.call_id != pending_container[0].get("call_id"):
                        await websocket.send_json(
                            ErrorMessage(
                                message="Pending tool call does not match call_id",
                                code="CALL_ID_MISMATCH",
                            ).model_dump()
                        )
                        continue
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
                    if msg.call_id != pending_container[0].get("call_id"):
                        await websocket.send_json(
                            ErrorMessage(
                                message="Pending tool call does not match call_id",
                                code="CALL_ID_MISMATCH",
                            ).model_dump()
                        )
                        continue
                    tool_name = pending_container[0].get("tool", "unknown")
                    run_session_id, run_model, run_provider_keys = _pending_runtime_context(
                        pending_container[0], session_id, current_model, current_provider_keys
                    )
                    loop = await get_sdk_loop(
                        user_id,
                        workspace_id,
                        session_id=run_session_id,
                        model=run_model,
                        provider_keys=run_provider_keys,
                    )
                    edited_args = msg.edited_args or {}
                    approved_call_id = pending_container[0].get("call_id") or msg.call_id
                    loop.approve_tool_call(
                        ToolCall(
                            id=approved_call_id,
                            name=tool_name,
                            arguments=edited_args,
                        )
                    )
                    pending_container[0] = None
                    conversation = await aget_message_store(user_id, workspace_id)
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(
                            session_id=run_session_id, limit=50
                        )
                    )
                    # Execute the approved tool directly — the instruction-only
                    # retry (the old approved-nudge message) looped forever because
                    # re-proposals never match the approved call (see
                    # conversation._execute_approved_tool).
                    retry_msgs.extend(
                        await _execute_approved_tool(
                            loop,
                            tool_name,
                            edited_args,
                            approved_call_id or f"call_{secrets.token_hex(4)}",
                        )
                    )
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, run_session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=run_model, provider_keys=run_provider_keys,
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
            session_id = _resolve_ws_session_id(msg, session_id)
            verbose = getattr(msg, "verbose", verbose)
            msg_model: str | None = getattr(msg, "model", None)
            msg_provider_keys: dict[str, str] | None = getattr(msg, "provider_keys", None)
            current_model = msg_model
            current_provider_keys = msg_provider_keys

            if not hasattr(msg, "content"):
                continue

            content = msg.content
            conversation = await aget_message_store(user_id, workspace_id)

            # If user types "approve" while a tool is pending, trigger retry
            if pending_container[0] and content.strip().lower() in ("approve", "yes", "accept"):
                tool_name = pending_container[0].get("tool", "unknown")
                run_session_id, run_model, run_provider_keys = _pending_runtime_context(
                    pending_container[0], session_id, msg_model, msg_provider_keys
                )
                loop = await get_sdk_loop(
                    user_id,
                    workspace_id,
                    session_id=run_session_id,
                    model=run_model,
                    provider_keys=run_provider_keys,
                )
                loop.approve_tool_call(
                    ToolCall(
                        id=pending_container[0].get("call_id") or "",
                        name=tool_name,
                        arguments=pending_container[0].get("args") or {},
                    )
                )
                pending_container[0] = None
                # Fall through — the message is added below once

            import time
            t0 = time.monotonic()

            t1 = time.monotonic()

            _persist_ws_conversation_message(conversation, "user", content, session_id=session_id)
            t2 = time.monotonic()

            recent_messages = conversation.get_messages_with_summary(
                session_id=session_id, limit=50
            )
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

            # Resolve rubric for verification
            ws_rubric = None
            ws_settings = get_settings()
            ws_verification = getattr(ws_settings, "verification", None)
            if ws_verification and getattr(ws_verification, "enabled", False) and getattr(ws_verification, "default_rubric", ""):
                ws_rubric = ws_verification.default_rubric

            stream_loop_holder: dict[str, Any] = {}
            cancel_event = asyncio.Event()
            deferred_control: str | None = None
            stream_cancelled = False
            stream_task = asyncio.create_task(
                _run_agent_stream(
                    websocket, user_id, sdk_messages, conversation, session_id,
                    pending_ref=pending_container, workspace_id=workspace_id,
                    model=msg_model, provider_keys=msg_provider_keys,
                    cancel_event=cancel_event,
                    rubric=ws_rubric,
                    stream_loop_out=stream_loop_holder,
                )
            )
            while not stream_task.done():
                get_task = asyncio.create_task(control_queue.get())
                done, pending = await asyncio.wait(
                    {stream_task, get_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if stream_task in done:
                    if get_task in done:
                        deferred_control = get_task.result()
                        if deferred_control is None:
                            cancel_event.set()
                            stream_task.cancel()
                            try:
                                await stream_task
                            except asyncio.CancelledError:
                                pass
                            raise WebSocketDisconnect()
                    else:
                        get_task.cancel()
                    break
                raw_control = get_task.result()
                if raw_control is None:
                    cancel_event.set()
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
                    raise WebSocketDisconnect()
                try:
                    control_data = json.loads(raw_control)
                except json.JSONDecodeError:
                    continue
                control_msg = parse_client_message(control_data)
                if isinstance(control_msg, CancelMessage) or control_data.get("type") == "cancel":
                    cancel_event.set()
                    await websocket.send_json(DoneMessage(response="Cancelled").model_dump())
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
                    stream_cancelled = True
                    break
                if isinstance(control_msg, SteerMessage):
                    steer_text = control_msg.content
                    if steer_text.strip():
                        from src.sdk.runner import get_user_loop

                        active_loop = get_user_loop(user_id, session_id)
                        if active_loop is not None:
                            # Persist at injection time (correct transcript
                            # position) via the loop's steer sink — NOT here,
                            # or the follow-up run would persist it twice.
                            def _persist_steer(m: str) -> None:
                                _persist_ws_conversation_message(
                                    conversation,
                                    "user",
                                    m,
                                    session_id=session_id,
                                    metadata={"steer": True},
                                )

                            active_loop.set_steer_sink(_persist_steer)
                            active_loop.steer(steer_text)
                            await websocket.send_json(
                                SteerAckMessage(content=steer_text).model_dump()
                                | {"workspace_id": workspace_id}
                            )
                        else:
                            await websocket.send_json(
                                ErrorMessage(
                                    message="No active agent run to steer",
                                    code="NO_ACTIVE_RUN",
                                ).model_dump()
                            )
                    continue
                if isinstance(control_msg, PingMessage):
                    await websocket.send_json(PongMessage().model_dump())
                elif control_msg is not None:
                    await websocket.send_json(
                        ErrorMessage(
                            message="Agent is currently running; only cancel, steer, and ping are accepted",
                            code="AGENT_BUSY",
                        ).model_dump()
                    )
            if not stream_cancelled:
                await stream_task
            # After stream finishes: if a tool was interrupted, wait for approval
            while pending_container[0] is not None:
                tool_name = pending_container[0].get("tool", "unknown")
                if deferred_control is not None:
                    raw = deferred_control
                    deferred_control = None
                else:
                    try:
                        raw = await asyncio.wait_for(control_queue.get(), timeout=300)
                    except TimeoutError:
                        pending_container[0] = None
                        break
                    if raw is None:
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
                is_edit_approve = msg_type in ("edit_and_approve", "edit_approve")
                if is_approve:
                    call_id = data.get("call_id") or pending_container[0].get("call_id")
                    if call_id != pending_container[0].get("call_id"):
                        await websocket.send_json(
                            ErrorMessage(
                                message="Pending tool call does not match call_id",
                                code="CALL_ID_MISMATCH",
                            ).model_dump()
                        )
                        continue
                    run_session_id, run_model, run_provider_keys = _pending_runtime_context(
                        pending_container[0], session_id, msg_model, msg_provider_keys
                    )
                    loop = await get_sdk_loop(
                        user_id,
                        workspace_id,
                        session_id=run_session_id,
                        model=run_model,
                        provider_keys=run_provider_keys,
                    )
                    approved_args = pending_container[0].get("args") or {}
                    approved_call_id = pending_container[0].get("call_id") or call_id
                    loop.approve_tool_call(
                        ToolCall(
                            id=approved_call_id,
                            name=tool_name,
                            arguments=approved_args,
                        )
                    )
                    pending_container[0] = None
                    # Execute the approved tool directly instead of the
                    # instruction-only retry that looped forever (B4).
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(
                            session_id=run_session_id, limit=50
                        )
                    )
                    retry_msgs.extend(
                        await _execute_approved_tool(
                            loop,
                            tool_name,
                            approved_args,
                            approved_call_id or f"call_{secrets.token_hex(4)}",
                        )
                    )
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, run_session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=run_model, provider_keys=run_provider_keys,
                    )
                elif is_edit_approve:
                    call_id = data.get("call_id") or pending_container[0].get("call_id")
                    if call_id != pending_container[0].get("call_id"):
                        await websocket.send_json(
                            ErrorMessage(
                                message="Pending tool call does not match call_id",
                                code="CALL_ID_MISMATCH",
                            ).model_dump()
                        )
                        continue
                    edited_args = data.get("edited_args") or {}
                    approved_call_id = pending_container[0].get("call_id") or call_id
                    run_session_id, run_model, run_provider_keys = _pending_runtime_context(
                        pending_container[0], session_id, msg_model, msg_provider_keys
                    )
                    loop = await get_sdk_loop(
                        user_id,
                        workspace_id,
                        session_id=run_session_id,
                        model=run_model,
                        provider_keys=run_provider_keys,
                    )
                    loop.approve_tool_call(
                        ToolCall(
                            id=approved_call_id,
                            name=tool_name,
                            arguments=edited_args,
                        )
                    )
                    pending_container[0] = None
                    retry_msgs = _messages_from_conversation(
                        conversation.get_messages_with_summary(
                            session_id=run_session_id, limit=50
                        )
                    )
                    # Execute the approved tool with the edited args directly
                    # (B4) — instruction-only retries looped forever.
                    retry_msgs.extend(
                        await _execute_approved_tool(
                            loop,
                            tool_name,
                            edited_args,
                            approved_call_id or f"call_{secrets.token_hex(4)}",
                        )
                    )
                    await _run_agent_stream(
                        websocket, user_id, retry_msgs, conversation, run_session_id,
                        pending_ref=pending_container, workspace_id=workspace_id,
                        model=run_model, provider_keys=run_provider_keys,
                    )
                elif is_reject:
                    call_id = data.get("call_id") or pending_container[0].get("call_id")
                    if call_id != pending_container[0].get("call_id"):
                        await websocket.send_json(
                            ErrorMessage(
                                message="Pending tool call does not match call_id",
                                code="CALL_ID_MISMATCH",
                            ).model_dump()
                        )
                        continue
                    pending_container[0] = None
                    break

            # Steer follow-up (Pi-style): a steer that arrived while the agent
            # was generating text (no tool boundary to inject at) is delivered
            # as the next turn. The steer was already persisted at receive
            # time, so the follow-up reloads history and runs it.
            #
            # Audit E25: RunService unregisters the loop when the stream ends,
            # so get_user_loop is always None here. _run_agent_stream captured
            # the live loop via execute_stream's on_stream_end callback.
            follow_loop = stream_loop_holder.get("loop")
            # P2-1: drain ALL pending steers — each queued steer gets its own
            # follow-up turn, not just the first.
            while follow_loop is not None and follow_loop.has_pending_steer():
                follow_steer = follow_loop.pop_steer()
                if not follow_steer:
                    break
                follow_msgs = _messages_from_conversation(
                    conversation.get_messages_with_summary(
                        session_id=session_id, limit=50
                    )
                )
                await _run_agent_stream(
                    websocket, user_id, follow_msgs, conversation, session_id,
                    pending_ref=pending_container, workspace_id=workspace_id,
                    model=msg_model, provider_keys=msg_provider_keys,
                )

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
    finally:
        if not reader_task.done():
            reader_task.cancel()
