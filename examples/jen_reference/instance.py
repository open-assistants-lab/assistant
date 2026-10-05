"""Prepare local instance files only; never invoke Docker or a customer service."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

PACKAGE_VERSION = "0.1.0"
IMAGE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[a-f0-9]{64}")
ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
CONTENT = (
    "PROFILE.md", "domain.json", "fixture.py",
    "Tools/fixture_store_read/TOOL.md", "Tools/fixture_store_pause/TOOL.md",
)
ROOT_CONTENT = ("config.yaml", "compose.yaml")
REQUIRED = ("PROFILE.md", "domain.json", "Tools/fixture_store_read/TOOL.md",
            "Tools/fixture_store_pause/TOOL.md", "config.yaml", ".env.example")
ENV_KEYS = {"API_KEY", "ASSISTANT_IMAGE", "INSTANCE_PORT", "SOLO_BYPASS",
            "LANGFUSE_ENABLED", "SCHEDULING_SUBAGENT_ENABLED"}


@dataclass(frozen=True)
class InstancePaths:
    root: Path
    data: Path
    env_file: Path
    metadata: Path

    @classmethod
    def at(cls, root: Path) -> InstancePaths:
        return cls(root, root / "data", root / ".env", root / "instance.json")


def _safe_path(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink_path")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_configuration(paths: InstancePaths) -> dict[str, str]:
    """Parse only the supported keys. Never include values in errors."""
    values: dict[str, str] = {}
    for line in paths.env_file.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in ENV_KEYS or key in values:
            raise ValueError("invalid_configuration")
        values[key] = value
    if not values.get("API_KEY") or not IMAGE_RE.fullmatch(values.get("ASSISTANT_IMAGE", "")):
        raise ValueError("missing_configuration")
    if values.get("SOLO_BYPASS") != "false":
        raise ValueError("unsafe_auth_configuration")
    port = values.get("INSTANCE_PORT", "")
    if not port.isdigit() or not 0 <= int(port) <= 65535:
        raise ValueError("invalid_port")
    if values.get("LANGFUSE_ENABLED") != "false" or values.get("SCHEDULING_SUBAGENT_ENABLED") != "false":
        raise ValueError("unsafe_optional_services")
    return values


def prepare_instance(package_dir: Path, destination: Path, *, instance_id: str,
                     owner_label: str, engine_image: str) -> InstancePaths:
    if not ID_RE.fullmatch(instance_id) or not owner_label.strip():
        raise ValueError("invalid_instance_identity")
    if not IMAGE_RE.fullmatch(engine_image):
        raise ValueError("unpinned_engine_image")
    _safe_path(destination)
    _safe_path(package_dir)
    if destination.exists():
        raise ValueError("destination_exists")
    if not package_dir.is_dir() or any(not (package_dir / f).is_file() for f in REQUIRED):
        raise ValueError("missing_package_content")
    if any(p.is_symlink() for p in package_dir.rglob("*")):
        raise ValueError("package_symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".jen-stage-", dir=destination.parent))
    try:
        paths = InstancePaths.at(staging)
        paths.data.mkdir(mode=0o700)
        hashes = {}
        for name in CONTENT:
            src = package_dir / name
            if not src.is_file():
                continue
            target = paths.data / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
            hashes[f"data/{name}"] = _digest(target)
        for name in ROOT_CONTENT:
            src = package_dir / name
            if src.is_file():
                shutil.copyfile(src, staging / name)
                hashes[name] = _digest(staging / name)
        paths.metadata.write_text(json.dumps({
            "schema_version": 1, "package_version": PACKAGE_VERSION,
            "engine_image": engine_image, "instance_id": instance_id,
            "owner_label": owner_label, "content_hashes": hashes,
        }, indent=2) + "\n")
        fd = os.open(paths.env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"API_KEY={secrets.token_urlsafe(32)}\nASSISTANT_IMAGE={engine_image}\n"
                    "INSTANCE_PORT=0\nSOLO_BYPASS=false\nLANGFUSE_ENABLED=false\n"
                    "SCHEDULING_SUBAGENT_ENABLED=false\n")
        # Reserve exclusively before publishing; never replace another instance.
        destination.mkdir(mode=0o700)
        try:
            staging.rename(destination)
        except BaseException:
            destination.rmdir()  # Our exclusive, still-empty reservation only.
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return InstancePaths.at(destination)


def validate_instance(paths: InstancePaths) -> list[str]:
    problems: list[str] = []
    try:
        _safe_path(paths.root)
        if any(p.is_symlink() for p in paths.root.rglob("*")):
            return ["instance_symlink"]
        config = read_configuration(paths)
        if paths.env_file.stat().st_mode & 0o077:
            problems.append("insecure_configuration_permissions")
    except (OSError, ValueError):
        config = {}
        problems.append("missing_configuration")
    try:
        meta = json.loads(paths.metadata.read_text())
        if (meta.get("schema_version") != 1 or meta.get("package_version") != PACKAGE_VERSION
                or not ID_RE.fullmatch(meta.get("instance_id", ""))):
            problems.append("invalid_metadata")
        if config and config["ASSISTANT_IMAGE"] != meta.get("engine_image"):
            problems.append("engine_image_mismatch")
        hashes = meta["content_hashes"]
        required_paths = {"data/" + f for f in REQUIRED if f not in {"config.yaml", ".env.example"}}
        if not isinstance(hashes, dict) or not required_paths.issubset(hashes):
            problems.append("missing_package_content")
        else:
            for name, expected in hashes.items():
                if name not in {*("data/" + f for f in CONTENT), *ROOT_CONTENT}:
                    problems.append("invalid_metadata")
                    break
                if _digest(paths.root / name) != expected:
                    problems.append("content_drift")
    except (OSError, ValueError, KeyError, TypeError):
        problems.append("invalid_or_missing_content")
    return sorted(set(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--package", type=Path, default=Path(__file__).parent)
    prep.add_argument("--destination", type=Path, required=True)
    prep.add_argument("--instance-id", required=True)
    prep.add_argument("--owner-label", required=True)
    prep.add_argument("--engine-image", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--instance", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare_instance(args.package, args.destination, instance_id=args.instance_id,
                             owner_label=args.owner_label, engine_image=args.engine_image)
            print("instance_prepared")
            return 0
        problems = validate_instance(InstancePaths.at(args.instance))
        print(json.dumps({"valid": not problems, "reasons": problems}))
        return int(bool(problems))
    except (OSError, ValueError):
        print("instance_operation_refused")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
