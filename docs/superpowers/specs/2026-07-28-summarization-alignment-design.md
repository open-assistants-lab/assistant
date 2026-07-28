# Summarization Middleware Alignment Spec

> **Date:** 2026-07-28
> **Goal:** Align our `SummarizationMiddleware` 100% with LangChain's implementation, keeping `on_summarize` callback and `force_summarize()` as our extensions.

---

## Overview

Rewrite `src/sdk/middleware_summarization.py` to match LangChain's `SummarizationMiddleware` API and behavior. Remove summary quality validation and duplicate prevention guard. Add AI/Tool pair preservation, flexible trigger/keep config, model-aware token counting, provider-reported token checking, and message trimming.

---

## Changes

### New types

```python
ContextSize = tuple[Literal["tokens", "messages", "fraction"], int | float]
TriggerClause = TypedDict("TriggerClause", total=False, tokens=int, messages=int, fraction=float)
```

### Constructor

```python
class SummarizationMiddleware(Middleware):
    def __init__(
        self,
        model: str,
        *,
        trigger: ContextSize | TriggerClause | list[ContextSize | TriggerClause] | None = None,
        keep: ContextSize = ("messages", 20),
        token_counter: TokenCounter | None = None,
        summary_prompt: str = DEFAULT_SUMMARY_PROMPT,
        trim_tokens_to_summarize: int | None = 4000,
        on_summarize: SummaryCallback | None = None,
    ): ...
```

- `model`: Full model string (e.g. `ollama-cloud:deepseek-v4-flash`). Used via `create_model_from_config()` to create a provider for summary generation.
- `trigger`: Flexible trigger config. `None` = no auto-trigger (only `force_summarize` works). Supports:
  - `("tokens", 8000)` — trigger when total tokens >= 8000
  - `("messages", 50)` — trigger when message count >= 50
  - `("fraction", 0.8)` — trigger at 80% of model's max input tokens
  - `{"tokens": 4000, "messages": 10}` — AND: both must be met
  - `[("fraction", 0.8), ("messages", 100)]` — OR: either triggers
- `keep`: What to preserve after summarization. Same `ContextSize` type but single value (not list).
  - `("messages", 20)` — keep most recent 20 messages (default)
  - `("tokens", 2000)` — keep most recent 2000 tokens
  - `("fraction", 0.3)` — keep 30% of model's max input tokens
- `token_counter`: Optional custom token counter. Defaults to `count_tokens_approximately` with model-aware tuning.
- `summary_prompt`: Prompt template. Defaults to LangChain's `DEFAULT_SUMMARY_PROMPT` (SESSION INTENT, SUMMARY, ARTIFACTS, NEXT STEPS). Contains `{messages}` placeholder.
- `trim_tokens_to_summarize`: Max tokens to send to summary LLM. `None` = no trimming. Default 4000.
- `on_summarize`: Our extension. Callback invoked with summary content on success.

### Token counting

Model-aware approximate counter:

```python
def _get_approximate_token_counter(model: LLMProvider) -> TokenCounter:
    provider_type = getattr(model, "provider_type", "")
    if "anthropic" in provider_type:
        # Anthropic: ~3.3 chars per token (from LangChain's offline experiment)
        return partial(count_tokens_approximately, chars_per_token=3.3)
    return partial(count_tokens_approximately)
```

```python
def count_tokens_approximately(
    messages: list[Message],
    *,
    chars_per_token: float = 4.0,
) -> int:
    """Approximate token count based on character length."""
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        total += max(1, int(len(content) / chars_per_token))
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                total += max(1, int(len(tc.name) / chars_per_token))
                total += max(1, int(len(json.dumps(tc.arguments)) / chars_per_token))
        if msg.reasoning:
            total += max(1, int(len(msg.reasoning) / chars_per_token))
        total += 4  # role overhead
    return total
```

Note: LangChain's `use_usage_metadata_scaling` adjusts the approximate count by scaling against provider-reported tokens. This is complex and provider-specific. We skip it — the provider-reported token check (`_should_summarize_based_on_reported_tokens`) already handles the case where local counting is inaccurate.

