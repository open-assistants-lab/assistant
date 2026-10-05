"""B9: skills discovery must isolate bad skills, not collapse the catalog (#95-#98)."""

from __future__ import annotations

from src.skills.storage import SkillStorage


def _write(base, name: str, front: str, body: str = "Body text\n"):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    return d


class TestOneBadSkillDoesNotBreakDiscovery:
    """#96: one unreadable SKILL.md aborted discovery of every skill."""

    def test_undecodable_skill_is_skipped_others_survive(self, tmp_path):
        base = tmp_path / "skills"
        _write(base, "good_one", "name: good-one\ndescription: fine\n")
        bad = base / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_bytes(b"valid start\n---\n\xff\xfe not utf-8\n")

        storage = SkillStorage(base)
        skills, diagnostics = storage.load_skills_with_diagnostics()

        names = [s["name"] for s in skills]
        assert "good-one" in names, f"one undecodable file emptied the catalog: {names}"
        assert diagnostics, "the skipped file produced no diagnostic"

    def test_unreadable_skill_is_skipped_others_survive(self, tmp_path, monkeypatch):
        base = tmp_path / "skills"
        _write(base, "good_two", "name: good-two\ndescription: fine\n")
        _write(base, "other", "name: other\ndescription: fine\n")
        _write(base, "broken", "name: broken\ndescription: x\n")
        import pathlib as _pathlib

        real_read = _pathlib.Path.read_text

        def selective_read(self, *a, **k):
            if self.name == "SKILL.md" and "broken" in str(self.parent):
                raise OSError("permission denied")
            return real_read(self, *a, **k)

        monkeypatch.setattr("pathlib.Path.read_text", selective_read)
        storage = SkillStorage(base)
        skills, diagnostics = storage.load_skills_with_diagnostics()
        names = [s["name"] for s in skills]
        assert "good-two" in names and "other" in names, names
        assert any("broken" in str(d) for d in diagnostics), diagnostics


class TestMalformedMetadataIsIsolated:
    """#95: a wrong-typed metadata value must not poison the catalog."""

    def test_non_string_name_falls_back_and_catalog_survives(self, tmp_path):
        base = tmp_path / "skills"
        _write(base, "listy", "name: [a, b]\ndescription: fine\n")
        _write(base, "fine", "name: fine\ndescription: fine\n")
        storage = SkillStorage(base)
        skills, diagnostics = storage.load_skills_with_diagnostics()
        names = [s["name"] for s in skills]
        assert "fine" in names, names
        assert all(isinstance(s["name"], str) and s["name"] for s in skills), names
        assert all(isinstance(s["description"], str) for s in skills), skills

    def test_registry_metadata_assignment_never_raises(self, tmp_path, monkeypatch):
        """scope/workspace_id assignment happens over every discovered skill."""
        import src.skills.registry as reg_mod

        registry = reg_mod.SkillRegistry(skills_dir=str(tmp_path / "skills"))
        base = tmp_path / "skills"
        base.mkdir(parents=True, exist_ok=True)
        _write(base, "ok", "name: ok\ndescription: fine\n")
        registry.skills_dir = base
        monkeypatch.setattr(registry, "_seed_system_skills", lambda: None)
        catalog = registry.get_all_skills()
        assert [s["name"] for s in catalog] == ["ok"], catalog
        assert catalog[0]["metadata"]["scope"] == "user"


class TestCatalogEntriesAreLoadable:
    """#97: a catalog entry whose name cannot be loaded is a dead entry."""

    def test_fallback_name_is_sanitized_to_a_loadable_form(self, tmp_path):
        base = tmp_path / "skills"
        # Directory name is fine, frontmatter name is invalid -> falls back
        # to the directory name; that must be loadable by name.
        d = base / "my_skill"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: 'Bad Name!!'\ndescription: fine\n---\nBody\n", encoding="utf-8"
        )
        storage = SkillStorage(base)
        skills, _ = storage.load_skills_with_diagnostics()
        assert skills, "the skill vanished"
        name = skills[0]["name"]
        loaded = storage.load_skill(name)
        assert loaded is not None, f"catalog listed {name!r} but load_skill rejected it"

    def test_catalog_entry_can_always_be_loaded_by_its_name(self, tmp_path):
        base = tmp_path / "skills"
        _write(base, "a_skill", "name: 'NOPE!'\ndescription: d\n")
        _write(base, "B-Ok", "name: b-ok\ndescription: d\n")
        storage = SkillStorage(base)
        for s in storage.load_skills():
            assert storage.load_skill(s["name"]) is not None, s["name"]


class TestOversizedDescriptionDoesNotHideTheCatalog:
    """#98: one over-budget entry hid every later skill."""

    def test_over_budget_entry_is_skipped_not_fatal(self, monkeypatch):
        """A giant first description must not hide every later skill (#98)."""
        import src.sdk.runner as runner_mod

        big = "x" * 5000
        skills = [
            {"name": "huge", "description": big, "metadata": {}},
            {"name": "small", "description": "tiny", "metadata": {}},
        ]

        class _Registry:
            def get_all_skills(self):
                return skills

            def get_load_count(self, name):
                return 0

        import src.skills.registry as reg_mod

        monkeypatch.setattr(reg_mod, "get_skill_registry", lambda **kw: _Registry())
        section = runner_mod._get_skills_context("u")
        assert "small" in section, "the over-budget entry hid the later skills"
        assert "huge" not in section, "the oversized entry should have been dropped"
