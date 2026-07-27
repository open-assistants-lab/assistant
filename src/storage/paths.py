"""Deployment-aware data path resolution.

Two deployment modes:
- solo: Single user on desktop (.dmg) or server. user_id defaults to "default_user".
  data/ contains project-level data (cache, templates, logs, traces, jobs).
- team: Multiple users on one server. Each user gets per-user data under ea_root.

User data lives under ea_root (defaults to ~/Assistant/).
Project data lives under data/ (cache, templates, logs, traces, jobs).
"""

import re
import warnings
from pathlib import Path

from src.config import get_settings

DEFAULT_USER_ID = "default_user"
_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_path_id(value: str, field: str) -> str:
    """Validate a path segment used as an application identifier."""
    if not value or not _PATH_ID_RE.fullmatch(value):
        raise ValueError(
            f"Invalid {field}: must contain only letters, numbers, underscores, and hyphens"
        )
    return value


_GITIGNORE_CONTENT = """\
# Databases
*.db

# Large binary directories
Memory/
Files/
Email/
Contacts/
Todos/
Conversation/

# Cache and temp
.versions/
.env
*.log
"""


class DataPaths:
    """Resolves data paths based on deployment mode and user identity.

    User data lives under ea_root (defaults to ~/Assistant/).
    Project data lives under data/ (cache, templates, logs, traces, jobs).

    In solo mode: user_id defaults to "default_user", team_id is None.
    In team mode: user_id comes from auth (JWT), team_id from config.

    Team data is stored at data/teams/{team_id}/ and accessed via team_* methods.
    Team paths return None when team_id is not set (solo mode).
    """

    def __init__(
        self,
        deployment: str | None = None,
        data_path: str | None = None,
        user_id: str | None = None,
        team_id: str | None = None,
        workspace_id: str | None = None,
        ea_root: str | None = None,
    ):
        settings = get_settings()
        self.deployment = deployment or settings.deployment
        self.base = Path(data_path or settings.data_path or "data")
        self.user_id = _validate_path_id(user_id or DEFAULT_USER_ID, "user_id")
        self.team_id = team_id
        self.workspace_id = _validate_path_id(workspace_id or "personal", "workspace_id")
        self.base.mkdir(parents=True, exist_ok=True)

        if ea_root:
            self._ea_root = Path(ea_root)
        else:
            configured = settings.deployment.ea_root
            if configured:
                self._ea_root = Path(configured)
            else:
                self._ea_root = Path.home() / "Assistant"

        self._ea_root.mkdir(parents=True, exist_ok=True)
        self._git_ensured = False

    # -- Root properties --

    def _ensure_git(self) -> None:
        """Init a git repo at the root directory if not already present."""
        git_dir = self._ea_root / ".git"
        gitignore = self._ea_root / ".gitignore"
        if git_dir.exists():
            return
        try:
            import subprocess
            subprocess.run(
                ["git", "init"],
                cwd=self._ea_root,
                capture_output=True,
                timeout=10,
            )
            if not gitignore.exists():
                gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        except Exception:
            pass  # git not available — fine, autoresearch falls back

    @property
    def root(self) -> Path:
        """Root of all user data under ea_root."""
        if not self._git_ensured:
            self._git_ensured = True
            self._ensure_git()
        return self._ea_root

    @property
    def team_root(self) -> None:
        """Team root (solo mode always returns None)."""
        return None

    # -- User-scoped methods --

    def user_skills_dir(self) -> Path:
        p = self.user_dir / "Skills"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_subagents_dir(self) -> Path:
        p = self.user_dir / "Subagents"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_tools_dir(self) -> Path:
        p = self.user_dir / "Tools"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def files_dir(self) -> Path:
        p = self.user_dir / "Files"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_prompt_path(self) -> Path:
        return self.user_dir / "AGENTS.md"

    def email_dir(self) -> Path:
        p = self.user_dir / "Email"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def gmail_cache_dir(self) -> Path:
        p = self.user_dir / "Email" / "gmail_cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def contacts_dir(self) -> Path:
        p = self.user_dir / "Contacts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def todos_dir(self) -> Path:
        p = self.user_dir / "Todos"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def conversation_dir(self) -> Path:
        p = self.user_dir / "Conversation"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_memory_dir(self) -> Path:
        p = self.user_dir / "Memory" / "global"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_apps_dir(self) -> Path:
        p = self.user_dir / "Apps"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_mcp_config(self) -> Path:
        return self.user_dir / ".mcp.json"

    def research_dir(self) -> Path:
        p = self.user_dir / "Research" / self.workspace_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def companion_dir(self) -> Path:
        p = self.user_dir / "Companion"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def companion_notifications_db(self) -> Path:
        return self.companion_dir() / "notifications.db"

    def companion_memory_db(self) -> Path:
        return self.companion_dir() / "memory.db"

    # -- Workspace compatibility methods (runtime storage is user-scoped) --

    def workspace_skills_dir(self) -> Path:
        return self.user_skills_dir()

    def workspace_subagents_dir(self) -> Path:
        return self.user_subagents_dir()

    def workspace_tools_dir(self) -> Path:
        return self.user_tools_dir()

    def workspace_files_dir(self) -> Path:
        return self.files_dir()

    def workspace_memory_dir(self) -> Path:
        return self.user_memory_dir()

    def workspace_conversation_path(self) -> Path:
        return self.conversation_dir() / "app.db"

    def workspace_cache(self) -> Path:
        return self.user_dir / ".file_cache.json"

    def versions_dir(self) -> Path:
        p = self.user_dir / ".versions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -- Convenience: DB file paths --

    def conversation_db(self) -> Path:
        return self.conversation_dir() / "messages.db"

    def email_db(self) -> Path:
        return self.email_dir() / "emails.db"

    def contacts_db(self) -> Path:
        return self.contacts_dir() / "contacts.db"

    def todos_db(self) -> Path:
        return self.todos_dir() / "todos.db"

    def work_queue_db(self) -> Path:
        return self.user_subagents_dir() / "work_queue.db"

    # -- Deprecated wrappers (redirect to new methods with warnings) --

    def workspace_dir(self) -> Path:
        """Deprecated: use workspace_files_dir() instead."""
        warnings.warn(
            "workspace_dir() is deprecated, use workspace_files_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.workspace_files_dir()

    def skills_dir(self) -> Path:
        warnings.warn(
            "skills_dir() is deprecated, use user_skills_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_skills_dir()

    def global_skills_dir(self) -> Path:
        warnings.warn(
            "global_skills_dir() is deprecated, use user_skills_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_skills_dir()

    def subagents_dir(self) -> Path:
        warnings.warn(
            "subagents_dir() is deprecated, use user_subagents_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_subagents_dir()

    def global_subagents_dir(self) -> Path:
        warnings.warn(
            "global_subagents_dir() is deprecated, use user_subagents_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_subagents_dir()

    def agent_defs_dir(self) -> Path:
        warnings.warn(
            "agent_defs_dir() is deprecated, use user_subagents_dir() / 'agent_defs'",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_subagents_dir() / "agent_defs"

    def global_memory_dir(self) -> Path:
        warnings.warn(
            "global_memory_dir() is deprecated, use user_memory_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_memory_dir()

    def memory_dir(self) -> Path:
        warnings.warn(
            "memory_dir() is deprecated, use user_memory_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_memory_dir()

    def memory_db(self) -> Path:
        return self.user_memory_dir() / "memory.db"

    def user_config_dir(self) -> Path:
        warnings.warn(
            "user_config_dir() is deprecated, use user_prompt_path() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_prompt_path()

    def gmail_cache(self) -> Path:
        warnings.warn(
            "gmail_cache() is deprecated, use gmail_cache_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.gmail_cache_dir()

    def mcp_config_path(self) -> Path:
        warnings.warn(
            "mcp_config_path() is deprecated, use user_mcp_config()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_mcp_config()

    def apps_dir(self) -> Path:
        warnings.warn(
            "apps_dir() is deprecated, use user_apps_dir()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.user_apps_dir()

    # -- User-global (cross-workspace) data --

    @property
    def user_dir(self) -> Path:
        """Root of user data."""
        if self.user_id == DEFAULT_USER_ID:
            return self.root
        p = self.root / "Users" / self.user_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -- Templates --

    @property
    def templates(self) -> Path:
        return self.base / "templates"

    def template_path(self, name: str) -> Path:
        return self.templates / f"{name}.json"

    # -- Team data (returns None if team_id not set, i.e. solo mode) --

    def team_dir(self) -> Path | None:
        """Root of team data: data/teams/{team_id}/. None in solo mode."""
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id

    def team_skills_dir(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "skills"

    def team_apps_dir(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "apps"

    def team_contacts_dir(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "contacts"

    def team_todos_dir(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "todos"

    def team_memory_dir(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "memory"

    def team_files_dir(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "files"

    def team_mcp_config_path(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / ".mcp.json"

    def team_config_path(self) -> Path | None:
        if not self.team_id:
            return None
        return self.base / "teams" / self.team_id / "config.yaml"

    # -- Shared data (legacy, will be removed in Phase 8 folder cleanup) --

    @property
    def shared(self) -> Path:
        """Legacy shared directory. Use team_* methods instead."""
        return self.base / "shared"

    def shared_apps_dir(self) -> Path:
        """Legacy. Use team_apps_dir() instead."""
        p = self.shared / "apps"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -- System paths (not user-scoped) --

    def model_cache_path(self) -> Path:
        p = self.base / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p / "models.json"

    def logs_dir(self) -> Path:
        p = self.base / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def traces_path(self) -> Path:
        p = self.base / "traces"
        p.mkdir(parents=True, exist_ok=True)
        return p / "traces.jsonl"

    def jobs_db_path(self) -> Path:
        return self.base / "jobs.db"

    def jobs_results_db_path(self) -> Path:
        return self.base / "jobs_results.db"


_paths_cache: dict[tuple[str, str], DataPaths] = {}


def get_paths(
    user_id: str | None = None,
    team_id: str | None = None,
    workspace_id: str | None = None,
) -> DataPaths:
    """Get DataPaths instance (cached per user_id+team_id pair).

    In solo mode: user_id defaults to "default_user", team_id is None.
    In team mode: user_id comes from auth (JWT), team_id from config.
    workspace_id is NOT part of the cache key — it changes per session.
    """
    uid = _validate_path_id(user_id or DEFAULT_USER_ID, "user_id")
    tid = team_id  # None for solo mode
    cache_key = (uid, tid or "")

    if cache_key not in _paths_cache:
        _paths_cache[cache_key] = DataPaths(user_id=uid, team_id=tid)

    dp = _paths_cache[cache_key]
    if workspace_id and workspace_id != dp.workspace_id:
        return DataPaths(user_id=uid, team_id=tid, workspace_id=workspace_id)
    return dp
