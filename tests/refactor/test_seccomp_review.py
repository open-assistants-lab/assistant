"""Pure provenance/JSON refusals, not Linux containment or operator authorization proof."""

import copy
import hashlib
import json

import pytest


def baseline():
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "defaultErrnoRet": 1,
        "archMap": [{"architecture": "SCMP_ARCH_AARCH64", "subArchitectures": ["SCMP_ARCH_ARM"]}],
        "syscalls": [{"names": ["read", "write"], "action": "SCMP_ACT_ALLOW"}],
        "x-preserved": {"label": "synthetic baseline", "enabled": False},
    }


def encoded(value):
    raw = json.dumps(value).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def test_hash_mismatch_rejected():
    from tests.refactor.linux.seccomp_review import load_baseline

    raw, _ = encoded(baseline())
    with pytest.raises(ValueError):
        load_baseline(raw, "0" * 64)


@pytest.mark.parametrize("digest", ["", "main", "a" * 63, "g" * 64, "A" * 64])
def test_malformed_hash_rejected(digest):
    from tests.refactor.linux.seccomp_review import load_baseline

    raw, _ = encoded(baseline())
    with pytest.raises(ValueError):
        load_baseline(raw, digest)


@pytest.mark.parametrize("raw", [
    b'{"defaultAction":"SCMP_ACT_ALLOW","defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}',
    b'{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[{"names":["read"],"action":"SCMP_ACT_ALLOW","action":"SCMP_ACT_ERRNO"}]}',
])
def test_duplicate_keys_rejected(raw):
    from tests.refactor.linux.seccomp_review import load_baseline

    with pytest.raises(ValueError):
        load_baseline(raw, hashlib.sha256(raw).hexdigest())


def test_permissive_default_rejected():
    from tests.refactor.linux.seccomp_review import load_baseline

    profile = baseline()
    profile["defaultAction"] = "SCMP_ACT_ALLOW"
    with pytest.raises(ValueError):
        load_baseline(*encoded(profile))


@pytest.mark.parametrize("rules", [
    [], None, {}, [1], [{}],
    [{"names": [], "action": "SCMP_ACT_ALLOW"}],
    [{"names": [False], "action": "SCMP_ACT_ALLOW"}],
    [{"names": ["read"], "action": ""}],
    [{"names": ["read"], "action": "SCMP_ACT_ALLOW", "args": {}}],
    [{"names": ["read"], "action": "SCMP_ACT_ALLOW", "args": [0]}],
    [{"names": ["read"], "action": "SCMP_ACT_ALLOW", "includes": []}],
    [{"names": ["read"], "action": "SCMP_ACT_ALLOW", "excludes": False}],
])
def test_malformed_syscall_rules_rejected(rules):
    from tests.refactor.linux.seccomp_review import load_baseline

    profile = baseline()
    profile["syscalls"] = rules
    with pytest.raises(ValueError):
        load_baseline(*encoded(profile))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_rejected(constant):
    from tests.refactor.linux.seccomp_review import load_baseline

    raw = json.dumps(baseline())[:-1].encode() + b',"extra":' + constant.encode() + b'}'
    with pytest.raises(ValueError):
        load_baseline(raw, hashlib.sha256(raw).hexdigest())


def test_valid_baseline_bytes_unchanged():
    from tests.refactor.linux.seccomp_review import load_baseline

    original = baseline()
    raw, digest = encoded(original)
    original_bytes = bytes(raw)
    assert load_baseline(raw, digest) == original
    assert raw == original_bytes
    assert original == baseline()


def provenance():
    return {
        "source_url": "https://github.com/moby/moby/blob/" + "a" * 40 + "/vendor/github.com/moby/profiles/seccomp/default.json",
        "source_revision": "a" * 40,
        "profile_sha256": "b" * 64,
        "engine_version": "29.8.0",
        "engine_commit": "3ce5872",
        "architecture": "aarch64",
        "mapping_kind": "exact",
        "mapping_evidence": "synthetic evidence: not an actual source mapping",
        "review_reference": "synthetic review: not operator authorization",
    }


def validate(record):
    from tests.refactor.linux.seccomp_review import validate_provenance_record

    validate_provenance_record(record, "29.8.0", "3ce5872", "aarch64", "b" * 64)


@pytest.mark.parametrize("revision", ["main", "docker-v29.8.0", "3ce5872", "z" * 40])
def test_unpinned_source_revision_rejected(revision):
    record = provenance()
    record["source_revision"] = revision
    with pytest.raises(ValueError):
        validate(record)


@pytest.mark.parametrize("field", ["engine_version", "engine_commit", "architecture", "profile_sha256"])
def test_engine_mapping_mismatch_rejected(field):
    record = provenance()
    record[field] = "different"
    with pytest.raises(ValueError):
        validate(record)


@pytest.mark.parametrize("kind,reference", [("substitute", ""), ("substitute-reviewed", ""), ("unverified", "fake")])
def test_unreviewed_substitute_rejected(kind, reference):
    record = provenance()
    record["mapping_kind"] = kind
    record["review_reference"] = reference
    with pytest.raises(ValueError):
        validate(record)


@pytest.mark.parametrize("field", ["source_url", "mapping_evidence", "review_reference"])
def test_missing_provenance_evidence_rejected(field):
    record = provenance()
    record[field] = " "
    with pytest.raises(ValueError):
        validate(record)


@pytest.mark.parametrize("kind", ["exact", "substitute-reviewed"])
def test_valid_provenance_record_preserved(kind):
    record = provenance()
    record["mapping_kind"] = kind
    original = copy.deepcopy(record)
    validate(record)
    assert record == original
