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
