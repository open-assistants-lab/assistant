# Data Architecture

This document defines how user data is stored, isolated, and shared across deployment models.

---

## Deployment Models

There are two deployment modes. **Same code, same paths.** The only difference is whether `data/shared/` is a local folder or a mounted network volume.

| Mode | Platform | Users per instance | Isolation boundary |
|------|----------|--------------------|--------------------|
| **Solo** | .dmg / .exe on desktop | 1 | OS process |
| **Multi-user** | Docker container per user, org gets many containers | 1 per container | Container |

In both modes, the app sees exactly one user per instance. The container IS the isolation boundary — no multi-tenancy within a single process.

---

## Directory Structure

```
data/
├── private/                         # Container-local, never shared
│   ├── conversation/messages.db    # Chat history
│   ├── email/emails.db             # Email data
│   ├── contacts/contacts.db        # Personal contacts
│   ├── todos/todos.db              # Personal todos
│   └── apps/                       # Personal apps
│       ├── library/data.db
│       ├── crm/data.db
│       └── crm/.chromadb/
├── shared/                          # Mounted volume (org-wide)
│   └── apps/                        # Shared apps
│       ├── team_crm/data.db        # Has _app_shares for access control
│       └── team_crm/.chromadb/
└── templates/                       # App templates (schema-only)
    ├── crm_template.json
    └── project_tracker.json
```

### Solo mode

- `data/shared/` may not exist or be empty (no one to share with).
- `data/private/` contains the single user's data.
- The `private/` prefix is kept for consistency, even though there's only one user.

### Multi-user mode

- `data/private/` is container-local — email, contacts, conversation Never cross container boundaries.
- `data/shared/` is a **mounted volume** visible to all containers in the same org.
- Each container writes to its own `data/private/` but can read/write `data/shared/` for collaborative apps.

---

## Why This Design

### What we chose

- **Container-per-user** as the isolation boundary. Simple, proven, no row-level security in code.
- **Flat `data/private/` and `data/shared/`** structure. No `users/{user_id}/` nesting — the container already isolates, so the user directory is redundant.
- **Two scopes for apps**: personal (in `private/apps/`) and shared (in `shared/apps/`). A shared app lives on the shared volume with a `_app_shares` table for role-based access.
- **Export/Import** as the universal sharing mechanism that works across all deployment modes.
- **Mounted volume** for org-wide shared apps in multi-user mode. No API calls between containers, no cross-container filesystem access.
- **Same codepaths** for solo and multi-user. The deployment config only determines whether `data/shared/` is a local folder or a network mount.

### What we avoided

| Rejected | Why |
|----------|-----|
| `data/users/{user_id}/` nesting | Redundant when each container serves one user. Adds path complexity for no isolation benefit. |
| Separate "team-hosted" vs "cloud-hosted" data models | The difference is infrastructure (container-per-user vs shared process), not data architecture. Same code handles both. |
| Multi-tenancy in a single process | Requires row-level security, user-scoped queries on every operation, and one DB mistake exposes all data. Containers are simpler and safer. |
| Cross-container API calls for app sharing | Adds latency, auth complexity, and failure modes. A shared volume is simpler and faster. |
| `orgs/{org_id}/` nesting in data paths | The org is a deployment boundary, not a data path concern. Org routing happens at the load balancer, not in the app. |
| Per-user SQLite inside a shared process | Requires connection pooling, VACUUM coordination, and schema-per-user migrations. One-DB-per-container is proven and simple. |

---

## App Sharing

### Personal vs Shared Apps

| Type | Location | Access |
|------|----------|--------|
| Personal app | `data/private/apps/{name}/` | Owner only |
| Shared app | `data/shared/apps/{name}/` | Owner + authorized users via `_app_shares` |

An app starts as personal. The owner promotes it to shared with `app_share`, which moves or links it to the shared volume.

### Access Control (Shared Apps)

The `_app_shares` table lives inside the shared app's SQLite DB:

```sql
CREATE TABLE _app_shares (
    share_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL,  -- 'viewer' | 'editor' | 'admin'
    created_at  INTEGER NOT NULL
);
```

| Role | Permissions |
|------|------------|
| **viewer** | Query rows, search (FTS5 + semantic), read schema |
| **editor** | All viewer permissions + insert, update, delete rows |
| **admin** | All editor permissions + modify schema, add/remove columns, share with others, delete app |

### Sharing Tools

