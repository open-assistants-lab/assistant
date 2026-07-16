"""User storage for Assistant."""

from pathlib import Path

from src.storage.paths import get_paths


class UserStorage:
    """Manages user-specific storage directories."""

    def __init__(self, user_id: str):
        """Initialize user storage.

        Args:
            user_id: Unique user identifier (also used as thread_id)
        """
        self.user_id = user_id
        self.base_dir = get_paths(user_id).user_dir
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        directories = [
            self.base_dir,
            self.base_dir / ".memory",
            self.base_dir / ".memory" / "chromadb",
            self.base_dir / ".vault",
            self.base_dir / "skills",
            self.base_dir / "projects",
            self.base_dir / "databases",
            self.base_dir / "notes",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def memory_db_path(self) -> Path:
        """Get path to SQLite memory database."""
        return self.base_dir / ".memory" / "memory.db"

    @property
    def chromadb_path(self) -> Path:
        """Get path to ChromaDB storage."""
        return self.base_dir / ".memory" / "chromadb"

    @property
    def vault_path(self) -> Path:
        """Get path to encrypted vault."""
        return self.base_dir / ".vault" / "vault.db"

    @property
    def skills_dir(self) -> Path:
        """Get path to user skills directory."""
        return self.base_dir / "skills"

    @property
    def projects_dir(self) -> Path:
        """Get path to user projects directory."""
        return self.base_dir / "projects"

    @property
    def databases_dir(self) -> Path:
        """Get path to user databases directory."""
        return self.base_dir / "databases"

    @property
    def notes_dir(self) -> Path:
        """Get path to user notes directory."""
        return self.base_dir / "notes"

    def get_project_dir(self, project_name: str) -> Path:
        """Get path to a specific project directory."""
        return self.projects_dir / project_name

    def get_database_path(self, database_name: str) -> Path:
        """Get path to a user database."""
        return self.databases_dir / f"{database_name}.db"

    def get_notes_path(self, collection: str) -> Path:
        """Get path to a notes collection."""
        return self.notes_dir / collection


_user_storage_cache: dict[str, UserStorage] = {}


def get_user_storage(user_id: str) -> UserStorage:
    """Get or create user storage.

    Args:
        user_id: Unique user identifier

    Returns:
        UserStorage instance for the user
    """
    if user_id not in _user_storage_cache:
        _user_storage_cache[user_id] = UserStorage(user_id)
    return _user_storage_cache[user_id]


def clear_user_storage_cache() -> None:
    """Clear user storage cache (useful for testing)."""
    _user_storage_cache.clear()
