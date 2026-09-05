"""Subagent tools — SDK-native implementation with async work_queue jobs.

Management:
    subagent_create   — create agent profile, persist to disk
    subagent_update   — amend existing profile (partial update)
    subagent_delete   — remove profile + cancel any running tasks
    subagent_list     — list user's profiles + active tasks

Runtime:
    subagent_start    — start a subagent job and return immediately with job ID
    subagent_check    — check one job status/result
    subagent_tasks    — list active/recent jobs
    subagent_instruct — course-correct a running subagent
    subagent_cancel   — kill a running subagent
"""

from __future__ import annotations

import json
from typing import Any

from agentprofile.models import AgentProfile

from src.app_logging import get_logger
from src.sdk.agent_validation import validate_agent_def
from src.sdk.coordinator import get_coordinator
from src.sdk.subagent_models import TaskStatus
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

def _parse_object_json(value: str | None, field_name: str) -> tuple[dict[str, Any] | None, str | None]:
    if value is None:
        return None, None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        return None, f"Error: Invalid {field_name} JSON: {e}"
    if not isinstance(parsed, dict):
        return None, f"Error: {field_name} must be a JSON object."
    return parsed, None


def _format_task(row: dict[str, Any], task_id: str) -> str:
    progress = json.loads(row.get("progress") or "{}")
    status = row.get("status", "unknown")
    agent_name = row.get("agent_name", "unknown")

    lines = [f"## Task: {task_id}", f"**Agent:** {agent_name}", f"**Status:** {status}"]

    if progress:
        steps = progress.get("steps_completed", 0)
        phase = progress.get("phase", "")
        msg = progress.get("message", "")
        stuck = progress.get("stuck", False)
        lines.append(f"**Steps completed:** {steps}")
        if phase:
            lines.append(f"**Phase:** {phase}")
        if msg:
            lines.append(f"**Last action:** {msg}")
        if stuck:
            lines.append("**STUCK** - doom loop detected")

    if status == "completed":
        result_data = json.loads(row.get("result") or "{}")
        output = result_data.get("output", "")
        truncated = result_data.get("truncated", False)
        lines.append(f"\n**Output:**\n{output[:500]}{'...' if len(output) > 500 else ''}")
        if truncated:
            lines.append("(output truncated)")
    elif status == "failed":
        lines.append(f"\n**Error:** {row.get('error', 'Unknown')}")
    elif status == "cancelled":
        lines.append("\nTask was cancelled by supervisor.")

    return "\n".join(lines)


@tool
async def subagent_create(
    name: str,
    user_id: str,
    workspace_id: str = "personal",
    description: str = "",
    model: str | None = None,
    tools: list[str] | None = None,
    system_prompt: str | None = None,
    skills: list[str] | None = None,
    max_llm_calls: int = 50,
    cost_limit_usd: float = 1.0,
    timeout_seconds: int = 300,
    provider_options: str | None = None,
    output_schema: str | None = None,
    handoff_instructions: str | None = None,
) -> str:
    """Create a new subagent with specified configuration.

    Args:
        name: Unique name for the subagent (alphanumeric, hyphens, underscores)
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        description: What this subagent does (shown to LLM for routing)
        model: Model to use (e.g., 'anthropic:claude-sonnet-4-20250514')
        tools: List of tool names to allow (None = all native tools)
        system_prompt: Custom system prompt
        skills: List of skill names to inject
        max_llm_calls: Per-task LLM call limit (default 50)
        cost_limit_usd: Per-task cost limit in USD (default 1.0)
        timeout_seconds: Per-task hard wall-clock timeout (default 300)
        provider_options: Provider-specific options as JSON object
        output_schema: Expected output schema as JSON object
        handoff_instructions: Instructions for returning work to supervisor

    Returns:
        Success message or validation errors
    """
    provider_options_dict, error = _parse_object_json(provider_options, "provider_options")
    if error:
        return error
    output_schema_dict, error = _parse_object_json(output_schema, "output_schema")
    if error:
        return error

    if skills is None:
        skills = []

    agent_profile = AgentProfile(
        name=name,
        description=description,
        model=model or "",
        system_prompt=system_prompt or "",
        tools=tools or [],
        skills=skills,
        max_llm_calls=max_llm_calls,
        cost_limit_usd=cost_limit_usd,
        timeout_seconds=timeout_seconds,
        provider_options=provider_options_dict or {},
        output_schema_def=output_schema_dict,
        handoff_instructions=handoff_instructions,
    )

    errors = validate_agent_def(agent_profile, user_id=user_id, workspace_id=workspace_id)
    if errors:
        return "Error: " + "; ".join(errors)

    coordinator = get_coordinator(user_id, workspace_id)

    existing = coordinator.load_def(name)
    if existing is not None:
        return f"Error: Subagent '{name}' already exists. Use subagent_update to amend it."

    await coordinator.create(agent_profile)

    lines = [f"Subagent '{name}' created successfully."]
    if model:
        lines.append(f"Model: {model}")
    if tools:
        lines.append(f"Tools: {', '.join(tools)}")
    lines.append(f"Max LLM calls: {max_llm_calls}, Cost limit: ${cost_limit_usd}")

    return "\n".join(lines)


