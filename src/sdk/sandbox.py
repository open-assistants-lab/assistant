"""SandboxBackend seam (R-SB1, SB1-1/SB1-2) — service-definition split.

Service Definition: `SandboxBackend` protocol — every process-spawning tool
runs its subprocesses through a backend chosen by config, so trust-tier
enforcement (spec §6.2 table) is by construction and swapping isolation
levels is a config change, not a code change.

Providers: `NullSandboxBackend` (passthrough; tests), `SoftSandboxBackend`
(Phase-2 default for trusted users: workspace-cwd, scrubbed env, resource
caps, static write-path validation). Kernel-enforced isolation is the
Soft+UID refinement / Phase-3 hard backend (bwrap/runc) — out of scope here.

Consumers: `code_execute` (SB1-3), `shell_execute` + `cli_adapter` +
`browser_agent` (SB1-4). Policy (allowlist, metacharacter ban) stays IN
FRONT of the seam — the seam is transport, not policy.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from src.app_logging import get_logger

logger = get_logger()

# Env allowlist: only these pass through to sandboxed processes.
_ENV_ALLOWLIST = {
    "PATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "USER",
    "SHELL",
    "TERM",
    "TZ",
}
# Env denylist (checked before allowlist for defense in depth on names like
# "OLLAMA_API_KEY" that neither end in a listed suffix nor equal a key).
_ENV_DENY_PATTERNS = re.compile(
    r"(?i)api_key|_token$|^token|secret|password|credential|passwd|private",
)

# Static write-target scan (soft/advisory — kernel isolation is Soft+UID,
# explicitly not implemented here): quoted absolute paths in source are
# resolved against the workspace root; traversal/home shortcuts checked.
_ABS_PATH = re.compile(r"['\"](/[^'\"]+)['\"]")
_TRAVERSAL = re.compile(r"(?:^|[\s'\"=(])\.\./|(^|[\s'\"=(])~/")
_WRITE_CALLS = re.compile(
    r"(?i)\b(?:open|write_text|write_bytes|shutil|os\.remove|os\.unlink|os\.makedirs|os\.mkdir)\b",
)


@dataclass
class SandboxLimits:
    """Resource caps applied to sandboxed execution."""

    timeout_seconds: float = 30.0
    max_output_bytes: int = 200_000
    env_mode: str = "scrubbed"  # "scrubbed" | "inherit"
    memory_mb: int = 512  # SB1 review P1: RLIMIT_AS cap


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@runtime_checkable
class SandboxBackend(Protocol):
    """Service definition: uniform subprocess transport for tools."""

    def run(
        self,
        argv: list[str],
        cwd: Path,
        limits: SandboxLimits | None = None,
        *,
        env_extra: dict[str, str] | None = None,
    ) -> SandboxResult: ...

    def validate_write_path(self, path: Path, workspace_root: Path) -> str | None:
        """Return an error string when *path* escapes *workspace_root*."""
        ...

    def validate_source(self, source: str, workspace_root: Path) -> str | None:
        """Static check of a script's write targets (soft backends)."""
        ...

    def setup(self, user_id: str, workspace_id: str) -> None: ...

    def teardown(self) -> None: ...


def scrub_env(mode: str = "scrubbed") -> dict[str, str]:
    """Build the child-process environment (audit-grade: no secrets leak)."""
    if mode == "inherit":
        return dict(os.environ)
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if _ENV_DENY_PATTERNS.search(key):
            continue
        if key in _ENV_ALLOWLIST or key.endswith(("_DIR", "_ROOT", "_HOST")):
            env[key] = value
    return env


def path_outside_workspace(path: Path, workspace_root: Path) -> bool:
    try:
        path.resolve().relative_to(workspace_root.resolve())
        return False
    except ValueError:
        return True


class NullSandboxBackend:
    """Passthrough backend (tests/dev): full env, no caps, no validation."""

    def run(
        self,
        argv: list[str],
        cwd: Path,
        limits: SandboxLimits | None = None,
        *,
        env_extra: dict[str, str] | None = None,
    ) -> SandboxResult:
        lim = limits or SandboxLimits()
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, no shell
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=lim.timeout_seconds,
                env=env_extra,
            )
            return SandboxResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired:
            return SandboxResult(-1, timed_out=True)

    def validate_write_path(self, path: Path, workspace_root: Path) -> str | None:
        return None  # passthrough: no validation

    def validate_source(self, source: str, workspace_root: Path) -> str | None:
        return None

    def setup(self, user_id: str, workspace_id: str) -> None:
        return None

    def teardown(self) -> None:
        return None


