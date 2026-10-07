"""Hermetic developer pytest launcher; no installs, live inference or customer stores."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


def verification_environment(
    root: Path, scratch: Path, environ: Mapping[str, str]
) -> dict[str, str]:
    # Allowlist, not credential-name guessing. No proxies, cloud credentials,
    # deployment roots, inherited provider settings or shared API tokens.
    env = {key: environ[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT") if key in environ}
    env.update(
        HOME=str(scratch / "home"),
        XDG_CACHE_HOME=str(scratch / "cache"),
        TMPDIR=str(scratch / "tmp"),
        DEPLOYMENT_DATA_ROOT=str(scratch / "data_root"),
        DEPLOYMENT_DATA_PATH=str(scratch / "data"),
        LOGGING_JSON_DIR=str(scratch / "logs"),
        LANGFUSE_ENABLED="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        UV_OFFLINE="1",
        PIP_NO_INDEX="1",
        PYTHONPATH=str(root),
    )
    return env


def copy_cached_build_dependencies(source: Path, destination: Path) -> None:
    """Copy approved public build packages into an owned offline cache.

    No operator cache writes or transitive copying of editable/private packages.
    """
    source = source.resolve(strict=True)
    destination.mkdir(parents=True, exist_ok=False)
    for package in ("hatchling", "packaging", "pathspec", "pluggy", "tomlkit", "tomli", "trove-classifiers", "editables"):
        wheel_root = source / "wheels-v5" / "pypi" / package
        if not wheel_root.is_dir():
            continue
        if wheel_root.is_symlink():
            raise ValueError("build-package index must not be a symlink")
        index = source / "simple-v18" / "pypi" / f"{package}.rkyv"
        if index.is_symlink():
            raise ValueError("build-package version index must not be a symlink")
        if index.is_file():
            copied_index = destination / "simple-v18" / "pypi" / index.name
            copied_index.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(index, copied_index)
        copied_root = destination / "wheels-v5" / "pypi" / package
        copied_root.mkdir(parents=True)
        for entry in wheel_root.iterdir():
            if entry.is_symlink():
                archive = entry.resolve(strict=True)
                relative = archive.relative_to(source) if archive.is_relative_to(source) else None
                if relative is None or relative.parts[0] != "archive-v0":
                    raise ValueError("build package points outside cached archives")
                for item in archive.rglob("*"):
                    if item.is_symlink() and not item.resolve(strict=True).is_relative_to(archive):
                        raise ValueError("cached archive contains an escaping reference")
                copied_archive = destination / relative
                if not copied_archive.exists():
                    shutil.copytree(archive, copied_archive)
                (copied_root / entry.name).symlink_to(copied_archive, target_is_directory=True)
            elif entry.is_file():
                shutil.copy2(entry, copied_root / entry.name)
            else:
                raise ValueError("unexpected cached build-package entry")


def _python(root: Path) -> str:
    candidates = [root / ".venv/bin/python"]
    if root.parent.name == ".worktrees":
        candidates.append(root.parent.parent / ".venv/bin/python")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--build-cache", type=Path, help="copy approved cached build packages offline")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    pytest_args = args.pytest_args
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    root = Path(__file__).resolve().parents[1]
    shadow = root / ".env"
    # Exclusive and owned: never overwrite or remove another actor's env file.
    fd = os.open(shadow, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as env_file:
        env_file.write("# Owned offline-verifier shadow; no deployment credentials.\n")
    owned_stat = shadow.stat()
    try:
        with tempfile.TemporaryDirectory(prefix="ar-", dir="/tmp") as temporary:
            scratch = Path(temporary)
            env = verification_environment(root, scratch, os.environ)
            if args.build_cache is not None:
                cached = scratch / "build-cache"
                copy_cached_build_dependencies(args.build_cache, cached)
                env["UV_CACHE_DIR"] = str(cached)
            for key in (
                "HOME",
                "XDG_CACHE_HOME",
                "TMPDIR",
                "DEPLOYMENT_DATA_ROOT",
                "DEPLOYMENT_DATA_PATH",
                "LOGGING_JSON_DIR",
            ):
                Path(env[key]).mkdir(parents=True)
            command = [
                _python(root),
                "-m",
                "pytest",
                "-p",
                "tests.refactor.offline_guard",
                *pytest_args,
            ]
            child = subprocess.Popen(command, cwd=root, env=env, start_new_session=True)
            try:
                status = child.wait(timeout=args.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                status = 124
            print(f"Verification process exit: {status}", flush=True)
            return status
    finally:
        current = shadow.stat()
        if (current.st_dev, current.st_ino) != (owned_stat.st_dev, owned_stat.st_ino):
            raise RuntimeError("verifier shadow replaced; refusing to delete it")
        shadow.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
