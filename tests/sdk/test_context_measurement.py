import json

import pytest

from src.sdk.context_measurement import (
    build_context_snapshot,
    estimate_message_tokens,
    estimate_prepared_tokens,
    estimate_text_tokens,
    estimate_tool_schema_tokens,
    resolve_context_window,
)
from src.sdk.messages import Message, ToolCall, Usage
from src.sdk.providers.base import ModelInfo
from src.sdk.run_models import ContextFreshness, ContextSnapshot, ContextSource
from src.sdk.tools import ToolDefinition


def _length(text: str) -> int:
    return len(text)


def _tool(name: str = "lookup") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="Look up a value",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_text_message_roles_and_content_contribute(role: str) -> None:
    empty = estimate_message_tokens([Message(role=role)], _length)  # type: ignore[arg-type]
    populated = estimate_message_tokens([Message(role=role, content="content")], _length)  # type: ignore[arg-type]

    assert empty > 0
    assert populated > empty


def test_reasoning_contributes() -> None:
    plain = Message.assistant("answer")
    reasoned = Message.assistant("answer", reasoning="private reasoning")

    assert estimate_message_tokens([reasoned], _length) > estimate_message_tokens([plain], _length)


def test_assistant_tool_call_fields_contribute() -> None:
    plain = Message.assistant()
    called = Message.assistant(
        tool_calls=[ToolCall(id="call-1", name="lookup", arguments={"query": "weather"})]
    )

    assert estimate_message_tokens([called], _length) > estimate_message_tokens([plain], _length)


def test_tool_result_call_id_and_name_contribute() -> None:
    unnamed = Message(role="tool", content="sunny")
    named = Message.tool_result("call-1", "sunny", name="lookup")

    assert estimate_message_tokens([named], _length) > estimate_message_tokens([unnamed], _length)


def test_usage_provider_and_storage_metadata_do_not_contribute() -> None:
    base = Message.assistant("answer")
    enriched = Message.assistant(
        "answer",
        usage=Usage(input_tokens=999, output_tokens=888),
        provider_metadata={"openai": {"request_id": "large-provider-extra"}},
    )
    enriched.storage_id = "large-storage-extra"  # type: ignore[attr-defined]

    assert estimate_message_tokens([enriched], _length) == estimate_message_tokens([base], _length)


def test_multimodal_content_uses_stable_compact_json() -> None:
    first = Message.user("")
    first.content = [{"type": "image", "source": {"url": "x", "kind": "remote"}}]
    second = Message.user("")
    second.content = [{"source": {"kind": "remote", "url": "x"}, "type": "image"}]

    assert estimate_message_tokens([first], _length) == estimate_message_tokens([second], _length)


def test_custom_estimator_is_used_exactly_for_message_wire_fields() -> None:
    message = Message.user("abc")

    assert estimate_message_tokens([message], _length) == 4 + len("user") + len("abc")


def test_each_message_has_fixed_overhead() -> None:
    one = estimate_message_tokens([Message.user("")], lambda _text: 0)
    two = estimate_message_tokens([Message.user(""), Message.assistant()], lambda _text: 0)

    assert one == 4
    assert two == 8


def test_selected_tool_schema_contributes() -> None:
    assert estimate_tool_schema_tokens([_tool()], _length) > 0


def test_unselected_tool_schema_does_not_contribute() -> None:
    assert estimate_tool_schema_tokens(None, _length) == 0
    assert estimate_tool_schema_tokens([], _length) == 0


def test_tool_schema_order_does_not_change_total() -> None:
    tools = [_tool("alpha"), _tool("beta")]

    assert estimate_tool_schema_tokens(tools, _length) == estimate_tool_schema_tokens(
        list(reversed(tools)), _length
    )


def test_tool_schema_uses_openai_format_as_compact_sorted_json() -> None:
    tool = _tool()
    wire_json = json.dumps(tool.to_openai_format(), sort_keys=True, separators=(",", ":"))

    assert estimate_tool_schema_tokens([tool], _length) == len(wire_json)


def test_prepared_tokens_sum_messages_and_selected_schemas() -> None:
    messages = [Message.system("rules"), Message.user("question")]
    tools = [_tool()]

    assert estimate_prepared_tokens(messages, tools, _length) == estimate_message_tokens(
        messages, _length
    ) + estimate_tool_schema_tokens(tools, _length)


