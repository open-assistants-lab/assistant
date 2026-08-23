"""Tests for memory tools — memory_profile (post-CoreMem-0.10 recall digest)."""

from unittest.mock import MagicMock, patch

TEST_USER = "test_memory_user"


class _Result:
    def __init__(self, content: str, ts: str, score: float = 1.0):
        self.memory = MagicMock()
        self.memory.content = content
        self.memory.ts = ts
        self.score = score


class TestMemoryProfile:
    def test_memory_profile_no_context(self):
        from src.sdk.tools_core.memory import memory_profile

        with patch("src.storage.messages.get_message_store") as mock_store_fn:
            mock_core = MagicMock()
            mock_core.recall.return_value = []
            mock_store = MagicMock()
            mock_store.core = mock_core
            mock_store_fn.return_value = mock_store

            result = memory_profile.invoke({"user_id": TEST_USER})

        assert "No recent conversation context" in result
        mock_core.recall.assert_called_once()
        kwargs = mock_core.recall.call_args.kwargs
        assert kwargs["strategy"] == "episodic"
        assert kwargs["session_cap"] == 2
        assert "ts_after" in kwargs

    def test_memory_profile_with_context(self):
        from src.sdk.tools_core.memory import memory_profile

        with patch("src.storage.messages.get_message_store") as mock_store_fn:
            mock_core = MagicMock()
            mock_core.recall.return_value = [
                _Result("Name is Alice and she works at TechCorp", "2026-08-01T10:00:00"),
                _Result("Prefers concise answers, likes Python", "2026-08-02T09:00:00", 0.8),
            ]
            mock_store = MagicMock()
            mock_store.core = mock_core
            mock_store_fn.return_value = mock_store

            result = memory_profile.invoke({"user_id": TEST_USER})

        assert "Working Memory" in result
        assert "Name is Alice" in result
        assert "Prefers concise answers" in result
        mock_core.recall.assert_called_once()
