"""Storage module for Assistant."""

from src.storage.messages import (
    Message,
    MessageStore,
    SearchResult,
    get_message_store,
)
from src.storage.user_storage import UserStorage, get_user_storage

__all__ = [
    "UserStorage",
    "get_user_storage",
    "MessageStore",
    "get_message_store",
    "Message",
    "SearchResult",
]
