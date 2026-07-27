"""WebSocket protocol message types and serialization.

This module defines the bidirectional message protocol for the
/ws/conversation endpoint. It serves as the contract between
the frontend (web client, HTML test harness) and the backend.

The protocol is designed to be:
- Simple: JSON messages, typed, no binary frames
- Bidirectional: client sends messages, server streams responses
- Extensible: unknown types are ignored (forward compatibility)

Phase 5 adds block-structured streaming messages:
- TextStartMessage, TextDeltaMessage, TextEndMessage
- ToolInputStartMessage, ToolInputDeltaMessage, ToolInputEndMessage
- ReasoningStartMessage, ReasoningDeltaMessage, ReasoningEndMessage
- ToolResultMessage (replaces ToolEndMessage for actual results)
- ToolCallMessage (complete tool call with parsed args)

Backward-compatible messages are preserved:
- AiTokenMessage, ToolStartMessage, ToolEndMessage, ReasoningMessage
"""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field

# ─── Client → Server Messages ───


class UserMessage(BaseModel):
    """Client sends a chat message to the agent."""

    type: str = "user_message"
    content: str
    user_id: str = "default_user"
    verbose: bool = False
    workspace_id: str = "personal"
    session_id: str | None = None
    model: str | None = None
    provider_keys: dict[str, str] | None = None


class AuthMessage(BaseModel):
    """Client sends API key for authentication (first message after WS connect)."""

    type: Literal["auth"] = "auth"
    api_key: str


class ApproveMessage(BaseModel):
    """Client approves a pending tool call (HITL)."""

    type: str = "approve"
    call_id: str


class RejectMessage(BaseModel):
    """Client rejects a pending tool call (HITL)."""

    type: str = "reject"
    call_id: str
    reason: str = ""


class EditAndApproveMessage(BaseModel):
    """Client edits tool call arguments and approves (HITL)."""

    type: str = "edit_and_approve"
    call_id: str
    edited_args: dict[str, Any]


class CancelMessage(BaseModel):
    """Client cancels an ongoing agent execution."""

    type: str = "cancel"


class PingMessage(BaseModel):
    """Client sends heartbeat."""

    type: str = "ping"


# ─── Server → Client Messages (Block-Structured Streaming) ───


class TextStartMessage(BaseModel):
    """Text content block begins."""

    type: str = "text_start"
    session_id: str = ""


class TextDeltaMessage(BaseModel):
    """Streaming text delta within a text block."""

    type: str = "text_delta"
    content: str
    session_id: str = ""


class TextEndMessage(BaseModel):
    """Text content block ends."""

    type: str = "text_end"
    session_id: str = ""


class ToolInputStartMessage(BaseModel):
    """Tool input block begins — the model is generating tool call arguments."""

    type: str = "tool_input_start"
    tool: str
    call_id: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolInputDeltaMessage(BaseModel):
    """Streaming argument delta for a tool call."""

    type: str = "tool_input_delta"
    call_id: str
    content: str = ""


class ToolInputEndMessage(BaseModel):
    """Tool input block ends — all arguments have been streamed."""

    type: str = "tool_input_end"
    call_id: str
    tool: str = ""


class ToolCallMessage(BaseModel):
    """Complete tool call with fully parsed arguments."""

    type: str = "tool_call"
    tool: str
    call_id: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResultMessage(BaseModel):
    """Tool execution result (emitted by AgentLoop after tool execution)."""

    type: str = "tool_result"
    tool: str
    call_id: str
    result_preview: str = ""


class ReasoningStartMessage(BaseModel):
    """Reasoning/thinking block begins."""

    type: str = "reasoning_start"
    session_id: str = ""


class ReasoningDeltaMessage(BaseModel):
    """Streaming reasoning/thinking delta."""

    type: str = "reasoning_delta"
    content: str
    session_id: str = ""


class ReasoningEndMessage(BaseModel):
    """Reasoning/thinking block ends."""

    type: str = "reasoning_end"
    session_id: str = ""


# ─── Server → Client Messages (Backward-Compatible) ───


class AiTokenMessage(BaseModel):
    """Streaming AI text token (backward compat alias for TextDeltaMessage)."""

    type: str = "ai_token"
    content: str
    session_id: str = ""


class ToolStartMessage(BaseModel):
    """Tool call started (backward compat alias for ToolInputStartMessage)."""

    type: str = "tool_start"
    tool: str
    call_id: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolEndMessage(BaseModel):
    """Tool call completed (backward compat)."""

    type: str = "tool_end"
    tool: str
    call_id: str
    result_preview: str = ""


class InterruptMessage(BaseModel):
    """Agent requests human approval for a tool call."""

    type: str = "interrupt"
    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[str] = Field(default_factory=lambda: ["approve", "reject", "edit"])


