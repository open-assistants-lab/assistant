/**
 * Unit tests for the SSE parser — no server required.
 * Run: npm test  (after build, via node --test)
 */

import assert from "node:assert/strict";
import test from "node:test";
import { parseSSEStream } from "../src/sse.js";

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(body: ReadableStream<Uint8Array>) {
  const out = [];
  for await (const msg of parseSSEStream(body)) out.push(msg);
  return out;
}

test("parses a single data event", async () => {
  const msgs = await collect(streamFrom(['data: {"type":"text_delta","data":{"delta":"hi"}}\n\n']));
  assert.equal(msgs.length, 1);
  assert.deepEqual(JSON.parse(msgs[0].data), { type: "text_delta", data: { delta: "hi" } });
});

test("ignores heartbeat comments and blank lines", async () => {
  const msgs = await collect(
    streamFrom([": ping\n\n", "\n", 'data: {"type":"done","data":{}}\n\n']),
  );
  assert.equal(msgs.length, 1);
  assert.equal(JSON.parse(msgs[0].data).type, "done");
});

test("handles events split across chunk boundaries", async () => {
  const msgs = await collect(
    streamFrom([
      'data: {"type":"tool_input_st',
      'art","data":{"name":"files_re',
      'ad"}}\n\n',
    ]),
  );
  assert.equal(msgs.length, 1);
  assert.equal(JSON.parse(msgs[0].data).data.name, "files_read");
});

test("handles CRLF line endings", async () => {
  const msgs = await collect(
    streamFrom(['data: {"a":1}\r\n\r\ndata: {"b":2}\r\n\r\n']),
  );
  assert.equal(msgs.length, 2);
});

test("flushes a trailing event without final blank line", async () => {
  const msgs = await collect(streamFrom(['data: {"type":"error"}\n'])); // no \n\n at end
  assert.equal(msgs.length, 1);
});

test("multi-line data fields are joined with newline", async () => {
  const msgs = await collect(streamFrom(["data: line1\ndata: line2\n\n"]));
  assert.equal(msgs[0].data, "line1\nline2");
});