subagent_create.annotations = ToolAnnotations(title="Create Subagent", destructive=True)


@tool
async def subagent_update(
    name: str,
    user_id: str,
    workspace_id: str = "personal",
    model: str | None = None,
    description: str | None = None,
    tools: list[str] | None = None,
    system_prompt: str | None = None,
    skills: list[str] | None = None,
    max_llm_calls: int | None = None,
    cost_limit_usd: float | None = None,
    timeout_seconds: int | None = None,
    provider_options: str | None = None,
    output_schema: str | None = None,
    handoff_instructions: str | None = None,
) -> str:
    """Amend an existing subagent. Only specified fields are updated.

    Args:
        name: Subagent name to update
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        model: New model override
        description: New description
        tools: New tool allowlist (replaces entirely)
        system_prompt: New system prompt
        skills: New skills list (replaces entirely)
        max_llm_calls: New LLM call limit
        cost_limit_usd: New cost limit
        timeout_seconds: New timeout
        provider_options: New provider options as JSON object
        output_schema: New output schema as JSON object
        handoff_instructions: New handoff instructions

    Returns:
        Confirmation message
    """
    coordinator = get_coordinator(user_id, workspace_id)
    existing = coordinator.load_def(name)
    if existing is None:
        return f"Error: Subagent '{name}' not found."

    update_kwargs: dict[str, Any] = {}
    if model is not None:
        update_kwargs["model"] = model
    if description is not None:
        update_kwargs["description"] = description
    if tools is not None:
        update_kwargs["tools"] = tools
    if system_prompt is not None:
        update_kwargs["system_prompt"] = system_prompt
    if skills is not None:
        update_kwargs["skills"] = skills
    if max_llm_calls is not None:
        update_kwargs["max_llm_calls"] = max_llm_calls
    if cost_limit_usd is not None:
        update_kwargs["cost_limit_usd"] = cost_limit_usd
    if timeout_seconds is not None:
        update_kwargs["timeout_seconds"] = timeout_seconds
    if provider_options is not None:
        parsed, error = _parse_object_json(provider_options, "provider_options")
        if error:
            return error
        update_kwargs["provider_options"] = parsed or {}
    if output_schema is not None:
        parsed, error = _parse_object_json(output_schema, "output_schema")
        if error:
            return error
        update_kwargs["output_schema_def"] = parsed
    if handoff_instructions is not None:
        update_kwargs["handoff_instructions"] = handoff_instructions

    if not update_kwargs:
        return f"No fields specified to update for subagent '{name}'."

    updated_def = existing.model_copy(update=update_kwargs)
    errors = validate_agent_def(updated_def, user_id=user_id, workspace_id=workspace_id)
    if errors:
        return "Error: " + "; ".join(errors)

    updated = await coordinator.update(name, **update_kwargs)

    if updated is None:
        return f"Error: Failed to update subagent '{name}'."

    return f"Subagent '{name}' updated: {', '.join(update_kwargs.keys())}"


subagent_update.annotations = ToolAnnotations(title="Update Subagent", destructive=True)


