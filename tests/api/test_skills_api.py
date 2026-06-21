"""Skills API tests."""

import warnings
from pathlib import Path

import pytest


class TempPaths:
    def __init__(self, user_skills: Path, workspace_skills: Path, base_path: Path):
        self._user_skills = user_skills
        self._workspace_skills = workspace_skills
        self.base = base_path

    def skills_dir(self) -> Path:
        warnings.warn("skills_dir() deprecated, use user_skills_dir()")
        return self.user_skills_dir()

    def user_skills_dir(self) -> Path:
        self._user_skills.mkdir(parents=True, exist_ok=True)
        return self._user_skills

    def workspace_skills_dir(self) -> Path:
        self._workspace_skills.mkdir(parents=True, exist_ok=True)
        return self._workspace_skills


def write_skill(
    root: Path,
    name: str,
    description: str,
    body: str = "Body",
    extra_frontmatter: str = "",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}metadata:\n"
        "  disable_model_invocation: true\n---\n"
        f"# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def skill_api_tmp(tmp_path, monkeypatch):
    from src.http.routers import skills as skills_router
    from src.skills.registry import SkillRegistry

    user_root = tmp_path / "user-skills"
    workspace_root = tmp_path / "workspace-skills"
    registries = {}

    def fake_get_paths(user_id=None, **kwargs):
        return TempPaths(user_root, workspace_root, tmp_path / "item-scopes")

    def fake_get_skill_registry(user_id="default_user"):
        key = (user_id,)
        if key not in registries:
            registries[key] = SkillRegistry(
                skills_dir=user_root,
                user_id=user_id,
            )
        return registries[key]

    monkeypatch.setattr(skills_router, "get_paths", fake_get_paths)
    monkeypatch.setattr(skills_router, "get_skill_registry", fake_get_skill_registry)
    return user_root, workspace_root


def test_list_returns_user_and_workspace_skills_with_scope_fields(client, skill_api_tmp):
    user_root, workspace_root = skill_api_tmp
    write_skill(user_root, "user-skill", "User skill")
    write_skill(workspace_root, "workspace-skill", "Workspace skill")

    r = client.get("/skills", params={"user_id": "u1"})

    assert r.status_code == 200
    skills = {skill["name"]: skill for skill in r.json()["skills"]}
    assert skills["user-skill"] == {
        "name": "user-skill",
        "description": "User skill",
        "is_loaded": False,
        "disable_model_invocation": True,
    }
    assert "is_system" not in skills["user-skill"]


