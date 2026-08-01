"""HTTP response coverage for canonical run contracts."""

import src.sdk as sdk
from src.http.models import MessageResponse
from src.sdk.run_events import RunEvent, parse_run_event
from src.sdk.run_models import (
    ContextSnapshot,
    RubricEvaluation,
    RunResult,
    RunStatus,
    RunUsage,
    VerificationOutcome,
)


def _run_result() -> RunResult:
    return RunResult(
        run_id="run-123",
        session_id="session-123",
        status=RunStatus.COMPLETED,
        attempt=1,
        model="openai:gpt-4.1",
        response="Answer",
        usage=RunUsage(),
        verification=VerificationOutcome(evaluations=()),
    )


def test_message_response_contains_canonical_run_result() -> None:
    run = _run_result()

    response = MessageResponse(response="Answer", run=run)

    assert response.run is run
    assert response.run == run
    assert response.model_dump(mode="json")["run"]["run_id"] == "run-123"


def test_message_response_without_run_preserves_existing_defaults() -> None:
    response = MessageResponse(response="Answer")

    assert response.run is None
    assert response.reasoning is None
    assert response.error is None
    assert response.verbose_data is None
    assert response.tool_calls is None
    assert response.verification is None
    assert response.usage is None


def test_message_response_accepts_legacy_verification_and_usage_with_run() -> None:
    response = MessageResponse.model_validate(
        {
            "response": "Answer",
            "run": _run_result(),
            "verification": {
                "status": "satisfied",
                "iterations": 1,
                "evaluations": [{"score": 1}],
            },
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
    )

    assert response.verification is not None
    assert response.verification.status == "satisfied"
    assert response.verification.iterations == 1
    assert response.verification.evaluations == [{"score": 1}]
    assert response.usage == {"input_tokens": 12, "output_tokens": 3}


def test_sdk_exports_canonical_run_contracts() -> None:
    expected = {
        "RunEvent": RunEvent,
        "parse_run_event": parse_run_event,
        "RunResult": RunResult,
        "RunStatus": RunStatus,
        "RunUsage": RunUsage,
        "VerificationOutcome": VerificationOutcome,
        "RubricEvaluation": RubricEvaluation,
        "ContextSnapshot": ContextSnapshot,
    }

    assert {name: getattr(sdk, name) for name in expected} == expected
    assert callable(sdk.parse_run_event)
    assert expected.keys() <= set(sdk.__all__)


def test_message_response_schema_describes_nullable_run_result() -> None:
    schema = MessageResponse.model_json_schema()
    run_schema = schema["properties"]["run"]

    assert run_schema["default"] is None
    assert {entry.get("$ref") or entry.get("type") for entry in run_schema["anyOf"]} == {
        "#/$defs/RunResult",
        "null",
    }
