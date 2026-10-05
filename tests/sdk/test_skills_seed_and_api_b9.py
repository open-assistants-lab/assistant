"""B9: seed refresh fidelity, logical-name operations, draft validation (#91-#94, #99-#102)."""

from __future__ import annotations

from pathlib import Path

import pytest


def _seed(root: Path, name: str, desc: str = "v1", resources: dict | None = None):
    d = root / name
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nBody\n", encoding="utf-8")
    for rel, content in (resources or {}).items():
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return d


def _registry(user_dir):
    from src.skills.registry import SkillRegistry

    return SkillRegistry(skills_dir=str(user_dir))


class TestSeedRefreshFidelity:
    """#91: refresh missed resource-only changes and overwrote user edits."""

    def test_resource_only_seed_update_is_applied(self, tmp_path, monkeypatch):
        seed_src = tmp_path / "seeds"
        user_dir = tmp_path / "user"
        seed_src.mkdir()
        user_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        # point the registry at a temp "seeds/skills" tree
        (tmp_path / "seeds" / "skills").mkdir(parents=True, exist_ok=True)
        _seed(seed_src / "skills", "demo", desc="v1", resources={"scripts/run.sh": "echo old\n"})
        reg = _registry(user_dir)
        reg._seed_system_skills()
        user_script = user_dir / "demo" / "scripts" / "run.sh"
        assert user_script.read_text() == "echo old\n"

        # resource-only seed change (SKILL.md untouched)
        (seed_src / "skills" / "demo" / "scripts" / "run.sh").write_text("echo new\n", encoding="utf-8")
        reg2 = _registry(user_dir)
        reg2._seed_system_skills()
        assert user_script.read_text() == "echo new\n", "a resource-only seed update was missed"

    def test_user_edited_resource_files_are_never_overwritten(self, tmp_path, monkeypatch):

        seed_src = tmp_path / "seeds"
        user_dir = tmp_path / "user"
        seed_src.mkdir()
        user_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "seeds" / "skills").mkdir(parents=True, exist_ok=True)
        _seed(seed_src / "skills", "demo", desc="v1", resources={"scripts/run.sh": "echo old\n"})
        reg = _registry(user_dir)
        reg._seed_system_skills()
        user_script = user_dir / "demo" / "scripts" / "run.sh"
        user_script.write_text("# MY EDITS\necho mine\n", encoding="utf-8")

        (seed_src / "skills" / "demo" / "scripts" / "run.sh").write_text("echo new\n", encoding="utf-8")
        (seed_src / "skills" / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: v2\n---\nBody v2\n", encoding="utf-8"
        )
        _registry(user_dir)._seed_system_skills()
        assert "# MY EDITS" in user_script.read_text(), "the user-edited resource file was overwritten"

    def test_untouched_skill_still_refreshes_its_skill_md(self, tmp_path, monkeypatch):

        seed_src = tmp_path / "seeds"
        user_dir = tmp_path / "user"
        seed_src.mkdir()
        user_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "seeds" / "skills").mkdir(parents=True, exist_ok=True)
        _seed(seed_src / "skills", "demo", desc="v1")
        _registry(user_dir)._seed_system_skills()
        (seed_src / "skills" / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: v2\n---\nBody v2\n", encoding="utf-8"
        )
        _registry(user_dir)._seed_system_skills()
        assert "description: v2" in (user_dir / "demo" / "SKILL.md").read_text()