def test_detail_returns_full_content_and_metadata(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    write_skill(user_root, "detail-skill", "Detail skill", "Detailed instructions")

    r = client.get(
        "/skills/detail-skill",
        params={"user_id": "u1"},
    )

    data = r.json()
    assert r.status_code == 200
    assert data["name"] == "detail-skill"
    assert "Detailed instructions" in data["content"]
    assert data["disable_model_invocation"] is True


def test_detail_includes_supported_frontmatter_fields(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    write_skill(
        user_root,
        "detail-frontmatter",
        "Detail frontmatter",
        "Detailed instructions",
        extra_frontmatter="license: MIT\ncompatibility: '>=1.0'\n",
    )

    r = client.get(
        "/skills/detail-frontmatter",
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    data = r.json()
    assert data["license"] == "MIT"
    assert data["compatibility"] == ">=1.0"
    assert data["frontmatter"]["license"] == "MIT"
    assert data["frontmatter"]["compatibility"] == ">=1.0"


def test_detail_missing_returns_404(client, skill_api_tmp):
    r = client.get("/skills/missing", params={"user_id": "u1"})

    assert r.status_code == 404


def test_create_workspace_skill_writes_to_user_scope(client, skill_api_tmp):
    user_root, _ = skill_api_tmp

    r = client.post(
        "/skills",
        json={
            "name": "created-skill",
            "description": "Created skill",
            "content": "# Created\n\nInstructions",
            "scope": "workspace",
        },
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    assert (user_root / "created-skill" / "SKILL.md").exists()
    data = r.json()
    assert data["name"] == "created-skill"
    assert data["description"] == "Created skill"


def test_create_skill_resets_user_loops(client, skill_api_tmp, monkeypatch):
    from src.sdk import runner

    reset_calls = []
    monkeypatch.setattr(runner, "reset_user_sdk_loops", lambda user_id: reset_calls.append(user_id))

    r = client.post(
        "/skills",
        json={
            "name": "created-reset",
            "description": "Created reset",
            "content": "# Created\n\nInstructions",
            "scope": "workspace",
        },
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    assert reset_calls == ["u1"]


def test_update_skill_content_updates_file_and_returns_detail(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    write_skill(user_root, "update-skill", "Original", "Old content")

    r = client.put(
        "/skills/update-skill",
        json={"content": "# Updated\n\nNew content", "scope": "workspace"},
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "update-skill"
    assert data["description"] == "Original"
    assert data["disable_model_invocation"] is True
    assert "New content" in data["content"]
    assert "New content" in (user_root / "update-skill" / "SKILL.md").read_text()


def test_update_skill_resets_user_loops(client, skill_api_tmp, monkeypatch):
    from src.sdk import runner

    user_root, _ = skill_api_tmp
    write_skill(user_root, "update-reset", "Original", "Old content")
    reset_calls = []
    monkeypatch.setattr(runner, "reset_user_sdk_loops", lambda user_id: reset_calls.append(user_id))

    r = client.put(
        "/skills/update-reset",
        json={"content": "# Updated\n\nNew content", "scope": "workspace"},
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    assert reset_calls == ["u1"]


def test_delete_skill_removes_scoped_dir(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    skill_dir = write_skill(user_root, "delete-skill", "Delete skill")

    r = client.delete(
        "/skills/delete-skill",
        params={"user_id": "u1", "scope": "workspace"},
    )

    assert r.status_code == 200
    assert r.json()["status"] == "deleted"
    assert not skill_dir.exists()


def test_delete_skill_resets_user_loops(client, skill_api_tmp, monkeypatch):
    from src.sdk import runner

    user_root, _ = skill_api_tmp
    write_skill(user_root, "delete-reset", "Delete reset")
    reset_calls = []
    monkeypatch.setattr(runner, "reset_user_sdk_loops", lambda user_id: reset_calls.append(user_id))

    r = client.delete(
        "/skills/delete-reset",
        params={"user_id": "u1", "scope": "workspace"},
    )

    assert r.status_code == 200
    assert reset_calls == ["u1"]


def test_create_skill_ignores_deprecated_scope_field(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    r = client.post(
        "/skills",
        json={
            "name": "deprecated-scope",
            "description": "Deprecated scope",
            "content": "Content",
            "scope": "team",
        },
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    assert (user_root / "deprecated-scope" / "SKILL.md").exists()


def test_invalid_skill_name_returns_400(client, skill_api_tmp):
    r = client.post(
        "/skills",
        json={
            "name": "../bad",
            "description": "Bad name",
            "content": "Content",
            "scope": "user",
        },
        params={"user_id": "u1"},
    )

    assert r.status_code == 400


def test_invalid_user_id_is_rejected_before_registry_or_paths(client, monkeypatch):
    from src.http.routers import skills as skills_router

    def fail_get_skill_registry(**kwargs):
        raise AssertionError("registry should not be constructed")

    def fail_get_paths(*args, **kwargs):
        raise AssertionError("paths should not be constructed")

    monkeypatch.setattr(skills_router, "get_skill_registry", fail_get_skill_registry)
    monkeypatch.setattr(skills_router, "get_paths", fail_get_paths)

    r = client.post(
        "/skills",
        json={
            "name": "no-registry",
            "description": "No registry",
            "content": "Content",
            "scope": "user",
        },
        params={"user_id": "../../bad"},
    )

    assert r.status_code == 400


def test_create_duplicate_skill_returns_409(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    write_skill(user_root, "duplicate-skill", "Original", "Original content")

    r = client.post(
        "/skills",
        json={
            "name": "duplicate-skill",
            "description": "Duplicate",
            "content": "New content",
            "scope": "workspace",
        },
        params={"user_id": "u1"},
    )

    assert r.status_code == 409
    assert "Original content" in (user_root / "duplicate-skill" / "SKILL.md").read_text()


def test_create_blank_description_returns_400_without_file(client, skill_api_tmp):
    user_root, _ = skill_api_tmp

    r = client.post(
        "/skills",
        json={
            "name": "blank-description",
            "description": "   ",
            "content": "Content",
            "scope": "workspace",
        },
        params={"user_id": "u1"},
    )

    assert r.status_code == 400
    assert not (user_root / "blank-description" / "SKILL.md").exists()


def test_update_blank_description_returns_400_without_changing_file(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    write_skill(user_root, "no-blank-update", "Original", "Original content")
    skill_file = user_root / "no-blank-update" / "SKILL.md"
    original = skill_file.read_text()

    r = client.put(
        "/skills/no-blank-update",
        json={"description": "   ", "scope": "workspace"},
        params={"user_id": "u1"},
    )

    assert r.status_code == 400
    assert skill_file.read_text() == original


def test_update_picks_user_skill_when_both_exist(client, skill_api_tmp):
    user_root, workspace_root = skill_api_tmp
    write_skill(user_root, "shared-skill", "User original", "User old")
    write_skill(workspace_root, "shared-skill", "Workspace original", "Workspace old")

    r = client.put(
        "/skills/shared-skill",
        json={"description": "User updated", "content": "User new", "scope": "user"},
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    assert r.json()["description"] == "User updated"
    assert "User new" in (user_root / "shared-skill" / "SKILL.md").read_text()
    assert "Workspace old" in (workspace_root / "shared-skill" / "SKILL.md").read_text()


def test_update_preserves_supported_top_level_frontmatter(client, skill_api_tmp):
    user_root, _ = skill_api_tmp
    write_skill(
        user_root,
        "frontmatter-skill",
        "Original",
        "Old content",
        extra_frontmatter=(
            "license: MIT\n"
            "compatibility: '>=1.0'\n"
            "allowed-tools: shell_execute\n"
        ),
    )

    r = client.put(
        "/skills/frontmatter-skill",
        json={"description": "Updated", "scope": "workspace"},
        params={"user_id": "u1"},
    )

    assert r.status_code == 200
    updated = (user_root / "frontmatter-skill" / "SKILL.md").read_text()
    assert "description: Updated" in updated
    assert "license: MIT" in updated
    assert "compatibility: '>=1.0'" in updated
    assert "allowed-tools: shell_execute" in updated
