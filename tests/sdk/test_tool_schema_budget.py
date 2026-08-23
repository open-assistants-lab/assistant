"""Tool schema budget guard.

Prevents the always-on tool schema cost from creeping back up. The browser
family was reduced from 20 tools (~934 tokens) to 6 core tools (~300 tokens)
in the browser-tools migration; the long-tail lives in the web-automation
skill via the agent-browser CLI.
"""

from __future__ import annotations

import json

from src.sdk.native_tools import get_native_tools

# 6 core tools measured at 1,209 chars ≈ 302 tokens (2026-08-20).
# Budget: 1,500 chars ≈ 375 tokens — headroom for docstring tweaks.
BROWSER_SCHEMA_BUDGET_CHARS = 1500
BROWSER_TOOL_COUNT = 6


def _browser_tools():
    return [td for td in get_native_tools() if td.name.startswith("browser")]


def test_browser_tool_count_is_core_only():
    names = sorted(td.name for td in _browser_tools())
    assert names == [
        "browser_click",
        "browser_eval",
        "browser_fill",
        "browser_open",
        "browser_screenshot",
        "browser_snapshot",
    ]


def test_browser_schema_within_budget():
    total = sum(len(json.dumps(td.parameters, default=str)) for td in _browser_tools())
    assert total <= BROWSER_SCHEMA_BUDGET_CHARS, (
        f"browser schema grew to {total} chars "
        f"(budget {BROWSER_SCHEMA_BUDGET_CHARS}) — long-tail tools belong in the "
        f"web-automation skill, not the native registry"
    )


def test_removed_long_tail_tools_not_registered():
    removed = {
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_hover",
        "browser_get_title",
        "browser_get_text",
        "browser_get_html",
        "browser_get_url",
        "browser_tab_new",
        "browser_tab_close",
        "browser_back",
        "browser_forward",
        "browser_wait_text",
        "browser_sessions",
        "browser_close_all",
        "browser_status",
    }
    registered = {td.name for td in _browser_tools()}
    assert not (removed & registered)
