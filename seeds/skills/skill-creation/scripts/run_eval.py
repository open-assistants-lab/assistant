#!/usr/bin/env python3
"""Run trigger evaluation for a skill description."""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

# Change to project root (5 levels up: scripts -> skill-creator -> skills_seed -> src -> project_root)  # noqa: E402
project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

spec = importlib.util.spec_from_file_location(
    "utils", project_root / "src/skills_seed/skill-creator/scripts/utils.py"
)
utils = importlib.util.module_from_spec(spec)
spec.loader.exec_module(utils)
parse_skill_md = utils.parse_skill_md


def run_single_query(query, skill_name, description=None, user_id="default_user", skill_path=None):
    user_skills_dir = f"data/users/{user_id}/skills"

    try:
        # Use provided skill_path or search in default locations
        if skill_path:
            skill_file = Path(skill_path) / "SKILL.md"
        else:
            skill_file = Path(f"{user_skills_dir}/{skill_name}/SKILL.md")
            if not skill_file.exists():
                skill_file = Path(f"src/skills_seed/{skill_name}/SKILL.md")

        if not skill_file.exists():
            return {"query": query, "triggered": False, "error": f"Skill not found: {skill_file}"}

        # Parse skill file directly to get triggers
        from src.skills.models import parse_skill_file

        skill = parse_skill_file(skill_file)

        if not skill:
            return {"query": query, "triggered": False, "error": "Failed to parse skill file"}

        skill_name_lower = skill.get("name", "").lower()
        # Use passed description if available, otherwise read from skill file
        skill_desc = description.lower() if description else skill.get("description", "").lower()

        query_lower = query.lower()
        trigger_keywords = []

        # Add skill name
        for word in skill_name_lower.split():
            if len(word) > 2:
                trigger_keywords.append(word)

        # Filter common words
        common_words = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "but",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "shall",
            "can",
            "need",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "under",
            "again",
            "further",
            "then",
            "once",
        }

        for word in skill_desc.split():
            word = word.strip(".,!?;:()[]{}").lower()
            if len(word) > 3 and word not in common_words:
                trigger_keywords.append(word)

        matches = [kw for kw in trigger_keywords if kw in query_lower]
        triggered = len(matches) >= 1

    except Exception as e:
        return {"query": query, "triggered": False, "error": str(e)}

    return {"query": query, "triggered": triggered, "error": None}


def run_eval(
    eval_set,
    skill_name,
    description,
    user_id="default_user",
    num_workers=1,
    timeout=30,
    model_name=None,
    runs_per_query=1,
    trigger_threshold=0.5,
    skill_path=None,
):
    results = []

    for item in eval_set:
        query = item["query"]
        result = run_single_query(query, skill_name, description, user_id, skill_path)

        trigger_rate = 1.0 if result.get("triggered") else 0.0
        should_trigger = item.get("should_trigger", False)

        if should_trigger:
            did_pass = trigger_rate >= 0.5
        else:
            did_pass = trigger_rate < 0.5

        results.append(
            {
                "query": query,
                "should_trigger": should_trigger,
                "trigger_rate": trigger_rate,
                "triggers": 1 if result.get("triggered") else 0,
                "runs": 1,
                "pass": did_pass,
            }
        )

    passed = sum(1 for r in results if r["pass"])
    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", required=True)
    parser.add_argument("--skill-path", required=True)
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    name, description, _ = parse_skill_md(Path(args.skill_path))

    output = run_eval(eval_set, name, description, args.user_id)

    if args.verbose:
        print(
            f"Results: {output['summary']['passed']}/{output['summary']['total']} passed",
            file=sys.stderr,
        )
        for r in output["results"]:
            status = "PASS" if r["pass"] else "FAIL"
            print(
                f"  [{status}] expected={r['should_trigger']}: {r['query'][:60]}", file=sys.stderr
            )

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
