# @open-assistants-lab/assistant-sdk (TypeScript)

Typed TypeScript client SDK for the Assistant API — REST, SSE streaming, and the
WebSocket conversation protocol. Zero runtime dependencies; works in Node 18+ and
browsers.

> ⚠️ **Preview — non-frozen contract.** This is `0.1.0-preview.1`: the event
> envelopes and routes follow the current server wire contract and may change
> before a stable `1.0` release. Pin the version; read the changelog.

## Install

```bash
npm install @open-assistants-lab/assistant-sdk@preview
```

## Quick start

```ts
import { AssistantClient } from "@open-assistants-lab/assistant-sdk";

const client = new AssistantClient({
  baseUrl: "http://localhost:8080", // your deployed assistant
  apiKey: process.env.ASSISTANT_API_KEY, // omit when auth is disabled
  userId: "alice", // per-user data namespace (see server docs)
});

// Blocking round-trip
const res = await client.send("What is 2+2?");
console.log(res.response);

// SSE streaming — every server event, in order
for await (const event of client.stream("draft a plan")) {
  if (event.type === "text_delta") process.stdout.write(event.data.delta);
}

// Or collect the full run
const { text, events } = await client.collect("draft a plan");
```

## WebSocket conversation

```ts
import { ConversationSocket } from "@open-assistants-lab/assistant-sdk";

const socket = new ConversationSocket({ baseUrl: "http://localhost:8080", apiKey: "..." });
await socket.connect(); // resolves after auth_ok
socket.say("hello");
socket.on((msg) => console.log(msg));
```

## API surface

| Method | Endpoint (via `/v1`) | Purpose |
|---|---|---|
| `client.send()` | `POST /v1/message` | blocking round-trip |
| `client.stream()` | `POST /v1/message/stream` | SSE async iterable of events |
| `client.collect()` | same | full run: text + all events |
| `client.approve()/reject()/cancel()` | `POST /v1/message/*` | HITL interrupts |
| `client.getConversation()/listSessions()` | `GET /v1/...` | history |
| `client.socket()` | `/v1/ws/conversation` | WS protocol |

All routes sit under the **`/v1`** API prefix (default; pass `basePath: ""` for
legacy unprefixed servers).

## Events

Stream events mirror the server envelope `{"type", "data"}` — see
`src/types.ts` for the typed shapes (block events `text_start`/`text_delta`/
`text_end`, `tool_call`/`tool_result`, `reasoning_*`, `usage`, `done`, `error`,
HITL `interrupt`). Unknown fields are preserved (forward-compatible).

## Tests

```bash
npm install && npm test     # mock-based, no server needed
ASSISTANT_URL=http://localhost:8080 npm test   # + live integration test
```

## Preview caveat

The wire contract (event envelopes, route shapes) is stable **in practice** but
explicitly non-frozen until a partner has exercised both transports. Breaking
changes may land in `0.1.0-preview.*` releases. Stable `1.0.0` follows the
server contract freeze.

## Publish (maintainers)

```bash
npm run build && npm test
npm publish --tag preview   # never `latest` until the contract freezes
```

Requires npm credentials (npmjs.com) for the `@open-assistants-lab` scope.