### Provider-reported token check

Check if the last AI message has `usage.total_tokens` that exceeds the trigger threshold, even if local token counting is lower:

```python
def _should_summarize_based_on_reported_tokens(
    self, messages: list[Message], threshold: float
) -> bool:
    last_ai = next((m for m in reversed(messages) if m.role == "assistant"), None)
    if last_ai and last_ai.usage and last_ai.usage.input_tokens:
        # usage.input_tokens is the provider's reported input token count
        if last_ai.usage.input_tokens >= threshold:
            return True
    return False
```

### AI/Tool pair preservation

When splitting messages into "to summarize" and "to preserve", ensure AIMessage with `tool_calls` is never separated from its ToolMessage responses:

```python
@staticmethod
def _find_safe_cutoff_point(messages: list[Message], cutoff_index: int) -> int:
    """Find safe cutoff that doesn't split AI/Tool message pairs."""
    if cutoff_index >= len(messages):
        return cutoff_index

    # If message at cutoff is a tool result, search backward for the AI message
    # that initiated the tool call
    if messages[cutoff_index].role != "tool":
        return cutoff_index

    # Collect tool_call_ids from consecutive ToolMessages at/after cutoff
    tool_call_ids: set[str] = set()
    idx = cutoff_index
    while idx < len(messages) and messages[idx].role == "tool":
        if messages[idx].tool_call_id:
            tool_call_ids.add(messages[idx].tool_call_id)
        idx += 1

    # Search backward for AIMessage with matching tool_calls
    for i in range(cutoff_index - 1, -1, -1):
        msg = messages[i]
        if msg.role == "assistant" and msg.tool_calls:
            ai_ids = {tc.id for tc in msg.tool_calls if tc.id}
            if tool_call_ids & ai_ids:
                return i  # Include the AIMessage in the "to summarize" part

    # Fallback: advance past ToolMessages to avoid orphaned tool responses
    return idx
```

### Message trimming before summary

Trim old messages to fit within `trim_tokens_to_summarize` before sending to summary LLM:

```python
def _trim_messages_for_summary(self, messages: list[Message]) -> list[Message]:
    if self.trim_tokens_to_summarize is None:
        return messages
    # Keep last N messages that fit within the token budget
    total = 0
    cutoff = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        t = self._count_message_tokens(messages[i])
        if total + t > self.trim_tokens_to_summarize:
            cutoff = i + 1
            break
        total += t
    return messages[cutoff:]
```

### Summary LLM call

Use provider directly (no separate AgentLoop):

```python
async def _acreate_summary(self, messages_to_summarize: list[Message]) -> str:
    if not messages_to_summarize:
        return "No previous conversation history."

    trimmed = self._trim_messages_for_summary(messages_to_summarize)
    if not trimmed:
        return "Previous conversation was too long to summarize."

    formatted = self._messages_to_conversation_text(trimmed)
    prompt = self.summary_prompt.format(messages=formatted)

    try:
        provider = create_model_from_config(self.model)
        response = await provider.chat([
            Message.system("You are a context extraction assistant."),
            Message.user(prompt),
        ])
        content = response.content if isinstance(response.content, str) else str(response.content)
        return content.strip()
    except Exception as e:
        return f"Error generating summary: {e!s}"

def _create_summary(self, messages_to_summarize: list[Message]) -> str:
    """Sync version. Wraps async via event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — can't run_until_complete
            # Fallback: create a new thread with its own event loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self._acreate_summary(messages_to_summarize))
                return future.result()
        return loop.run_until_complete(self._acreate_summary(messages_to_summarize))
    except RuntimeError:
        return asyncio.run(self._acreate_summary(messages_to_summarize))

def _messages_to_conversation_text(self, messages: list[Message]) -> str:
    """Serialize messages as formatted text for the summary prompt's {messages} placeholder."""
    lines = []
    for msg in messages:
        role = msg.role
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)
```

### Message token counting

Uses the configured `token_counter`:

```python
def _count_message_tokens(self, msg: Message) -> int:
    return self.token_counter([msg])
```

### Summary message type

