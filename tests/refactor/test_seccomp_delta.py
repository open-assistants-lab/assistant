"""One reviewed structural addition, never general namespace permission."""

import copy
from pathlib import Path

import pytest

from tests.refactor.test_seccomp_review import baseline

NOOP = {"names": ["unshare"], "action": "SCMP_ACT_ALLOW",
        "args": [{"index": 0, "value": 0, "op": "SCMP_CMP_EQ"}]}


def test_adds_exact_noop_rule_without_mutation():
    from tests.refactor.linux.seccomp_review import (
        assert_noop_only_delta,
        make_noop_unshare_profile,
    )

    original = baseline()
    before = copy.deepcopy(original)
    candidate = make_noop_unshare_profile(original)
    assert original == before
    assert candidate["syscalls"] == before["syscalls"] + [NOOP]
    assert_noop_only_delta(original, candidate)
    candidate["x-preserved"]["label"] = "changed"
    assert original == before


def test_explicit_unshare_denial_rejected():
    from tests.refactor.linux.seccomp_review import make_noop_unshare_profile

    original = baseline()
    original["syscalls"].append({"names": ["unshare"], "action": "SCMP_ACT_ERRNO"})
    with pytest.raises(ValueError):
        make_noop_unshare_profile(original)


def test_existing_diagnostic_rule_rejected():
    from tests.refactor.linux.seccomp_review import make_noop_unshare_profile

    original = baseline()
    original["syscalls"].append(copy.deepcopy(NOOP))
    with pytest.raises(ValueError):
        make_noop_unshare_profile(original)


@pytest.mark.parametrize("conditions", [{}, {"includes": {}}, {"includes": {"caps": []}}])
def test_unconditional_unshare_allow_rejected(conditions):
    from tests.refactor.linux.seccomp_review import make_noop_unshare_profile

    original = baseline()
    original["syscalls"].append({"names": ["unshare"], "action": "SCMP_ACT_ALLOW", **conditions})
    with pytest.raises(ValueError):
        make_noop_unshare_profile(original)


def test_conditional_baseline_allow_preserved():
    from tests.refactor.linux.seccomp_review import (
        assert_noop_only_delta,
        make_noop_unshare_profile,
    )

    original = baseline()
    original["syscalls"].append({"names": ["mount", "unshare"], "action": "SCMP_ACT_ALLOW",
                                "includes": {"caps": ["CAP_SYS_ADMIN"]}})
    before = copy.deepcopy(original)
    candidate = make_noop_unshare_profile(original)
    assert candidate["syscalls"][:-1] == before["syscalls"]
    assert_noop_only_delta(original, candidate)
    assert original == before


@pytest.mark.parametrize("field,value", [("defaultAction", "SCMP_ACT_ALLOW"),
                                         ("archMap", []), ("defaultErrnoRet", True)])
def test_extra_or_altered_rules_rejected(field, value):
    from tests.refactor.linux.seccomp_review import assert_noop_only_delta

    original = baseline()
    candidate = copy.deepcopy(original)
    candidate["syscalls"].append(copy.deepcopy(NOOP))
    candidate[field] = value
    with pytest.raises(ValueError):
        assert_noop_only_delta(original, candidate)


@pytest.mark.parametrize("field,value", [("index", False), ("index", 0.0), ("value", False),
                                         ("value", 0.0), ("value", 0x10000000)])
def test_nonzero_or_bool_argument_rejected(field, value):
    from tests.refactor.linux.seccomp_review import assert_noop_only_delta

    original = baseline()
    candidate = copy.deepcopy(original)
    candidate["syscalls"].append(copy.deepcopy(NOOP))
    candidate["syscalls"][-1]["args"][0][field] = value
    with pytest.raises(ValueError):
        assert_noop_only_delta(original, candidate)


def test_frozen_real_baseline_and_candidate_have_only_reviewed_delta():
    from tests.refactor.linux.seccomp_review import assert_noop_only_delta, load_baseline

    root = Path(__file__).parent / "linux/fixtures/seccomp"
    original = load_baseline((root / "docker-29.8.0-baseline.json").read_bytes(),
                             "536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74")
    candidate = load_baseline((root / "docker-29.8.0-noop-unshare.json").read_bytes(),
                              "6a744f09389794f4de85126a9c853d90a73226823270ac34cb1186a3ba3c5a42")
    assert len(original["syscalls"]) == 33
    assert len(candidate["syscalls"]) == 34
    assert_noop_only_delta(original, candidate)


def test_clone_rule_addition_rejected():
    from tests.refactor.linux.seccomp_review import assert_noop_only_delta

    original = baseline()
    candidate = copy.deepcopy(original)
    candidate["syscalls"].extend([copy.deepcopy(NOOP), {"names": ["clone"], "action": "SCMP_ACT_ALLOW"}])
    with pytest.raises(ValueError):
        assert_noop_only_delta(original, candidate)
