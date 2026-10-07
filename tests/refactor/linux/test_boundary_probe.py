"""Actual preflight prerequisites and synthetic uncontained control.

These are not the full eight-case boundary qualification: candidate launch must
be possible before asserting isolation/egress/restart/termination coverage.
"""

def test_nonroot_linux_and_scrubbed_environment(probe):
    result = probe("python -c 'import os,sys; assert sys.platform == \"linux\"; "
                       "assert os.getuid() == 20001; "
                       "assert set(os.environ) <= {\"PATH\", \"PWD\", \"LC_CTYPE\"}; "
                       "print(\"linux uid=20001 sanitized environment\")'")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_uncontained_control_exposes_only_synthetic_sentinel(probe):
    result = probe("mkdir -p /tmp/synthetic-other-tenant; "
                       "printf SYNTHETIC-PRIVATE-SENTINEL > /tmp/synthetic-other-tenant/private; "
                       "cat /tmp/synthetic-other-tenant/private")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "SYNTHETIC-PRIVATE-SENTINEL"


def test_candidate_bubblewrap_is_installed(probe):
    result = probe("bwrap --version")
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_candidate_can_launch_curated_nonroot_namespace(probe):
    # Same required mount/user/pid/net/ipc mechanics as the current backend.
    # No relaxed seccomp/capabilities, host-root bind, or privileged execution.
    result = probe(
        "mkdir /tmp/workspace; "
        "bwrap --ro-bind /probe-rootfs / --dir /workspace "
        "--bind /tmp/workspace /workspace --chdir /workspace "
        "--proc /proc --dev /dev --tmpfs /tmp --tmpfs /run --tmpfs /var/run "
        "--unshare-user --unshare-pid --unshare-net --unshare-ipc "
        "--die-with-parent --new-session -- /bin/sh -ec "
        "'printf SYNTHETIC-AUTHORIZED-WRITE > /workspace/check; "
        "read -r value < /workspace/check || test -n \"$value\"; "
        "test \"$value\" = SYNTHETIC-AUTHORIZED-WRITE; printf allowed'"
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == "allowed"
