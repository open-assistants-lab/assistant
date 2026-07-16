# Summarization Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist conversation summaries to `MessageStore` so token savings survive across HTTP requests and agent loop invocations.

**Architecture:** `SummarizationMiddleware` already has an `on_summarize` async callback that fires after each successful summary. We wire it in `runner.py:create_sdk_loop()` to call `MessageStore.add_summary_message()`. The HTTP endpoints already load history via `get_messages_with_summary(50)` which returns the latest summary + recent messages — so persistence takes effect automatically on the next request. The `_messages_from_conversation()` function in `runner.py` already handles `role='summary'` messages by wrapping them as `[SUMMARY OF PREVIOUS CONVERSATION]\n{content}`.

**Note on summary accumulation:** Old summaries are NOT deleted — they accumulate in the DB (each ~1-3 KB, negligible cost even after 10K cycles). `get_messages_with_summary()` only ever loads the latest one (`ORDER BY id DESC LIMIT 1`), so old rows are invisible to the runtime. Keeping them preserves the full compression chain in case information recovery is ever needed.

**Tech Stack:** Python 3.13, SummarizationMiddleware, MessageStore (SQLite via HybridDB), asyncio

**Files touched:**
| File | Action | Responsibility |
|------|--------|----------------|
| `src/sdk/runner.py:188-199` | Modify | Wire `on_summarize` callback in `create_sdk_loop()` |
| `tests/sdk/test_middleware_conformance.py` | Modify | Add test that `on_summarize` callback is invoked during summarization |
| `tests/sdk/test_runner.py` | Create | Integration test that persisted summary is loaded back via `get_messages_with_summary` |

---

### Task 1: Test that `on_summarize` callback is invoked during `abefore_model`

**Files:**
- Modify: `tests/sdk/test_middleware_conformance.py` (after line 195, before the WS protocol tests)

- [ ] **Step 1: Write the failing test**

Add to `TestSDKSummarizationMiddleware` class:

```python
@pytest.mark.asyncio
async def test_sdk_summarization_callback_invoked_on_summarize(self):
    from unittest.mock import AsyncMock
    from src.sdk.messages import Message
    from src.sdk.middleware_summarization import SummarizationMiddleware
    from src.sdk.state import AgentState

    callback = AsyncMock()
    mw = SummarizationMiddleware(
        trigger_tokens=10,
        keep_tokens=5,
        on_summarize=callback,
    )

    # Mock _generate_summary to return a summary instead of calling LLM
    mw._generate_summary = AsyncMock(return_value="This is a test summary of the conversation.")

    # Create enough messages to exceed trigger_tokens=10
    msgs = [Message.user(f"Message number {i} about various topics.") for i in range(20)]
    state = AgentState(messages=msgs)

    result = await mw.abefore_model(state)

    assert result is not None
    assert "messages" in result
    callback.assert_awaited_once()
    callback.assert_awaited_with("This is a test summary of the conversation.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/sdk/test_middleware_conformance.py::TestSDKSummarizationMiddleware::test_sdk_summarization_callback_invoked_on_summarize -v`

Expected: FAIL — either because `AsyncMock` isn't imported yet (import error) or the test logic needs fixing.

- [ ] **Step 3: Fix any import issues, verify test passes**

The code already supports async callbacks at `middleware_summarization.py:243-253` so this should work.

Run again: `uv run pytest tests/sdk/test_middleware_conformance.py::TestSDKSummarizationMiddleware::test_sdk_summarization_callback_invoked_on_summarize -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/sdk/test_middleware_conformance.py
git commit -m "test: verify on_summarize callback is invoked during abefore_model"
```

---

### Task 2: Wire `on_summarize` in `create_sdk_loop()`

**Files:**
- Modify: `src/sdk/runner.py:188-199`

- [ ] **Step 1: Write a failing integration test first**

Create a new test file `tests/sdk/test_runner.py`:

