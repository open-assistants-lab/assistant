/**
 * AssistantClient — typed client for the Assistant HTTP API.
 *
 * Covers:
 * - POST /message            → send()      (blocking)
 * - POST /message/stream     → stream()    (SSE, async iterable)
 * - HITL endpoints           → approve() / reject() / cancel()
 * - Conversation history     → getConversation() / listSessions() / ...
 * - GET /models              → listModels()
 * - /ws/conversation         → socket()    (typed WebSocket wrapper)
 */

import { parseSSEStream } from "./sse.js";
import type {
  ConversationMessage,
  MessageRequest,
  MessageResponse,
  ModelInfo,
  SessionSummary,
  StreamEvent,
  TextDeltaData,
} from "./types.js";
import { ConversationSocket, type ConversationSocketOptions } from "./ws.js";

export interface AssistantClientOptions {
  /** Server origin, e.g. "http://localhost:8000". No trailing slash needed. */
  baseUrl: string;
  /** API key (sent as Bearer token). Optional when auth is disabled. */
  apiKey?: string;
  /** Default user_id for requests. Default: "default_user". */
  userId?: string;
  /** Custom fetch implementation. Defaults to globalThis.fetch. */
  fetch?: typeof fetch;
  /** API path prefix. Defaults to "/v1" (stable surface, roadmap P0-T5).
   *  Pass "" for the legacy unprefixed routes. */
  basePath?: string;
}

export class AssistantApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as Record<string, unknown>).detail)
        : JSON.stringify(body);
    super(`Assistant API error ${status}: ${detail}`);
    this.name = "AssistantApiError";
    this.status = status;
    this.body = body;
  }
}

export interface SendOptions extends Omit<MessageRequest, "message" | "user_id"> {}

export class AssistantClient {
  private baseUrl: string;
  private apiKey?: string;
  private defaultUserId: string;
  private fetchImpl: typeof fetch;
  private basePath: string;

  constructor(options: AssistantClientOptions) {
    if (!options.baseUrl) throw new Error("baseUrl is required");
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.basePath = options.basePath ?? "/v1";
    this.apiKey = options.apiKey;
    this.defaultUserId = options.userId ?? "default_user";
    const impl = options.fetch ?? globalThis.fetch;
    if (!impl) throw new Error("No fetch implementation available");
    this.fetchImpl = impl.bind(globalThis);
  }

  /** Route under the API prefix (default "/v1" — roadmap P0-T5). */
  private route(p: string): string {
    return `${this.baseUrl}${this.basePath}${p}`;
  }

  // ─── Core conversation ────────────────────────────────────────────────────

  /** Send a message and wait for the complete response. */
  async send(message: string, options: SendOptions & { userId?: string } = {}): Promise<MessageResponse> {
    return this.postJson("/message", {
      message,
      user_id: options.userId ?? this.defaultUserId,
      ...stripKeys(options, ["userId"]),
    });
  }

