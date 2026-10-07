"""Fresh instance authority/configuration on the existing receipt database path.

Provisioning methods are trusted operator setup, not account/login APIs. They must
never be exposed to arbitrary user code. OS protection is qualified separately.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from src.http.auth.resolver import UserIdentity
from src.sdk.authority_models import Actor, CompanyRole, InstanceMode, InstancePolicy
from src.sdk.run_models import CanonicalModel

_ID = re.compile(r"^[0-9a-f]{32}$")
_SCHEMA = """
CREATE TABLE engine_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    kind TEXT NOT NULL, schema_version INTEGER NOT NULL, instance_id TEXT NOT NULL
);
CREATE TABLE instance_config (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    mode TEXT NOT NULL, policy_json TEXT NOT NULL
);
CREATE TABLE instance_members (
    user_id TEXT PRIMARY KEY, role TEXT NOT NULL, scopes_json TEXT NOT NULL,
    revision INTEGER NOT NULL, enabled INTEGER NOT NULL
);
CREATE TABLE identity_bindings (
    issuer TEXT NOT NULL, subject TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES instance_members(user_id),
    PRIMARY KEY (issuer, subject)
);
CREATE TABLE instance_teams (team_id TEXT PRIMARY KEY);
CREATE TABLE instance_team_members (
    team_id TEXT NOT NULL REFERENCES instance_teams(team_id),
    user_id TEXT NOT NULL REFERENCES instance_members(user_id),
    PRIMARY KEY (team_id, user_id)
);
"""


class IncompatibleStoreError(ValueError):
    """Old/unqualified stores are refused without migration or attribution."""


def _opaque_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("expected a provisioned opaque identifier")
    return value


class InstanceState:
    """Async domain operations; constructor performs no filesystem mutations."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).absolute()
        if (
            self.path.name != "engine-state.db"
            or self.path.parent.name != "private"
            or self.path.parent.parent.parent.name != "Instances"
        ):
            raise ValueError("expected fresh Instances/{id}/private/engine-state.db layout")
        self.instance_id = _opaque_id(self.path.parent.parent.name)
        self._root = self.path.parents[3].resolve()
        self.path = self._root / "Instances" / self.instance_id / "private" / self.path.name
        self._initialized = False

    def _validate_paths(self) -> None:
        for path in (self.path.parents[2], self.path.parents[1], self.path.parent, self.path):
            if path.is_symlink():
                raise ValueError("instance state cannot traverse a symlink")

    def _check_existing(self) -> None:
        # Immutable read deliberately does not recover/migrate/create old WAL or
        # SHM files. Metadata is checkpointed when a fresh store is initialized.
        try:
            with sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
                row = db.execute(
                    "SELECT kind, schema_version, instance_id FROM engine_metadata"
                    " WHERE singleton = 1"
                ).fetchone()
                if row != ("assistant-engine-state", 2, self.instance_id):
                    raise IncompatibleStoreError("incompatible instance store; use a fresh store")
        except sqlite3.DatabaseError as exc:
            raise IncompatibleStoreError(
                "unqualified store; retained data was not migrated"
            ) from exc

    async def initialize(self) -> None:
        self._validate_paths()
        if self.path.exists():
            await asyncio.to_thread(self._check_existing)
        else:
            # The trusted root is operator-selected; these newly created private
            # directories are not shared user-writable runtime storage.
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._validate_paths()
            # Exclusive creation avoids adopting a file racing initialization.
            with self.path.open("xb"):
                pass
            self.path.chmod(0o600)
            async with aiosqlite.connect(self.path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA synchronous=FULL")
                await db.executescript(_SCHEMA)
                await db.execute(
                    "INSERT INTO engine_metadata VALUES (1, 'assistant-engine-state', 2, ?)",
                    (self.instance_id,),
                )
                await db.commit()
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._initialized = True

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        if not self._initialized:
            raise RuntimeError("instance state must be initialized before use")
        self._validate_paths()
        async with aiosqlite.connect(self.path.as_uri() + "?mode=rw", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA synchronous=FULL")
            yield db

    async def provision_instance(
        self, *, mode: InstanceMode, default_model: CanonicalModel
    ) -> None:
        """Explicit deployment setup; cannot overwrite a configured instance."""
        if mode not in ("SL", "SR", "MT", "MU"):
            raise ValueError("unsupported instance mode")
        policy = InstancePolicy(instance_id=self.instance_id, default_model=default_model)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            row = await (await db.execute("SELECT * FROM instance_config")).fetchone()
            if row is not None:
                if row["mode"] != mode or row["policy_json"] != policy.model_dump_json():
                    raise ValueError("instance already provisioned; use revisioned configuration")
                return
            await db.execute(
                "INSERT INTO instance_config VALUES (1, ?, ?)", (mode, policy.model_dump_json())
            )
            await db.commit()

    async def provision_member(
        self,
        *,
        issuer: str,
        subject: str,
        user_id: str,
        role: CompanyRole,
        scopes: tuple[str, ...] = (),
        enabled: bool = True,
    ) -> None:
        """Map a deployment-authenticated subject to protected company grants."""
        _opaque_id(user_id)
        if not issuer or not subject or role not in ("owner", "admin", "staff"):
            raise ValueError("explicit issuer/subject/role required")
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            binding = await (
                await db.execute(
                    "SELECT user_id FROM identity_bindings WHERE issuer=? AND subject=?",
                    (issuer, subject),
                )
            ).fetchone()
            if binding is not None and binding["user_id"] != user_id:
                raise PermissionError("subject already bound; identity cannot be reassigned")
            await db.execute(
                "INSERT INTO instance_members VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,"
                " scopes_json=excluded.scopes_json, enabled=excluded.enabled,"
                " revision=instance_members.revision+1",
                (user_id, role, json.dumps(scopes), int(enabled)),
            )
            await db.execute(
                "INSERT OR IGNORE INTO identity_bindings VALUES (?, ?, ?)",
                (issuer, subject, user_id),
            )
            await db.commit()

    async def provision_team(self, team_id: str) -> None:
        _opaque_id(team_id)
        async with self._connect() as db:
            await db.execute("INSERT OR IGNORE INTO instance_teams VALUES (?)", (team_id,))
            await db.commit()

    async def set_team_member(self, team_id: str, user_id: str, *, enabled: bool) -> None:
        _opaque_id(team_id)
        _opaque_id(user_id)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if enabled:
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO instance_team_members VALUES (?, ?)", (team_id, user_id)
                )
            else:
                cursor = await db.execute(
                    "DELETE FROM instance_team_members WHERE team_id=? AND user_id=?",
                    (team_id, user_id),
                )
            if cursor.rowcount:
                await db.execute(
                    "UPDATE instance_members SET revision=revision+1 WHERE user_id=?", (user_id,)
                )
            await db.commit()

    async def authorize(self, identity: UserIdentity, instance_id: str) -> Actor:
        if (
            instance_id != self.instance_id
            or identity.instance_id != instance_id
            or not identity.issuer
            or not identity.subject
            or not identity.key_id
            or identity.trust_domain not in ("untrusted", "desktop")
        ):
            raise PermissionError("individual instance-bound authentication required")
        async with self._connect() as db:
            config = await (await db.execute("SELECT mode FROM instance_config")).fetchone()
            if config is None:
                raise PermissionError("instance not provisioned")
            if identity.trust_domain == "desktop" and config["mode"] != "SL":
                raise PermissionError("local launch identity is not remote/company authentication")
            row = await (
                await db.execute(
                    "SELECT m.* FROM instance_members m JOIN identity_bindings b"
                    " ON m.user_id=b.user_id WHERE b.issuer=? AND b.subject=? AND m.enabled=1",
                    (identity.issuer, identity.subject),
                )
            ).fetchone()
            if row is None:
                raise PermissionError("subject has no active instance membership")
        allowed_scopes = set(json.loads(row["scopes_json"]))
        return Actor(
            instance_id=self.instance_id,
            user_id=row["user_id"],
            key_id=identity.key_id,
            role=row["role"],
            scopes=tuple(sorted(allowed_scopes.intersection(identity.scopes))),
            membership_revision=row["revision"],
        )

    async def teams_of(self, actor: Actor) -> tuple[str, ...]:
        if actor.instance_id != self.instance_id:
            raise PermissionError("actor belongs to another instance")
        async with self._connect() as db:
            member = await (
                await db.execute(
                    "SELECT revision, enabled FROM instance_members WHERE user_id=?",
                    (actor.user_id,),
                )
            ).fetchone()
            if (
                member is None
                or not member["enabled"]
                or member["revision"] != actor.membership_revision
            ):
                raise PermissionError("membership changed; resolve current authority")
            rows = await (
                await db.execute(
                    "SELECT team_id FROM instance_team_members WHERE user_id=? ORDER BY team_id",
                    (actor.user_id,),
                )
            ).fetchall()
        return tuple(row["team_id"] for row in rows)
