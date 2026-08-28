"""User prompt tools — per-user custom instructions across all workspaces."""

import json
from datetime import UTC, datetime
from typing import Any

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.sdk.user_prompt import load_user_prompt, save_user_prompt
from src.storage.paths import DEFAULT_USER_ID

logger = get_logger()


@tool
def user_prompt_get(user_id: str =  DEFAULT_USER_ID) -> str:
    """Get the current user's custom prompt.

    Returns the prompt if set, or a message saying none is configured.

    Args:
        user_id: User identifier (injected automatically)

    Returns:
        User prompt text or empty notice
    """
    prompt = load_user_prompt(user_id)
    if not prompt:
        return "No custom prompt configured for this user."
    return prompt


user_prompt_get.annotations = ToolAnnotations(
    title="Get User Prompt", read_only=True, idempotent=True
)


@tool
def user_prompt_set(prompt: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Set the user's custom prompt (persistent instructions for all workspaces).

    This prompt is injected into the system prompt before workspace-specific
    instructions. Use it for instructions that should apply everywhere, e.g.
    preferred communication style, timezone, naming conventions.

    Pass an empty string to clear the prompt.

    Args:
        prompt: The custom prompt text. Empty string to clear.
        user_id: User identifier (injected automatically)

    Returns:
        Confirmation message
    """
    save_user_prompt(user_id, prompt)
    logger.info(
        "user_prompt.set",
        {"length": len(prompt), "set": bool(prompt)},
        user_id=user_id,
    )
    if not prompt:
        return "User prompt cleared."
    return f"User prompt saved ({len(prompt)} chars)."


user_prompt_set.annotations = ToolAnnotations(
    title="Set User Prompt", destructive=True
)


# --- Knowledge interview loop (P1-T4) ---------------------------------------
#
# Gap-report-driven interview of the owner when corpus search misses show the
# reference knowledge is incomplete. State is disk-backed (active_interview.json
# in the user's Interviews dir) so it survives across tool calls and turns; the
# finished Q/A transcript is persisted for the review pipeline.

_ACTIVE_STATE = "active_interview.json"


def _interview_paths(user_id: str) -> tuple[Any, Any]:
    from src.storage.paths import get_paths

    d = get_paths(user_id=user_id).interviews_dir()
    return d, d / _ACTIVE_STATE


def _load_interview_state(user_id: str) -> dict[str, Any] | None:
    _d, active = _interview_paths(user_id)
    if not active.exists():
        return None
    try:
        state: dict[str, Any] = json.loads(active.read_text(encoding="utf-8"))
        return state if state.get("questions") else None
    except (json.JSONDecodeError, OSError):
        return None


def _save_interview_state(user_id: str, state: dict[str, Any]) -> None:
    _d, active = _interview_paths(user_id)
    _d.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _question_for_gap(gap: str) -> str:
    return (
        f"Can you share your approach, templates, or reference material "
        f"for '{gap}'? Anything you want me to always follow here?"
    )


@tool
def interview_start(gaps: list[str] | str, user_id: str = DEFAULT_USER_ID) -> str:
    """Start a knowledge interview from a gap report.

    Call this when corpus searches show the reference knowledge is
    incomplete. Each gap becomes exactly one interview question; the loop
    then proceeds one question at a time via interview_ask until all are
    answered. Pass each failed search topic as a gap.

    Args:
        gaps: Knowledge gaps to cover (one question per gap). Accepts a
            list or a JSON string encoding one.
        user_id: User identifier (injected automatically)

    Returns:
        JSON string: {"questions_total", "question_index", "question",
        "questions"} or {"error": true, ...}
    """
    raw: list[Any] = [gaps] if isinstance(gaps, str) else list(gaps)
    if isinstance(gaps, str):
        try:
            parsed = json.loads(gaps)
            raw = parsed if isinstance(parsed, list) else [gaps]
        except json.JSONDecodeError:
            pass
    gaps = [str(g).strip() for g in raw if str(g).strip()]
    if not gaps:
        return json.dumps(
            {"error": True, "message": "No gaps provided — nothing to interview about."}
        )

    if _load_interview_state(user_id) is not None:
        return json.dumps(
            {
                "error": True,
                "message": (
                    "An interview is already active. Continue with "
                    "interview_ask or close it with interview_finish first."
                ),
            }
        )

    questions = [_question_for_gap(g) for g in gaps]
    state = {
        "gaps": gaps,
        "questions": questions,
        "answers": [],
        "question_index": 0,
        "started_ts": datetime.now(UTC).isoformat(),
    }
    _save_interview_state(user_id, state)
    logger.info(
        "interview.start",
        {"gaps": len(gaps)},
        user_id=user_id,
    )
    return json.dumps(
        {
            "questions_total": len(questions),
            "question_index": 0,
            "question": questions[0],
            "questions": questions,
        }
    )


interview_start.annotations = ToolAnnotations(title="Start Knowledge Interview")


@tool
def interview_ask(answer: str, user_id: str = DEFAULT_USER_ID) -> str:
    """Record the user's answer to the current interview question.

    Advances to the next question in the active interview. When all
    questions are answered, the response signals completion — then call
    interview_finish to persist the transcript.

    Args:
        answer: The user's answer to the current question
        user_id: User identifier (injected automatically)

    Returns:
        JSON string with the next question, or completion status
    """
    state = _load_interview_state(user_id)
    if state is None:
        return json.dumps(
            {"error": True, "message": "No active interview. Call interview_start first."}
        )
    idx = state["question_index"]
    state["answers"].append({"question": state["questions"][idx], "answer": answer})
    state["question_index"] = idx + 1
    _save_interview_state(user_id, state)

    total = len(state["questions"])
    if state["question_index"] >= total:
        return json.dumps(
            {
                "answered": total,
                "complete": True,
                "message": ("All questions answered. Call interview_finish to save the transcript."),
            }
        )
    return json.dumps(
        {
            "answered": state["question_index"],
            "complete": False,
            "question_index": state["question_index"],
            "question": state["questions"][state["question_index"]],
        }
    )


interview_ask.annotations = ToolAnnotations(title="Record Interview Answer")


@tool
def interview_finish(user_id: str = DEFAULT_USER_ID) -> str:
    """Close the active interview and persist the transcript.

    Persists the full Q/A transcript (with the originating gap report) to
    the user's Interviews dir and clears the active state. Mid-interview
    finish marks the transcript incomplete rather than discarding answers.

    Returns:
        JSON string: {"answered", "total", "complete", "transcript_path"}
    """
    state = _load_interview_state(user_id)
    if state is None:
        return json.dumps(
            {"error": True, "message": "No active interview to finish."}
        )
    total = len(state["questions"])
    answered = len(state["answers"])
    transcript = {
        "gaps": state["gaps"],
        "qa": state["answers"],
        "complete": answered >= total,
        "started_ts": state["started_ts"],
        "finished_ts": datetime.now(UTC).isoformat(),
    }
    out_dir, active = _interview_paths(user_id)
    started = str(state["started_ts"]).replace(":", "").replace("-", "")
    path = out_dir / f"interview-{started}.json"
    path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    active.unlink(missing_ok=True)
    logger.info(
        "interview.finish",
        {"answered": answered, "total": total, "complete": transcript["complete"]},
        user_id=user_id,
    )
    return json.dumps(
        {
            "answered": answered,
            "total": total,
            "complete": transcript["complete"],
            "transcript_path": str(path),
        }
    )


interview_finish.annotations = ToolAnnotations(title="Finish Knowledge Interview")
