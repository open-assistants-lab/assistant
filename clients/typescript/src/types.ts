/**
 * Assistant API contract types.
 *
 * Mirrors the Python server models:
 * - src/http/models.py            (REST request/response)
 * - src/http/routers/conversation.py (SSE event envelope: {"type", "data"})
 * - src/http/ws_protocol.py       (WebSocket message protocol)
 *
 * Unknown fields are preserved via index signatures so forward-compatible
 * servers never break older clients.
 */

// ─── REST: requests ─────────────────────────────────────────────────────────

export interface VerificationRequest {
  rubric?: string | null;
  /** "off" | "on" | "auto" — per-request override of configured mode. */
  mode?: "off" | "on" | "auto" | null;
}

export interface MessageRequest {
  message: string;
  model?: string | null;
  user_id?: string | null;
  session_id?: string | null;
  verbose?: boolean;
  provider_keys?: Record<string, string> | null;
  verification?: VerificationRequest | null;
}

// ─── REST: responses ────────────────────────────────────────────────────────

export interface UsageInfo {
  input_tokens?: number;
  output_tokens?: number;
  reasoning_tokens?: number;
  [key: string]: unknown;
}

export interface RubricCriterion {
  name?: string;
  passed?: boolean;
  gap?: string;
  [key: string]: unknown;
}

export interface RubricEvaluation {
  attempt?: number;
  result?: string;
  explanation?: string;
  criteria?: RubricCriterion[];
  [key: string]: unknown;
}

export interface VerificationVerdict {
  status?: string | null;
  iterations?: number;
  attempts?: number;
  max_attempts?: number;
  explanation?: string | null;
  criteria?: Record<string, unknown>[];
  evaluations?: Record<string, unknown>[];
  [key: string]: unknown;
}

export interface ToolCallRecord {
  tool?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  args?: Record<string, unknown>;
  call_id?: string;
  output?: string;
  [key: string]: unknown;
}

export interface MessageResponse {
  response: string;
  reasoning?: string | null;
  error?: string | null;
  verbose_data?: Record<string, unknown> | null;
  tool_calls?: ToolCallRecord[] | null;
  verification?: VerificationVerdict | null;
  usage?: UsageInfo | null;
}

export interface ConversationMessage {
  role: string;
  content: string;
  source?: string | null;
  timestamp?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface SessionSummary {
  session_id: string;
  title?: string | null;
  [key: string]: unknown;
}

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  provider_display?: string;
  key_source?: string;
  billing_mode?: string;
}

// ─── SSE stream events (POST /message/stream) ───────────────────────────────
//
// Every SSE `data:` payload is a canonical envelope: {"type": ..., "data": {...}}
// (see conversation.py sse()/sse_raw()). Heartbeat comments (`: ping`) are
// consumed by the parser and never surface as events.

