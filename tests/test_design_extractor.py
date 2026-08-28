"""P1-T6: design-system extractor — URL -> look & feel -> SKILL.md draft.

The draft goes to the skill review queue and NEVER enters get_loaded_skills()
(or the scanned skills dir) until a human approves it. After approval the
skill loads through the normal registry path (skills_load-able).
"""

import http.server
import threading
from pathlib import Path

import pytest

FIXTURE_CSS = """
:root {
  --brand: #1a5fb4;
  --alert: #e01b24;
  --cream: #f6f5f4;
  --ink: #241f31;
}
body {
  margin: 0;
  padding: 24px;
  font-family: 'Inter', 'Helvetica Neue', sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: var(--ink);
  background: var(--cream);
}
h1 { font-size: 32px; color: var(--brand); }
.card {
  padding: 16px 32px;
  margin-top: 48px;
  border-radius: 12px;
  border: 1px solid var(--ink);
  background: #ffffff;
}
.btn {
  padding: 8px;
  border-radius: 4px;
  background: var(--brand);
  color: #ffffff;
}
"""

FIXTURE_INDEX = """<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="/static/styles.css">
  <style>.inline-note { margin-bottom: 8px; color: #e01b24; }</style>
  <title>Agency Home</title>
</head>
<body>
  <h1>Welcome</h1>
  <div class="card">Card copy</div>
  <button class="btn">Go</button>
</body>
</html>
"""

FIXTURE_ABOUT = """<!DOCTYPE html>
<html><head><link rel="stylesheet" href="/static/styles.css"></head>
<body><h1>About</h1><p>More copy with padding: 24px in an inline attr.</p></body></html>
"""


@pytest.fixture()
def fixture_site(tmp_path: Path):
    """Serve a 3-file site (2 HTML + 1 CSS) over localhost HTTP."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "styles.css").write_text(FIXTURE_CSS, encoding="utf-8")
    (tmp_path / "index.html").write_text(FIXTURE_INDEX, encoding="utf-8")
    (tmp_path / "about.html").write_text(FIXTURE_ABOUT, encoding="utf-8")

    import functools

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)
    )

    class QuietServer(http.server.ThreadingHTTPServer):
        def __init__(self):
            super().__init__(("127.0.0.1", 0), handler)

        def log_message(self, *args: object) -> None:  # silence test noise
            pass

    server = QuietServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    thread.join(timeout=5)


def _registry(tmp_path: Path):
    from src.skills.registry import SkillRegistry

    return SkillRegistry(skills_dir=tmp_path / "Skills")


def test_extract_drafts_skill_with_named_tokens(fixture_site: str, tmp_path: Path):
    """Design-system SKILL.md draft with >=5 named color/type/spacing tokens."""
    from src.sdk.tools_core.design_extractor import design_extract

    reg = _registry(tmp_path)
    result = design_extract.function(fixture_site, registry=reg)

    assert result.is_error is False
    draft_path = Path(result.structured_content["draft_path"])  # type: ignore[index]
    assert draft_path.exists()

    content = draft_path.read_text(encoding="utf-8")
    # Named tokens: CSS custom property definitions like `--color-primary: #...`
    token_lines = [
        line
        for line in content.splitlines()
        if line.strip().startswith("--") and ":" in line
    ]
    assert len(token_lines) >= 5, f"expected >=5 named tokens, got {token_lines}"

    # Token categories covered: color, type, spacing
    names = " ".join(token_lines)
    assert "--color-" in names
    assert "--font-" in names
    assert "--space-" in names


def test_draft_has_parseable_frontmatter(fixture_site: str, tmp_path: Path):
    """Frontmatter (name + description) parses via the skills registry parser."""
    from src.sdk.tools_core.design_extractor import design_extract
    from src.skills.models import parse_skill_file

    reg = _registry(tmp_path)
    result = design_extract.function(fixture_site, registry=reg)
    draft_path = Path(result.structured_content["draft_path"])  # type: ignore[index]

    skill = parse_skill_file(draft_path)
    assert skill is not None, "draft SKILL.md must parse cleanly"
    name = skill["name"]
    assert name and name.strip()
    assert skill["description"] and skill["description"].strip()
    # Name passes the Agent Skills spec (lowercase a-z 0-9 hyphens)
    import re as _re

    assert _re.fullmatch(r"[a-z0-9-]+", name)
    assert "design" in name


def test_draft_hidden_until_approved_then_skills_loadable(
    fixture_site: str, tmp_path: Path
):
    """Auto-draft never enters the loaded/available skills until approved."""
    from src.sdk.tools_core.design_extractor import design_extract

    reg = _registry(tmp_path)
    before_available = set(reg.get_all_skills() and [s["name"] for s in reg.get_all_skills()])

    result = design_extract.function(fixture_site, registry=reg)
    name = result.structured_content["draft_name"]  # type: ignore[index]

    # Draft invisible to the scanned skills list and the loaded set.
    after_available = [s["name"] for s in reg.get_all_skills()]
    assert name not in after_available
    assert name not in reg.get_loaded_skills()
    assert sorted(after_available) == sorted(before_available)
    assert reg.get_skill(name) is None

    # Human review: approve -> draft enters the skills dir and loads.
    approved_path = reg.approve_skill_draft(name)
    assert approved_path.exists()
    loaded = reg.get_skill(name)
    assert loaded is not None
    assert loaded["name"] == name

    # skills_load-able: registry can resolve it for the loader.
    reg.mark_skill_loaded(name)
    assert name in reg.get_loaded_skills()

    # Draft queue no longer holds it.
    assert reg.get_skill_draft(name) is None


def test_review_queue_roundtrip(tmp_path: Path):
    """put/list/get draft + reject flow without any design extraction."""
    reg = _registry(tmp_path)
    assert reg.list_skill_drafts() == []

    reg.put_skill_draft(
        "acme-design",
        "---\nname: acme-design\ndescription: draft skill\n---\nbody",
source="https://acme.test",
    )
    drafts = reg.list_skill_drafts()
    assert [d["name"] for d in drafts] == ["acme-design"]
    assert drafts[0]["source"] == "https://acme.test"

    parsed = reg.get_skill_draft("acme-design")
    assert parsed is not None
    assert parsed["description"] == "draft skill"

    reg.reject_skill_draft("acme-design")
    assert reg.list_skill_drafts() == []
    assert reg.get_skill_draft("acme-design") is None


def test_approve_missing_and_duplicate_guards(tmp_path: Path):
    reg = _registry(tmp_path)
    with pytest.raises(FileNotFoundError):
        reg.approve_skill_draft("missing-skill")

    marker_content = (
        "---\nname: acme-design\ndescription: live copy\n---\nb"
    )
    reg.put_skill_draft("acme-design", marker_content, source="")
    # Simulate a live skill already at the destination (customized copy).
    assert reg.get_skill_draft("acme-design") is not None
    reg.reject_skill_draft("acme-design")
    reg.put_skill_draft("acme-design", marker_content, source="")
    target = tmp_path / "Skills" / "acme-design" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nname: acme-design\ndescription: live copy\n---\nb", encoding="utf-8")

    with pytest.raises(FileExistsError):
        reg.approve_skill_draft("acme-design")


def test_extract_fetch_failure_surfaces_cleanly(tmp_path: Path):
    from src.sdk.tools_core.design_extractor import design_extract

    reg = _registry(tmp_path)
    result = design_extract.function("http://127.0.0.1:1/", registry=reg)
    assert result.is_error is True
    assert "fetch" in result.content.lower() or "unreachable" in result.content.lower()