Use `role="user"` with `source` kwarg to distinguish internal messages — same pattern as LangChain's `HumanMessage` with `additional_kwargs`:

```python
# Summary (from summarisation middleware)
summary_msg = Message(role="user", content=f"Here is a summary of the conversation to date:\n\n{summary}", source="summarization_middleware")

# Grader feedback (from rubric middleware)  
grader_msg = Message(role="user", content=feedback, source="rubric_middleware")
```

No changes to `Role` Literal — stays as `Literal["system", "user", "assistant", "tool"]`. No changes to `to_openai()`, `to_anthropic()`, `to_ollama()`, or `to_gemini()` — they already handle `role="user"`.

The `source` field is set via `Message`'s `model_config = {"extra": "allow"}` — it's stored as an extra attribute, not a typed field. Consumers can check `getattr(msg, "source", None)` to identify the message origin.

The `_messages_from_conversation` converter in `runner.py` handles `role == "summary"` (existing storage role) by creating `Message(role="user", content=f"[SUMMARY OF PREVIOUS CONVERSATION]\n{content}", source="summarization_middleware")`.

### Summary prompt

Use LangChain's `DEFAULT_SUMMARY_PROMPT` verbatim:

```
<role>
Context Extraction Assistant
</role>

<primary_objective>
Your sole objective in this task is to extract the highest quality/most relevant context from the conversation history below.
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must extract the highest quality/most relevant pieces of information from your conversation history.
...
</objective_information>

<instructions>
## SESSION INTENT
What is the user's primary goal or request?

## SUMMARY
Extract and record all of the most important context from the conversation history.

## ARTIFACTS
What artifacts, files, or resources were created, modified, or accessed?

## NEXT STEPS
What specific tasks remain to be completed?
</instructions>

<messages>
Messages to summarize:
{messages}
</messages>
```

### Hooks

Both sync and async. The sync version wraps the async version using `asyncio.get_event_loop().run_until_complete()` (not `asyncio.run()` which fails inside an existing event loop):

```python
def before_model(self, state: AgentState) -> dict[str, Any] | None:
    """Sync version — wraps async _acreate_summary."""
    ...  # same logic but calls _create_summary

async def abefore_model(self, state: AgentState) -> dict[str, Any] | None:
    """Async version — calls _acreate_summary."""
    ...
```

Both follow the same logic:
1. Count tokens
2. Check `_should_summarize()` (includes provider-reported token check)
3. Determine cutoff index (with AI/Tool pair preservation)
4. Partition messages
5. Generate summary
6. Build new messages: `[summary_msg, *preserved_messages]`
7. Fire `on_summarize` callback
8. Return `{"messages": new_messages}`

### Public token counting

Keep a public `count_tokens` method for the summarize tool (`src/sdk/tools_core/summarize.py` uses `summary_mw._total_tokens`):

```python
def count_tokens(self, messages: list[Message]) -> int:
    """Public token count for a list of messages."""
    return self.token_counter(messages)
```

Update `src/sdk/tools_core/summarize.py` to use `summary_mw.count_tokens(loop.state.messages)` instead of `summary_mw._total_tokens(...)`.

### force_summarize with instructions

The summarize tool passes an `instructions` parameter to focus the summary. Keep this:

```python
async def force_summarize(self, state: AgentState, instructions: str | None = None) -> bool:
    """Force summarization, bypassing trigger check.

    Used by overflow recovery in AgentLoop and manual /summarize tool.
    If instructions are provided, they are prepended to the summary prompt
    to focus the summary on specific areas.
    """
    ...
```

When `instructions` is provided, prepend `[Focus: {instructions}]` to the conversation text before generating the summary.

### Removed

- **Summary quality validation** — no more checking for failure phrases or minimum length. Trust the summary LLM.
- **Duplicate prevention guard** (`_last_summary_msg_count`) — LangChain doesn't have it. The natural message count change after summarization prevents immediate re-trigger.
- **`_prune_tool_outputs()`** — replaced by `_trim_messages_for_summary()` which uses token-based trimming.
- **Separate AgentLoop for summary** — replaced by direct `provider.chat()` call.