class MiddlewareMessage(BaseModel):
    """Middleware event (verbose mode)."""

    type: str = "middleware"
    name: str
    event: str
    data: dict[str, Any] = Field(default_factory=dict)


class ReasoningMessage(BaseModel):
    """Reasoning/thinking token (backward compat alias for ReasoningDeltaMessage)."""

    type: str = "reasoning"
    content: str
    session_id: str = ""


class DoneMessage(BaseModel):
    """Agent execution completed."""

    type: str = "done"
    response: str = ""
    message_id: str = ""
    total_llm_calls: int = 0
    cost_usd: float = 0.0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tools_called: list[str] = []


class AuthOkMessage(BaseModel):
    """API key accepted, client may now send messages."""

    type: Literal["auth_ok"] = "auth_ok"


class ErrorMessage(BaseModel):
    """Error from the agent."""

    type: str = "error"
    message: str
    code: str = "AGENT_ERROR"


class PongMessage(BaseModel):
    """Server heartbeat response."""

    type: str = "pong"


class SkillsLoadMessage(BaseModel):
    """Agent loaded a skill into context."""

    type: str = "skills_load"
    name: str


class CanvasUpdateMessage(BaseModel):
    """Agent-generated HTML canvas update for the canvas tab."""

    type: str = "canvas_update"
    surface_id: str
    action: Literal["create", "update", "destroy"]
    html: str = ""


# ─── Message Parsing ───

CLIENT_MESSAGE_TYPES = {
    "user_message": UserMessage,
    "approve": ApproveMessage,
    "reject": RejectMessage,
    "edit_and_approve": EditAndApproveMessage,
    "cancel": CancelMessage,
    "ping": PingMessage,
}

SERVER_MESSAGE_TYPES = {
    # Block-structured
    "text_start": TextStartMessage,
    "text_delta": TextDeltaMessage,
    "text_end": TextEndMessage,
    "tool_input_start": ToolInputStartMessage,
    "tool_input_delta": ToolInputDeltaMessage,
    "tool_input_end": ToolInputEndMessage,
    "tool_call": ToolCallMessage,
    "tool_result": ToolResultMessage,
    "reasoning_start": ReasoningStartMessage,
    "reasoning_delta": ReasoningDeltaMessage,
    "reasoning_end": ReasoningEndMessage,
    # Backward-compatible
    "ai_token": AiTokenMessage,
    "tool_start": ToolStartMessage,
    "tool_end": ToolEndMessage,
    "reasoning": ReasoningMessage,
    # Common
    "interrupt": InterruptMessage,
    "middleware": MiddlewareMessage,
    "done": DoneMessage,
    "error": ErrorMessage,
    "pong": PongMessage,
    # Canvas
    "canvas_update": CanvasUpdateMessage,
    "skills_load": SkillsLoadMessage,
}


def parse_client_message(
    data: dict[str, Any],
) -> (
    UserMessage
    | ApproveMessage
    | RejectMessage
    | EditAndApproveMessage
    | CancelMessage
    | PingMessage
    | None
):
    """Parse a client message from raw dict. Returns None for unknown types."""
    msg_type = data.get("type", "")
    msg_cls = CLIENT_MESSAGE_TYPES.get(msg_type)
    if msg_cls is None:
        return None
    try:
        return cast(
            UserMessage
            | ApproveMessage
            | RejectMessage
            | EditAndApproveMessage
            | CancelMessage
            | PingMessage
            | None,
            msg_cls(**data),
        )
    except Exception:
        return None


_ServerMessage = (
    TextStartMessage
    | TextDeltaMessage
    | TextEndMessage
    | ToolInputStartMessage
    | ToolInputDeltaMessage
    | ToolInputEndMessage
    | ToolCallMessage
    | ToolResultMessage
    | ReasoningStartMessage
    | ReasoningDeltaMessage
    | ReasoningEndMessage
    | AiTokenMessage
    | ToolStartMessage
    | ToolEndMessage
    | ReasoningMessage
    | InterruptMessage
    | MiddlewareMessage
    | SkillsLoadMessage
    | DoneMessage
    | ErrorMessage
    | PongMessage
    | CanvasUpdateMessage
)


def parse_server_message(
    data: dict[str, Any],
) -> _ServerMessage | None:
    """Parse a server message from raw dict. Returns None for unknown types."""
    msg_type = data.get("type", "")
    msg_cls = SERVER_MESSAGE_TYPES.get(msg_type)
    if msg_cls is None:
        return None
    try:
        return cast(_ServerMessage | None, msg_cls(**data))
    except Exception:
        return None
