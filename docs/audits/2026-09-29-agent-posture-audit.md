# Agent Posture Audit — 2026-09-29

**Scope:** why the agent underperforms a simpler reference harness, and what
that says about where this platform has invested.
**Status:** findings recorded; remediation tracked in the plan below.
**Method:** head-to-head against `pi` on an identical task, same model, same
working directory.

---

## 1. The measurement

Identical prompt, identical model (`deepseek-v4.1-flash` via ollama cloud),
identical directory, run sequentially from a clean checkout of the fixture.

**Task:** *a small Python project is at `/tmp/bench/app`, its test suite is
failing, run the tests, find the bug, fix it, do not modify the test file.*

| | ours | pi |
|---|---|---|
| Wall clock | 228 s | **10 s** |
| Tool calls | 80 | ~8 |
| Input tokens | 372,212 | ~10–20× fewer |
| Local file reads | **0** | 16 (all `bash`) |
| Test suite after | ❌ still 1 failing | ✅ 3 passed |
| Agent's own report | *"I haven't been able to complete the task yet"* | success |

A second run, on a harder task (diagnosing a dependency failure with the answer
sitting in a git checkout the prompt named), reproduced the same shape: 247 s, 40
tool calls, 887k tokens, and the run **never produced an answer** — it returned
`Run limit reached: max_tokens_total (1000000) exceeded`.

**Conclusion: the agent is not fast. It is failing, and burning the budget
while failing.** The original hypothesis — that ours was "a few quick turns" —
was inverted.

## 2. Root causes

### 2.1 The filesystem boundary made ordinary work impossible

`files_*` refused **any** absolute path outside the user's data root:

```
files_list(path="/tmp/bench/app")   -> Error: Absolute path outside EA root
files_read(path="/tmp/bench/app/jobq.py") -> Error: Absolute path outside EA root
```

The agent was given a directory and could not open it with the tools designed to
read files. It probed with `files_list` **32 times** before falling back to
`shell_execute` — which works, so **the boundary was bypassed, just expensively**.

The escape hatch meant to solve this, `filesystem.workspace_root`, was
half-wired: its absolute-path branch validated against `paths.root` and never
against `workspace_root`, and the same check was duplicated in two branches.

**Fixed** in `8e928c6b`: `filesystem.allowed_roots` (data root plus configured
project roots), one boundary resolver instead of two, and an error that names
the allowed roots and points at `shell_execute`.

### 2.2 Nothing bounded a run that could not make progress

The loop already had a duplicate-call guard (US-003) for **identical**
`(tool, args)`. The expensive case is invisible to it: **the same tool with
different arguments** — 32 `files_list` calls across sibling directories, 7
`web_fetch` calls on one file via different URLs.

**Fixed:** a per-tool budget held on run state beside the existing guard.

### 2.3 A path could mean two things

`files_list(path=".")` resolves to the user data root, **not** the process cwd,
and answered `Empty directory: .` — a confident, wrong reply. An agent told
"the project is here" is actively misled.

**Fixed:** an empty listing names the directory it actually inspected.

## 3. Five documented promises that were false

