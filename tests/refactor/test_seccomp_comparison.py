"""Pure refusal and ordering tests, not actual Docker qualification."""

import json
from dataclasses import replace

import pytest

from tests.refactor.linux.namespace_diagnostic import FLAGS

IMAGE = "sha256:" + "a" * 64


def rows(candidate=False):
    return [
        {
            "operation": op,
            "flags": hex(flag),
            "succeeded": flag == 0 and (op == "clone" or candidate),
            "errno": 0 if flag == 0 and (op == "clone" or candidate) else 1,
            "reason": "OK" if flag == 0 and (op == "clone" or candidate) else "EPERM",
        }
        for op in ("clone", "unshare")
        for flag in FLAGS.values()
    ]


def output(observations):
    return "\n".join(
        f"{label} {json.dumps(row)}"
        for label, row in zip(list(FLAGS) * 2, observations, strict=True)
    )


def run(candidate=False):
    from tests.refactor.linux.seccomp_comparison import DiagnosticRun

    return DiagnosticRun(rows(candidate), IMAGE, "aarch64", 20001, 20001, 0, 1, 2, 0, True)


def test_matrix_requires_all_fourteen_cases():
    from tests.refactor.linux.seccomp_comparison import assert_attribution_matrix, parse_diagnostic

    assert parse_diagnostic(output(rows())) == rows()
    assert_attribution_matrix(run(), run(), run(True))
    broken = run(True)
    broken.observations.pop()
    with pytest.raises(ValueError):
        assert_attribution_matrix(run(), run(), broken)


def test_process_success_not_namespace_success():
    from tests.refactor.linux.seccomp_comparison import assert_attribution_matrix

    with pytest.raises(ValueError):
        assert_attribution_matrix(run(), run(), run())


def test_baseline_mismatch_prevents_candidate_launch():
    from tests.refactor.linux.seccomp_comparison import run_comparison

    calls = []

    def execute(phase):
        calls.append(phase)
        return run(phase == "B")

    with pytest.raises(ValueError):
        run_comparison(execute)
    assert calls == ["D", "B"]


def test_namespace_success_rejected():
    from tests.refactor.linux.seccomp_comparison import assert_attribution_matrix

    candidate = run(True)
    candidate.observations[1].update(succeeded=True, errno=0, reason="OK")
    with pytest.raises(ValueError):
        assert_attribution_matrix(run(), run(), candidate)


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra"])
def test_missing_or_duplicate_rows_rejected(change):
    from tests.refactor.linux.seccomp_comparison import parse_diagnostic

    lines = output(rows()).splitlines()
    if change == "missing":
        lines.pop()
    elif change == "duplicate":
        lines[-1] = lines[0]
    else:
        lines.append("unexpected diagnostic")
    with pytest.raises(ValueError):
        parse_diagnostic("\n".join(lines))


@pytest.mark.parametrize("suffix", [" extra", "duplicate-key", "nonfinite-errno"])
def test_diagnostic_duplicate_keys_or_trailing_text_rejected(suffix):
    from tests.refactor.linux.seccomp_comparison import parse_diagnostic

    lines = output(rows()).splitlines()
    if suffix == "duplicate-key":
        lines[0] = lines[0].replace('"errno": 0', '"errno": 0, "errno": 0')
    elif suffix == "nonfinite-errno":
        lines[0] = lines[0].replace('"errno": 0', '"errno": NaN')
    else:
        lines[0] += suffix
    with pytest.raises(ValueError):
        parse_diagnostic("\n".join(lines))


@pytest.mark.parametrize(
    "field,value", [("succeeded", 1), ("errno", False), ("errno", 1), ("reason", "EPERM")]
)
def test_malformed_or_contradictory_errno_rejected(field, value):
    from tests.refactor.linux.seccomp_comparison import parse_diagnostic

    observations = rows()
    observations[0][field] = value
    with pytest.raises(ValueError):
        parse_diagnostic(output(observations))


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_id", "latest"),
        ("architecture", "x86_64"),
        ("uid", 0),
        ("cap_effective", False),
        ("no_new_privileges", 0),
        ("seccomp_mode", 0),
        ("process_exit", 1),
        ("cleanup_confirmed", False),
    ],
)
def test_missing_or_wrong_policy_facts_rejected(field, value):
    from tests.refactor.linux.seccomp_comparison import assert_attribution_matrix

    with pytest.raises(ValueError):
        assert_attribution_matrix(run(), run(), replace(run(True), **{field: value}))


def test_mutable_or_mixed_image_rejected():
    from tests.refactor.linux.seccomp_comparison import assert_attribution_matrix

    with pytest.raises(ValueError):
        assert_attribution_matrix(run(), run(), replace(run(True), image_id="sha256:" + "b" * 64))


def test_changed_profile_refused_before_docker_launch(tmp_path):
    from tests.refactor.linux.seccomp_comparison import prepare_reviewed_profiles

    b = tmp_path / "b"
    p = tmp_path / "p"
    b.write_text("changed")
    p.write_text("changed")
    with pytest.raises(ValueError):
        prepare_reviewed_profiles(b, p, "a" * 64, "b" * 64, tmp_path / "owned")


def test_comparison_requires_both_execution_flags():
    from tests.refactor.linux.seccomp_comparison import require_comparison_opt_in

    for a, b in ((False, False), (True, False), (False, True)):
        with pytest.raises(ValueError):
            require_comparison_opt_in(a, b)
    require_comparison_opt_in(True, True)


@pytest.mark.parametrize("mode", ["missing", "duplicate", "wrong_keys", "stderr"])
def test_mandatory_policy_fact_line_refused_when_unusable(mode):
    import subprocess

    from tests.refactor.linux.seccomp_comparison import record_diagnostic

    facts = {
        "architecture": "aarch64",
        "uid": 20001,
        "gid": 20001,
        "cap_effective": 0,
        "no_new_privileges": 1,
        "seccomp_mode": 2,
    }
    if mode == "wrong_keys":
        facts.pop("uid")
    line = "policy-facts " + json.dumps(facts) + "\n"
    text = ("" if mode == "missing" else line * (2 if mode == "duplicate" else 1)) + output(rows())
    process = subprocess.CompletedProcess([], 0, text, "unexpected" if mode == "stderr" else "")
    with pytest.raises(ValueError):
        record_diagnostic(process, {"image_id": IMAGE, "cleanup_confirmed": True})


def test_reviewed_snapshot_is_exclusive_and_readonly(tmp_path):
    import hashlib
    from pathlib import Path

    from tests.refactor.linux.seccomp_comparison import prepare_reviewed_profiles

    fixture = Path(__file__).parent / "linux/fixtures/seccomp"
    b = fixture / "docker-29.8.0-baseline.json"
    p = fixture / "docker-29.8.0-noop-unshare.json"
    owned = tmp_path / "snapshots"
    arguments = (
        b,
        p,
        hashlib.sha256(b.read_bytes()).hexdigest(),
        hashlib.sha256(p.read_bytes()).hexdigest(),
        owned,
    )
    bc, pc = prepare_reviewed_profiles(*arguments)
    assert bc.read_bytes() == b.read_bytes() and pc.read_bytes() == p.read_bytes()
    assert bc.stat().st_mode & 0o777 == pc.stat().st_mode & 0o777 == 0o400
    with pytest.raises(FileExistsError):
        prepare_reviewed_profiles(*arguments)