@tool
async def subagent_start(
    agent_name: str,
    task: str,
    user_id: str,
    workspace_id: str = "personal",
    parent_id: str | None = None,
) -> str:
    """Start a subagent to execute a task. Returns job ID immediately.

    The subagent runs in the background. Use subagent_check to check status.
    Use subagent_instruct to send course-corrections.
    Use subagent_cancel to kill a stuck or misbehaving subagent.

    Args:
        agent_name: Name of the subagent to start
        task: Task description/prompt
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        parent_id: Correlation ID to group related tasks

    Returns:
        Job ID and status message
    """
    coordinator = get_coordinator(user_id, workspace_id)

    existing = coordinator.load_def(agent_name)
    if existing is None:
        return f"Error: Subagent '{agent_name}' not found. Create it first with subagent_create."

    task_id_str = await coordinator.start(agent_name, task, parent_id=parent_id)

    return f"""Subagent job started for '{agent_name}'.

**Job ID**: {task_id_str}
**Task**: {task[:100]}
**Status**: Running in background...

Use `subagent_check` with job ID to check status and results."""


subagent_start.annotations = ToolAnnotations(title="Start Subagent", open_world=True)


@tool
async def subagent_delegate(
    agent_name: str,
    task: str,
    user_id: str,
    workspace_id: str = "personal",
    parent_id: str | None = None,
    timeout_seconds: int = 120,
) -> str:
    """Run a subagent and wait for the result. Returns the subagent's output.

    Unlike subagent_start which fires and forgets, this tool blocks until
    the subagent completes and returns the result inline. Use this when
    you need the subagent's output to continue your work.

    Multiple subagent_delegate calls in the same turn run in parallel.

    For long-running tasks (>2 min), use subagent_start instead so the
    user can continue chatting while the subagent works.

    Args:
        agent_name: Name of the subagent to run
        task: Task description/prompt for the subagent
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        parent_id: Correlation ID to group related tasks
        timeout_seconds: Maximum seconds to wait (default 120)

    Returns:
        The subagent's output text
    """
    coordinator = get_coordinator(user_id, workspace_id)

    existing = coordinator.load_def(agent_name)
    if existing is None:
        return f"Error: Subagent '{agent_name}' not found. Create it first with subagent_create."

    try:
        result = await coordinator.delegate(
            agent_name, task, parent_id=parent_id,
            timeout_seconds=timeout_seconds,
        )
        return result
    except Exception as e:
        return f"Error running '{agent_name}': {type(e).__name__}: {e}"


subagent_delegate.annotations = ToolAnnotations(
    title="Run Subagent (wait for result)",
    destructive=True,
    open_world=True,
)


@tool
async def subagent_list(user_id: str, workspace_id: str = "personal") -> str:
    """List all subagents for the user and their active tasks.

    Args:
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        List of subagents with their configs and active tasks
    """
    coordinator = get_coordinator(user_id, workspace_id)

    defs = await coordinator.list_defs()
    tasks = await coordinator.check_progress()

    if not defs and not tasks:
        return "No subagents found."

    lines = ["## Subagents\n"]
    for d in defs:
        lines.append(f"### {d.name}")
        if d.description:
            lines.append(f"  {d.description}")
        if d.model:
            lines.append(f"  **Model:** {d.model}")
        if d.tools:
            lines.append(f"  **Tools:** {', '.join(d.tools)}")
        lines.append(f"  **Max LLM calls:** {d.max_llm_calls}, **Cost limit:** ${d.cost_limit_usd}")
        lines.append("")

    if tasks:
        lines.append("## Active Tasks\n")
        for t in tasks:
            status = t.get("status", "unknown")
            name = t.get("agent_name", "unknown")
            task_id = t.get("id", "?")
            lines.append(f"- **{task_id}** ({name}): {status}")

    return "\n".join(lines)


subagent_list.annotations = ToolAnnotations(title="List Subagents", read_only=True, idempotent=True)


