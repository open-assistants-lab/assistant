"""Contacts storage and parsing from email."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, text

from src.app_logging import get_logger
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()

_engines: dict[str, object] = {}


def get_db_path(user_id: str) -> str:
    """Get SQLite database path for user."""
    from src.storage.paths import get_paths

    uid = user_id or DEFAULT_USER_ID
    return str(get_paths(uid).contacts_db())


def get_engine(user_id: str) -> Any:
    """Get SQLAlchemy engine (cached per user)."""
    if user_id in _engines:
        return _engines[user_id]
    db_path = get_db_path(user_id)
    engine = create_engine(f"sqlite:///{db_path}")
    _init_db(engine)
    _engines[user_id] = engine
    return engine


def _init_db(engine: Any) -> None:
    """Initialize database schema."""
    with engine.connect() as conn:
        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                name TEXT,
                first_name TEXT,
                last_name TEXT,
                company TEXT,
                phone TEXT,
                source TEXT DEFAULT 'email',
                email_account TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER
            )
        """)
        )

        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS contact_emails (
                id TEXT PRIMARY KEY,
                contact_id TEXT NOT NULL,
                email TEXT NOT NULL,
                is_primary INTEGER DEFAULT 0,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            )
        """)
        )

        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS contact_tags (
                contact_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                FOREIGN KEY (contact_id) REFERENCES contacts(id)
            )
        """)
        )

        # UNIQUE email enforcement (audit B17): legacy DBs are migrated by
        # deduping then creating the unique index; fresh DBs get it directly.
        has_unique = conn.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='uq_contacts_email'")
        ).scalar()
        if not has_unique:
            conn.execute(
                text(
                    "DELETE FROM contacts WHERE rowid NOT IN "
                    "(SELECT MIN(rowid) FROM contacts GROUP BY email)"
                )
            )
            conn.execute(text("DROP INDEX IF EXISTS idx_contacts_email"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_email ON contacts(email)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(name)"))

        conn.commit()


def parse_name_from_email(email: str) -> tuple[str | None, str | None]:
    """Parse first/last name from email address."""
    local = email.split("@")[0] if "@" in email else email
    parts = local.replace(".", " ").replace("_", " ").split()
    if len(parts) >= 2:
        return parts[0].capitalize(), " ".join(parts[1:]).capitalize()
    elif len(parts) == 1:
        return parts[0].capitalize(), None
    return None, None


def parse_contacts_from_email(
    user_id: str,
    account_id: str,
    from_addr: str | None,
    from_name: str | None,
    to_addrs: list[str] | None,
    cc_addrs: list[str] | None,
) -> list[dict[str, Any]]:
    """Parse contacts from email fields."""
    contacts = []
    seen_emails = set()

    def add_contact(email: str, name: str | None = None, is_primary: bool = False) -> None:
        if not email or email in seen_emails:
            return
        seen_emails.add(email.lower())

        first_name, last_name = parse_name_from_email(email)
        if name:
            parts = name.split()
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else None

        contacts.append(
            {
                "email": email.lower(),
                "name": name,
                "first_name": first_name,
                "last_name": last_name,
                "is_primary": is_primary,
            }
        )

    if from_addr:
        add_contact(from_addr, from_name, True)

    if to_addrs:
        for addr in to_addrs:
            add_contact(str(addr).lower())

    if cc_addrs:
        for addr in cc_addrs:
            add_contact(str(addr).lower())

    return contacts


def save_contacts(user_id: str, account_id: str, contacts: list[dict[str, Any]]) -> int:
    """Save contacts to database, returns count of new contacts."""
    if not contacts:
        return 0

    engine = get_engine(user_id)
    new_count = 0

    with engine.connect() as conn:
        for contact in contacts:
            email = contact.get("email", "").lower()
            if not email:
                continue

            # Pre-check spans ALL sources: the unique index does too. A
            # manual row must never be silently overwritten by sync nor gain
            # a dangling contact_emails side-row (audit B17 fix round 1).
            existing = conn.execute(
                text("SELECT id FROM contacts WHERE email = :email"),
                {"email": email},
            ).fetchone()

            if existing:
                if contact.get("name"):
                    conn.execute(
                        text("""
                            UPDATE contacts SET name = :name, updated_at = :updated_at
                            WHERE id = :id AND (name IS NULL OR name = '')
                        """),
                        {
                            "name": contact["name"],
                            "updated_at": int(datetime.now(UTC).timestamp()),
                            "id": existing[0],
                        },
                    )
                continue

            # Missing → atomic insert; rowcount distinguishes the race-lost case.
            contact_id = str(uuid.uuid4())
            cur = conn.execute(
                text("""
                    INSERT INTO contacts
                    (id, email, name, first_name, last_name, source, email_account, created_at)
                    VALUES (:id, :email, :name, :first_name, :last_name, :source, :email_account, :created_at)
                    ON CONFLICT(email) DO NOTHING
                """),
                {
                    "id": contact_id,
                    "email": email,
                    "name": contact.get("name"),
                    "first_name": contact.get("first_name"),
                    "last_name": contact.get("last_name"),
                    "source": "email",
                    "email_account": account_id,
                    "created_at": int(datetime.now(UTC).timestamp()),
                },
            )
            if cur.rowcount != 1:
                continue

            conn.execute(
                text("""
                    INSERT INTO contact_emails (id, contact_id, email, is_primary)
                    VALUES (:id, :contact_id, :email, 1)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "contact_id": contact_id,
                    "email": email,
                },
            )

            new_count += 1

        conn.commit()

    logger.info("contacts_parsed", {"new": new_count}, user_id=user_id)
    return new_count


def get_contacts(user_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Get all contacts."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, email, name, first_name, last_name, company, phone, source, created_at
                FROM contacts
                ORDER BY name ASC, email ASC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )

        contacts = []
        for row in result:
            contacts.append(dict(row._mapping))

        return contacts


def get_contact(
    user_id: str, contact_id: str | None = None, email: str | None = None
) -> dict[str, Any] | None:
    """Get single contact by ID or email."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        if contact_id:
            result = conn.execute(
                text("SELECT * FROM contacts WHERE id = :id"),
                {"id": contact_id},
            ).fetchone()
        elif email:
            result = conn.execute(
                text("SELECT * FROM contacts WHERE email = :email"),
                {"email": email.lower()},
            ).fetchone()
        else:
            return None

        if not result:
            return None

        contact = dict(result._mapping)

        emails = conn.execute(
            text("SELECT email, is_primary FROM contact_emails WHERE contact_id = :id"),
            {"id": contact["id"]},
        ).fetchall()
        contact["emails"] = [{"email": e[0], "is_primary": bool(e[1])} for e in emails]

        tags = conn.execute(
            text("SELECT tag FROM contact_tags WHERE contact_id = :id"),
            {"id": contact["id"]},
        ).fetchall()
        contact["tags"] = [t[0] for t in tags]

        return contact


def add_contact(
    user_id: str,
    email: str,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    """Add manual contact. Uses INSERT OR IGNORE to prevent TOCTOU race condition."""
    engine = get_engine(user_id)

    contact_id = str(uuid.uuid4())
    first_name, last_name = parse_name_from_email(email)
    if name:
        parts = name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) if len(parts) > 1 else None

    with engine.connect() as conn:
        # Conflict-safe insert (audit B17): the UNIQUE index on email makes
        # the check-then-insert race impossible; rowcount drives the result.
        result_cursor = conn.execute(
            text("""
                INSERT INTO contacts
                (id, email, name, first_name, last_name, phone, company, source, created_at)
                VALUES (:id, :email, :name, :first_name, :last_name, :phone, :company, :source, :created_at)
                ON CONFLICT(email) DO NOTHING
            """),
            {
                "id": contact_id,
                "email": email.lower(),
                "name": name,
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
                "company": company,
                "source": "manual",
                "created_at": int(datetime.now(UTC).timestamp()),
            },
        )

        inserted = result_cursor.rowcount == 1
        if inserted:
            conn.execute(
                text("""
                    INSERT INTO contact_emails (id, contact_id, email, is_primary)
                    VALUES (:id, :contact_id, :email, 1)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "contact_id": contact_id,
                    "email": email.lower(),
                },
            )

        conn.commit()

    if not inserted:
        return {"success": False, "error": "Contact already exists"}

    logger.info("contact_added", {"email": email}, user_id=user_id)
    return {"success": True, "contact_id": contact_id}


def update_contact(
    user_id: str,
    contact_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    """Update contact."""
    contact = get_contact(user_id, contact_id=contact_id, email=email)
    if not contact:
        return {"success": False, "error": "Contact not found"}

    engine = get_engine(user_id)

    with engine.connect() as conn:
        updates = []
        params = {"id": contact["id"], "updated_at": int(datetime.now(UTC).timestamp())}

        if name is not None:
            updates.append("name = :name")
            params["name"] = name
            parts = name.split()
            updates.append("first_name = :first_name")
            params["first_name"] = parts[0]
            if len(parts) > 1:
                updates.append("last_name = :last_name")
                params["last_name"] = " ".join(parts[1:])
            else:
                updates.append("last_name = :last_name")
                params["last_name"] = None

        if phone is not None:
            updates.append("phone = :phone")
            params["phone"] = phone

        if company is not None:
            updates.append("company = :company")
            params["company"] = company

        if updates:
            updates.append("updated_at = :updated_at")
            query = f"UPDATE contacts SET {', '.join(updates)} WHERE id = :id"
            conn.execute(text(query), params)
            conn.commit()

    logger.info("contact_updated", {"contact_id": contact["id"]}, user_id=user_id)
    return {"success": True}


def delete_contact(user_id: str, contact_id: str | None = None, email: str | None = None) -> dict[str, Any]:
    """Delete contact."""
    contact = get_contact(user_id, contact_id=contact_id, email=email)
    if not contact:
        return {"success": False, "error": "Contact not found"}

    engine = get_engine(user_id)

    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM contact_emails WHERE contact_id = :id"), {"id": contact["id"]}
        )
        conn.execute(text("DELETE FROM contact_tags WHERE contact_id = :id"), {"id": contact["id"]})
        conn.execute(text("DELETE FROM contacts WHERE id = :id"), {"id": contact["id"]})
        conn.commit()

    logger.info("contact_deleted", {"contact_id": contact["id"]}, user_id=user_id)
    return {"success": True}


def search_contacts(user_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search contacts by name, email, company. Case-insensitive."""
    engine = get_engine(user_id)
    pattern = f"%{query}%"

    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, email, name, first_name, last_name, company, phone, source, created_at
                FROM contacts
                WHERE name LIKE :pattern COLLATE NOCASE
                   OR email LIKE :pattern COLLATE NOCASE
                   OR company LIKE :pattern COLLATE NOCASE
                   OR first_name LIKE :pattern COLLATE NOCASE
                   OR last_name LIKE :pattern COLLATE NOCASE
                ORDER BY name ASC
                LIMIT :limit
            """),
            {"pattern": pattern, "limit": limit},
        )

        return [dict(row._mapping) for row in result]


def get_contacts_count(user_id: str) -> int:
    """Get total contacts count."""
    engine = get_engine(user_id)

    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM contacts")).fetchone()
        return result[0] if result else 0
