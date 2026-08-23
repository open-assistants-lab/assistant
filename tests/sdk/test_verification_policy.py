"""Selective verification policy tests (C11 decision matrix)."""

from __future__ import annotations

from src.sdk.verification_policy import (
    VerificationPolicy,
    VerificationSignals,
    detect_code,
    detect_risk_keywords,
    should_verify,
    skip_reason,
)


def _policy(mode: str = "auto", **kw) -> VerificationPolicy:
    defaults = dict(
        mode=mode,
        skip_max_response_chars=200,
        verify_min_history_tokens=4000,
        verify_min_response_chars=800,
    )
    defaults.update(kw)
    return VerificationPolicy(**defaults)


def _sig(**kw) -> VerificationSignals:
    defaults = dict(
        tool_names=[],
        destructive_tool_used=False,
        response_chars=50,
        history_tokens=500,
        has_code=False,
        risk_keyword_hit=False,
        run_failed=False,
    )
    defaults.update(kw)
    return VerificationSignals(**defaults)


def _trivial(**kw) -> VerificationSignals:
    return _sig(**kw)


def _risky(**kw) -> VerificationSignals:
    return _sig(destructive_tool_used=True, **kw)


class TestShouldVerify:
    def test_mode_on_always_verifies(self):
        assert should_verify(_trivial(), _policy("on")) is True

    def test_mode_off_never_verifies(self):
        assert should_verify(_trivial(), _policy("off")) is False
        assert should_verify(_risky(), _policy("off")) is False

    def test_auto_trivial_turn_is_skipped(self):
        assert should_verify(_trivial(), _policy("auto")) is False

    def test_auto_short_answer_with_code_verifies(self):
        assert should_verify(_trivial(has_code=True), _policy("auto")) is True

    def test_auto_destructive_tool_verifies(self):
        assert should_verify(_trivial(destructive_tool_used=True), _policy("auto")) is True

    def test_auto_risky_tool_verifies(self):
        assert should_verify(_trivial(tool_names=["files_write"]), _policy("auto")) is True

    def test_auto_risk_keyword_verifies(self):
        assert should_verify(_trivial(risk_keyword_hit=True), _policy("auto")) is True

    def test_auto_long_history_verifies(self):
        assert should_verify(_trivial(history_tokens=5000), _policy("auto")) is True

    def test_auto_long_response_verifies(self):
        assert should_verify(_trivial(response_chars=900), _policy("auto")) is True

    def test_auto_failed_run_verifies(self):
        assert should_verify(_trivial(run_failed=True), _policy("auto")) is True

    def test_auto_any_tool_verifies(self):
        assert should_verify(_trivial(tool_names=["time_get"]), _policy("auto")) is True

    def test_auto_long_response_but_empty_tools_and_no_code_still_skips_when_under_threshold(self):
        assert should_verify(_trivial(response_chars=199), _policy("auto")) is False

    def test_skip_reason_reports_the_failing_criterion(self):
        reason = skip_reason(_trivial(has_code=True), _policy("auto"))
        assert reason is not None
        assert "code_output" in reason
        assert skip_reason(_trivial(), _policy("auto")) is None


class TestDetectors:
    def test_detect_code_fences(self):
        assert detect_code("Here:\n```python\nx = 1\n```")
        assert detect_code("use `files_read` first")

    def test_detect_code_no_false_positive(self):
        assert not detect_code("plain text without code")

    def test_detect_risk_keywords(self):
        assert detect_risk_keywords("reset my password", _policy().risk_keywords)
        assert detect_risk_keywords("DELETE FROM users", ("drop table", "delete"))
        assert not detect_risk_keywords("hello world", _policy().risk_keywords)