### Kept (our extensions)

- **`on_summarize` callback** — persists summary to conversation store via `get_message_store(user_id).add_summary_message(content)`.
- **`force_summarize()`** — used by overflow recovery in `AgentLoop._run_react_loop` (when `ProviderContextOverflowError` fires) and by the manual `/summarize` tool. LangChain doesn't need this because LangGraph's `trim_messages` runs automatically in the graph runtime. We don't have that infrastructure — our `AgentLoop` calls the provider directly, so overflow recovery must be explicit. Bypasses the trigger check and always summarizes.
- **Settings-based config** — `config.yaml` + env vars mapped to the new `trigger`/`keep` types.

### Config mapping

```yaml
memory:
  summarization:
    enabled: true
    model: "ollama-cloud:deepseek-v4-flash"
    trigger: ["tokens", 8000]
    keep: ["messages", 20]
    trim_tokens_to_summarize: 4000
```

Env vars:
- `SUMMARY_TRIGGER_TOKENS=8000` → maps to `trigger=("tokens", 8000)`
- `SUMMARY_KEEP_MESSAGES=20` → maps to `keep=("messages", 20)`

Backward compat: if old `trigger_tokens` and `keep_tokens` fields are set in config (integers), convert at load time:

```python
class SummarizationConfig(_BaseSettings):
    enabled: bool = True
    model: str = Field(default="ollama:minimax-m2.5")
    trigger: list[Any] = Field(default_factory=lambda: ["tokens", 50000])
    keep: list[Any] = Field(default_factory=lambda: ["messages", 20])
    trim_tokens_to_summarize: int | None = 4000

    # Old fields for backward compat
    trigger_tokens: int | None = None
    keep_tokens: int | None = None

    model_config = SettingsConfigDict(env_prefix="SUMMARY_")

    def get_trigger(self) -> ContextSize | TriggerClause | list[...] | None:
        if self.trigger_tokens is not None:
            return ("tokens", self.trigger_tokens)
        return tuple(self.trigger) if self.trigger else None

    def get_keep(self) -> ContextSize:
        if self.keep_tokens is not None:
            return ("tokens", self.keep_tokens)
        return tuple(self.keep) if self.keep else ("messages", 20)
```

### Runner wiring

In `create_sdk_loop()`:

```python
if summary_config.enabled:
    middlewares.append(
        SummarizationMiddleware(
            model=summary_config.model or model_str,
            trigger=summary_config.get_trigger(),
            keep=summary_config.get_keep(),
            trim_tokens_to_summarize=summary_config.trim_tokens_to_summarize,
            on_summarize=_persist_summary,
        )
    )
```

### Files

**Modify:**
- `src/sdk/middleware_summarization.py` — full rewrite
- `src/sdk/middleware_rubric.py` — update grader feedback to use `source="rubric_middleware"` instead of `lc_source`
- `src/config/settings.py` — update `SummarizationConfig` with new fields + `get_trigger()` / `get_keep()`
- `src/sdk/runner.py` — update middleware construction to use `get_trigger()` / `get_keep()`; update `_messages_from_conversation` to set `source` kwarg
- `src/sdk/tools_core/summarize.py` — update `summary_mw._total_tokens` → `summary_mw.count_tokens`
- `tests/sdk/test_summarization_overhaul.py` — update existing tests
- `tests/sdk/test_middleware_rubric.py` — update grader source assertion
- `config.yaml` — update summarization config format

### Testing

- Unit: trigger with tokens, messages, fraction
- Unit: AND/OR trigger clauses
- Unit: AI/Tool pair preservation (split doesn't orphan tool results)
- Unit: message trimming before summary
- Unit: sync `before_model` and async `abefore_model`
- Unit: `force_summarize()` bypasses trigger
- Unit: `on_summarize` callback fires
- Unit: error handling returns error string as summary
- Unit: summary message uses `role="user"` with `source="summarization_middleware"`
- Unit: grader feedback uses `role="user"` with `source="rubric_middleware"` (update RubricMiddleware)
- Integration: full agent run with summarization triggered, verify in Langfuse