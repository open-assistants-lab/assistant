"""Pure developer-review checks; not a policy compiler or authority service."""

import copy
import hashlib
import json
import math
import re
from typing import cast

_HASH = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value: str) -> object:
    raise ValueError("non-finite JSON constant")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def load_baseline(raw: bytes, expected_sha256: str) -> dict[str, object]:
    if not _HASH.fullmatch(expected_sha256):
        raise ValueError("expected hash must be canonical SHA-256")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("baseline hash mismatch")
    parsed = json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite,
                        parse_float=_finite_float)
    if not isinstance(parsed, dict) or parsed.get("defaultAction") != "SCMP_ACT_ERRNO":
        raise ValueError("baseline must be an object with an errno default deny")
    rules = parsed.get("syscalls")
    if not isinstance(rules, list) or not rules:
        raise ValueError("baseline needs a nonempty syscall rule list")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("syscall rule must be an object")
        names = rule.get("names")
        if not isinstance(names, list) or not names or any(
            not isinstance(name, str) or not name.strip() for name in names
        ):
            raise ValueError("rule needs nonempty string syscall names")
        action = rule.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("rule needs an action string")
        if "args" in rule and (
            not isinstance(rule["args"], list)
            or any(not isinstance(arg, dict) for arg in rule["args"])
        ):
            raise ValueError("rule arguments must be a list of objects")
        for field in ("includes", "excludes"):
            if field in rule and not isinstance(rule[field], dict):
                raise ValueError("rule conditions must be objects")
    return cast(dict[str, object], parsed)


def _canonical(profile: dict[str, object]) -> str:
    return json.dumps(profile, sort_keys=True, separators=(",", ":"), allow_nan=False)


def make_noop_unshare_profile(baseline: dict[str, object]) -> dict[str, object]:
    raw = _canonical(baseline).encode()
    load_baseline(raw, hashlib.sha256(raw).hexdigest())
    rules = cast(list[dict[str, object]], baseline["syscalls"])
    for rule in rules:
        if "unshare" not in cast(list[str], rule["names"]):
            continue
        if rule["action"] != "SCMP_ACT_ALLOW":
            raise ValueError("explicit unshare denial conflicts with diagnostic")
        guarded = False
        for field in ("includes", "excludes"):
            condition = cast(dict[str, object], rule.get(field, {}))
            if set(condition) - {"caps", "arches", "minKernel"}:
                raise ValueError("unknown unshare condition")
            for key in ("caps", "arches"):
                if key in condition:
                    values = condition[key]
                    if not isinstance(values, list) or any(
                        not isinstance(value, str) or not value.strip() for value in values
                    ):
                        raise ValueError("invalid unshare condition list")
                    guarded |= bool(values)
            if "minKernel" in condition:
                kernel = condition["minKernel"]
                if not isinstance(kernel, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", kernel):
                    raise ValueError("invalid unshare kernel condition")
                guarded = True
        if not guarded:
            raise ValueError("unconditional unshare allowance already present")
    candidate = copy.deepcopy(baseline)
    cast(list[dict[str, object]], candidate["syscalls"]).append({
        "names": ["unshare"], "action": "SCMP_ACT_ALLOW",
        "args": [{"index": 0, "value": 0, "op": "SCMP_CMP_EQ"}],
    })
    return candidate


def assert_noop_only_delta(baseline: dict[str, object], candidate: dict[str, object]) -> None:
    # Recheck baseline eligibility; independently compare all original fields
    # and the literal sole appended rule. JSON comparison distinguishes 0/False/0.0.
    make_noop_unshare_profile(baseline)
    rules = candidate.get("syscalls")
    original_rules = cast(list[object], baseline["syscalls"])
    if not isinstance(rules, list) or len(rules) != len(original_rules) + 1:
        raise ValueError("candidate needs exactly one additional rule")
    expected = {"names": ["unshare"], "action": "SCMP_ACT_ALLOW",
                "args": [{"index": 0, "value": 0, "op": "SCMP_CMP_EQ"}]}
    if json.dumps(rules[-1], sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True):
        raise ValueError("candidate rule is not the exact no-op addition")
    unchanged = copy.deepcopy(candidate)
    unchanged["syscalls"] = rules[:-1]
    if _canonical(unchanged) != _canonical(baseline):
        raise ValueError("candidate alters baseline fields or rules")


def validate_provenance_record(
    record: dict[str, str], engine_version: str, engine_commit: str,
    architecture: str, profile_sha256: str,
) -> None:
    # These are operator-supplied assertions. Successful syntax checks are not
    # evidence that a source mapping or user approval actually happened.
    for field in ("source_url", "source_revision", "profile_sha256", "engine_version",
                  "engine_commit", "architecture", "mapping_kind", "mapping_evidence",
                  "review_reference"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("missing provenance field")
    if not _REVISION.fullmatch(record["source_revision"]):
        raise ValueError("source revision must be a full pinned commit")
    revision = record["source_revision"]
    known_sources = {
        f"https://github.com/moby/moby/blob/{revision}/vendor/github.com/moby/profiles/seccomp/default.json",
        f"https://github.com/moby/profiles/blob/{revision}/seccomp/default.json",
    }
    if record["source_url"] not in known_sources:
        raise ValueError("source link must identify the same pinned official baseline")
    if not _HASH.fullmatch(profile_sha256) or not _HASH.fullmatch(record["profile_sha256"]):
        raise ValueError("invalid profile SHA-256")
    for field, expected in (("engine_version", engine_version), ("engine_commit", engine_commit),
                            ("architecture", architecture), ("profile_sha256", profile_sha256)):
        if record[field] != expected:
            raise ValueError("provenance mapping mismatch")
    if record["mapping_kind"] not in {"exact", "substitute-reviewed"}:
        raise ValueError("unreviewed provenance mapping")
