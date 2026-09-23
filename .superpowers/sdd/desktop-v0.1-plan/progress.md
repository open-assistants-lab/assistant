# SDD ledger — plan: /Users/eddy/Developer/Python/assistant/.superpowers/sdd/enterprise-roadmap/desktop-v0.1-plan.md

Pre-flight: native D3 work and backend reload work share the session-event/history contract; verified the native client consumes /conversation/turns and the route previously omitted session-log compression events.

Task D3-first-run: complete.
- Commits: 704bef73, ebff1e92, plus credential prerequisite b0b47c81.
- Evidence: `uv run native test` -> 114/114 passing; `uv run native build` -> ReleaseFast build.
- Coverage: selected-provider validation without sending classify secrets; Keychain handoff; catalog-to-picker transition; local-model scan; custom endpoint validation; reconnect; connector requests omit client identity.
- Mutation: removing the local-scan picker transition caused the dedicated test to fail (1 failed / 112 total), then source was restored.

Task D3-history-reload: complete.
- Commits: 311b2c20, f5333e58.
- Evidence: focused desktop contract suite 24 passed; conversation API suite 52 passed / 2 skipped; full Ruff passed; mypy ratchet passed at 42 errors against baseline 42.
- Contract: successful durable `context_compressed` events reload through `/conversation/turns` as `role=context` with `metadata.event_type=context_compressed`, preserving compact token text and omitting failed events.

Task D3-connector-identity: complete.
- Commit: ebff1e92.
- Ruling: native connector requests must omit client-selected identity; connector routes default to `default_user` server-side when no query is supplied. This preserves desktop ownership without breaking ConnectKit catalog access.
- Evidence: connector API tests 5 passed; Tools automation 10 passed / 2 skipped; 4-field connect-form automation passed.

Task D3-verification: complete for this implementation slice.
- Full pytest: 3160 passed / 26 skipped.
- Full Ruff: passed.
- Mypy ratchet: passed, 42 errors equal to baseline.
- Native tests: 114 passed.
- Native build: ReleaseFast passed.
- Frontend automation: 49 passed / 2 skipped.
- Current branch is clean.

Ruling: keep custom endpoint validation actionable but do not invent endpoint persistence; the existing backend contract validates an explicitly typed URL, while model selection remains in Settings because no approved custom-endpoint persistence/catalog contract exists.

D3 gate decision recorded in `docs/superpowers/plans/2026-09-24-d3-gate-decision.md`: PASS with non-blocking follow-ups. Inert Skills/Subagents navigation and dead quick-action enums remain separately scoped cleanup. Remaining roadmap work is the unwired Postgres receipt-store adapter for Jen; Gmail/email readback work is intentionally deferred.

Task D3-release-gates: complete.
- Evidence: `native validate app.zon` passed; `native doctor --manifest app.zon --strict` passed.
- Native tests: `uv run native test` -> 117/117 passed.
- Frontend automation: `bash tests/frontend_suite.sh --all` -> 49 passed / 2 skipped / 0 failed.
- ReleaseFast build: `native build` passed.
- Packaging: `native package --target macos --archive --signing adhoc` created `zig-out/package/assistant.app` and `assistant-0.1.0-macos-ReleaseFast.dmg`; `codesign --verify --deep --strict` passed.
- Note: the earlier `zig build test` command is not applicable because this zero-config app has no `build.zig`; `native test` is the supported test gate.
- Pre-existing unrelated working-tree changes remain intentionally uncommitted.
