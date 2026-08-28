"""Knowledge interview loop (P1-T4): gap-report-driven owner interview.

Gaps come from corpus search misses (P1-T1 corpus store). The interview
preserves its state on disk across tool calls and persists a full Q/A
transcript per session under the user's Interviews dir.
"""

import json
from pathlib import Path

import pytest

from src.sdk.tools_core.user_prompt import (
    interview_ask,
    interview_finish,
    interview_start,
)
from src.storage.paths import DataPaths

EIGHT_GAPS = [
    "retainer agreement terms",
    "invoicing schedule",
    "client onboarding checklist",
    "brand voice guidelines",
    "deliverable review process",
    "pricing exceptions policy",
    "contract renewal triggers",
    "meeting cadence preferences",
]


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate user data under tmp_path (data_root AND data_path) — same
    pattern as test_corpus.py so corpus/interview share the data tree."""
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("DEPLOYMENT_DATA_PATH", str(tmp_path / "data"))
    from src.storage import paths as paths_mod

    paths_mod._paths_cache.clear()
    from src.config.settings import reload_settings

    reload_settings()
    yield tmp_path
    paths_mod._paths_cache.clear()


def _start(user_id="interview_user", gaps=None):
    return json.loads(interview_start.invoke(args={"gaps": EIGHT_GAPS if gaps is None else gaps, "user_id": user_id}))


def test_interview_start_creates_question_per_gap(isolated_home):
    res = _start(gaps=["pricing tables", "retainer terms"])
    assert res["questions_total"] == 2
    assert res["question_index"] == 0
    # First question references the gap topic
    assert "pricing tables" in res["question"]


def test_gap_report_yields_at_least_one_question_per_gap(isolated_home):
    res = _start()
    assert res["questions_total"] == len(EIGHT_GAPS) == 8
    # Every gap produces a question — state on disk holds all 8
    dp = DataPaths(user_id="interview_user")
    state = json.loads(
        (dp.interviews_dir() / "active_interview.json").read_text()
    )
    assert len(state["questions"]) == 8
    assert state["questions"] == res["questions"]


def test_ask_records_answer_and_advances(isolated_home):
    _start()
    res = json.loads(
        interview_ask.invoke(
            args={"answer": "We bill monthly, net 30.", "user_id": "interview_user"}
        )
    )
    assert res["answered"] == 1
    assert "question" in res  # next gap question presented


def test_state_survives_across_tool_calls(isolated_home):
    """Ac1/3: state preserved across separate tool invocations (disk-backed)."""
    _start()
    # Re-import fresh bindings = fresh call contexts; state must persist
    from importlib import reload

    import src.sdk.tools_core.user_prompt as interview_mod

    reload(interview_mod)
    res = json.loads(
        interview_mod.interview_ask.invoke(args={"answer": "answer one", "user_id": "interview_user"})
    )
    assert res["answered"] == 1


def test_full_8_question_interview_persists_transcript(isolated_home):
    _start()
    for i in range(8):
        res = json.loads(
            interview_ask.invoke(args={"answer": f"answer {i}", "user_id": "interview_user"})
        )
    assert res["complete"] is True

    fin = json.loads(interview_finish.invoke(args={"user_id": "interview_user"}))
    assert fin["answered"] == 8
    # Transcript persisted under the user's Interviews dir
    transcript = Path(fin["transcript_path"])
    assert transcript.exists()
    data = json.loads(transcript.read_text())
    assert len(data["qa"]) == 8
    assert data["qa"][0]["question"].startswith("Can you")
    assert data["gaps"] == EIGHT_GAPS


def test_transcripts_isolated_per_user(isolated_home, monkeypatch, tmp_path):
    monkeypatch.setenv("DEPLOYMENT_DATA_ROOT", str(tmp_path / "root"))
    from src.storage import paths as paths_mod

    paths_mod._paths_cache.clear()
    interview_start.invoke(args={"gaps": ["gap a"], "user_id": "alice"})
    interview_start.invoke(args={"gaps": ["gap b"], "user_id": "bob"})

    from src.storage.paths import DataPaths

    a = DataPaths(user_id="alice").interviews_dir()
    b = DataPaths(user_id="bob").interviews_dir()
    assert a != b
    assert a.parent.name == "alice" or "alice" in str(a)


def test_ask_without_active_session(isolated_home):
    res = json.loads(interview_ask.invoke(args={"answer": "hi", "user_id": "nobody_active"}))
    assert res.get("error") is True


def test_finish_without_active_session(isolated_home):
    res = json.loads(interview_finish.invoke(args={"user_id": "nobody_active"}))
    assert res.get("error") is True


def test_start_with_empty_gaps_rejected(isolated_home):
    res = _start(gaps=[])
    assert res.get("error") is True


def test_mid_interview_finish_marks_incomplete(isolated_home):
    _start(gaps=["gap one", "gap two"])
    interview_ask.invoke(args={"answer": "answer 1", "user_id": "interview_user"})
    fin = json.loads(interview_finish.invoke(args={"user_id": "interview_user"}))
    assert fin["answered"] == 1
    assert fin["complete"] is False
    assert Path(fin["transcript_path"]).exists()


def test_corpus_miss_feeds_interview_gap(isolated_home):
    """Integration with P1-T1: a corpus search miss becomes an interview gap."""
    from src.sdk.tools_core.corpus import search_corpus

    hits = json.loads(
        search_corpus.invoke(args={"query": "zzz_no_such_topic", "user_id": "interview_user"})
    )
    assert hits["results"] == []
    res = _start(gaps=["zzz_no_such_topic"])
    assert res["questions_total"] == 1
    assert "zzz_no_such_topic" in res["question"]