class TestDeletedSeedsDoNotReturn:
    """#99: removing a seed upstream brought it back on the next reload."""

    def test_removed_seed_is_deleted_when_untouched(self, tmp_path, monkeypatch):

        seed_src = tmp_path / "seeds"
        user_dir = tmp_path / "user"
        seed_src.mkdir()
        user_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "seeds" / "skills").mkdir(parents=True, exist_ok=True)
        _seed(seed_src / "skills", "keep", desc="v1")
        _seed(seed_src / "skills", "gone", desc="v1")
        _registry(user_dir)._seed_system_skills()
        assert (user_dir / "gone").exists()

        import shutil
        shutil.rmtree(seed_src / "skills" / "gone")
        _registry(user_dir)._seed_system_skills()
        assert not (user_dir / "gone").exists(), "the deleted seed reappeared"
        assert (user_dir / "keep").exists()

    def test_user_edited_removed_seed_is_kept(self, tmp_path, monkeypatch):
        import shutil

        seed_src = tmp_path / "seeds"
        user_dir = tmp_path / "user"
        seed_src.mkdir()
        user_dir.mkdir()
        monkeypatch.chdir(tmp_path)
        (tmp_path / "seeds" / "skills").mkdir(parents=True, exist_ok=True)
        _seed(seed_src / "skills", "gone", desc="v1")
        _registry(user_dir)._seed_system_skills()
        (user_dir / "gone" / "SKILL.md").write_text(
            "---\nname: gone\ndescription: mine\n---\nMy work\n", encoding="utf-8"
        )
        shutil.rmtree(seed_src / "skills" / "gone")
        _registry(user_dir)._seed_system_skills()
        assert (user_dir / "gone").exists(), "a user-edited skill was deleted by seed removal"


class TestLogicalNameOperations:
    """#94: API and drafts address skills by their logical name."""

    @pytest.mark.asyncio
    async def test_api_update_works_when_directory_differs(self, tmp_path, monkeypatch):
        """skills/{dir}/SKILL.md with frontmatter name: demo - PUT /skills/demo works."""
        from starlette.testclient import TestClient

        import src.http.routers.skills as api

        skills_dir = tmp_path / "skills"
        d = skills_dir / "custom-dir"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: demo\ndescription: original\n---\nBody\n", encoding="utf-8"
        )
        monkeypatch.setattr(api, "_skill_dir", lambda user_id: skills_dir)
        monkeypatch.setattr(api, "_get_registry", lambda user, ws=None: _registry(skills_dir))
        monkeypatch.setattr(api, "_reset_user_loops", lambda user: None)
        monkeypatch.setattr(api, "_load_user_caps", lambda user: {})
        monkeypatch.setattr(api, "resolve_user_id", lambda request, uid: uid)
        from src.http.main import app

        with TestClient(app) as client:
            resp = client.put(
                "/skills/demo",
                params={"user_id": "u"},
                json={"description": "updated description"},
            )
        assert resp.status_code == 200, resp.text
        assert "updated description" in (d / "SKILL.md").read_text()

    def test_draft_approval_refuses_a_logical_name_collision(self, tmp_path):
        (tmp_path / "user" / "demo").mkdir(parents=True, exist_ok=True)
        reg = _registry(tmp_path / "user")
        (tmp_path / "user" / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: live\n---\nLive\n", encoding="utf-8"
        )
        draft = reg._draft_dir("fresh-draft")
        draft.mkdir(parents=True)
        (draft / "SKILL.md").write_text(
            "---\nname: demo\ndescription: collision attempt\n---\nNew\n", encoding="utf-8"
        )
        with pytest.raises(FileExistsError):
            reg.approve_skill_draft("fresh-draft")


class TestInvalidDraftsAreNotPromoted:
    """#101: a draft with no description was promoted and became unloadable."""

    def test_draft_without_description_is_refused(self, tmp_path):
        reg = _registry(tmp_path / "user")
        (tmp_path / "user").mkdir(parents=True, exist_ok=True)
        draft = reg._draft_dir("broken")
        draft.mkdir(parents=True)
        (draft / "SKILL.md").write_text("---\nname: broken\n---\nnot a skill\n", encoding="utf-8")
        with pytest.raises(ValueError):
            reg.approve_skill_draft("broken")
        assert not (tmp_path / "user" / "broken").exists(), (
            "the invalid draft was moved into the live catalog"
        )


class TestBundledEvalScriptStarts:
    """#102: run_eval.py imported a deleted path."""

    def test_run_eval_import_resolves(self):
        import importlib.util

        script = Path("seeds/skills/skill-creation/scripts/run_eval.py").resolve()
        assert script.exists(), "the bundled run_eval.py is missing"
        # Importing it executes the import block that used to fail.
        spec = importlib.util.spec_from_file_location("run_eval_probe", script)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except SystemExit:
            pass  # argparse with no args exits; the import succeeded
        except ModuleNotFoundError as exc:  # pragma: no cover - the reported bug
            pytest.fail(f"run_eval.py still imports a deleted path: {exc}")
