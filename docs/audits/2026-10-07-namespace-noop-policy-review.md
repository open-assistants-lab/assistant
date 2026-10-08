# No-op namespace attribution — exact artifact execution review

Status: **baseline accepted; candidate prepared; custom-policy execution NOT authorized/run**.
User accepted the version-linked baseline after checkpoint `dda6bb3c`; raw B and
`baseline-provenance.json` frozen in `37b07acf`. It is not a live-filter export.

## Complete files and hashes

- [B: raw byte-frozen baseline](../../tests/refactor/linux/fixtures/seccomp/docker-29.8.0-baseline.json)
  — 13,470 bytes, 33 rules;
  SHA-256 `536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`.
- [P: complete canonical candidate](../../tests/refactor/linux/fixtures/seccomp/docker-29.8.0-noop-unshare.json)
  — 16,852 bytes, 34 rules;
  SHA-256 `6a744f09389794f4de85126a9c853d90a73226823270ac34cb1186a3ba3c5a42`.
- [Exact structural delta](../../tests/refactor/linux/fixtures/seccomp/noop-only-delta.json).
- [Readable diff](../../tests/refactor/linux/fixtures/seccomp/noop-only-review.diff):
  comparison uses canonical B for display only; actual B execution uses the raw
  approved bytes/hash above. P canonical formatting reorders object keys, not
  original rule order or meaning. No other structural field changes.

Sole addition at `/syscalls/33`:

```json
{"names":["unshare"],"action":"SCMP_ACT_ALLOW","args":[{"index":0,"value":0,"op":"SCMP_CMP_EQ"}]}
```

It permits **only unshare(0)**. No namespace-bearing unshare, clone/clone3, setns,
mount, privilege or network permission is added. Original conditional rules and
all other fields remain unchanged; false/0.0 cannot substitute for integer0.
This applies to the whole owned outer container, not only a chosen inner process.

## Proposed fixed execution recipe — not executed yet

Same immutable cached public image for all cases:
`sha256:5a14a72ed183851752e631650b5d838c4396644def08c88dddf410545df300f6`.
Use the explicitly selected local Docker Unix endpoint, generated owned name/CID
and label; no global/default/daemon/customer-container changes.

All three use the following argv shape (no shell-generated Docker options):

```text
docker --host <reviewed-local-unix-endpoint> create --pull=never
  --name <generated-owned-name> --label assistant.authority.probe=<same-name>
  --network=none --read-only --user=20001:20001 --cap-drop=ALL
  --security-opt=no-new-privileges:true --tmpfs /tmp:rw,nosuid,nodev
  [only for B/P: --security-opt=seccomp=<owned-read-only-reviewed-profile-snapshot>]
  --entrypoint=/usr/bin/env <same-immutable-image-id>
  -i PATH=/usr/local/bin:/usr/bin:/bin /bin/sh -c <fixed-trusted-diagnostic>
docker --host <same-endpoint> start --attach <recorded-owned-CID>
```

D: no custom profile. B: raw reviewed B. P: reviewed P. D/B must pass matching
fourteen-case observations, runtime facts and confirmed cleanup **before P starts**.
D/B ordinary clone succeeds, all other diagnostic rows EPERM. P additionally
allows only no-op unshare; all namespace rows still EPERM. Any mismatch stops.

Client reads the profile; it is not mounted into execution. No host/customer stores,
Docker socket, source/.env, ports or secret environment. No root/CAP_SYS_ADMIN,
seccomp-unconfined, no-new-privileges removal or global cleanup. Creation/attachment,
metadata capture, exact-ID/label removal and confirmed absence follow Task 3's
reviewed helper implementation; that implementation is still pending.

## Preparation evidence and limitations

Delta tests observed RED: 16 missing-function failures, then GREEN. Frozen real
artifact checksum/structural test added as a data-verification check.
Latest full offline suite: **3,933 passed, 31 skipped, 69 warnings, 266.78s, exit0**.
Scoped Ruff/mypy clean. Previous intermittent failures remain recorded/unresolved.
No default Docker skips or unit/wheel assertions qualify the Linux boundary.

**Requested permission:** review these exact B/P bytes/hashes/diff and authorize
only this D/B/P no-op diagnostic comparison in owned disposable containers.
Approval does not permit a broader namespace allowlist, privilege/security toggle,
permanent worker backend, daemon/customer changes, main integration or publication.
Passing this comparison only supports the no-op filter attribution; full bwrap
and eight-case filesystem/process/egress/restart/termination qualification remain.
