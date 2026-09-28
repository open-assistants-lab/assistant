"""Token usage must survive every Ollama payload shape (#48).

Reported symptom: every SSE `usage` event carried zeros, `/usage/summary` was
empty, and `cost_limit_usd` was unreachable. Two provider defects produced
exactly that:

* a `usage` object that is *present but zeroed* took precedence over the
  authoritative native `prompt_eval_count`/`eval_count`, because
  ``dict.get(key, fallback)`` only falls back when the key is **absent**; and
* `usage` in OpenAI `input_tokens`/`output_tokens` shape missed both known keys
  and produced zeros with no fallback at all.

Every emitted usage event must therefore report the best value actually
present, and an all-zero payload must not emit a misleading event.
"""

from __future__ import annotations

from typing import Any

import pytest


def _provider() -> Any:
    from src.sdk.providers.ollama import OllamaCloud

    return OllamaCloud.__new__(OllamaCloud)


def _stream_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    chunks = _provider()._parse_chunk(payload, {}, {}, set())
    usage = [c for c in chunks if c.canonical_type == "usage"]
    if not usage:
        return None
    return usage[0].usage.input_tokens, usage[0].usage.output_tokens


def _nonstream_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    message = _provider()._parse_response(payload)
    if message.usage is None:
        return None
    return message.usage.input_tokens, message.usage.output_tokens


# --------------------------------------------------------------------------
# streaming
# --------------------------------------------------------------------------


def test_native_counts_are_used_when_no_usage_object_is_present() -> None:
    assert _stream_usage(
        {"done": True, "content": "x", "prompt_eval_count": 123, "eval_count": 45}
    ) == (123, 45)


def test_openai_shaped_usage_is_understood() -> None:
    """input_tokens/output_tokens previously fell through to zero."""
    assert _stream_usage(
        {"done": True, "content": "x", "usage": {"input_tokens": 99, "output_tokens": 7}}
    ) == (99, 7)


def test_prompt_completion_shaped_usage_is_understood() -> None:
    assert _stream_usage(
        {"done": True, "content": "x", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    ) == (10, 5)


def test_zeroed_usage_object_does_not_hide_the_native_counts() -> None:
    """The reported bug: a present-but-zeroed usage suppressed the real numbers."""
    assert _stream_usage(
        {
            "done": True,
            "content": "x",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "prompt_eval_count": 123,
            "eval_count": 45,
        }
    ) == (123, 45)


def test_partial_usage_falls_back_per_field() -> None:
    assert _stream_usage(
        {
            "done": True,
            "content": "x",
            "usage": {"prompt_tokens": 0, "completion_tokens": 5},
            "prompt_eval_count": 123,
        }
    ) == (123, 5)


def test_all_zero_payload_emits_no_usage_event() -> None:
    """An all-zero event is worse than none: it reads as measured-and-free."""
    assert (
        _stream_usage(
            {"done": True, "content": "x", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
        )
        is None
    )


# --------------------------------------------------------------------------
# non-streaming has the same defect shape
# --------------------------------------------------------------------------


def test_nonstream_zeroed_usage_falls_back_to_native_counts() -> None:
    assert _nonstream_usage(
        {
            "message": {"content": "x"},
            "done": True,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "prompt_eval_count": 321,
            "eval_count": 21,
        }
    ) == (321, 21)


def test_nonstream_openai_shape_is_understood() -> None:
    assert _nonstream_usage(
        {
            "message": {"content": "x"},
            "done": True,
            "usage": {"input_tokens": 50, "output_tokens": 4},
        }
    ) == (50, 4)


def test_nonstream_native_counts_still_work() -> None:
    assert _nonstream_usage(
        {"message": {"content": "x"}, "done": True, "prompt_eval_count": 9, "eval_count": 3}
    ) == (9, 3)


def test_reasoning_estimate_is_preserved_with_fallback_counts() -> None:
    message = _provider()._parse_response(
        {
            "message": {"content": "x", "thinking": "deep thoughts"},
            "done": True,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "prompt_eval_count": 40,
            "eval_count": 8,
        }
    )
    assert message.usage is not None
    assert message.usage.input_tokens == 40
    assert message.usage.reasoning_tokens == len("deep thoughts") // 4


# --------------------------------------------------------------------------
# the contract that makes cost_limit_usd enforceable
# --------------------------------------------------------------------------


def test_reported_tokens_drive_cost_accumulation() -> None:
    """A limit over an unmeasured quantity is decorative; usage must be real.

    `cost_limit_usd` is enforced against CostTracker's running total, which is
    fed only by provider-reported tokens. This is the chain the zeroed usage
    events were breaking.
    """
    from src.sdk.loop import CostTracker
    from src.sdk.providers.base import ModelCost

    tracker = CostTracker()
    tracker.add_usage(
        input_tokens=1_000_000,
        output_tokens=0,
        cost=ModelCost(input=1.0, output=2.0),
    )
    tracker.add_usage(
        input_tokens=0,
        output_tokens=1_000_000,
        cost=ModelCost(input=1.0, output=2.0),
    )

    assert tracker.total_input_tokens == 1_000_000
    assert tracker.total_output_tokens == 1_000_000
    assert tracker.total_cost_usd == pytest.approx(3.0)

    from src.sdk.loop import RunConfig

    assert "cost_limit_usd" in (tracker.exceeds_limits(RunConfig(cost_limit_usd=1.0)) or "")