@pytest.mark.parametrize("text", ["", "a", "four", "longer deterministic text", "\u2603"])
def test_default_text_estimate_is_deterministic_and_nonnegative(text: str) -> None:
    assert estimate_text_tokens(text) == estimate_text_tokens(text)
    assert estimate_text_tokens(text) >= 0


def test_empty_prepared_context_is_zero() -> None:
    assert estimate_message_tokens([], estimate_text_tokens) == 0
    assert estimate_prepared_tokens([], None, estimate_text_tokens) == 0


def test_resolve_context_window_finds_exact_openai_model() -> None:
    seen: list[str] = []

    def list_models(provider: str) -> list[ModelInfo]:
        seen.append(provider)
        return [ModelInfo(id="gpt-5", provider_id="openai", context_window=400_000)]

    assert resolve_context_window("openai:gpt-5", list_models) == 400_000
    assert seen == ["openai"]


def test_resolve_context_window_preserves_openrouter_slash_remainder() -> None:
    models = [
        ModelInfo(
            id="anthropic/claude-sonnet-4",
            provider_id="openrouter",
            context_window=200_000,
        )
    ]

    assert resolve_context_window("openrouter:anthropic/claude-sonnet-4", lambda _: models) == 200_000


@pytest.mark.parametrize(
    "models",
    [
        [],
        [ModelInfo(id="other", provider_id="openai", context_window=10)],
        [ModelInfo(id="gpt-5", provider_id="other", context_window=10)],
        [ModelInfo(id="gpt-5", provider_id="openai", context_window=0)],
        [ModelInfo(id="gpt-5", provider_id="openai", context_window=-1)],
    ],
)
def test_resolve_context_window_rejects_unknown_mismatch_and_nonpositive(
    models: list[ModelInfo],
) -> None:
    assert resolve_context_window("openai:gpt-5", lambda _: models) is None


def test_resolve_context_window_returns_none_on_lister_error() -> None:
    def fail(_provider: str) -> list[ModelInfo]:
        raise RuntimeError("registry unavailable")

    assert resolve_context_window("openai:gpt-5", fail) is None


def test_snapshot_carries_identity_provenance_and_derived_percentage() -> None:
    snapshot = build_context_snapshot(
        model="openai:gpt-5",
        messages=[Message.user("abcdef")],
        tools=[_tool()],
        attempt=2,
        llm_call_index=3,
        source=ContextSource.PREPARED_CONTEXT,
        freshness=ContextFreshness.LIVE,
        estimator=_length,
        context_window_resolver=lambda _model: 100,
    )
    expected = estimate_prepared_tokens([Message.user("abcdef")], [_tool()], _length)

    assert snapshot.model == "openai:gpt-5"
    assert snapshot.attempt == 2
    assert snapshot.llm_call_index == 3
    assert snapshot.source is ContextSource.PREPARED_CONTEXT
    assert snapshot.freshness is ContextFreshness.LIVE
    assert snapshot.estimated_tokens == expected
    assert snapshot.context_window == 100
    assert snapshot.percentage == expected
    assert snapshot.estimated is True


def test_snapshot_has_null_window_and_percentage_when_model_is_unknown() -> None:
    snapshot = build_context_snapshot(
        model="custom:model",
        messages=[],
        tools=None,
        attempt=1,
        llm_call_index=1,
        source=ContextSource.HISTORY_ESTIMATE,
        freshness=ContextFreshness.STALE,
        context_window_resolver=lambda _model: None,
    )

    assert snapshot.estimated_tokens == 0
    assert snapshot.context_window is None
    assert snapshot.percentage is None


def test_snapshot_json_roundtrip_preserves_generated_contract() -> None:
    snapshot = build_context_snapshot(
        model="openai:gpt-5",
        messages=[Message.system("rules")],
        tools=None,
        attempt=1,
        llm_call_index=1,
        source=ContextSource.PREPARED_CONTEXT,
        freshness=ContextFreshness.LIVE,
        context_window_resolver=lambda _model: 1_000,
    )

    assert ContextSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_public_callable_contracts_accept_injected_functions() -> None:
    def estimator(text: str) -> int:
        return len(text.split())

    def lister(provider: str) -> list[ModelInfo]:
        return [ModelInfo(id="model", provider_id=provider, context_window=123)]

    assert estimate_text_tokens("one two") >= 0
    assert estimate_message_tokens([Message.user("one two")], estimator) == 7
    assert resolve_context_window("provider:model", lister) == 123


