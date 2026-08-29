"""Review queue API and skill-acceptance metric tests (P1-T8)."""

import json
from pathlib import Path

from src.skills.registry import get_skill_registry, reset_skill_registries
from src.storage.paths import get_paths
from tests.evaluation.skill_acceptance import calculate_acceptance


def _content(name: str, body: str = "Original") -> str:
    return f"---\nname: {name}\ndescription: Review fixture\n---\n\n# {body}\n"


def _registry(user_id: str):
    reset_skill_registries()
    return get_skill_registry(user_id)


def test_list_returns_pending_drafts_with_metadata(client, test_user_id):
    registry = _registry(test_user_id)
    registry.put_skill_draft("pending-one", _content("pending-one"), source="fixture")

    response = client.get("/review/drafts", params={"user_id": test_user_id})

    assert response.status_code == 200
    draft = response.json()["drafts"][0]
    assert draft["name"] == "pending-one"
    assert draft["metadata"]["source"] == "fixture"
    assert "drafted_at" in draft["metadata"]


def test_approve_promotes_draft_and_sets_status(client, test_user_id):
    registry = _registry(test_user_id)
    registry.put_skill_draft("approve-me", _content("approve-me"))

    response = client.post(
        "/review/drafts/approve-me/approve", params={"user_id": test_user_id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert registry.get_skill("approve-me") is not None
    outcome = get_paths(test_user_id).user_dir / "private/review/approve-me.json"
    assert json.loads(outcome.read_text())["status"] == "approved"


def test_revise_then_approve_marks_approved_with_edit(client, test_user_id):
    registry = _registry(test_user_id)
    registry.put_skill_draft("revise-me", _content("revise-me"), source="original")
    revised = _content("revise-me", "Human revision")

    response = client.post(
        "/review/drafts/revise-me/revise",
        params={"user_id": test_user_id},
        json={"content": revised},
    )
    assert response.status_code == 200
    assert (registry.drafts_dir / "revise-me/SKILL.md").read_text() == revised

    approved = client.post(
        "/review/drafts/revise-me/approve", params={"user_id": test_user_id}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved_with_edit"


def test_flag_sets_status_and_removes_pending_draft(client, test_user_id):
    registry = _registry(test_user_id)
    registry.put_skill_draft("flag-me", _content("flag-me"))

    response = client.post("/review/drafts/flag-me/flag", params={"user_id": test_user_id})

    assert response.status_code == 200
    assert response.json()["status"] == "flagged"
    assert registry.get_skill_draft("flag-me") is None


def test_approve_all_empties_queue_without_orphans(client, test_user_id):
    registry = _registry(test_user_id)
    for name in ("draft-a", "draft-b", "draft-c"):
        registry.put_skill_draft(name, _content(name))
        assert client.post(
            f"/review/drafts/{name}/approve", params={"user_id": test_user_id}
        ).status_code == 200

    assert client.get("/review/drafts", params={"user_id": test_user_id}).json() == {"drafts": []}
    assert not registry.drafts_dir.exists() or not any(registry.drafts_dir.iterdir())


def test_traversal_name_has_no_filesystem_effects(client, test_user_id):
    registry = _registry(test_user_id)
    registry.put_skill_draft("safe-draft", _content("safe-draft"))
    before = sorted(str(path.relative_to(registry.skills_dir.parent)) for path in registry.skills_dir.parent.rglob("*"))

    response = client.post(
        "/review/drafts/%2E%2E%2Fsafe-draft/approve", params={"user_id": test_user_id}
    )

    assert 400 <= response.status_code < 500
    after = sorted(str(path.relative_to(registry.skills_dir.parent)) for path in registry.skills_dir.parent.rglob("*"))
    assert after == before


def test_revised_then_approved_lowers_acceptance_ratio(tmp_path: Path):
    review = tmp_path / "review"
    skills = tmp_path / "Skills"
    review.mkdir()
    for name, status in (("plain", "approved"), ("edited", "approved_with_edit")):
        (review / f"{name}.json").write_text(json.dumps({"name": name, "status": status}))
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(_content(name))

    metric = calculate_acceptance(review, skills)

    assert metric == {"approved": 1, "total": 2, "ratio": 0.5}