@tool
async def subagent_check(
    task_id: str,
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Check progress/status of one subagent job.

    Args:
        task_id: Specific task/job ID to check
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Task status and progress information
    """
    coordinator = get_coordinator(user_id, workspace_id)

    async def _get_task() -> dict[str, Any] | None:
        db = await coordinator._get_db()
        return await db.get_task(task_id)

    row = await _get_task()
    if row is None:
        return f"No task found with ID: {task_id}"

    return _format_task(row, task_id)


subagent_check.annotations = ToolAnnotations(title="Subagent Check", read_only=True, idempotent=True)


@tool
async def subagent_tasks(
    user_id: str =  DEFAULT_USER_ID,
    workspace_id: str = "personal",
    status: str | None = None,
) -> str:
    """List active/recent subagent jobs.

    Args:
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)
        status: Optional status filter: pending, running, cancelling, completed, failed, cancelled

    Returns:
        List of matching jobs
    """
    status_filter = None
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            valid = ", ".join(s.value for s in TaskStatus)
            return f"Error: Invalid status '{status}'. Valid statuses: {valid}."

    coordinator = get_coordinator(user_id, workspace_id)

    async def _get_tasks() -> list[dict[str, Any]]:
        db = await coordinator._get_db()
        return await db.check_progress(status=status_filter)

    tasks = await _get_tasks()
    if not tasks:
        return "No tasks found." if status_filter else "No active tasks."

    lines = ["## Tasks\n"]
    for t in tasks:
        t_status = t.get("status", "unknown")
        t_name = t.get("agent_name", "unknown")
        tid = t.get("id", "?")
        lines.append(f"- **{tid}** ({t_name}): {t_status}")

    return "\n".join(lines)


subagent_tasks.annotations = ToolAnnotations(title="Subagent Tasks", read_only=True, idempotent=True)


@tool
async def subagent_instruct(
    task_id: str,
    message: str,
    user_id: str,
    workspace_id: str = "personal",
) -> str:
    """Send a course-correction instruction to a running subagent.

    The instruction is injected as a system message on the subagent's next iteration.

    Args:
        task_id: The task ID to instruct
        message: The instruction message to inject
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Confirmation message
    """
    coordinator = get_coordinator(user_id, workspace_id)

    async def _instruct() -> bool | None:
        db = await coordinator._get_db()
        row = await db.get_task(task_id)
        if row is None:
            return None
        if row.get("status") not in ("pending", "running"):
            return None
        ok = await db.add_instruction(task_id, message)
        return ok

    result = await _instruct()

    if result is None:
        return f"Error: Task '{task_id}' not found or not running."
    if not result:
        return f"Error: Failed to send instruction to task '{task_id}'."

    return f"Instruction sent to task '{task_id}': {message[:100]}"


subagent_instruct.annotations = ToolAnnotations(title="Instruct Subagent")


@tool
async def subagent_cancel(task_id: str, user_id: str, workspace_id: str = "personal") -> str:
    """Cancel a running or pending subagent task.

    Sets cancel_requested flag. The subagent's SubagentContext will
    raise TaskCancelledError on its next iteration.

    Args:
        task_id: The task ID to cancel
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Cancellation confirmation
    """
    coordinator = get_coordinator(user_id, workspace_id)

    async def _cancel() -> bool:
        return await coordinator.cancel(task_id)

    ok = await _cancel()

    if ok:
        return f"Task '{task_id}' cancellation requested. The subagent will terminate on its next iteration."
    return f"Error: Task '{task_id}' not found."


subagent_cancel.annotations = ToolAnnotations(title="Cancel Subagent", destructive=True)


@tool
async def subagent_delete(name: str, user_id: str, workspace_id: str = "personal") -> str:
    """Delete a subagent definition and cancel any running tasks.

    Args:
        name: Subagent name to delete
        user_id: The user ID (required)
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        Deletion confirmation
    """
    coordinator = get_coordinator(user_id, workspace_id)

    async def _delete() -> bool:
        return await coordinator.delete(name)

    ok = await _delete()

    if ok:
        return f"Subagent '{name}' deleted. Any running tasks have been cancelled."
    return f"Error: Subagent '{name}' not found."


subagent_delete.annotations = ToolAnnotations(title="Delete Subagent", destructive=True)
