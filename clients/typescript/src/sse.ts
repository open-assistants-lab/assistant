/**
 * Minimal SSE parser over a fetch ReadableStream.
 *
 * Works in Node 18+ and browsers (no dependencies). Handles:
 * - `data:` fields (single and multi-line)
 * - comment lines (`: ping` heartbeats) — ignored
 * - event boundaries (\n\n, \r\n\r\n)
 */

export interface SSEMessage {
  event?: string;
  data: string;
}

const CRLF = "\r\n";

export async function* parseSSEStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line. Normalize \r\n → \n first.
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const msg = parseRawEvent(rawEvent);
        if (msg) yield msg;
      }
    }
    // Flush any trailing event without a final blank line.
    const tail = parseRawEvent(buffer.replace(/\r\n/g, "\n"));
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

function parseRawEvent(raw: string): SSEMessage | undefined {
  let dataLines: string[] = [];
  let event: string | undefined;

  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank or heartbeat comment
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    } else if (line.startsWith("event:")) {
      event = line.slice(6).replace(/^ /, "");
    }
    // `id:` / `retry:` not used by the Assistant API — ignored.
  }

  if (dataLines.length === 0) return undefined;
  return { event, data: dataLines.join("\n") };
}
