"""Skill acceptance engineering-gate metric (P1-T8)."""

import json
from pathlib import Path
from typing import Any

from src.storage.paths import DEFAULT_USER_ID, get_paths

THRESHOLD = 0.70


def calculate_acceptance(review_dir: Path, skills_dir: Path) -> dict[str, Any]:
    """Calculate unedited approvals over all completed human-review outcomes."""
    outcomes: list[dict[str, Any]] = []
    if review_dir.exists():
        for path in sorted(review_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(item, dict) and item.get("status") in {
                "approved",
                "approved_with_edit",
                "rejected",
                "flagged",
            }:
                outcomes.append(item)

    approved = sum(
        1
        for item in outcomes
        if item.get("status") == "approved"
        and (skills_dir / str(item.get("name", "")) / "SKILL.md").is_file()
    )
    total = len(outcomes)
    ratio = approved / total if total else 0.0
    return {"approved": approved, "total": total, "ratio": ratio}


def acceptance_summary(user_id: str = DEFAULT_USER_ID) -> str:
    """Return the one-line acceptance gate result for reports and CLI output."""
    paths = get_paths(user_id)
    metric = calculate_acceptance(
        paths.user_dir / "private" / "review",
        paths.user_skills_dir(),
    )
    pct = metric["ratio"] * 100
    if metric["ratio"] >= THRESHOLD:
        return "acceptance >= 70%: ACCEPTED"
    return f"acceptance >= 70%: FAIL ({pct:.1f}%)"


def main() -> None:
    """Print the engineering gate result."""
    print(acceptance_summary())


if __name__ == "__main__":
    main()
