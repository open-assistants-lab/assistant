"""Interview tools must be registered in the native tool registry so they
reach the runtime (review P1: tools existed but were unreachable)."""

from src.sdk.native_tools import _registry


def test_interview_tools_registered():
    names = {t.name for t in _registry.list_tools()}
    assert {"interview_start", "interview_ask", "interview_finish"} <= names


def test_interview_tool_category():
    from src.sdk.native_tools import get_tool_category

    # category_verb convention: interview_* derives to "interview"
    assert get_tool_category("interview_start") == "interview"
