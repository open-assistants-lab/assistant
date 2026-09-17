"""Read saved custom-tool output without file access or command execution."""

import json

from src.sdk.tool_results import read_result
from src.sdk.tools import ToolAnnotations, ToolResult, tool
from src.storage.paths import DEFAULT_USER_ID


@tool
def tool_result_read(
    result_id: str,
    offset: int = 0,
    limit: int = 5000,
    user_id: str = DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> ToolResult:
    """Read saved tool output by result ID; offset/limit are characters (max 5000).

    Continue at the returned end while has_more is true. Never reruns a command.
    Results are scoped to the current user/workspace and expire after seven days
    or eviction. Does not require files_read.
    """
    page = read_result(result_id, offset, limit, user_id, workspace_id)
    return ToolResult(
        content=json.dumps(page, ensure_ascii=False),
        structured_content=page,
        is_error="error" in page,
    )


tool_result_read.annotations = ToolAnnotations(
    title="Read Tool Result", read_only=True, destructive=False, open_world=False,
)