```python
"""Tests for SDK runner (create_sdk_loop, run_sdk_agent, etc.)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sdk.messages import Message
from src.sdk.state import AgentState


@pytest.mark.asyncio
async def test_create_sdk_loop_summarization_persists_summary():
    """Verify that when summarization fires, the summary is persisted to MessageStore."""
    from src.sdk.runner import create_sdk_loop

    # Set low trigger tokens so summarization fires easily
    with patch("src.sdk.runner.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = True
        settings.memory.summarization.trigger_tokens = 10
        settings.memory.summarization.keep_tokens = 5
        settings.memory.summarization.model = "fake-model"
        settings.agent.model = "fake-model"

        with patch("src.sdk.runner.create_model_from_config") as mock_create_provider:
            mock_provider = AsyncMock()
            mock_provider.model = "fake-model"
            mock_create_provider.return_value = mock_provider

            with patch("src.sdk.runner.get_message_store") as mock_get_store:
                mock_store = MagicMock()
                mock_get_store.return_value = mock_store

                loop = await create_sdk_loop(user_id="test_persist_user")

                # The SummarizationMiddleware should have on_summarize wired
                summarization_mw = None
                for mw in loop.middlewares:
                    if mw.__class__.__name__ == "SummarizationMiddleware":
                        summarization_mw = mw
                        break

                assert summarization_mw is not None
                assert summarization_mw._on_summarize is not None

                # Now trigger summarization by calling abefore_model with many messages
                summarization_mw._generate_summary = AsyncMock(return_value="Persisted summary content.")

                msgs = [Message.user(f"Long message number {i} for triggering summarization.") for i in range(30)]
                state = AgentState(messages=msgs)
                result = await summarization_mw.abefore_model(state)

                assert result is not None, "Summarization should have triggered"

                # Verify the callback was called (it should call add_summary_message)
                assert mock_get_store.call_count >= 1
                assert mock_store.add_summary_message.called
                call_arg = mock_store.add_summary_message.call_args[0][0]
                assert "Persisted summary content" in call_arg


@pytest.mark.asyncio
async def test_create_sdk_loop_summarization_no_persist_when_disabled():
    """Verify no on_summarize callback when summarization is disabled."""
    from src.sdk.runner import create_sdk_loop

    with patch("src.sdk.runner.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.memory.summarization.enabled = False
        settings.agent.model = "fake-model"

        with patch("src.sdk.runner.create_model_from_config") as mock_create_provider:
            mock_provider = AsyncMock()
            mock_provider.model = "fake-model"
            mock_create_provider.return_value = mock_provider

            loop = await create_sdk_loop(user_id="test_no_persist")

            summarization_mw = None
            for mw in loop.middlewares:
                if mw.__class__.__name__ == "SummarizationMiddleware":
                    summarization_mw = mw
                    break

            assert summarization_mw is None, "Summarization middleware should not exist when disabled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/sdk/test_runner.py -v`

Expected: FAIL — because the `on_summarize` callback is not wired in `runner.py` yet.

- [ ] **Step 3: Wire the callback in `create_sdk_loop()`**

Change `src/sdk/runner.py:188-199` from:

```python
    summary_config = settings.memory.summarization

    middlewares: list[Any] = []

    if summary_config.enabled:
        middlewares.append(
            SummarizationMiddleware(
                trigger_tokens=summary_config.trigger_tokens,
                keep_tokens=summary_config.keep_tokens,
                model=model_str,
            )
        )
```

To:

```python
    summary_config = settings.memory.summarization

    middlewares: list[Any] = []

    if summary_config.enabled:
        from src.storage.messages import get_message_store

        async def _persist_summary(content: str) -> None:
            try:
                store = get_message_store(user_id, workspace_id)
                store.add_summary_message(content)
                logger.info(
                    "summarization.persisted",
                    {"summary_length": len(content)},
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(
                    "summarization.persist_failed",
                    {"error": str(e)},
                    user_id=user_id,
                )

        middlewares.append(
            SummarizationMiddleware(
                trigger_tokens=summary_config.trigger_tokens,
                keep_tokens=summary_config.keep_tokens,
                model=model_str,
                on_summarize=_persist_summary,
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/sdk/test_runner.py -v`

Expected: PASS

- [ ] **Step 5: Run full SDK test suite to confirm no regressions**

Run: `uv run pytest tests/sdk/ -v`

Expected: All previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/sdk/runner.py tests/sdk/test_runner.py
git commit -m "feat: persist conversation summaries to MessageStore"
```

---

### Task 3: End-to-end verification (optional, manual)

- [ ] **Step 1: Start the server**

```bash
uv run assistant http
```

- [ ] **Step 2: Send enough messages to trigger summarization (>50K tokens)**

```bash
# Send a long message
curl -X POST http://localhost:8000/message \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer test" \
  -d '{"user_id": "test_user", "message": "Tell me about '$(python -c "print('x' * 50000)")'" }'
```

- [ ] **Step 3: Verify summary was persisted**

Check the DB directly:
```bash
python -c "
from src.storage.messages import get_message_store
store = get_message_store('test_user')
msgs = store.get_messages_with_summary(50)
for m in msgs:
    print(f'[{m.role}] {m.content[:100]}...')
"
```

Expected: A `summary` role message appears, followed by recent messages after it.

---