def test_estimate_message_tokens_tolerates_objects_without_reasoning() -> None:
    """Regression: storage-layer Message dataclasses (src.storage.messages.Message)
    lack the SDK Message's `reasoning` attribute. estimate_message_tokens must
    not crash when mixed/historic message objects are passed in — it should
    treat a missing `reasoning` as None.
    """
    from dataclasses import dataclass
    from datetime import UTC, datetime

    @dataclass
    class StorageMessage:
        id: str
        ts: datetime
        role: str
        content: str
        metadata: dict | None = None
        session_id: str = ""
        source: str | None = None

    historic = StorageMessage(
        id="m1", ts=datetime.now(UTC), role="user", content="hello"
    )
    sdk_msg = Message.assistant(content="hi there", reasoning="thinking")

    # Mixing storage + SDK messages must not raise AttributeError.
    total = estimate_message_tokens([historic, sdk_msg], _length)
    assert total > 0
    # The SDK message's reasoning contributes tokens; the storage one is treated as no reasoning.
    only_sdk = estimate_message_tokens([sdk_msg], _length)
    only_storage = estimate_message_tokens([historic], _length)
    assert total == only_sdk + only_storage


# --- Audit P3: token-count measurement reuse ---------------------------------

from src.sdk.middleware_summarization import (
    SummarizationMiddleware,
    count_tokens_approximately,
)


def test_token_based_cutoff_matches_brute_force_reference() -> None:
    """Suffix-sum cutoff must equal a brute-force earliest-index reference."""
    mw = SummarizationMiddleware(model="mock:test", keep=("tokens", 60))
    messages = [
        Message.system("s" * 100),
        Message.user("u" * 100),
        Message.assistant("a" * 100),
        Message.user("b" * 100),
    ]
    counts = [count_tokens_approximately([m]) for m in messages]

    def reference() -> int:
        for i in range(len(messages)):
            if sum(counts[i:]) <= 60:
                return i
        return len(messages)

    cutoff = mw._find_token_based_cutoff(messages)
    # _find_safe_cutoff_point must not move a non-tool cutoff.
    assert cutoff == reference()


def test_token_based_cutoff_counts_each_message_once() -> None:
    """The cutoff pass must count per-message (suffix sums), never slices."""
    calls: list[int] = []

    original = count_tokens_approximately
    mw = SummarizationMiddleware(model="mock:test", keep=("tokens", 60))

    def spy(messages):
        calls.append(len(messages))
        return original(messages)

    mw._partial_token_counter = spy  # type: ignore[assignment]
    messages = [
        Message.system("s" * 100),
        Message.user("u" * 100),
        Message.assistant("a" * 100),
        Message.user("b" * 100),
    ]
    mw._find_token_based_cutoff(messages)
    assert calls
    assert all(size == 1 for size in calls), f"expected per-message counts, got sizes {calls}"


def test_sync_before_model_is_noop_unless_debug_logging(monkeypatch) -> None:
    """Sync before_model must not tokenize unless debug logging is enabled."""
    import src.sdk.middleware_summarization as mw_module

    mw = SummarizationMiddleware(model="mock:test", keep=("tokens", 60))
    state = _state_with_many_messages()
    token_calls = []

    original = mw.token_counter
    mw.token_counter = lambda msgs: (token_calls.append(msgs), original(msgs))[1]  # type: ignore[assignment]

    # INFO level (default): no-op.
    monkeypatch.setattr(mw_module.logger, "_should_log", lambda level: level >= 20)
    assert mw.before_model(state) is None
    assert token_calls == []

    # DEBUG level: tokenized and trigger checked.
    monkeypatch.setattr(mw_module.logger, "_should_log", lambda level: level >= 10)
    assert mw.before_model(state) is None
    assert token_calls


def _state_with_many_messages():
    from src.sdk.state import AgentState

    return AgentState(messages=[Message.user("x" * 500)] * 20)
