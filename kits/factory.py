#!/usr/bin/env python3
"""Kit factory — stamp a new vertical kit from the _template conventions.

Verticals are content, not code: this script (plus the _template dir) is the
only engine-side artifact a partner needs. Usage:

    uv run python kits/factory.py --name recruiting \
        --description "Recruiting coordination methodology" \
        --persona "Recruiting coordinator playbook agent"

Creates kits/<name>/ with kit.yaml, PROFILE.md, 2 skill stubs, a rubric,
and eval/personas.yaml. Never touches src/.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_TEMPLATE = Path(__file__).parent / "_template"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        raise SystemExit("name must contain [a-z0-9] characters")
    return slug


def stamp_kit(name: str, description: str, persona: str, kits_root: Path) -> Path:
    """Author a new vertical kit into kits_root/name from _template conventions."""
    kit_dir = kits_root / name
    if kit_dir.exists():
        raise SystemExit(f"kit {name!r} already exists at {kit_dir}")

    (kit_dir / "skills").mkdir(parents=True)
    (kit_dir / "rubrics").mkdir()
    (kit_dir / "eval").mkdir()

    (kit_dir / "kit.yaml").write_text(
        f"name: {name}\ndescription: {description}\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (kit_dir / "PROFILE.md").write_text(
        f'---\nname: {name}\ndescription: {persona}\nmodel: ""\n'
        "tools: [memory_profile]\n---\n\n"
        f"You are the {description} assistant. Use saved methodology skills; "
        "never invent domain facts — index them in the corpus instead.\n",
        encoding="utf-8",
    )
    template_skills = _TEMPLATE / "skills"
    if template_skills.is_dir():
        for skill_stub in sorted(p for p in template_skills.iterdir() if p.is_dir()):
            (kit_dir / "skills" / skill_stub.name).mkdir()
            src = skill_stub / "SKILL.md"
            if src.exists():
                (kit_dir / "skills" / skill_stub.name / "SKILL.md").write_text(
                    src.read_text(encoding="utf-8").replace("{{name}}", name), encoding="utf-8"
                )
    (kit_dir / "rubrics" / "engagement-rubric.md").write_text(
        f"# {description.title()} Engagement Rubric\n\n"
        "- Response cites the applicable playbook step, not generic advice.\n"
        "- No invented domain facts, commitments, or figures.\n"
        "- Tone matches persona style.\n",
        encoding="utf-8",
    )
    (kit_dir / "eval" / "personas.yaml").write_text(
        "personas:\n"
        "  - id: p1\n"
        "    name: Primary Persona\n"
        "    style: detailed\n"
        f"    description: {persona}\n"
        "    sample_phrases:\n"
        "      - walk me through the playbook\n",
        encoding="utf-8",
    )
    return kit_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="Stamp a new vertical kit (content only).")
    ap.add_argument("--name", required=True, help="kit slug, e.g. recruiting")
    ap.add_argument("--description", required=True, help="one-line methodology description")
    ap.add_argument("--persona", required=True, help="persona description for PROFILE.md")
    ap.add_argument("--kits-root", default=str(Path(__file__).parent))
    args = ap.parse_args()
    name = _slug(args.name)
    kit_dir = stamp_kit(name, args.description, args.persona, Path(args.kits_root))

    from src.sdk.kit import kit_validate

    problems = kit_validate(kit_dir)
    if problems:
        raise SystemExit(f"stamped kit failed validation: {problems}")
    print(f"kit stamped + valid: {kit_dir}")


if __name__ == "__main__":
    main()
