"""Throwaway cached-image probes with confirmed owned cleanup; not a backend."""

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, cast

IMAGE = "sha256:5a14a72ed183851752e631650b5d838c4396644def08c88dddf410545df300f6"


def run_recorded_probe(
    script: str,
    *,
    docker_host: str,
    image: str = IMAGE,
    seccomp_profile: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    if not docker_host.startswith("unix:///"):
        raise ValueError("explicit local Docker Unix socket required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("full immutable image reference required")
    docker = ["docker", "--host", docker_host]
    name = "authority-probe-" + uuid.uuid4().hex[:12]
    cid: str | None = None
    facts: dict[str, object] = {"requested_image": image, "cleanup_confirmed": False}

    def call(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([*docker, *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError("Docker operation failed; absence/cleanup not inferred")
        return result

    def inspect(identifier: str) -> dict[str, Any]:
        objects = json.loads(call(["inspect", identifier]).stdout)
        if not isinstance(objects, list) or len(objects) != 1:
            raise RuntimeError("ambiguous container metadata")
        if not isinstance(objects[0], dict):
            raise RuntimeError("invalid container metadata")
        return cast(dict[str, Any], objects[0])

    def inventory() -> list[str]:
        selector = "id=" + cid if cid else "name=^/" + name + "$"
        ids = call(
            ["ps", "--all", "--no-trunc", "--filter", selector, "--format", "{{.ID}}"]
        ).stdout.splitlines()
        if len(ids) > 1 or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
            raise RuntimeError("ambiguous owned inventory")
        if cid is not None and ids and ids != [cid]:
            raise RuntimeError("inventory returned a different exact container ID")
        return ids

    metadata = json.loads(call(["image", "inspect", image]).stdout)
    if (
        len(metadata) != 1
        or metadata[0].get("Os") != "linux"
        or metadata[0].get("Architecture") not in {"arm64", "aarch64"}
    ):
        raise ValueError("diagnostic requires Linux aarch64 image")
    facts["architecture"] = "aarch64"
    command = [
        "create",
        "--pull=never",
        "--name",
        name,
        "--label",
        "assistant.authority.probe=" + name,
        "--network=none",
        "--read-only",
        "--user=20001:20001",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev",
    ]
    if seccomp_profile is not None:
        command += ["--security-opt", "seccomp=" + str(seccomp_profile.resolve(strict=True))]
    command += [
        "--entrypoint",
        "/usr/bin/env",
        image,
        "-i",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "/bin/sh",
        "-c",
        script,
    ]
    try:
        created = call(command).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", created):
            raise RuntimeError("missing valid created container ID")
        cid = created
        container = inspect(cid)
        if (
            container.get("Id") != cid
            or container.get("Config", {}).get("Labels", {}).get("assistant.authority.probe")
            != name
        ):
            raise RuntimeError("container ownership mismatch")
        facts.update(image_id=container["Image"], container_id=cid, argv=[*docker, *command])
        # A diagnostic child failure is evidence, not an API-absence assertion.
        process = subprocess.run(
            [*docker, "start", "--attach", cid], capture_output=True, text=True, timeout=45
        )
        return process, facts
    finally:
        ids = inventory()
        if ids:
            container = inspect(ids[0])
            if (
                container.get("Id") != ids[0]
                or container.get("Config", {}).get("Labels", {}).get("assistant.authority.probe")
                != name
            ):
                raise RuntimeError("refusing cleanup of unowned container")
            call(["rm", "-f", ids[0]])
        if inventory():
            raise RuntimeError("owned removal not confirmed")
        facts["cleanup_confirmed"] = True


def run_probe(
    script: str, *, docker_host: str, image: str = IMAGE
) -> subprocess.CompletedProcess[str]:
    process, _ = run_recorded_probe(script, docker_host=docker_host, image=image)
    return process
