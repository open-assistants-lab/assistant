"""Authorized exact no-op comparison only, never full MT qualification."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.refactor.linux.probe_helpers import IMAGE, run_recorded_probe
from tests.refactor.linux.seccomp_comparison import (
    prepare_reviewed_profiles,
    record_diagnostic,
    require_comparison_opt_in,
    run_comparison,
)

B_SHA = "536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74"
P_SHA = "6a744f09389794f4de85126a9c853d90a73226823270ac34cb1186a3ba3c5a42"


def test_reviewed_default_baseline_noop_comparison(request, tmp_path):
    config = request.config
    if not config.getoption("--namespace-policy-comparison"):
        pytest.skip("separate exact-profile execution authorization required")
    require_comparison_opt_in(
        config.getoption("--linux-probe"), config.getoption("--namespace-policy-comparison")
    )
    # These exact hashes/image bind the reviewed permission, not arbitrary CLI files.
    if config.getoption("--baseline-sha256") != B_SHA or config.getoption("--noop-sha256") != P_SHA:
        raise ValueError("comparison permission is for the reviewed hashes only")
    if config.getoption("--probe-image") != IMAGE:
        raise ValueError("comparison permission is for the reviewed image only")
    # CLI flags alone are not approval. The executing operator records the actual
    # preceding user permission in the owned task ledger (not an authentication token).
    authorization_path = (
        Path(__file__).parents[3]
        / ".superpowers/sdd/2026-10-07-linux-namespace-policy-attribution/execution-authorization.json"
    )
    authorization = json.loads(authorization_path.read_text())
    if (
        authorization.get("baseline_sha256"),
        authorization.get("candidate_sha256"),
        authorization.get("image"),
        authorization.get("user_request"),
    ) != (B_SHA, P_SHA, IMAGE, "plz proceed"):
        raise ValueError("recorded operator permission does not match this comparison")
    b, p = prepare_reviewed_profiles(
        Path(config.getoption("--baseline-seccomp")),
        Path(config.getoption("--noop-seccomp")),
        B_SHA,
        P_SHA,
        tmp_path / "reviewed-profiles",
    )
    source = Path(__file__).with_name("namespace_diagnostic.py").read_text()
    script = (
        "python - <<'WRITE_FIXED_DIAGNOSTIC'\nfrom pathlib import Path\n"
        "Path('/tmp/namespace_diagnostic.py').write_text(" + repr(source) + ")\n"
        "WRITE_FIXED_DIAGNOSTIC\npython /tmp/namespace_diagnostic.py"
    )
    captured = {}

    def execute(phase):
        process, facts = run_recorded_probe(
            script,
            docker_host=config.getoption("--docker-host"),
            image=IMAGE,
            seccomp_profile={"D": None, "B": b, "P": p}[phase],
        )
        result = record_diagnostic(process, facts)
        captured[phase] = {"run": asdict(result), "metadata": facts}
        print(phase, json.dumps(captured[phase], sort_keys=True))
        return result

    run_comparison(execute)
    assert set(captured) == {"D", "B", "P"}
