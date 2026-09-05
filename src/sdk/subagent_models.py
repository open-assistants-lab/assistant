"""Subagent models — SubagentResult, task status types, and error classes.

V1 subagent architecture: delegation-and-return with SQLite work_queue coordination.

Agent definition is now handled by the OSS `agentprofile` package (AgentProfile).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

DEFAULT_DISALLOWED_TOOLS = [
    "subagent_create",
    "subagent_update",
    "subagent_delete",
    "subagent_list",
    "subagent_start",
    "subagent_check",
    "subagent_tasks",
    "subagent_instruct",
    "subagent_cancel",
    "research_start",
    "research_list",
]

DEFAULT_SAFE_DENIED_TOOLS = [
    "shell_execute",
    "browser_click",
    "browser_fill",
    "browser_eval",
    "browser_open",
]

SAFE_DISALLOWED_TOOLS = list(DEFAULT_DISALLOWED_TOOLS) + list(DEFAULT_SAFE_DENIED_TOOLS)

DEFAULT_MAX_LLM_CALLS = 50
DEFAULT_COST_LIMIT_USD = 1.0
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_OUTPUT_CHARS = 10000


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubagentResult(BaseModel):
    """Structured result from a subagent invocation."""

    name: str
    task: str
    success: bool
    output: str
    truncated: bool = False
    cost_usd: float = 0.0
    llm_calls: int = 0
    error: str | None = None
    structured_output: dict | list | str | int | float | bool | None = None


class TaskCancelledError(Exception):
    """Raised when a task is cancelled by the supervisor."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"Task {task_id} cancelled by supervisor")


class MaxCallsExceededError(Exception):
    """Raised when max_llm_calls is exceeded."""

    def __init__(self, task_id: str, limit: int):
        self.task_id = task_id
        self.limit = limit
        super().__init__(f"Task {task_id}: exceeded max_llm_calls ({limit})")


class CostLimitExceededError(Exception):
    """Raised when cost_limit_usd is exceeded."""

    def __init__(self, task_id: str, limit: float):
        self.task_id = task_id
        self.limit = limit
        super().__init__(f"Task {task_id}: exceeded cost_limit_usd (${limit})")
