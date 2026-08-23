"""Todo storage - DB operations for user todos extracted from emails."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.app_logging import get_logger

logger = get_logger()


def get_db_path(user_id: str) -> str:
    """Get SQLite database path for user."""
    from src.storage.paths import get_paths

    uid = user_id or "default_user"
    return str(get_paths(uid).todos_db())


def get_engine(user_id: str) -> Any:
    """Get SQLAlchemy engine with schema initialized."""
    db_path = get_db_path(user_id)
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    _init_db(engine)
    return engine


def _init_db(engine: Any) -> None:
    """Initialize database schema."""
    with engine.connect() as conn:
        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS todos (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                due_date INTEGER,
                email_id TEXT,
                source TEXT DEFAULT 'email',
                created_at INTEGER NOT NULL,
                updated_at INTEGER
            )
        """)
        )

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_todos_email_id ON todos(email_id)"))

        conn.commit()


def add_todo(
    user_id: str,
    content: str,
    status: str = "pending",
    priority: int = 0,
    due_date: int | None = None,
    email_id: str | None = None,
    source: str = "email",
) -> dict[str, Any]:
    """Add a new todo."""
    engine = get_engine(user_id)
    todo_id = str(uuid.uuid4())
    ts = int(datetime.now(UTC).timestamp())

    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO todos (id, content, status, priority, due_date, email_id, source, created_at)
                VALUES (:id, :content, :status, :priority, :due_date, :email_id, :source, :created_at)
            """),
            {
                "id": todo_id,
                "content": content,
                "status": status,
                "priority": priority,
                "due_date": due_date,
                "email_id": email_id,
                "source": source,
                "created_at": ts,
            },
        )
        conn.commit()

    logger.info("todo_added", {"todo_id": todo_id, "source": source}, user_id=user_id)
    return {"id": todo_id, "content": content, "status": status}


def get_todos(user_id: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Get all todos for a user."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        if status:
            result = conn.execute(
                text("""
                    SELECT id, content, status, priority, due_date, email_id, source, created_at, updated_at
                    FROM todos
                    WHERE status = :status
                    ORDER BY priority DESC, created_at DESC
                    LIMIT :limit
                """),
                {"status": status, "limit": limit},
            )
        else:
            result = conn.execute(
                text("""
                    SELECT id, content, status, priority, due_date, email_id, source, created_at, updated_at
                    FROM todos
                    ORDER BY priority DESC, created_at DESC
                    LIMIT :limit
                """),
                {"limit": limit},
            )

        return [dict(row._mapping) for row in result]


def update_todo(
    user_id: str,
    todo_id: str,
    content: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    due_date: int | None = None,
) -> dict[str, Any]:
    """Update a todo."""
    engine = get_engine(user_id)
    ts = int(datetime.now(UTC).timestamp())

    updates = []
    params: dict[str, Any] = {"id": todo_id, "updated_at": ts}

    if content is not None:
        updates.append("content = :content")
        params["content"] = content
    if status is not None:
        updates.append("status = :status")
        params["status"] = status
    if priority is not None:
        updates.append("priority = :priority")
        params["priority"] = priority
    if due_date is not None:
        updates.append("due_date = :due_date")
        params["due_date"] = due_date

    if not updates:
        return {"success": False, "error": "No fields to update"}

    updates.append("updated_at = :updated_at")
    query = f"UPDATE todos SET {', '.join(updates)} WHERE id = :id"

    with engine.connect() as conn:
        conn.execute(text(query), params)
        conn.commit()

    logger.info("todo_updated", {"todo_id": todo_id}, user_id=user_id)
    return {"success": True}


def delete_todo(user_id: str, todo_id: str) -> dict[str, Any]:
    """Delete a todo."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM todos WHERE id = :id"), {"id": todo_id})
        conn.commit()

    logger.info("todo_deleted", {"todo_id": todo_id}, user_id=user_id)
    return {"success": True}


def get_todo_by_email(user_id: str, email_id: str) -> list[dict[str, Any]]:
    """Get todos extracted from a specific email."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, content, status, priority, due_date, email_id, source, created_at
                FROM todos
                WHERE email_id = :email_id
            """),
            {"email_id": email_id},
        )
        return [dict(row._mapping) for row in result]


def todos_count(user_id: str) -> dict[str, int]:
    """Get todo counts by status."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM todos")).fetchone()[0]
        pending = conn.execute(
            text("SELECT COUNT(*) FROM todos WHERE status = 'pending'")
        ).fetchone()[0]
        in_progress = conn.execute(
            text("SELECT COUNT(*) FROM todos WHERE status = 'in_progress'")
        ).fetchone()[0]
        completed = conn.execute(
            text("SELECT COUNT(*) FROM todos WHERE status = 'completed'")
        ).fetchone()[0]

    return {
        "total": total,
        "pending": pending,
        "in_progress": in_progress,
        "completed": completed,
    }
