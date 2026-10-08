"""Throwaway syscall diagnostic under unchanged container policy; no security toggles."""

import ctypes
import errno
import json
import os
import platform
import subprocess
import sys

FLAGS = {
    "none": 0,
    "newuser": 0x10000000,
    "newmount": 0x00020000,
    "newpid": 0x20000000,
    "newnet": 0x40000000,
    "newipc": 0x08000000,
    "bwrap_namespaces": 0x10000000 | 0x00020000 | 0x20000000 | 0x40000000 | 0x08000000,
}


def child(operation: str, flags: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    if operation == "unshare":
        libc.unshare.argtypes = [ctypes.c_int]
        libc.unshare.restype = ctypes.c_int
        result = libc.unshare(flags)
    else:
        # Linux native clone ABI on the observed aarch64 target only.
        if platform.machine() != "aarch64":
            raise RuntimeError("diagnostic clone syscall number is aarch64-only")
        libc.syscall.restype = ctypes.c_long
        result = libc.syscall(
            ctypes.c_long(220),
            ctypes.c_ulong(flags | 17),
            ctypes.c_void_p(0),
            ctypes.c_void_p(0),
            ctypes.c_void_p(0),
            ctypes.c_ulong(0),
        )
        if result == 0:
            os._exit(0)
        if result > 0:
            _, status = os.waitpid(result, 0)
            if status != 0:
                raise RuntimeError("owned diagnostic child did not exit cleanly")
    error = ctypes.get_errno() if result < 0 else 0
    print(
        json.dumps(
            {
                "operation": operation,
                "flags": hex(flags),
                "succeeded": result >= 0,
                "errno": error,
                "reason": errno.errorcode.get(error, "OK"),
            }
        )
    )


if __name__ == "__main__":
    if len(sys.argv) == 3:
        child(sys.argv[1], int(sys.argv[2]))
    else:
        if platform.machine() != "aarch64":
            raise RuntimeError("diagnostic is aarch64-only")
        from pathlib import Path

        status = dict(
            line.split(":", 1)
            for line in Path("/proc/self/status").read_text().splitlines()
            if ":" in line
        )
        print(
            "policy-facts "
            + json.dumps(
                {
                    "architecture": platform.machine(),
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "cap_effective": int(status["CapEff"].strip(), 16),
                    "no_new_privileges": int(status["NoNewPrivs"].strip()),
                    "seccomp_mode": int(status["Seccomp"].strip()),
                }
            )
        )
        for operation in ("clone", "unshare"):
            for label, flags in FLAGS.items():
                result = subprocess.run(
                    [sys.executable, __file__, operation, str(flags)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                print(label, result.stdout.strip(), result.stderr.strip())
                if result.returncode != 0:
                    raise SystemExit(result.returncode)
