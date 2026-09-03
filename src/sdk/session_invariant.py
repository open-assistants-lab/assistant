"""Model-visible ⟺ logged invariant (R-SL1, P1-T12).

Dev/test-time assertion: every model request's message boundary and folded
header are rebuildable from the session-event log. A deliberately unlogged
input fails the assert — closing the "agent claims the model saw X" class
of dispute with evidence.
"""

from __future__ import annotations

from src.sdk.messages import Message
from src.sdk.session_events import (
    derive_system_prompt,
    deriveMessages,
)


class SessionInvariantError(AssertionError):
    """A model-visible message is missing from (or diverges in) the log."""


def _role_content(msg: Message) -> tuple[str, str]:
    content = msg.content if isinstance(msg.content, str) else ""
    return msg.role, content


def assert_model_visible_logged(
    session_id: str,
    user_id: str,
    messages: list[Message],
    system_prompt: str | None = None,
) -> None:
    """Assert every model-visible message + the folded header is in the log.

    Compares role+content (and tool_call ids/names for assistant turns with
    calls) of `messages` against the log projection. Raises
    SessionInvariantError on the first divergence.
    """
    derived = deriveMessages(session_id, user_id)
    derived_header = derive_system_prompt(session_id, user_id)

    if system_prompt is not None:
        if derived_header is None:
            raise SessionInvariantError(
                "folded header (system prompt) is not logged for "
                f"session {session_id}"
            )
        if derived_header != system_prompt:
            raise SessionInvariantError(
                f"folded header diverges: logged {derived_header!r:.80} != "
                f"requested {system_prompt!r:.80}"
            )

    def _key(m: Message) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        role, content = _role_content(m)
        tools = tuple(
            (tc.id, tc.name) for tc in (m.tool_calls or [])
        )
        return (role, content, tools)

    derived_keys = [_key(m) for m in derived]

    for idx, msg in enumerate(messages):
        if msg.role == "system":
            continue  # header checked separately
        k = _key(msg)
        if k not in derived_keys:
            role, content = _role_content(msg)
            raise SessionInvariantError(
                f"model-visible message #{idx} ({role}: {content[:60]!r}) "
                f"is not logged in session {session_id}"
            )
