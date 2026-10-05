"""Redacted local checks; no inference, tool execution, redirects, or remote hosts."""
from __future__ import annotations

import argparse
import ipaddress
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from agentprofile import load_profile

from examples.jen_reference.fixture import read_store
from examples.jen_reference.instance import InstancePaths, read_configuration, validate_instance
from src.sdk.tools_custom import load_tool_meta

EXPECTED_TOOLS = {"fixture_store_read", "fixture_store_pause"}


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    checks: dict[str, str]


def _loopback_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"} or "%" in (parsed.hostname or "")):
            return None
        if not ipaddress.ip_address(parsed.hostname or "").is_loopback:
            return None
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return None
        return url.rstrip("/")
    except ValueError:
        return None


def check_readiness(paths: InstancePaths, *, runtime_url: str | None = None) -> ReadinessResult:
    problems = validate_instance(paths)
    checks = {"package": "ok" if not problems else "invalid",
              "configuration": "invalid", "profile": "invalid", "tools": "invalid",
              "fixture_state": "invalid", "runtime": "not_checked"}
    if "instance_symlink" in problems:
        return ReadinessResult(False, checks)
    config = {}
    try:
        config = read_configuration(paths)
        checks["configuration"] = "ok"
    except (OSError, ValueError):
        pass
    try:
        profile = load_profile(paths.data / "PROFILE.md")
        if profile.name == "jen-reference":
            checks["profile"] = "ok"
    except Exception:
        pass  # Parser errors may include content; expose only stable reason.
    try:
        tools = [load_tool_meta(paths.data / "Tools" / name / "TOOL.md") for name in EXPECTED_TOOLS]
        if all(t and t["name"] in EXPECTED_TOOLS for t in tools):
            checks["tools"] = "ok"
    except (OSError, ValueError, KeyError):
        pass
    try:
        rows = [read_store(paths.data / ".fixture/state.sqlite3", name) for name in ("fixture-alpha", "fixture-beta")]
        if all(isinstance(r["revision"], int) and r["revision"] >= 1 for r in rows):
            checks["fixture_state"] = "ok"
    except (OSError, sqlite3.Error, ValueError):
        pass
    if runtime_url is not None:
        base = _loopback_url(runtime_url)
        if base is None:
            checks["runtime"] = "invalid_url"
        elif any(v != "ok" for k, v in checks.items() if k != "runtime"):
            checks["runtime"] = "static_checks_failed"
        else:
            checks["runtime"] = "unavailable"
            try:
                with httpx.Client(timeout=5, follow_redirects=False, trust_env=False,
                                  headers={"Authorization": "Bearer " + config["API_KEY"]}) as client:
                    health = client.get(base + "/health")
                    if health.status_code == 200:
                        catalog = client.get(base + "/tools")
                        if catalog.status_code == 200:
                            data = catalog.json()["tools"]
                            checks["runtime"] = "catalog_mismatch"
                            if isinstance(data, list) and all(isinstance(t, dict) for t in data):
                                names = {t["name"] for t in data if t.get("source") == "custom" and t.get("enabled") is True}
                                checks["runtime"] = "ok" if names == EXPECTED_TOOLS else "catalog_mismatch"
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
    return ReadinessResult(all(v == "ok" for v in checks.values()), checks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--runtime-url")
    args = parser.parse_args()
    result = check_readiness(InstancePaths.at(args.instance), runtime_url=args.runtime_url)
    print(json.dumps(asdict(result)))
    return int(not result.ready)


if __name__ == "__main__":
    raise SystemExit(main())