| Claim | Reality | Now |
|---|---|---|
| *Child file access is confined to that workspace* (v0.6.18) | `workspace_id` is threaded to the path resolver, then **discarded**; the child is confined to the **user's** directory | corrected in the changelog |
| *Workspaces are isolated project containers* (#47) | all six `workspace_*` accessors return **user-scoped** paths | model corrected, decision recorded |
| *`SCHEDULING_SUBAGENT_ENABLED=1` enables scheduling* (v0.6.24) | **config.yaml beat every env var**; the endpoint still returned 403 | env now outranks yaml |
| *`MCP_EXPOSURE` sets the exposure* (v0.6.22) | same cause | same fix |
| *Token usage is recorded* (#48) | the Ollama parser **zeroed** every usage event; `cost_limit_usd` was unreachable | fixed |

**Pattern:** not one external report said *"it cannot do the job."* All five
said *"your documentation says X and the code does Y."* Two were found by
smoke-testing our own published image.

## 4. The structural finding

The **governance plane** received the investment: capability scoping, per-item
`allow/ask/deny`, HITL interrupts, typed receipt outcomes, an append-only audit
stream, three sandbox backends, a governance firewall, and a subagent
coordinator with frozen manifests and drift refusal.

The **execution plane** got the leftovers. The loop could not read a file it was
told to read, could not be stopped from repeating itself, and has no
self-verification at all — `verification.enabled` is `false` by default and the
741-character system prompt contains no instruction to plan, check or confirm
anything.

The reference harness has **no governance plane at all** — *"Pi does not include
a built-in permission system… containerize Pi"* — and it completes the same task
in 10 seconds.

**This is not an argument for deleting the governance plane.** It buys
per-tenant enforcement a container cannot give cheaply, which is the right
trade for the hosted product. It is an argument that the execution plane must
demonstrably work before more is added to it.

## 5. Open risks, named

- **`shell_execute` is not contained.** `root_path` is passed to the sandbox as
  a **cwd**, not a jail; a command can `cd /` and read anything. The tightened
  `files_*` boundary is a *tooling* boundary. This is #40 and it is the largest
  remaining gap between what we imply and what we enforce.
- **No self-verification.** A fast, unverified answer is the failure mode worth
  fearing, and nothing currently checks the work.
- **21 `workspace_files_dir()` call sites** know nothing about `allowed_roots`.
  Harmless today; each needs a decision when profiles land.
- **19 documented env vars against 65 config keys.** Now that env outranks
  yaml, un-documented ones are newly discoverable and unverified.

## 6. Decisions deliberately deferred

- **Profiles / `Assistants/<id>/` layout.** Two wrong designs were produced
  before the premise was checked: the desktop client **cannot** point at a remote
  server today (`api_base_url` defaults to a sentinel, the sidecar binds
  loopback only). Designing the data layout for an unbuilt mode is how the
  cache-as-replica and `replica/` mistakes happened. Deferred until the mode is
  specified. The one durable finding: the server **already** decides identity —
  *"the client never chooses `user_id`"*, `src/http/auth/__init__.py:48`.
- **Renaming `workspace_*` accessors.** ~21 call sites, cosmetic.
- **`Users/<id>/` → `Assistants/<id>/` data move.** Live data in `docker/data/`;
  needs the profile spec and a `desktop_migration.py`-shaped migration first.

## 7. Benchmark verdict (2026-10-01): parity achieved, and the gap was the instrument

The parity benchmark (`scripts/bench/agent_parity.sh`, ours vs the Pi harness,
same model `deepseek-v4.1-flash`, same task, n≥5 samples per round) now
**passes 6/6** at 12–36 s / 6–15 calls, against Pi's 7–12 s — inside the
script's own ≤2× gate.

The traced breakdown (Langfuse generations + ClickStack operational spans,
both live from a real run) says the remaining wall clock is **model time**:
of a 14 s run, **12.05 s is 7 model generations** (1.1–2.3 s each), ~1.3 s is
tool execution, and the framework adds under a second. There is no harness
overhead left worth chasing; Pi's profile is the same shape.

Getting there required three fixes, one per failure mode, and the instrument
itself was the biggest one:

1. **The instrument graded a phantom.** A `#` comment inside a
   backslash-continued launch chain detached `FILESYSTEM_ALLOWED_ROOTS` from
   the server, so the agent was refused the task directory, did the
   reasonable thing (copied the app into its own data dir and fixed the
   *copy*, tests ALL PASS there), and was failed against the untouched
   original. Ten samples across two rounds were invalidated by this —
   including a "negative result" for the `run_tests` tool that was never
   actually measured. The server now logs its effective filesystem boundary
   at startup and the benchmark asserts that precondition from the server's
   own output, aborting loudly when it is wrong.
2. **The duplicate guard served stale receipts for state-changing tools**
   (a `run_tests` re-run after an edit was answered with the pre-edit FAILING
   result, telling the model its fix did not work when it did). The guard now
   applies to read-only tools only; probing budgets still bound loops.
3. **`run_tests` shipped** — a bounded pytest runner over the sandbox seam,
   deliberately not `read_only`-auto-approved, described to point at the
   failing test as the spec.

Process lesson, recorded: the environment-precondition bug class (NoDecode,
launch-chain comments, stale ingest credentials) has now cost this project
three rounds of wasted measurement. Every one was found by reading one trace
end to end rather than aggregate results. The precondition check added to the
benchmark is the structural fix: the instrument now verifies itself.

Issue #37 (dropped stream wedges a session) is verified fixed against the
published image and closed: disconnect teardown releases the session lock,
`/message/cancel` works, and a new message is accepted within seconds.
