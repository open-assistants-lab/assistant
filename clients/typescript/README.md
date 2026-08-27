# assistant-client

Typed TypeScript client SDK for the [Assistant](../../) server — REST, SSE streaming,
and the WebSocket conversation protocol. Zero runtime dependencies.

Works in Node 18+ and browsers.

## Install

```bash
npm install assistant-client   # once published
# or from this repo:
npm install /path/to/assistant/clients/typescript
```

## Quick start

```typescript
import { AssistantClient } from "assistant-client";

const client = new AssistantClient({
  baseUrl: "http://localhost:8000",
  apiKey: process.env.EA_API_KEY, // optional — auth is off for localhost by default
});

// Blocking request
const reply = await client.send("What's on my calendar today?");
console.log(reply.response);

// Streaming (SSE)
for await (const event of client.stream("Write a haiku about databases")) {
  if (event.type === "text_delta") process.stdout.write(event.data.delta);
  if (event.type === "tool_input_start") console.log("→ tool:", event.data.name);
  if (event.type === "interrupt") console.log("needs approval:", event.data.tool);
}

// Or collect the full answer in one call
const { text, events } = await client.collect("Summarize my todos");
```

## Human-in-the-loop (tool approvals)

```typescript
for await (const event of client.stream("Delete the old drafts")) {
  if (event.type === "interrupt") {
    // Ask your user, then approve or reject
    const ok = await askUser(event.data);
    if (ok) {
      const res = await client.approve(event.data.call_id!); // returns an SSE stream
    } else {
      await client.reject(event.data.call_id!, "not today");
    }
  }
}
```

## WebSocket (bidirectional + mid-turn steering)

Requires a global `WebSocket` (browsers and Node 22+ have one; on older Node pass
`wsImpl: require("ws")`).

```typescript
const socket = client.socket({ session_id: "my-session" });
await socket.connect(); // resolves after auth_ok when apiKey is set

socket.on((msg) => {
  switch (msg.type) {
    case "text_delta":     process.stdout.write(msg.content); break;
    case "done":           console.log("\ncost:", msg.cost_usd); break;
    case "steer_ack":      console.log("steer queued"); break;
    case "error":          console.error(msg.message); break;
  }
});

socket.say("Start a long research task");
socket.steer("Actually focus on TypeScript only");
socket.cancel();
socket.close();
```

## Conversation history & models

```typescript
await client.listSessions();
await client.getConversation({ sessionId: "my-session", limit: 50 });
await client.deleteSession("my-session");
await client.clearConversation();
await client.listModels();
```

## API surface

| Method | Endpoint | Notes |
|---|---|---|
| `send()` | `POST /message` | blocking |
| `stream()` / `collect()` | `POST /message/stream` | SSE with heartbeat filtering |
| `approve()` / `reject()` / `cancel()` | `/message/approve`, `/reject`, `/cancel` | HITL |
| `getConversation()` / `listSessions()` / `deleteSession()` / `clearConversation()` | `/conversation*` | history |
| `listModels()` | `GET /models` | provider catalog |
| `socket()` | `WS /ws/conversation` | typed WS protocol |

All server message types are exported as discriminated unions
(`StreamEvent`, `ServerMessage`), so unknown future event types never crash your code —
they just arrive as `{ type: string; data }`.

## Development

```bash
npm install        # dev deps (typescript, @types/node)
npm test           # typecheck + unit tests (no server needed)
npm run build      # emit dist/
```

Integration tests against a running server (`uv run assistant http`) are planned.