class SoftSandboxBackend:
    """Phase-2 default for trusted users.

    - cwd is forced to the caller-provided workspace root
    - env is scrubbed (allowlist + secret denylist)
    - timeout + output caps enforced
    - resource limits (FSIZE/CPU/NPROC) via preexec_fn
    - static write-target validation: `validate_source` scans code for
      absolute outside-workspace write targets BEFORE spawn; `validate_write_path`
      resolves any explicit path against the workspace root.
    """

    def run(
        self,
        argv: list[str],
        cwd: Path,
        limits: SandboxLimits | None = None,
        *,
        env_extra: dict[str, str] | None = None,
    ) -> SandboxResult:
        lim = limits or SandboxLimits()
        env = scrub_env(lim.env_mode)
        if env_extra:
            env.update(env_extra)

        def _preexec() -> None:  # pragma: no cover - runs in child
            import resource

            resource.setrlimit(resource.RLIMIT_FSIZE, (lim.max_output_bytes * 8, lim.max_output_bytes * 8))
            resource.setrlimit(resource.RLIMIT_CPU, (int(lim.timeout_seconds) + 2, int(lim.timeout_seconds) + 2))
            # M4/SB1 review P1: memory cap — a runaway code_execute must not
            # OOM the host. RLIMIT_NPROC kept per docstring claim.
            # RLIMIT_AS/NPROC are Linux-only; macOS lacks both — cap where
            # the platform supports it (review P1 was about host OOM).
            for rname, value in (
                ("RLIMIT_AS", getattr(lim, "memory_mb", 512) * 1024 * 1024),
                ("RLIMIT_NPROC", 256),
            ):
                rlimit = getattr(resource, rname, None)
                if rlimit is not None:
                    try:
                        resource.setrlimit(rlimit, (value, value))
                    except (OSError, ValueError):
                        pass
            try:
                os.setsid()
            except OSError:
                pass

        try:
            proc = subprocess.run(  # noqa: S603 - argv list, no shell
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=lim.timeout_seconds,
                env=env,
                preexec_fn=_preexec,
            )
            return SandboxResult(
                proc.returncode,
                proc.stdout[: lim.max_output_bytes],
                proc.stderr[: lim.max_output_bytes],
            )
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            return SandboxResult(
                -1,
                str(out)[: lim.max_output_bytes],
                str(err)[: lim.max_output_bytes],
                timed_out=True,
            )

    def validate_write_path(self, path: Path, workspace_root: Path) -> str | None:
        if path_outside_workspace(path, workspace_root):
            return (
                f"Rejected: {path} is outside the workspace root "
                f"{workspace_root} (soft sandbox write-path validation)."
            )
        return None

    def validate_source(self, source: str, workspace_root: Path) -> str | None:
        """Static scan: reject code whose write targets escape the workspace."""
        if not _WRITE_CALLS.search(source):
            return None
        if _TRAVERSAL.search(source):
            return (
                "Rejected: source uses traversal (../.. or ~/..) — soft "
                "sandbox write-path validation."
            )
        root = workspace_root.resolve()
        for match in _ABS_PATH.finditer(source):
            candidate = Path(match.group(1))
            if path_outside_workspace(candidate, root):
                return (
                    "Rejected: source references a path outside the "
                    f"workspace ({match.group(1)}) — soft sandbox "
                    "write-path validation."
                )
        return None

    def setup(self, user_id: str, workspace_id: str) -> None:
        return None

    def teardown(self) -> None:
        return None


def get_sandbox_backend() -> Any:
    """Select the backend from settings (config change, not code change)."""
    from src.config import get_settings

    settings = get_settings()
    cfg = getattr(settings, "sandbox", None)
    backend = getattr(cfg, "backend", "soft") if cfg else "soft"
    if backend == "null":
        # SB1 review P1: null = full inherited env, no caps — never silent.
        logger.warning(
            "sandbox.null_backend_selected",
            {"detail": "SANDBOX_BACKEND=null: no caps, full env inheritance; test-only"},
        )
        return NullSandboxBackend()
    return SoftSandboxBackend()