  /**
   * Send a message and iterate the SSE stream.
   *
   * Yields parsed StreamEvent objects. Heartbeat comments are filtered out
   * by the parser. Example:
   *
   *   for await (const event of client.stream("hello")) {
   *     if (event.type === "text_delta") process.stdout.write(event.data.delta);
   *   }
   */
  async *stream(message: string, options: SendOptions & { userId?: string } = {}): AsyncGenerator<StreamEvent> {
    const body = JSON.stringify({
      message,
      user_id: options.userId ?? this.defaultUserId,
      ...stripKeys(options, ["userId"]),
    });
    const res = await this.fetchImpl(this.route("/message/stream"), {
      method: "POST",
      headers: this.headers(),
      body,
    });
    if (!res.ok || !res.body) {
      throw new AssistantApiError(res.status, await safeJson(res));
    }

    for await (const sse of parseSSEStream(res.body)) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(sse.data);
      } catch {
        continue; // non-JSON payload — skip
      }
      const envelope = parsed as { type?: string; data?: Record<string, unknown> };
      yield {
        type: envelope.type ?? "unknown",
        data: envelope.data ?? {},
      } as StreamEvent;
    }
  }

  /** Collect a full run from stream(): concatenated text + all events. */
  async collect(message: string, options: SendOptions & { userId?: string } = {}): Promise<{
    text: string;
    events: StreamEvent[];
  }> {
    const parts: string[] = [];
    const events: StreamEvent[] = [];
    for await (const event of this.stream(message, options)) {
      events.push(event);
      if (event.type === "text_delta") {
        const delta = (event.data as TextDeltaData).delta;
        if (typeof delta === "string") parts.push(delta);
      } else if (event.type === "done" && !parts.length) {
        const resp = (event.data as DoneShape).result?.response;
        if (typeof resp === "string") parts.push(resp);
      }
    }
    return { text: parts.join(""), events };
  }

  // ─── HITL (human-in-the-loop) ─────────────────────────────────────────────

  async approve(callId: string, options: { userId?: string; sessionId?: string | null } = {}): Promise<Response> {
    return this.raw("POST", "/message/approve", {
      user_id: options.userId ?? this.defaultUserId,
      call_id: callId,
      session_id: options.sessionId,
    });
  }

  async reject(callId: string, options: { userId?: string; sessionId?: string | null; reason?: string } = {}): Promise<void> {
    await this.postJson("/message/reject", {
      user_id: options.userId ?? this.defaultUserId,
      call_id: callId,
      reason: options.reason ?? "",
      session_id: options.sessionId,
    });
  }

  async cancel(options: { userId?: string; sessionId?: string | null } = {}): Promise<void> {
    await this.postJson("/message/cancel", {
      user_id: options.userId ?? this.defaultUserId,
      session_id: options.sessionId,
    });
  }

  // ─── Conversation history ─────────────────────────────────────────────────

  async getConversation(options: { userId?: string; sessionId?: string | null; limit?: number } = {}) {
    const qs = new URLSearchParams({
      user_id: options.userId ?? this.defaultUserId,
      ...(options.limit ? { limit: String(options.limit) } : {}),
      ...(options.sessionId ? { session_id: options.sessionId } : {}),
    });
    const data = await this.getJson<{ messages: ConversationMessage[] }>(`/conversation?${qs}`);
    return data.messages;
  }

  async listSessions(userId?: string): Promise<SessionSummary[]> {
    const qs = new URLSearchParams({ user_id: userId ?? this.defaultUserId });
    const data = await this.getJson<{ sessions: SessionSummary[] }>(`/conversation/sessions?${qs}`);
    return data.sessions;
  }

  async deleteSession(sessionId: string, userId?: string): Promise<void> {
    const qs = new URLSearchParams({
      user_id: userId ?? this.defaultUserId,
      session_id: sessionId,
    });
    await this.fetchImpl(this.route(`/conversation/session?${qs}`), {
      method: "DELETE",
      headers: this.headers(),
    });
  }

  async clearConversation(userId?: string): Promise<void> {
    const qs = new URLSearchParams({ user_id: userId ?? this.defaultUserId });
    await this.fetchImpl(this.route(`/conversation?${qs}`), {
      method: "DELETE",
      headers: this.headers(),
    });
  }

  // ─── Models ───────────────────────────────────────────────────────────────

  async listModels(userId?: string): Promise<ModelInfo[]> {
    const qs = new URLSearchParams({ user_id: userId ?? this.defaultUserId });
    const data = await this.getJson<{ models: ModelInfo[] }>(`/models?${qs}`);
    return data.models;
  }

  // ─── WebSocket ────────────────────────────────────────────────────────────

  socket(options: Partial<ConversationSocketOptions> = {}): ConversationSocket {
    return new ConversationSocket({
      baseUrl: this.baseUrl,
      apiKey: this.apiKey,
      user_id: this.defaultUserId,
      ...options,
    });
  }

  // ─── Internals ────────────────────────────────────────────────────────────

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiKey) h["Authorization"] = `Bearer ${this.apiKey}`;
    return { ...h, ...extra };
  }

  private async raw(method: string, path: string, body?: unknown): Promise<Response> {
    return this.fetchImpl(this.route(path), {
      method,
      headers: this.headers(),
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  }

  private async postJson<T>(path: string, body: unknown): Promise<T> {
    const res = await this.raw("POST", path, body);
    if (!res.ok) throw new AssistantApiError(res.status, await safeJson(res));
    return (await res.json()) as T;
  }

  private async getJson<T>(pathWithQuery: string): Promise<T> {
    const res = await this.fetchImpl(this.route(pathWithQuery), {
      headers: this.headers(),
    });
    if (!res.ok) throw new AssistantApiError(res.status, await safeJson(res));
    return (await res.json()) as T;
  }
}

interface DoneShape {
  result?: { response?: string; [key: string]: unknown };
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return { raw: await res.text().catch(() => "") };
  }
}

function stripKeys<T extends object>(obj: T, keys: string[]): Omit<T, keyof never> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (!keys.includes(k)) out[k] = v;
  }
  return out as Omit<T, keyof never>;
}
