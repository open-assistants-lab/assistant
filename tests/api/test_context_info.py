"""Contract tests for the fallback history-based context-info endpoint."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from src.http.routers import conversation as conversation_router
from src.sdk.runner import _messages_from_conversation


@dataclass
class StoredMessage:
    role: str
    content: str
    metadata: dict[str, Any] | None = None
    session_id: str = "session-a"
    id: str = "message-1"
    ts: Any = None


class FakeMessageStore:
    def __init__(self, by_session: dict[str, list[StoredMessage]] | None = None) -> None:
        self.by_session = by_session or {}
        self.summary_calls: list[tuple[str, int]] = []
        self.raw_calls: list[tuple[str, int]] = []

    def get_messages_with_summary(self, *, session_id: str, limit: int) -> list[StoredMessage]:
        self.summary_calls.append((session_id, limit))
        return self.by_session.get(session_id, [])

    def get_messages_by_session_id(self, session_id: str, limit: int) -> list[StoredMessage]:
        self.raw_calls.append((session_id, limit))
        raise AssertionError("context-info must not load raw history")


class SummarizationConfig:
    def __init__(self, trigger: object = ("tokens", 4000), enabled: bool = True) -> None:
        self._trigger = trigger
        self.enabled = enabled

    def get_trigger(self) -> object:
        return self._trigger


def _host_settings(
    model: str = "host-model",
    *,
    trigger: object = ("tokens", 4000),
    enabled: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(model=model),
        memory=SimpleNamespace(
            summarization=SummarizationConfig(trigger=trigger, enabled=enabled)
        ),
    )


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    store: FakeMessageStore | None = None,
    saved_model: str | None = None,
    host_model: str = "host-model",
    trigger: object = ("tokens", 4000),
    enabled: bool = True,
    store_error: Exception | None = None,
    context_window: int | None = 1000,
) -> FakeMessageStore:
    message_store = store or FakeMessageStore()

    class FakeSettingsStore:
        def __init__(self, user_id: str) -> None:
            assert user_id

        def load(self) -> SimpleNamespace:
            if store_error is not None:
                raise store_error
            return SimpleNamespace(default_model=saved_model)

    monkeypatch.setattr(conversation_router, "get_message_store", lambda user_id: message_store)
    monkeypatch.setattr(
        conversation_router,
        "get_settings",
        lambda: _host_settings(host_model, trigger=trigger, enabled=enabled),
    )
    monkeypatch.setattr(
        conversation_router._user_settings_store, "UserSettingsStore", FakeSettingsStore
    )
    monkeypatch.setattr(
        conversation_router._context_measurement,
        "resolve_context_window",
        lambda model: context_window,
    )
    return message_store


def test_explicit_model_takes_precedence_over_saved_and_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(
        monkeypatch,
        saved_model="anthropic:claude-saved",
        host_model="openai/host-model",
    )

    result = conversation_router.get_context_info(model="openai:gpt-explicit")

    assert result["model"] == "openai:gpt-explicit"


def test_saved_canonical_model_takes_precedence_over_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, saved_model="anthropic:claude-saved", host_model="host-model")

    result = conversation_router.get_context_info()

    assert result["model"] == "anthropic:claude-saved"


def test_missing_saved_model_falls_back_to_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, saved_model=None, host_model="openai/host-model")

    result = conversation_router.get_context_info()

    assert result["model"] == "openai:host-model"


def test_corrupt_settings_store_falls_back_to_host(monkeypatch: pytest.MonkeyPatch) -> None:
    error = conversation_router._user_settings_store.SettingsConfigurationError("corrupt")
    _configure(monkeypatch, store_error=error, host_model="host-model")

    result = conversation_router.get_context_info()

    assert result["model"] == "ollama:host-model"


@pytest.mark.parametrize(
    ("host_model", "expected"),
    [
        ("llama3.2", "ollama:llama3.2"),
        ("openai/gpt-4.1", "openai:gpt-4.1"),
        ("openrouter/openai/gpt-4.1", "openrouter:openai/gpt-4.1"),
    ],
)
def test_legacy_host_model_normalization(
    monkeypatch: pytest.MonkeyPatch, host_model: str, expected: str
) -> None:
    _configure(monkeypatch, host_model=host_model)

    assert conversation_router.get_context_info()["model"] == expected


def test_invalid_explicit_model_returns_controlled_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        conversation_router.get_context_info(model="../../private/settings.json")

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid model identifier"
    assert "settings" not in str(exc_info.value.detail)


def test_exact_context_window_uses_shared_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    _configure(monkeypatch, context_window=8192)
    monkeypatch.setattr(
        conversation_router._context_measurement,
        "resolve_context_window",
        lambda model: seen.append(model) or 8192,
    )

    result = conversation_router.get_context_info(model="openai:gpt-4.1")

    assert seen == ["openai:gpt-4.1"]
    assert result["context_window"] == 8192


def test_unknown_context_window_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, context_window=None)

    result = conversation_router.get_context_info()

    assert result["context_window"] is None
    assert result["context_percentage"] is None


def test_history_load_is_summary_aware_and_session_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeMessageStore(
        {
            "session-a": [StoredMessage("user", "A retained", session_id="session-a")],
            "session-b": [StoredMessage("summary", "B sentinel", session_id="session-b")],
        }
    )
    _configure(monkeypatch, store=store)

    result = conversation_router.get_context_info(session_id="session-a")

    assert store.summary_calls == [("session-a", 100)]
    assert store.raw_calls == []
    assert result["current_tokens"] == conversation_router._context_measurement.estimate_message_tokens(
        _messages_from_conversation(store.by_session["session-a"])
    )


def test_token_estimate_includes_summary_and_retained_history(monkeypatch: pytest.MonkeyPatch) -> None:
    history = [
        StoredMessage("summary", "Earlier facts"),
        StoredMessage("user", "Retained question", id="message-2"),
    ]
    _configure(monkeypatch, store=FakeMessageStore({"session-a": history}))

    result = conversation_router.get_context_info(session_id="session-a")

    converted = _messages_from_conversation(history)
    assert len(converted) == 2
    assert "Earlier facts" in str(converted[0].content)
    assert result["current_tokens"] == conversation_router._context_measurement.estimate_message_tokens(
        converted
    )


def test_response_metadata_and_exact_percentage_can_exceed_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = [StoredMessage("user", "x" * 100)]
    _configure(
        monkeypatch,
        store=FakeMessageStore({"session-a": history}),
        context_window=10,
    )

    result = conversation_router.get_context_info(session_id="session-a")

    assert set(result) == {
        "model",
        "context_window",
        "current_tokens",
        "summarization_threshold",
        "summarization_enabled",
        "context_percentage",
        "source",
        "freshness",
        "estimated",
    }
    assert result["source"] == "history_estimate"
    assert result["freshness"] == "stale"
    assert result["estimated"] is True
    assert result["context_percentage"] == result["current_tokens"] / 10 * 100
    assert result["context_percentage"] > 100


def test_token_trigger_sets_summarization_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, trigger=("tokens", 777), enabled=True)

    result = conversation_router.get_context_info()

    assert result["summarization_threshold"] == 777
    assert result["summarization_enabled"] is True


def test_non_token_trigger_has_no_summarization_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, trigger=("messages", 20), enabled=False)

    result = conversation_router.get_context_info()

    assert result["summarization_threshold"] is None
    assert result["summarization_enabled"] is False


def test_context_info_handler_is_synchronous() -> None:
    assert not inspect.iscoroutinefunction(conversation_router.get_context_info)


def test_context_info_does_not_run_agent_compression_or_public_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(
        conversation_router.RunService,
        "execute",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("agent run called")),
    )
    monkeypatch.setattr(
        conversation_router._context_measurement,
        "build_context_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("snapshot sink called")),
    )

    result = conversation_router.get_context_info()

    assert result["source"] == "history_estimate"


def test_empty_session_id_defaults_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _configure(monkeypatch)

    conversation_router.get_context_info(session_id="")

    assert store.summary_calls == [("default", 100)]
