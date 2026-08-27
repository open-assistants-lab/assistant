"""Per-user prompt storage — persisted custom instructions across workspaces."""

from pathlib import Path

from src.storage.paths import DEFAULT_USER_ID, DataPaths


def _user_prompt_path(user_id: str) -> Path:
    return DataPaths(user_id=user_id).user_prompt_path()


def load_user_prompt(user_id: str =  DEFAULT_USER_ID) -> str:
    """Load the user's custom prompt. Returns empty string if not set."""
    path = _user_prompt_path(user_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_user_prompt(user_id: str, prompt: str) -> None:
    """Save the user's custom prompt to disk (atomic write via temp file + rename)."""

    path = _user_prompt_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(prompt.strip(), encoding="utf-8")
    tmp.rename(path)