export interface TextDeltaData {
  delta: string;
  [key: string]: unknown;
}
export interface ReasoningDeltaData {
  delta: string;
  [key: string]: unknown;
}
export interface ToolInputStartData {
  name: string;
  tool_call_id: string;
  args?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface ToolResultData {
  name?: string;
  tool?: string;
  tool_call_id?: string;
  content?: string;
  result_preview?: string;
  [key: string]: unknown;
}
export interface InterruptData {
  tool?: string;
  call_id?: string;
  args?: Record<string, unknown>;
  allowed_actions?: string[];
  [key: string]: unknown;
}
export interface DoneData {
  result?: {
    response?: string;
    status?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}
export interface ErrorEventData {
  code?: string;
  message?: string;
  content?: string;
  [key: string]: unknown;
}
export interface CanvasUpdateData {
  surface_id: string;
  action: string;
  html?: string;
  surface_type?: string;
  file_path?: string;
  [key: string]: unknown;
}

/** A single parsed SSE stream event. */
export type StreamEvent =
  | { type: "text_delta"; data: TextDeltaData }
  | { type: "reasoning_delta"; data: ReasoningDeltaData }
  | { type: "tool_input_start"; data: ToolInputStartData }
  | { type: "tool_result"; data: ToolResultData }
  | { type: "interrupt"; data: InterruptData }
  | { type: "done"; data: DoneData }
  | { type: "cancelled"; data: { content?: string; [k: string]: unknown } }
  | { type: "error"; data: ErrorEventData }
  | { type: "canvas_update"; data: CanvasUpdateData }
  | { type: string; data: Record<string, unknown> }; // passthrough for future types

// ─── WebSocket protocol (/ws/conversation) ──────────────────────────────────

export interface UserMessage {
  type: "user_message";
  content: string;
  user_id?: string;
  verbose?: boolean;
  workspace_id?: string;
  session_id?: string | null;
  model?: string | null;
  provider_keys?: Record<string, string> | null;
}
export interface AuthMessage {
  type: "auth";
  api_key: string;
}
export interface ApproveMessage {
  type: "approve";
  call_id: string;
}
export interface RejectMessage {
  type: "reject";
  call_id: string;
  reason?: string;
}
export interface EditAndApproveMessage {
  type: "edit_and_approve";
  call_id: string;
  edited_args: Record<string, unknown>;
}
export interface CancelMessage {
  type: "cancel";
}
export interface SteerMessage {
  type: "steer";
  content: string;
}
export interface PingMessage {
  type: "ping";
}

export type ClientMessage =
  | UserMessage
  | AuthMessage
  | ApproveMessage
  | RejectMessage
  | EditAndApproveMessage
  | CancelMessage
  | SteerMessage
  | PingMessage;

// Server → client

export interface SessionBound {
  session_id?: string;
}
export interface TextStartMsg extends SessionBound {
  type: "text_start";
}
export interface TextDeltaMsg extends SessionBound {
  type: "text_delta";
  content: string;
}
export interface TextEndMsg extends SessionBound {
  type: "text_end";
}
export interface ToolInputStartMsg {
  type: "tool_input_start";
  tool: string;
  call_id: string;
  args?: Record<string, unknown>;
}
export interface ToolInputDeltaMsg {
  type: "tool_input_delta";
  call_id: string;
  content?: string;
}
export interface ToolInputEndMsg {
  type: "tool_input_end";
  call_id: string;
  tool?: string;
}
export interface ToolCallMsg {
  type: "tool_call";
  tool: string;
  call_id: string;
  args?: Record<string, unknown>;
}
export interface ToolResultMsg {
  type: "tool_result";
  tool: string;
  call_id: string;
  result_preview?: string;
}
export interface ReasoningStartMsg extends SessionBound {
  type: "reasoning_start";
}
export interface ReasoningDeltaMsg extends SessionBound {
  type: "reasoning_delta";
  content: string;
}
export interface ReasoningEndMsg extends SessionBound {
  type: "reasoning_end";
}
export interface InterruptMsg {
  type: "interrupt";
  call_id: string;
  tool: string;
  args?: Record<string, unknown>;
  allowed_actions?: string[];
}
export interface MiddlewareMsg {
  type: "middleware";
  name: string;
  event: string;
  data?: Record<string, unknown>;
}
export interface SkillsLoadMsg {
  type: "skills_load";
  name: string;
}
export interface DoneMsg {
  type: "done";
  response?: string;
  message_id?: string;
  total_llm_calls?: number;
  cost_usd?: number;
  tool_calls?: Record<string, unknown>[];
  tools_called?: string[];
}
export interface ErrorMessage {
  type: "error";
  message: string;
  code?: string;
}
export interface PongMsg {
  type: "pong";
}
export interface SteerAckMsg {
  type: "steer_ack";
  content?: string;
}
export interface CanvasUpdateMsg {
  type: "canvas_update";
  surface_id: string;
  action: "create" | "update" | "destroy";
  html?: string;
}
export interface AuthOkMsg {
  type: "auth_ok";
}

/**
 * Discriminated union of every server → client WS message.
 * Narrow with `msg.type === "text_delta"` etc. Unknown types from newer
 * servers are surfaced as ServerMessage via a catch-all at runtime.
 */
export type ServerMessage =
  | TextStartMsg
  | TextDeltaMsg
  | TextEndMsg
  | ToolInputStartMsg
  | ToolInputDeltaMsg
  | ToolInputEndMsg
  | ToolCallMsg
  | ToolResultMsg
  | ReasoningStartMsg
  | ReasoningDeltaMsg
  | ReasoningEndMsg
  | InterruptMsg
  | MiddlewareMsg
  | SkillsLoadMsg
  | DoneMsg
  | ErrorMessage
  | PongMsg
  | SteerAckMsg
  | CanvasUpdateMsg
  | AuthOkMsg;

/** Parse raw JSON into a ServerMessage. Unknown types return undefined. */
export function parseServerMessage(raw: unknown): ServerMessage | undefined {
  if (typeof raw !== "object" || raw === null) return undefined;
  const msg = raw as Record<string, unknown>;
  switch (msg.type) {
    case "text_start":
    case "text_delta":
    case "text_end":
    case "tool_input_start":
    case "tool_input_delta":
    case "tool_input_end":
    case "tool_call":
    case "tool_result":
    case "reasoning_start":
    case "reasoning_delta":
    case "reasoning_end":
    case "interrupt":
    case "middleware":
    case "skills_load":
    case "done":
    case "error":
    case "pong":
    case "steer_ack":
    case "canvas_update":
    case "auth_ok":
      return msg as unknown as ServerMessage;
    default:
      return undefined;
  }
}
