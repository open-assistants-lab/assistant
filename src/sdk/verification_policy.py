"""Selective verification policy (C11).

A deterministic post-run decision: skip the grader for turns where it adds
least (no tools, short answer, no code, no risk keywords, short history).
Zero extra LLM calls — all signals are already in hand after the run.

Safety: explicit mode="on" always verifies; auto-mode never skips
destructive tools, code output, risk keywords, failed runs, long runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tools that always trigger verification when used in auto mode.
RISKY_TOOLS = frozenset(
    {
        "files_write",
        "files_edit",
        "files_delete",
        "files_rename",
        "shell_execute",
        "browser_eval",
        "browser_click",
        "browser_fill",
        "email_send",
        "app_delete",
        "app_delete_row",
        "app_column_delete",
        "subagent_delete",
        "web_fetch",
    }
)

# Fenced code blocks or inline code spans in the response.
_CODE_RE = re.compile(r"```|`[^`\n]+`")


@dataclass
class VerificationSignals:
    """Deterministic run signals collected after the agent run."""

    tool_names: list[str] = field(default_factory=list)
    destructive_tool_used: bool = False
    response_chars: int = 0
    history_tokens: int = 0
    has_code: bool = False
    risk_keyword_hit: bool = False
    run_failed: bool = False


@dataclass
class VerificationPolicy:
    """Thresholds + risk keywords (from VerificationConfig)."""

    mode: str = "off"
    skip_max_response_chars: int = 200
    verify_min_history_tokens: int = 4000
    verify_min_response_chars: int = 800
    risk_keywords: tuple[str, ...] = (
        "password",
        "api key",
        "secret",
        "credential",
        "token",
        "financial",
        "payment",
        "bank",
        "medical",
        "health",
        "delete",
        "remove file",
        "drop table",
        "sudo",
        "rm -rf",
    )


def detect_code(response: str) -> bool:
    """True if the response contains fenced or inline code."""
    return bool(_CODE_RE.search(response))


def detect_risk_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword appears case-insensitively in the text."""
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


def should_verify(signals: VerificationSignals, policy: VerificationPolicy) -> bool:
    """Decide whether the grader must run for this turn.

    Explicit "on" always verifies. Auto-mode skips ONLY turns that are
    simultaneously: tool-free, short-response, code-free, keyword-free,
    short-history, and not failed.
    """
    if policy.mode == "on":
        return True
    if policy.mode != "auto":
        return False  # "off" (default) never verifies unless requested

    if signals.run_failed:
        return True
    if signals.destructive_tool_used:
        return True
    if any(name in RISKY_TOOLS for name in signals.tool_names):
        return True
    if signals.has_code:
        return True
    if signals.risk_keyword_hit:
        return True
    if signals.history_tokens >= policy.verify_min_history_tokens:
        return True
    if signals.response_chars >= policy.verify_min_response_chars:
        return True
    if signals.tool_names:
        return True
    # The only skip path: auto mode + everything above clean + short response.
    return signals.response_chars >= policy.skip_max_response_chars


def skip_reason(signals: VerificationSignals, policy: VerificationPolicy) -> str | None:
    """Human-readable reason when the turn WOULD verify (for the audit log)."""
    if policy.mode == "on":
        return None
    if policy.mode != "auto":
        return None
    reasons: list[str] = []
    if signals.run_failed:
        reasons.append("run_failed")
    if signals.destructive_tool_used:
        reasons.append("destructive_tool")
    risky = [n for n in signals.tool_names if n in RISKY_TOOLS]
    if risky:
        reasons.append(f"risky_tool:{risky[0]}")
    if signals.has_code:
        reasons.append("code_output")
    if signals.risk_keyword_hit:
        reasons.append("risk_keyword")
    if signals.history_tokens >= policy.verify_min_history_tokens:
        reasons.append("long_history")
    if signals.response_chars >= policy.verify_min_response_chars:
        reasons.append("long_response")
    if signals.tool_names:
        reasons.append(f"tools:{','.join(signals.tool_names[:3])}")
    return ", ".join(reasons) if reasons else None