| Tool | Role | Description |
|------|------|-------------|
| `app_share` | admin | Grant a user access (viewer/editor/admin) |
| `app_unshare` | admin | Revoke a user's access |
| `app_shares_list` | admin | List who has access to an app |
| `app_export` | admin | Export app as `.ea-app` tarball (schema + data + embeddings) |
| `app_import` | any | Import `.ea-app` tarball (creates fork in user's personal space) |
| `app_template` | admin | Export schema-only template |
| `app_create_from_template` | any | Create app from a template |

### Export/Import Format (`.ea-app`)

A `.ea-app` file is a gzipped tarball:

```
my_app.ea-app
├── manifest.json          # { name, version, exported_at, schema }
├── schema.json            # { name, tables: { ... } }
├── tables/
│   ├── contacts.json       # Row data as JSON array
│   └── deals.json          # Row data as JSON array
└── embeddings/
    └── contacts_name.bin    # ChromaDB collection export
```

This works for all deployment modes:
- **Solo**: Export to file, send to another user, they import.
- **Multi-user**: Same, or use `app_share` for live collaboration.

---

## DataPaths Class

```python
from pathlib import Path
from src.config import get_settings


class DataPaths:
    """Resolves data paths based on deployment mode."""

    def __init__(self, deployment: str | None = None, base_path: str | None = None):
        settings = get_settings()
        self.deployment = deployment or settings.deployment  # "solo" | "multi-user"
        self.base = Path(base_path or settings.data_path or "data")

    @property
    def private(self) -> Path:
        """Container-local private data."""
        return self.base / "private"

    @property
    def shared(self) -> Path:
        """Org-wide shared data (mounted volume in multi-user)."""
        return self.base / "shared"

    @property
    def templates(self) -> Path:
        """App templates (schema-only)."""
        return self.base / "templates"

    # -- Personal data (container-local) --

    def conversation_db(self) -> Path:
        return self.private / "conversation" / "messages.db"

    def email_db(self) -> Path:
        return self.private / "email" / "emails.db"

    def contacts_db(self) -> Path:
        return self.private / "contacts" / "contacts.db"

    def todos_db(self) -> Path:
        return self.private / "todos" / "todos.db"

    def personal_apps(self) -> Path:
        return self.private / "apps"

    def personal_app(self, app_name: str) -> Path:
        return self.personal_apps() / app_name

    # -- Shared data (org-wide) --

    def shared_apps(self) -> Path:
        return self.shared / "apps"

    def shared_app(self, app_name: str) -> Path:
        return self.shared_apps() / app_name

    # -- Templates --

    def template(self, name: str) -> Path:
        return self.templates / f"{name}.json"
```

### Config

```yaml
# config.yaml
deployment: solo              # "solo" or "multi-user"
data_path: data
```

Solo mode:
- `data/shared/` may not exist — no one to share with.
- All paths resolve to `data/private/`.

Multi-user mode:
- `data/shared/` is a mounted volume.
- App sharing tools write to `data/shared/apps/`.

---

## Migration from Current Structure

Current (Phase 7):

```
data/users/{user_id}/
├── email/emails.db
├── contacts/contacts.db
├── todos/todos.db
├── conversation/messages.db
└── apps/{app_name}/data.db
```

Target (Phase 8+):

```
data/
├── private/
│   ├── email/emails.db
│   ├── contacts/contacts.db
│   ├── todos/todos.db
│   ├── conversation/messages.db
│   └── apps/{app_name}/data.db
└── shared/
    └── apps/{app_name}/data.db
```

Migration steps:
1. Add `DataPaths` class to `src/storage/paths.py`
2. Add `deployment` and `data_path` to `Settings`
3. Update `AppStorage`, `ConversationStorage`, `EmailDB`, `ContactsDB`, `TodosDB` to use `DataPaths`
4. Add `AppStorage.migrate_from_legacy()` that moves `data/users/{user_id}/` → `data/private/`
5. Auto-detect and migrate on first run if `data/private/` doesn't exist but `data/users/` does
6. Remove `data/users/` path after migration

---

## Implementation Order

| Phase | Task | Depends on |
|-------|------|-----------|
| 8a | Create `DataPaths` class + config | Phase 7 complete |
| 8b | Update all storage classes to use `DataPaths` | 8a |
| 8c | Add migration logic (`data/users/` → `data/private/`) | 8b |
| 8d | Add `_app_shares` table to `AppStorage` | 8a |
| 8e | Implement `app_share`, `app_unshare`, `app_shares_list` tools | 8d |
| 8f | Implement `app_export`, `app_import`, `app_template` tools | 8d |
| 8g | Update `AppStorage` to check `_app_shares` on read/write | 8d + 8e |

Phase 8a-8c is structural. 8d-8g is the sharing feature on top.

---

## Open Questions

- **Shared volume sync**: In multi-user mode with containers, how is `data/shared/` mounted? NFS? Docker volume? S3 FUSE? — This is an ops decision, not code. The app just reads/writes to the path.
- **ChromaDB in shared apps**: Shared apps need a shared ChromaDB instance (not per-container). The shared volume approach means the ChromaDB data directory lives on the shared volume. Concurrency is handled by ChromaDB's built-in locking or an external ChromaDB server.
- **Audit logging**: Enterprise deployments will want audit logs (who accessed what, when). Not implemented yet — add a `_app_audit` table in shared apps when needed.
- **Template marketplace**: A future feature — pre-built app templates curated by the team. Stored in `data/templates/` and distributed via `.ea-app` files.