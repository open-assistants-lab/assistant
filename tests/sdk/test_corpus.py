"""P1-T1: Ingest corpus — HybridDB-backed reference-knowledge store.

Ground-truth storage for reference knowledge (client records, case
history, price tables), distinct from skills. Read-only-at-ingest
discipline: nothing auto-commits into skills.
"""

import json
from pathlib import Path

import pytest

from src.sdk.tools_core.corpus import index_corpus, search_corpus
from src.storage.corpus import CorpusStore


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate user data under tmp_path (data_root AND data_path)."""
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path / "data"))
    from src.storage import paths as paths_mod

    paths_mod._paths_cache.clear()
    from src.config.settings import reload_settings

    reload_settings()
    yield tmp_path
    paths_mod._paths_cache.clear()
    reload_settings()


@pytest.fixture
def alice_store(isolated_home: Path) -> CorpusStore:
    return CorpusStore(user_id="alice")


_SEED = [
    ("acme/retainer", "The retainer agreement with Acme Corp sets a monthly "
     "fee of $5,000 for strategic advisory work. Invoicing happens on the "
     "first business day of each month."),
    ("acme/scope", "Acme Corp scope change requests require written approval "
     "before work begins. Verbal approvals are not binding."),
    ("pricing/2024", "Standard hourly rate is $220. Rush work bills at 1.5x. "
     "Nonprofit discount is 20 percent."),
]


def _seed(store: CorpusStore) -> None:
    for source, text in _SEED:
        store.index(text=text, source=source)


class TestCorpusStore:
    def test_index_search_roundtrip(self, alice_store: CorpusStore) -> None:
        for source, text in _SEED:
            alice_store.index(text=text, source=source)
        results = alice_store.search("retainer agreement", k=5)
        assert results, "expected at least one hit"
        assert "retainer" in results[0]["text"].lower()

    def test_user_isolation(self, isolated_home: Path) -> None:
        alice = CorpusStore(user_id="alice")
        _seed(alice)
        bob = CorpusStore(user_id="bob")
        # bob indexes different content, sees ONLY his own corpus
        bob.index(text="bob private note about cats", source="bob/1")
        a_hits = alice.search("cats")
        b_hits = bob.search("retainer agreement")
        assert a_hits == []  # alice's store knows nothing of bob's docs
        assert b_hits == []  # bob never sees alice's retainer

    def test_workspace_isolation(self, isolated_home: Path) -> None:
        a1 = CorpusStore(user_id="alice", workspace_id="work")
        a1.index(text="secret project falcon deadline", source="w/1")
        a2 = CorpusStore(user_id="alice", workspace_id="side")
        assert a2.search("falcon") == []

    def test_duplicate_source_idempotent(self, alice_store: CorpusStore) -> None:
        _seed(alice_store)
        _seed(alice_store)
        _seed(alice_store)
        assert alice_store.count() == len(_SEED)

    def test_empty_query_guard(self, alice_store: CorpusStore) -> None:
        with pytest.raises(ValueError, match="empty"):
            alice_store.search("   ")

    def test_confidence_ranking(self, alice_store: CorpusStore) -> None:
        _seed(alice_store)
        results = alice_store.search("retainer agreement", k=5)
        assert len(results) <= 5
        assert results[0]["confidence"] >= 0.5


class TestCorpusTools:
    def test_index_corpus_tool(self, isolated_home: Path) -> None:
        out = index_corpus.invoke(args={"text": _SEED[0][1], "source": "acme/retainer", "user_id": "alice"})
        data = json.loads(out)
        assert data["indexed"] >= 1
        assert data["source"] == "acme/retainer"

    def test_search_returns_snippets_with_source(self, isolated_home: Path) -> None:
        index_corpus.invoke(args={"text": _SEED[0][1], "source": "acme/retainer", "user_id": "alice"})
        out = search_corpus.invoke(args={"query": "retainer agreement", "user_id": "alice", "k": 5})
        data = json.loads(out)
        assert 1 <= len(data["results"]) <= 5
        hit = data["results"][0]
        assert hit["source"] == "acme/retainer"
        assert len(hit["snippet"]) > 0
        assert hit["confidence"] >= 0.5

    def test_search_read_only_annotation(self) -> None:
        anns = search_corpus.annotations
        assert anns is not None and anns.read_only is True
        # index is a write into the corpus store — must not advertise as read-only
        assert index_corpus.annotations is not None
        assert index_corpus.annotations.read_only is False

    def test_search_empty_query_rejected(self, isolated_home: Path) -> None:
        out = search_corpus.invoke(args={"query": "   ", "user_id": "alice"})
        data = json.loads(out)
        assert data["results"] == []

    def test_tool_result_surface_has_no_write_surface(self, isolated_home: Path) -> None:
        """Read-only guarantee: search output exposes no write paths/tools."""
        index_corpus.invoke(args={"text": _SEED[0][1], "source": "acme/retainer", "user_id": "alice"})
        out = search_corpus.invoke(args={"query": "retainer", "user_id": "alice"})
        for phrase in ("files_write", "index_corpus(", "write"):
            assert phrase not in out