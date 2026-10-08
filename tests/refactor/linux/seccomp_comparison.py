"""Strict diagnostic comparisons, not product receipts or boundary qualification."""

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tests.refactor.linux.namespace_diagnostic import FLAGS
from tests.refactor.linux.seccomp_review import (
    _finite_float,
    _nonfinite,
    _object,
    assert_noop_only_delta,
    load_baseline,
)


@dataclass
class DiagnosticRun:
    observations: list[dict[str, object]]
    image_id: str
    architecture: str
    uid: int
    gid: int
    cap_effective: int
    no_new_privileges: int
    seccomp_mode: int
    process_exit: int
    cleanup_confirmed: bool


def require_comparison_opt_in(linux: bool, comparison: bool) -> None:
    if linux is not True or comparison is not True:
        raise ValueError("both explicit execution flags required")


def parse_diagnostic(stdout: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen = set()
    for line in stdout.splitlines():
        label, separator, payload = line.partition(" ")
        if not separator or label not in FLAGS:
            raise ValueError("unexpected diagnostic line")
        row = json.loads(
            payload, object_pairs_hook=_object, parse_constant=_nonfinite, parse_float=_finite_float
        )
        if not isinstance(row, dict) or set(row) != {
            "operation",
            "flags",
            "succeeded",
            "errno",
            "reason",
        }:
            raise ValueError("invalid diagnostic row fields")
        op = row["operation"]
        if op not in ("clone", "unshare") or row["flags"] != hex(FLAGS[label]):
            raise ValueError("unknown operation or flags")
        if type(row["succeeded"]) is not bool or type(row["errno"]) is not int:
            raise ValueError("invalid diagnostic types")
        error = 0 if row["succeeded"] else 1
        reason = "OK" if row["succeeded"] else "EPERM"
        if row["errno"] != error or row["reason"] != reason:
            raise ValueError("contradictory diagnostic outcome")
        key = (op, label)
        if key in seen:
            raise ValueError("duplicate diagnostic row")
        seen.add(key)
        rows.append(row)
    if seen != {(op, label) for op in ("clone", "unshare") for label in FLAGS}:
        raise ValueError("all fourteen cases required")
    return rows


def record_diagnostic(
    process: subprocess.CompletedProcess[str], facts: dict[str, object]
) -> DiagnosticRun:
    if process.stderr:
        raise ValueError("unexpected diagnostic stderr")
    lines = process.stdout.splitlines()
    policy_lines = [line for line in lines if line.startswith("policy-facts ")]
    if len(policy_lines) != 1:
        raise ValueError("one policy-facts line required")
    policy = json.loads(
        policy_lines[0].removeprefix("policy-facts "),
        object_pairs_hook=_object,
        parse_constant=_nonfinite,
        parse_float=_finite_float,
    )
    if not isinstance(policy, dict) or set(policy) != {
        "architecture",
        "uid",
        "gid",
        "cap_effective",
        "no_new_privileges",
        "seccomp_mode",
    }:
        raise ValueError("invalid policy facts")
    observations = parse_diagnostic("\n".join(line for line in lines if line != policy_lines[0]))
    run = DiagnosticRun(
        observations,
        cast(str, facts.get("image_id")),
        policy["architecture"],
        policy["uid"],
        policy["gid"],
        policy["cap_effective"],
        policy["no_new_privileges"],
        policy["seccomp_mode"],
        process.returncode,
        cast(bool, facts.get("cleanup_confirmed")),
    )
    _check_state(run)
    return run


def _check_state(run: DiagnosticRun) -> None:
    if not isinstance(run.image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", run.image_id):
        raise ValueError("immutable image identity required")
    if run.architecture != "aarch64" or run.cleanup_confirmed is not True:
        raise ValueError("unsupported architecture or uncertain cleanup")
    for value, expected in (
        (run.uid, 20001),
        (run.gid, 20001),
        (run.cap_effective, 0),
        (run.no_new_privileges, 1),
        (run.seccomp_mode, 2),
        (run.process_exit, 0),
    ):
        if type(value) is not int or value != expected:
            raise ValueError("process policy or exit mismatch")


def _check(run: DiagnosticRun, candidate: bool = False) -> None:
    _check_state(run)
    lines = []
    for row in run.observations:
        if not isinstance(row, dict):
            raise ValueError("invalid observation")
        labels = [label for label, flag in FLAGS.items() if hex(flag) == row.get("flags")]
        if len(labels) != 1:
            raise ValueError("invalid flags")
        lines.append(labels[0] + " " + json.dumps(row, allow_nan=False))
    parsed = parse_diagnostic("\n".join(lines))
    for row in parsed:
        allowed = row["flags"] == "0x0" and (row["operation"] == "clone" or candidate)
        if row["succeeded"] is not allowed:
            raise ValueError("unexpected syscall permission")


def assert_baseline_pair(default: DiagnosticRun, baseline: DiagnosticRun) -> None:
    _check(default)
    _check(baseline)
    if default.image_id != baseline.image_id:
        raise ValueError("mixed image comparison")


def assert_attribution_matrix(
    default: DiagnosticRun, baseline: DiagnosticRun, candidate: DiagnosticRun
) -> None:
    assert_baseline_pair(default, baseline)
    _check(candidate, True)
    if candidate.image_id != default.image_id:
        raise ValueError("mixed image comparison")


def run_comparison(
    execute: Callable[[str], DiagnosticRun],
) -> tuple[DiagnosticRun, DiagnosticRun, DiagnosticRun]:
    default = execute("D")
    baseline = execute("B")
    assert_baseline_pair(default, baseline)
    candidate = execute("P")
    assert_attribution_matrix(default, baseline, candidate)
    return default, baseline, candidate


def prepare_reviewed_profiles(
    baseline_path: Path,
    candidate_path: Path,
    baseline_sha256: str,
    candidate_sha256: str,
    owned_dir: Path,
) -> tuple[Path, Path]:
    braw, praw = baseline_path.read_bytes(), candidate_path.read_bytes()
    baseline = load_baseline(braw, baseline_sha256)
    candidate = load_baseline(praw, candidate_sha256)
    assert_noop_only_delta(baseline, candidate)
    owned_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths = (owned_dir / "baseline.json", owned_dir / "candidate.json")
    for path, raw in zip(paths, (braw, praw), strict=True):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, "wb") as output:
            output.write(raw)
    return paths
