/**
 * Live integration — runs ONLY when ASSISTANT_URL is set (e.g. a deployed
 * container). Skipped otherwise, so `npm test` never needs a server.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { AssistantClient } from "../src/client.js";

const BASE = process.env.ASSISTANT_URL ?? "";
const maybe = BASE ? test : test.skip;

maybe("live: health + message round-trip over /v1", async () => {
  const client = new AssistantClient({ baseUrl: BASE, apiKey: process.env.ASSISTANT_API_KEY });
  const { text } = await client.collect("Reply with exactly OK", { userId: "ts_sdk_live" });
  assert.ok(text.length > 0, "expected a non-empty response");
});
