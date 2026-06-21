"""Todo tools — SDK-native implementation."""

from typing import Any

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool

logger = get_logger()


def _get_model() -> Any:
    from src.config import get_settings
    from src.sdk.providers.factory import create_provider

    settings = get_settings()
    model_str = getattr(settings.agent, "model", "ollama:minimax-m2.5")
    return create_provider(model_str)


def extract_todos_from_email(
    user_id: str,
    email_id: str,
    subject: str,
    body: str,
) -> list[dict[str, Any]]:
    from src.sdk.tools_core import todos_storage

    prompt = f"""You are an assistant that extracts action items/todos from emails.

Extract any tasks, action items, reminders, or things the user needs to do from this email.

Email Subject: {subject}

Email Body:
{body[:3000]}

Respond with a JSON array of todos. Each todo should have:
- "content": The task description (what needs to be done)
- "due_date": (optional) Unix timestamp if a due date is mentioned, otherwise null

Examples of todos to extract:
- "Follow up on the meeting notes"
- "Send invoice to client"
- "Review the proposal by Friday"
- "Call back regarding the project update"

Only extract clear action items. Ignore:
- Informational emails without action items
- Newsletter content
- Automated notifications

Respond with valid JSON only, no other text."""

    try:
        import asyncio

        from src.sdk.loop import AgentLoop
        from src.sdk.messages import Message

        provider = _get_model()
        loop = AgentLoop(provider=provider)
        messages = [
            Message.system("You extract action items from emails. Return only valid JSON."),
            Message.user(prompt),
        ]

        try:
            _ = asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                response_msg = executor.submit(asyncio.run, loop.run_single(messages)).result()
        except RuntimeError:
            response_msg = asyncio.run(loop.run_single(messages))

        if response_msg is None:
            return []

        content = (
            response_msg.content
            if isinstance(response_msg.content, str)
            else str(response_msg.content)
        )
        content = content.strip()

        import json

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        todos = json.loads(content)

        if not isinstance(todos, list):
            return []

        saved_todos = []
        for todo in todos:
            if isinstance(todo, dict) and todo.get("content"):
                result = todos_storage.add_todo(
                    user_id=user_id,
                    content=todo["content"],
                    status="pending",
                    priority=0,
                    due_date=todo.get("due_date"),
                    email_id=email_id,
                    source="email",
                )
                saved_todos.append(result)

        logger.info(
            "todos_extracted", {"email_id": email_id, "count": len(saved_todos)}, user_id=user_id
        )

        return saved_todos

    except Exception as e:
        logger.error(
            "todo_extraction_error", {"email_id": email_id, "error": str(e)}, user_id=user_id
        )
        return []


@tool
def todos_list(
    user_id: str,
    status: str | None = None,
    limit: int = 50,
) -> str:
    """List all todos for a user.

    Args:
        user_id: User identifier (REQUIRED)
        status: Filter by status - "pending", "in_progress", "completed" (optional)
        limit: Maximum number of todos to return (default: 50)

    Returns:
        Formatted list of todos
    """
    from src.sdk.tools_core import todos_storage

    todos = todos_storage.get_todos(user_id, status=status, limit=limit)
    counts = todos_storage.todos_count(user_id)

    if not todos:
        return f"No todos found. {counts['total']} total todos."

    lines = [f"### 📋 Todo List ({counts['total']} total)\n"]

    pending = [t for t in todos if t["status"] == "pending"]
    in_progress = [t for t in todos if t["status"] == "in_progress"]
    completed = [t for t in todos if t["status"] == "completed"]

    if in_progress:
        lines.append("**🔄 In Progress:**")
        for t in in_progress:
            lines.append(f"- [{t['id']}] {t['content']}")
        lines.append("")

    if pending:
        lines.append("**📝 Pending:**")
        for t in pending:
            lines.append(f"- [{t['id']}] {t['content']}")
        lines.append("")

    if completed:
        lines.append("**✅ Completed:**")
        for t in completed[:10]:
            lines.append(f"- ~~{t['content']}~~")
        if len(completed) > 10:
            lines.append(f"  ... and {len(completed) - 10} more")
        lines.append("")

    return "\n".join(lines)


todos_list.annotations = ToolAnnotations(title="List Todos", read_only=True, idempotent=True)


