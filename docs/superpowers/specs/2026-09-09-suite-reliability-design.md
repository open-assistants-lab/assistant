# Suite Reliability Design

## Goal
Repair the stale provider-options regression test and make `timeout 900 uv run pytest tests/ -q` complete deterministically without weakening production safety tests.

## Root cause
`AgentLoop.run()` now invokes `_run_impl(messages, cost_tracker=...)`. `tests/api/test_provider_options.py` replaces `_run_impl` with a spy that accepts only `messages`, so the spy raises `TypeError` before it observes `RunConfig.provider_options`. `RunService` captures the agent error and the endpoint returns 200, leaving the test with `KeyError: "po"`. A minimal signature-only experiment passed 1/1, confirming the production provider-options path is correct.

## Isolation
Desktop tests invoke `desktop_main()`, which writes directly to `os.environ`. The fixture must snapshot the full mapping, restore it exactly after every test, clear config/path/message caches, and never rely on a fixed set of environment names.

## Hang and network policy
The two mismatched approval tests must have explicit per-test time bounds rather than being globally deselected. Tests must not make outbound HTTP; the desktop loopback server checks remain local integration tests. Registry/provider construction tests must inject fakes or cached fixture data at their HTTP boundary.

## Verification
First obtain `--durations=10` evidence for API and SDK suites. Then run the repaired targeted tests, API suite, and full 900-second suite. A timeout is failure evidence, not a waiver: record the final active test and add only a narrowly scoped explicit timeout/cleanup fix for the identified straggler.
