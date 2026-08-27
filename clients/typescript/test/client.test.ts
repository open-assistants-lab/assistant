/**
 * AssistantClient unit tests — mock fetchImpl, no server required.
 * Verifies route paths (basePath=/v1 default), envelopes, and error handling.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { AssistantClient, AssistantApiError } from "../src/client.js";
import type { AssistantClientOptions } from "../src/client.js";

function sseResponse(events: Array<{ type: string; data: unknown }>): Response {
  const body = events
    .map((e) => `data: ${JSON.stringify({ type: e.type, data: e.data })}\n\n`)
    .join("");
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(body));
      c.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeClient(
  fetchImpl: typeof fetch,
  overrides: Partial<AssistantClientOptions> = {},
): { client: InstanceType<typeof import("../src/client.js").AssistantClient>; calls: Array<{ url: string; init: RequestInit }> } {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const wrapped = ((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    return fetchImpl(String(input), init);
  }) as typeof fetch;
  const client = new AssistantClient({ baseUrl: "http://test", apiKey: "k", fetch: wrapped, ...overrides });
  return { client, calls };
}

test("send() posts to /v1/message with Bearer + user_id", async () => {
  let capturedBody: unknown;
  const { client, calls } = makeClient((_url, init) => {
    capturedBody = JSON.parse(String(init?.body));
    return Promise.resolve(
      new Response(JSON.stringify({ response: "ok" }), { status: 200 }),
    );
  });
  const res = await client.send("hello", { userId: "alice", session_id: "s1" });
  assert.equal(res.response, "ok");
  assert.equal(new URL(calls[0].url).pathname, "/v1/message");
  assert.equal((capturedBody as Record<string, unknown>).user_id, "alice");
  assert.equal((calls[0].init.headers as Record<string, string>).Authorization, "Bearer k");
});

test("stream() hits /v1/message/stream and yields parsed events", async () => {
  const { client, calls } = makeClient((_url, _init) =>
    Promise.resolve(
      sseResponse([
        { type: "text_start", data: { block_id: "b1" } },
        { type: "text_delta", data: { delta: "hi" } },
        { type: "done", data: {} },
      ]),
    ),
  );
  const events = [];
  for await (const e of client.stream("hey", { userId: "u" })) events.push(e);
  assert.equal(new URL(calls[0].url).pathname, "/v1/message/stream");
  assert.deepEqual(events.map((e) => e.type), ["text_start", "text_delta", "done"]);
});

test("collect() concatenates text deltas", async () => {
  const { client } = makeClient((_url) =>
    Promise.resolve(
      sseResponse([
        { type: "text_delta", data: { delta: "he" } },
        { type: "text_delta", data: { delta: "llo" } },
        { type: "done", data: {} },
      ]),
    ),
  );
  const { text } = await client.collect("x");
  assert.equal(text, "hello");
});

test("basePath='' restores legacy unprefixed routes", async () => {
  const { client, calls } = makeClient((_url) => Promise.resolve(new Response("{}", { status: 200 })), { basePath: "" });
  await client.listSessions("u");
  assert.equal(new URL(calls[0].url).pathname, "/conversation/sessions");
});

test("approve() posts to /v1/message/approve", async () => {
  const { client, calls } = makeClient((_url) => Promise.resolve(new Response("{}", { status: 200 })));
  const res = await client.approve("call_1", { userId: "u", sessionId: "s" });
  assert.ok(res);
  assert.equal(new URL(calls[0].url).pathname, "/v1/message/approve");
});

test("API error surfaces AssistantApiError with status", async () => {
  const { client } = makeClient((_url) =>
    Promise.resolve(new Response(JSON.stringify({ detail: "nope" }), { status: 403 })),
  );
  await assert.rejects(client.listSessions("u"), (err: unknown) => {
    assert.ok(err instanceof AssistantApiError);
    assert.equal((err as AssistantApiError).status, 403);
    return true;
  });
});
