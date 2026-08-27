/**
 * ConversationSocket — typed wrapper over the /ws/conversation WebSocket.
 *
 * Requires a global WebSocket (native in browsers and Node 22+; for older
 * Node versions pass a WebSocket implementation via `wsImpl`).
 */

import type {
  ClientMessage,
  EditAndApproveMessage,
  ServerMessage,
  SteerMessage,
  UserMessage,
} from "./types.js";
import { parseServerMessage } from "./types.js";

export interface ConversationSocketOptions {
  baseUrl: string;
  apiKey?: string;
  user_id?: string;
  workspace_id?: string;
  session_id?: string | null;
  /** Custom WebSocket implementation (e.g. the `ws` package on old Node). */
  wsImpl?: typeof WebSocket;
  /** Milliseconds before connect/auth gives up. Default: 10_000. */
  connectTimeoutMs?: number;
}

type MessageHandler = (msg: ServerMessage) => void;

export class AssistantSocketError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AssistantSocketError";
  }
}

export class ConversationSocket {
  private ws: WebSocket | null = null;
  private handlers = new Set<MessageHandler>();
  private opts: Required<
    Pick<ConversationSocketOptions, "user_id" | "workspace_id" | "connectTimeoutMs">
  > &
    ConversationSocketOptions;

  constructor(options: ConversationSocketOptions) {
    this.opts = { user_id: "default_user", workspace_id: "personal", connectTimeoutMs: 10_000, ...options };
  }

  /** Connect (and authenticate when an apiKey is set). Resolves after auth_ok. */
  connect(): Promise<void> {
    const Impl = this.opts.wsImpl ?? globalThis.WebSocket;
    if (!Impl) {
      return Promise.reject(
        new AssistantSocketError(
          "No WebSocket implementation available. Pass one via `wsImpl` (e.g. `require('ws')`) on Node < 22.",
        ),
      );
    }

    const url = new URL(this.opts.baseUrl.replace(/^http/, "ws"));
    url.pathname = joinPath(url.pathname, "/ws/conversation");

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new AssistantSocketError("WebSocket connect timed out")),
        this.opts.connectTimeoutMs,
      );
      const cleanup = () => clearTimeout(timeout);

      let socket: WebSocket;
      try {
        socket = new Impl(url.toString());
      } catch (err) {
        cleanup();
        reject(err instanceof Error ? err : new AssistantSocketError(String(err)));
        return;
      }
      this.ws = socket;

      socket.onopen = () => {
        if (this.opts.apiKey) {
          this.send({ type: "auth", api_key: this.opts.apiKey });
        } else {
          cleanup();
          resolve();
        }
      };

      socket.onmessage = (ev: MessageEvent) => {
        let raw: unknown;
        try {
          raw = JSON.parse(typeof ev.data === "string" ? ev.data : "");
        } catch {
          return;
        }
        const msg = parseServerMessage(raw);
        if (!msg) return;

        if (msg.type === "auth_ok") {
          cleanup();
          resolve();
          return;
        }
        if (msg.type === "error" && msg.code === "AUTH_ERROR") {
          cleanup();
          reject(new AssistantSocketError(msg.message));
          return;
        }
        for (const handler of this.handlers) handler(msg);
      };

      socket.onerror = () => {
        cleanup();
        reject(new AssistantSocketError("WebSocket error"));
      };
    });
  }

  /** Register a handler for every server message. Returns an unsubscribe fn. */
  on(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  send(message: ClientMessage): void {
    if (!this.ws || this.ws.readyState !== 1) {
      throw new AssistantSocketError("Socket is not open — call connect() first");
    }
    this.ws.send(JSON.stringify(message));
  }

  /** Send a chat message with this socket's default user/workspace/session. */
  say(content: string, overrides: Partial<UserMessage> = {}): void {
    this.send({
      type: "user_message",
      content,
      user_id: this.opts.user_id,
      workspace_id: this.opts.workspace_id,
      session_id: this.opts.session_id ?? undefined,
      ...overrides,
    });
  }

  steer(content: string, overrides: Partial<SteerMessage> = {}): void {
    this.send({ type: "steer", content, ...overrides });
  }

  approve(callId: string): void {
    this.send({ type: "approve", call_id: callId });
  }

  reject(callId: string, reason = ""): void {
    this.send({ type: "reject", call_id: callId, reason });
  }

  editAndApprove(callId: string, editedArgs: Record<string, unknown>): void {
    const msg: EditAndApproveMessage = {
      type: "edit_and_approve",
      call_id: callId,
      edited_args: editedArgs,
    };
    this.send(msg);
  }

  cancel(): void {
    this.send({ type: "cancel" });
  }

  ping(): void {
    this.send({ type: "ping" });
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
    this.handlers.clear();
  }
}

function joinPath(base: string, suffix: string): string {
  const trimmed = base.endsWith("/") ? base.slice(0, -1) : base;
  return trimmed + suffix;
}
