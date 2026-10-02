# Multi-Provider Architecture Research — 2026-05-09

## How OpenCode Handles 75+ Providers Consistently

### Core Architecture

```
models.dev (4172+ models, capabilities metadata)
        │
        ▼
  fromModelsDevModel()  ───  transforms metadata into capability flags
        │
        ├── interleaved: { field: "reasoning_content" }  ← deepseek, kimi, etc.
        ├── toolcall: true                                ← tool-capable models
        ├── reasoning: true                               ← thinking/reasoning models
        └── temperature: true                             ← non-deterministic models
        │
        ▼
  Vercel AI SDK (@ai-sdk/openai, @ai-sdk/anthropic, etc.)
  ─── handles protocol: message serialization, tool loops, streaming
        │
        ▼
  Custom loaders (optional) ─── provider-specific overrides
        │
        ├── deepseek: auto-detects interleaved reasoning_content
        ├── anthropic: thinking beta headers
        ├── openai: responses() vs chat() routing
        └── github-copilot: model-version-based API selection
```

### The Key Pattern

OpenCode does NOT use provider-name-based conditionals. Instead, it uses **capability flags from models.dev**:

```typescript
// How OpenCode detects deepseek's reasoning_content requirement:
interleaved:
  model.interleaved ??
  existingModel?.capabilities.interleaved ??
  (!existingModel && apiNpm === "@ai-sdk/openai-compatible" && apiID.includes("deepseek")
    ? { field: "reasoning_content" }  // auto-detected from models.dev metadata
    : false),
```

Every model that uses `reasoning_content` as an interleaved field gets the same handling — not just deepseek. This is portable across providers.

### Bundle Provider Registry

OpenCode maps providers to their SDK packages:

```typescript
const BUNDLED_PROVIDERS = {
  "@ai-sdk/anthropic": ...,
  "@ai-sdk/openai": ...,
  "@ai-sdk/openai-compatible": ...,  // deepseek, ollama, groq, together, etc.
  "@ai-sdk/google": ...,
  "@ai-sdk/deepinfra": ...,
  // ... 20+ bundled providers
}
```

The AI SDK layer handles:
- Message serialization (JSON → provider-specific format)
- Tool calling loop (streaming detection, argument accumulation)
- Streaming protocol (SSE parsing, chunk assembly)
- Error handling (rate limits, auth errors, retries)

## What EA Should Adopt

### Current State (Brittle)

```python
# EA currently has provider-name hacks:
if "deepseek" in base_url:
    handle_reasoning_specially()  # only deepseek
if hasattr(delta, "reasoning_content"):
    events.append(StreamChunk.reasoning(...))  # hardcoded check
```

### Target State (Capability-Driven)

```python
# Parse from models.dev:
model.capabilities = {
    "interleaved": {"field": "reasoning_content"},  # deepseek, kimi, minimax
    "toolcall": True,
    "reasoning": True,
}

# In AgentLoop — capability-driven, not provider-driven:
if "reasoning_content" in model.interleaved_fields:
    preserve_reasoning_between_turns(message)
```

### Implementation Plan

| Step | What | Priority |
|---|---|---|
| 1 | Add `capabilities` field to ModelInfo from models.dev registry | P0 |
| 2 | Parse `interleaved` fields into a set on ModelInfo | P0 |
| 3 | In AgentLoop, preserve interleaved fields between tool turns | P0 |
| 4 | Wire memcore into MemoryMiddleware.before_agent() (Option A: pre-load injection) | P0 |
| 5 | Remove the deepseek-specific streaming hack in openai.py | P1 |
| 6 | Remove the non-streaming fallback in chat_stream() | P1 |

### Agent Loop Flow with Pre-Load Injection (Option A)

```
User message → MemoryMiddleware.before_agent()
    → memcore.search(user_message) → raw verbatim context
    → inject into system prompt as "## Relevant Memory\n..."
    → LLM reads context + answers directly
    → NO tool call needed for memory_search
```

This sidesteps the tool-calling → final-answer transition problem entirely.
The LLM never calls memory_search — it sees the results in its initial prompt.
