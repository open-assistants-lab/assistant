# Namespace baseline checkpoint — author self-review

Scope: checkpoint `6fd55c67`, Task 1's pure validator/tests and
[artifact/provenance evidence](2026-10-07-namespace-policy-artifact-review.md),
against the [approved plan](../superpowers/plans/2026-10-07-linux-namespace-policy-attribution.md).
User explicitly requested self-review; this is not an independent/fresh-context
review, plan completion, baseline acceptance or Docker policy authorization.

## Strengths

- Checks raw SHA-256 before JSON decoding, rejects duplicate keys and non-finite
  values (including exponent overflow), preserves the original baseline bytes.
- Exact engine/source/blob/profile hashes and the compiled-Go-vs-JSON limitation
  are recorded. Matching source is not claimed as live filter/binary attestation.
- Real RED→GREEN tests exercise pure behavior, not mocked qualification.
- Earlier whole-suite failures are disclosed by name; no isolation guard was
  relaxed and no failure suppressed. Default Docker skips are not readiness proof.

## Important finding — fixed

`tests/refactor/linux/seccomp_review.py`, provenance URL validation:
the checkpoint verified a full `source_revision` but only required nonempty
`source_url`. A URL could point to a moving branch, different commit, unrelated
repository, insecure endpoint or credential/query-bearing location while the
record passed the claimed pinned-source check.

Regression: **8 invalid-source cases failed, 2 valid-source controls passed** before
fix. The validator now accepts only canonical HTTPS blob URLs for the same full
revision, at the two known Moby baseline paths: engine-vendored JSON and profiles
repository JSON. This binds textual source claims; it still does not prove that
bytes were acquired from that URL, verify a signature or authenticate user approval.

Synthetic original fixtures now use the actual canonical vendored path instead of
an invented path. No assertion was weakened. Real byte-frozen B still awaits user
review; no fixture promotion/P generation or Docker policy application occurred.

## Scope rulings and remaining limits

- Exact canonical source paths are intentionally narrow. Other legitimate source
  mirrors/paths require reviewed extension, not silent generic URL acceptance.
- Validator remains a structural review helper, not a complete OCI/Moby semantic
  validator or provenance authority. Actual reviewed bytes, source mapping,
  conditional-rule behavior and Docker comparisons still have their separate gates.
- New policy/worker/egress/termination implementation is not judged as completed:
  Tasks 2/3 and the full Linux qualification remain pending.
- Historical native subprocess crashes and order-sensitive tests are unresolved;
  this passing rerun is not evidence that they were fixed by the URL check.

No other important/critical defect was identified within this checkpoint's scope.
The whole execution-authority branch's independent final review remains pending.

## Fresh verification

- Targeted validator/provenance tests: **52 passed**, exit0.
- Full offline Python suite (authorized cached build dependencies, no downloads):
  **3,916 passed, 31 skipped, 69 warnings, 270.72 seconds, process exit0**.
- Scoped Ruff and mypy clean; diff checks clean.
- Previous failed full runs remain recorded in the artifact review and execution
  ledger. No product-readiness assertion is inferred from source/unit/wheel tests.

Assessment: local self-review fix verified; ready for **baseline provenance review**,
not main merge or MT/native readiness. No Docker/source acquisition/probe/policy
operation was performed during this review, only isolated offline verification.