@tool
def todos_add(
    user_id: str,
    content: str = "",
    status: str = "pending",
    priority: int = 0,
    due_date: int | None = None,
) -> str:
    """Add a new todo (manual or from email).

    Args:
        user_id: User identifier (REQUIRED)
        content: Todo content/task description (REQUIRED)
        status: Initial status - "pending", "in_progress", "completed" (default: pending)
        priority: Priority level 0-5 (default: 0)
        due_date: Unix timestamp for due date (optional)

    Returns:
        Confirmation message
    """
    from src.sdk.tools_core import todos_storage

    if not content:
        return "Error: content is required"

    result = todos_storage.add_todo(
        user_id=user_id,
        content=content,
        status=status,
        priority=priority,
        due_date=due_date,
        source="manual",
    )

    return f"✅ Todo added: [{result['id']}] {content}"


todos_add.annotations = ToolAnnotations(title="Add Todo")


@tool
def todos_update(
    user_id: str,
    todo_id: str = "",
    content: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: int | None = None,
) -> str:
    """Update a todo.

    Args:
        user_id: User identifier (REQUIRED)
        todo_id: Todo ID to update (REQUIRED)
        content: New content (optional)
        status: New status - "pending", "in_progress", "completed" (optional)
        priority: New priority 0-5 (optional)
        due_date: New due date as Unix timestamp (optional)

    Returns:
        Confirmation message
    """
    from src.sdk.tools_core import todos_storage

    if not todo_id:
        return "Error: todo_id is required"

    result = todos_storage.update_todo(
        user_id=user_id,
        todo_id=todo_id,
        content=content,
        status=status,
        priority=priority,
        due_date=due_date,
    )

    if result.get("success"):
        return f"✅ Todo [{todo_id}] updated"
    return f"Error: {result.get('error', 'Unknown error')}"


todos_update.annotations = ToolAnnotations(title="Update Todo")


@tool
def todos_delete(
    user_id: str,
    todo_id: str = "",
) -> str:
    """Delete a todo.

    Args:
        user_id: User identifier (REQUIRED)
        todo_id: Todo ID to delete (REQUIRED)

    Returns:
        Confirmation message
    """
    from src.sdk.tools_core import todos_storage

    if not todo_id:
        return "Error: todo_id is required"

    result = todos_storage.delete_todo(user_id, todo_id)

    if result.get("success"):
        return f"✅ Todo [{todo_id}] deleted"
    return f"Error: {result.get('error', 'Unknown error')}"


todos_delete.annotations = ToolAnnotations(title="Delete Todo", destructive=True)


@tool
def todos_extract(
    user_id: str = "",
    email_id: str | None = None,
    account_name: str | None = None,
    limit: int = 10,
) -> str:
    """Extract todos from emails using LLM.

    Args:
        user_id: User identifier (REQUIRED)
        email_id: Specific email ID to extract from (optional - if not provided, extracts from recent emails)
        account_name: Account name to extract from (optional, for recent emails)
        limit: Number of recent emails to scan if email_id not provided (default: 10)

    Returns:
        Extraction results
    """
    if not user_id:
        return "Error: user_id is required."

    from sqlalchemy import text

    from src.sdk.tools_core.email_db import get_account_id_by_name, get_engine

    extracted = []

    if email_id:
        engine = get_engine(user_id)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT subject, body_text FROM emails WHERE message_id = :id"),
                {"id": email_id},
            ).fetchone()

            if not result:
                return f"Error: Email {email_id} not found"

            subject, body = result
            todos = extract_todos_from_email(user_id, email_id, subject, body or "")
            extracted.extend(todos)
    else:
        account_id = None
        if account_name:
            account_id = get_account_id_by_name(account_name, user_id)

        engine = get_engine(user_id)
        with engine.connect() as conn:
            if account_id:
                result = conn.execute(
                    text("""
                        SELECT message_id, subject, body_text
                        FROM emails
                        WHERE account_id = :account_id
                        ORDER BY timestamp DESC
                        LIMIT :limit
                    """),
                    {"account_id": account_id, "limit": limit},
                )
            else:
                result = conn.execute(
                    text("""
                        SELECT message_id, subject, body_text
                        FROM emails
                        ORDER BY timestamp DESC
                        LIMIT :limit
                    """),
                    {"limit": limit},
                )

            rows = result.fetchall()

            for row in rows:
                msg_id, subject, body = row
                existing = conn.execute(
                    text("SELECT id FROM todos WHERE email_id = :email_id"), {"email_id": msg_id}
                ).fetchone()

                if existing:
                    continue

                todos = extract_todos_from_email(user_id, msg_id, subject, body or "")
                extracted.extend(todos)

    if not extracted:
        return "No new todos extracted from emails."

    return f"✅ Extracted {len(extracted)} todo(s) from emails:\n" + "\n".join(
        f"- [{t['id']}] {t['content']}" for t in extracted
    )


todos_extract.annotations = ToolAnnotations(title="Extract Todos from Email", read_only=True)
