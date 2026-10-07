"""Throwaway cached-image preflight, not a qualified execution backend."""

import subprocess
import tempfile
import uuid
from pathlib import Path

# Locally cached public assistant 0.6.37, pinned by immutable image ID; never pull.
IMAGE = "sha256:5a14a72ed183851752e631650b5d838c4396644def08c88dddf410545df300f6"


def run_probe(script: str, *, docker_host: str) -> subprocess.CompletedProcess[str]:
    if not docker_host.startswith("unix:///"):
        raise ValueError("preflight requires an explicitly selected local Docker Unix socket")
    docker = ["docker", "--host", docker_host]
    name = "authority-probe-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="alp-", dir="/tmp") as directory:
        cidfile = Path(directory) / "cid"
        command = [
            *docker, "run", "--pull=never", "--rm", "--name", name,
            "--label", "assistant.authority.probe=" + name,
            "--cidfile", str(cidfile), "--network=none", "--read-only",
            "--user=20001:20001", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--tmpfs", "/tmp:rw,nosuid,nodev", "--entrypoint", "/usr/bin/env",
            IMAGE, "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
            "/bin/sh", "-c", script,
        ]
        try:
            return subprocess.run(command, capture_output=True, text=True, timeout=45)
        finally:
            if cidfile.exists():
                cid = cidfile.read_text().strip()
                inspect = subprocess.run(
                    [*docker, "inspect", "--format",
                     '{{index .Config.Labels "assistant.authority.probe"}}', cid],
                    capture_output=True, text=True, timeout=15,
                )
                if inspect.returncode == 0 and inspect.stdout.strip() == name:
                    subprocess.run([*docker, "rm", "-f", cid], check=True,
                                   capture_output=True, timeout=15)
