# Auto-Generated Chat Titles

**Date:** 2026-07-19  
**Status:** Design (pre-implementation)

## Problem

Chat session titles in the native app sidebar are derived from the first user message verbatim. Long first messages become long sidebar titles, and messages that start with generic greetings ("hi", "hello", "what is 2+2?") produce unhelpful titles. ChatGPT addresses this by generating a short summary title after the first exchange using a lightweight model call.

## Goal

After the first assistant response in a new chat, generate a concise (3-5 word) title summarizing the conversation topic using a `provider.chat()` call. Replace the verbatim first-message title. Re-generate is not needed — one-shot after first exchange.

## Design

### When to trigger

- After `stream_done` for the first assistant response in a chat.
- Check: is there exactly 1 user message in the chat? If yes, generate title. (Tool messages don't count — a first exchange with tool calls still has 1 user message.)
- NOT triggered for subsequent messages — title is set once and stays stable.
- NOT triggered for history-loaded chats (title already set).

### Where the summarization happens

**Backend-side** — a new endpoint `POST /conversation/title` that:
1. Takes `user_id` and `session_id`.
2. Uses the `title_model` from settings to generate a short title from the first user + assistant messages.
3. Returns `{"title": "Shanghai weather forecast"}` (3-5 words, no punctuation at end).

**Rationale for backend:** The native app shouldn't parse LLM output or manage a second streaming connection. The backend already has the provider abstraction and can use a cheaper/faster model.

### Backend: `POST /conversation/title`

```python
@router.post("/conversation/title")
async def generate_title(req: TitleRequest) -> dict[str, str]:
    """Generate a short title for a chat session."""
    conversation = get_message_store(req.user_id)
    messages = conversation.get_messages_by_session_id(req.session_id, limit=50)
    if len(messages) < 2:
        raise HTTPException(400, "Need at least user + assistant message")

    # Find first user message and first assistant message
    # (messages list may include tool messages before the assistant response)
    user_msg = ""
    assistant_msg = ""
    for msg in messages:
        if not user_msg and msg.role == "user":
            user_msg = msg.content
        elif not assistant_msg and msg.role == "assistant":
            assistant_msg = msg.content
        if user_msg and assistant_msg:
            break

    if not user_msg:
        raise HTTPException(400, "No user message found")
    if len(user_msg) < 5:
        raise HTTPException(400, "First message too short to summarize")
    if not assistant_msg.strip():
        raise HTTPException(400, "Empty assistant response")

    assistant_msg = assistant_msg[:500]
    title = await _summarize_title(user_msg, assistant_msg)
    if not title:
        raise HTTPException(500, "Title generation failed")

    conversation.update_session_title(req.session_id, title)
    return {"title": title, "session_id": req.session_id}
```

### `_summarize_title` implementation

```python
async def _summarize_title(user_msg: str, assistant_msg: str) -> str | None:
    """Generate a 3-5 word title via a simple provider.chat() call."""
    from src.config import get_settings
    from src.sdk.providers.factory import create_model_from_config
    from src.sdk.messages import Message

    settings = get_settings()
    model = settings.agent.title_model
    provider = create_model_from_config(model)

    prompt = (
        "Summarize the following conversation in 3-5 words. "
        "Use a short noun phrase. No punctuation at the end. No quotes.\n\n"
        f"User: {user_msg}\n"
        f"Assistant: {assistant_msg}\n\n"
        "Title:"
    )

    try:
        response = await provider.chat(
            messages=[Message.user(prompt)],
            tools=None,
            max_tokens=20,
            temperature=0.3,
        )
        title = response.content.strip().strip('"').strip("'").strip()
        # Strip trailing punctuation
        while title and title[-1] in ".。,;:!?,;:!?":
            title = title[:-1].strip()
        if len(title) > 40:
            title = title[:40]
        return title or None
    except Exception:
        return None
```

### `TitleRequest` model

```python
class TitleRequest(BaseModel):
    user_id: str = "default_user"
    session_id: str
```

### Title generation prompt

```
Summarize the following conversation in 3-5 words. Use a short noun phrase. No punctuation at the end. No quotes.

User: {user_msg}
Assistant: {assistant_msg}

Title:
```

- Max tokens: 20
- Temperature: 0.3 (deterministic-ish)
- Model: uses a dedicated title-model setting (defaults to the same as the chat model for now)
- **Provider call:** Use a simple `provider.chat()` with `max_tokens=20`, NOT an `AgentLoop` with tools. One-shot completion, no streaming.
- Title cap: 40 characters (truncate if model exceeds).

### Title model setting

A dedicated model setting for title generation, separate from the chat model. For now both default to the same value. This allows swapping in a cheaper/faster model later without touching chat behavior.

**Config (`src/config/settings.py`):**
```python
class AgentConfig(BaseModel):
    model: str = Field(default="deepseek:deepseek-v4-flash")
    title_model: str = Field(default="", description="Model for chat title summarization (empty = use model)")
```

**Endpoint:** `POST /conversation/title` uses `title_model` from settings if not explicitly passed in the request. The native app doesn't need to know which model — it just calls the endpoint and gets the title back.

### Fallbacks

- **Short greeting (user message < 5 chars):** Keep first-message title. Don't call the model for "hi" or "ok".
- **Model failure (network error, timeout, empty response):** Keep first-message title. No retry — cosmetic.
- **Truncate both ways:** Title capped at 40 chars. First-message fallback also capped at 60 chars (existing behavior).

### Persistence

Title stored on the **first user message's metadata** as `{"session_title": "Shanghai weather forecast"}`. No new table.

**Why not a separate table:** Single-user desktop app, no concurrency concerns, no JOIN needed. Metadata on first user message is simpler — no migration, no new schema.

**Storage change:** `update_session_title(session_id, title)` finds the first user message in that session and updates its metadata:
```python
def update_session_title(self, session_id: str, title: str) -> None:
    messages = self._core.fetch(limit=1, session_id=session_id, role="user")
    if not messages:
        return
    first = messages[0]
    meta = first.metadata or {}
    meta["session_title"] = title
    # Update via direct SQL since coremem doesn't support metadata updates
    with self._core.db._connect() as cur:
        cur.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            [json.dumps(meta), first.id],
        )
```

### `GET /conversation/sessions` update

The existing `get_sessions()` queries first user messages. Update to check for `session_title` in metadata first, falling back to content truncation:
```sql
SELECT m.session_id,
       COALESCE(json_extract(m.metadata, '$.session_title'), SUBSTR(m.content, 1, 60)) as title
FROM messages m
WHERE m.role = 'user' AND m.session_id != ''
ORDER BY m.ts ASC
```

Chats with generated titles show the generated title. Chats without (old chats, or if generation failed) fall back to the first-message truncation.

### Native app changes

1. **After `stream_done`**: if this is the first exchange (exactly 1 user message in the chat), fire `POST /conversation/title` with `session_id` and `user_id`. No model parameter — the backend uses `title_model` from settings.

2. **New `Msg` variant:**
   ```zig
   title_generated: native_sdk.EffectResponse,
   ```

3. **Handler:** On `title_generated` success, parse `{"title": "..."}` from response body, update `chat.title`. The `sessions_loaded` handler already updates titles from the API, so on restart the generated title persists.

4. **Fallback:** If the title generation fails (network error, backend down, 400 response), keep the current first-message title. No retry — it's cosmetic.

### Session_id passing

The `session_id` is already known by the native app (e.g., `chat-1`, `chat-2`, or a hash-based id). Pass it to the title endpoint along with `user_id`.

### Edge cases

- **Title generation in flight when chat is deleted:** The `title_generated` handler should check if the chat still exists (find by `session_id` matching `chat.sessionId()`) before updating. If not, no-op.
- **Title generation for a chat the user switched away from:** Update the chat's title in the model regardless of whether it's active — the title shows in the sidebar for all chats.
- **Multiple rapid sends before first response completes:** Title generation only fires after `stream_done` of the first response, so rapid sends don't trigger multiple title calls.
- **History-loaded chat:** Has more than 1 user message after load (or `history_loaded = true`), so the user-message-count check prevents re-triggering.

## What this spec does NOT cover

- Title editing by the user (future feature)
- Title regeneration (future feature)
- Multi-turn title refinement (future feature)
- Fallback to first-message title when model unavailable (handled by the COALESCE in SQL)

## Files affected

**Backend:**
- `src/config/settings.py` — add `title_model` to `AgentConfig`
- `src/http/routers/conversation.py` — new `POST /conversation/title` endpoint, `TitleRequest` model, `_summarize_title` helper
- `src/storage/messages.py` — `update_session_title()`, updated `get_sessions()` SQL with COALESCE on metadata

**Native:**
- `native-sdk-experiment/src/main.zig` — new `title_generated` Msg, fire after first `stream_done`, handler to update `chat.title`
- `native-sdk-experiment/src/tests.zig` — test that title generation fires after first exchange, test that title updates on response

## Implementation order

1. Backend: `title_model` setting in `AgentConfig`
2. Backend: `update_session_title()` + updated `get_sessions()` with COALESCE
3. Backend: `POST /conversation/title` endpoint with `_summarize_title`
4. Native: Fire `POST /conversation/title` after first `stream_done`
5. Native: Handle `title_generated` response, update `chat.title`
6. Tests: backend endpoint test + native handler test