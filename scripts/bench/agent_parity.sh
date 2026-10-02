#!/usr/bin/env bash
# Agent parity benchmark: this agent vs `pi`, same model, same task, same directory.
#
# The target is PARITY with a reference harness, not a number we invented.
#   scripts/bench/agent_parity.sh              # default task
#   OLLAMA_API_KEY=... scripts/bench/agent_parity.sh
#
# Scoring is on the objective signal first (does the suite go green), then wall
# clock. Artifacts land in $BENCH_DIR for inspection.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BENCH_DIR="${BENCH_DIR:-/tmp/agent-parity}"
MODEL="${PARITY_MODEL:-deepseek-v4.1-flash}"
API_PORT="${PARITY_API_PORT:-8079}"

pass=0; fail=0
note() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }

: "${OLLAMA_API_KEY:?set OLLAMA_API_KEY}"

# ---------------------------------------------------------------- fixture
build_fixture() {
  rm -rf "$BENCH_DIR"
  mkdir -p "$BENCH_DIR/app"

  # The bug: retry() delegates to enqueue(), whose dedup guard rejects the
  # re-queue, so a retried job is silently dropped.
  cat > "$BENCH_DIR/app/jobq.py" <<'PY'
"""A tiny durable job queue backed by a JSONL file."""

import json
from pathlib import Path

DATA = Path(__file__).with_name("jobs.jsonl")


class Queue:
    def __init__(self, path: Path = DATA):
        self.path = Path(path)
        self._seen: set[str] = set()
        self._done: set[str] = set()
        self._attempts: dict[str, int] = {}

    def jobs(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def enqueue(self, job_id: str, payload: dict) -> bool:
        if job_id in self._seen:
            return False
        self._seen.add(job_id)
        self._attempts[job_id] = 1
        with self.path.open("a") as fh:
            fh.write(json.dumps({"id": job_id, "payload": payload, "done": False}) + "\n")
        return True

    def mark_done(self, job_id: str) -> None:
        self._done.add(job_id)

    def retry(self, job_id: str, payload: dict) -> bool:
        if job_id in self._done:
            raise ValueError("cannot retry a completed job")
        return self.enqueue(job_id, payload)
PY

  cat > "$BENCH_DIR/app/test_queue.py" <<'PY'
import pytest
from jobq import Queue


def test_enqueue_persists(tmp_path):
    q = Queue(tmp_path / "j.jsonl")
    assert q.enqueue("a", {"x": 1}) is True
    assert q.enqueue("a", {"x": 1}) is False
    assert [j["id"] for j in q.jobs()] == ["a"]


def test_retry_requeues_and_increments_attempts(tmp_path):
    q = Queue(tmp_path / "j.jsonl")
    q.enqueue("a", {"x": 1})
    assert q.retry("a", {"x": 1}) is True
    assert [j["id"] for j in q.jobs()] == ["a", "a"]
    assert q._attempts["a"] == 2


def test_completed_cannot_be_retried(tmp_path):
    q = Queue(tmp_path / "j.jsonl")
    q.enqueue("a", {})
    q.mark_done("a")
    with pytest.raises(ValueError):
        q.retry("a", {})
PY

  cat > "$BENCH_DIR/TASK.txt" <<'EOF'
A small Python project is at /tmp/agent-parity/app. Its test suite is failing.

Run the tests, find the bug, fix it, and make the suite pass. Do not modify
the test file. Run the tests once more when you are finished and report the result.
EOF

  ( cd "$BENCH_DIR/app" \
    && git init -q && git add -A \
    && git -c user.email=bench@local -c user.name=bench commit -qm "fixture (buggy)" )
}

reset_fixture() { ( cd "$BENCH_DIR/app" && git checkout -q . && rm -f jobs.jsonl ); }
run_tests()    { ( cd "$BENCH_DIR/app" && uv run --project "$REPO_ROOT" pytest -q test_queue.py 2>&1 | tail -1 ); }

# ------------------------------------------------------------------- arms
note "1/5 baseline — the suite must start red"
build_fixture
BASE="$(run_tests)"
case "$BASE" in
  *"1 failed"*) ok "fixture starts broken ($BASE)" ;;
  *)            bad "fixture did not start broken: $BASE"; exit 1 ;;
esac

note "2/5 reference arm — pi"
PI_ELAPSED=-1; PI_RESULT="(skipped)"
if command -v pi >/dev/null 2>&1; then
  mkdir -p "$BENCH_DIR/piagent"
  python3 - "$BENCH_DIR/piagent/models.json" <<PY
import json, os, sys
json.dump({"providers": {"ollama": {
    "api": "openai-completions",
    "apiKey": os.environ["OLLAMA_API_KEY"],
    "baseUrl": "https://ollama.com/v1",
    "models": [{"id": "$MODEL", "contextWindow": 262144,
                "input": ["text"], "reasoning": True}]}}}, open(sys.argv[1], "w"))
PY
  reset_fixture
  START=$(date +%s)
  ( cd "$BENCH_DIR/app" && PI_CODING_AGENT_DIR="$BENCH_DIR/piagent" \
      timeout 900 pi --mode json --print --no-session \
        --provider ollama --model "$MODEL" \
        "$(cat "$BENCH_DIR/TASK.txt")" \
        > "$BENCH_DIR/pi.json" 2> "$BENCH_DIR/pi.err" )
  PI_ELAPSED=$(( $(date +%s) - START ))
  PI_RESULT="$(run_tests)"
  case "$PI_RESULT" in
    *"3 passed"*) ok "pi fixed it in ${PI_ELAPSED}s" ;;
    *)            bad "pi left it failing after ${PI_ELAPSED}s ($PI_RESULT)" ;;
  esac
else
  bad "pi is not installed; the reference arm cannot run"
fi

note "3/5 second reference arm — opencode"
# Same fixture, same task text, same model. opencode runs with its own global
# config; the task dir has no .opencode/, so it cannot see the repo's plugins
# or skills — a clean environment. Non-interactive `opencode run`.
#
# --auto grants the permissions the other arms already have: ours gets the
# task dir via FILESYSTEM_ALLOWED_ROOTS, pi runs unconstrained, but opencode's
# run mode AUTO-REJECTS permission requests (measured: one rejected bash call,
# immediate exit in 5s — that graded a permission policy, not the agent).
OPENCODE_MODEL="${OPENCODE_MODEL:-ollama-cloud/$MODEL}"
OC_ELAPSED=-1; OC_RESULT="(skipped)"
if command -v opencode >/dev/null 2>&1; then
  reset_fixture
  START=$(date +%s)
  ( cd "$BENCH_DIR/app" \
      && timeout 900 opencode run --auto --model "$OPENCODE_MODEL" \
        "$(cat "$BENCH_DIR/TASK.txt")" \
        > "$BENCH_DIR/opencode.log" 2> "$BENCH_DIR/opencode.err" )
  OC_ELAPSED=$(( $(date +%s) - START ))
  OC_RESULT="$(run_tests)"
  case "$OC_RESULT" in
    *"3 passed"*) ok "opencode fixed it in ${OC_ELAPSED}s" ;;
    *)            bad "opencode left it failing after ${OC_ELAPSED}s ($OC_RESULT)" ;;
  esac
else
  bad "opencode is not installed; the second reference arm cannot run"
fi

note "4/5 subject arm — this agent"
# Precondition: if the harness cannot read the task file, any measurement is
# invalid. Fail loudly rather than reporting the agent's behaviour as the result.
if ! FILESYSTEM_ALLOWED_ROOTS="$BENCH_DIR/app" DEPLOYMENT_DATA_ROOT="$BENCH_DIR/data" \
     DEPLOYMENT_DATA_PATH="$BENCH_DIR/path" uv run --project "$REPO_ROOT" \
     python - "$BENCH_DIR/app/jobq.py" <<'PYCHECK'
import asyncio, sys
from src.sdk.tools_core.filesystem import files_read
r = asyncio.run(files_read.ainvoke({"path": sys.argv[1], "user_id": "parity"}))
content = str(getattr(r, "content", r))
sys.exit(0 if "not under any allowed root" not in content else 1)
PYCHECK
then
  printf '  %sHARNESS INVALID%s — the agent cannot read %s/app/jobq.py.\n' "\033[31m" "\033[0m" "$BENCH_DIR"
  echo "  Set FILESYSTEM_ALLOWED_ROOTS to include the task dir before trusting a result."
  exit 1
fi
ok "harness can read the task file"


reset_fixture
mkdir -p "$BENCH_DIR/data" "$BENCH_DIR/path"
# FILESYSTEM_ALLOWED_ROOTS is REQUIRED here, not optional.
#
# The task directory is deliberately outside the agent's data root, so without
# this `files_read` refuses the exact file the task points at. The agent then
# adapts by routing around the broken tool — measured here as reading a .py file
# through `browser_open file://...` and launching a headless browser three times,
# which was most of a 768s run. Four consecutive runs were misread as agent
# behaviour until the refusal was reproduced directly.
#
# The precondition check below makes this class of harness bug loud.
DEPLOYMENT_DATA_ROOT="$BENCH_DIR/data" DEPLOYMENT_DATA_PATH="$BENCH_DIR/path" \
# NOTE: this launch chain must stay CONTIGUOUS. A '#' comment between
# backslash-continued lines DETACHES every var before it from the command —
# which is exactly how FILESYSTEM_ALLOWED_ROOTS was silently lost (and 10
# benchmark samples invalidated). Comments live ABOVE the chain, never inside.
# Traces: our own Langfuse (semantic+operational, full payload) AND our
# ClickStack (operational, filtered). ClickStack ingest needs the plain
# OTLP_AUTH_TOKEN — the MCP bearer is a different credential and 401s here.
# The benchmark accepts product tracing explicitly (the gate is off by
# default everywhere now).
LANGFUSE_ENABLED=1 \
FILESYSTEM_ALLOWED_ROOTS="$BENCH_DIR/app" \
OBSERVABILITY__OTEL__ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel/v1/traces" \
OBSERVABILITY__OTEL__HEADERS="$(python3 -c "import base64,os,json;print(json.dumps({'authorization':'Basic '+base64.b64encode((os.environ['LANGFUSE_PUBLIC_KEY']+':'+os.environ['LANGFUSE_SECRET_KEY']).encode()).decode()}))")" \
OBSERVABILITY__CLICKSTACK__ENDPOINT="$CLICKSTACK_BASE_URL/otlp/v1/traces" \
OBSERVABILITY__CLICKSTACK__HEADERS="{\"authorization\":\"$OTLP_AUTH_TOKEN\"}" \
DEPLOYMENT_MODE=local API_PORT="$API_PORT" \
  nohup uv run assistant http > "$BENCH_DIR/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 45); do
  curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1 && break
  sleep 2
done

# Precondition: the instrument verifies ITSELF. The server logs the effective
# filesystem boundary at startup; if the app dir is not writable the agent
# will route around the refusal (copy the app into its own data dir and fix
# the COPY), and this harness would grade a phantom. 10 samples were
# invalidated by exactly this before the check existed.
for _ in $(seq 1 15); do
  grep -q "filesystem boundary effective" "$BENCH_DIR/server.log" 2>/dev/null && break
  sleep 1
done
if ! grep "filesystem boundary effective" "$BENCH_DIR/server.log" 2>/dev/null | grep -q "agent-parity/app"; then
  echo "PRECONDITION FAILED: server's effective filesystem.allowed_roots does not include $BENCH_DIR/app" >&2
  echo "The agent cannot edit the app dir; results would grade a phantom copy. Aborting." >&2
  sed -n '1,40p' "$BENCH_DIR/server.log" >&2
  kill $SERVER_PID 2>/dev/null
  exit 3
fi

OUR_ELAPSED=-1; OUR_RESULT="(server did not start)"; OUR_CALLS='?'
if curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null 2>&1; then
  python3 - "$BENCH_DIR/req.json" <<PY
import json, sys
json.dump({"user_id": "parity", "workspace_id": "personal",
           "model": "ollama-cloud:$MODEL",
           "message": open("$BENCH_DIR/TASK.txt").read()}, open(sys.argv[1], "w"))
PY
  START=$(date +%s)
  curl -s --max-time 1200 -X POST "http://127.0.0.1:$API_PORT/v1/message" \
    -H 'Content-Type: application/json' -d @"$BENCH_DIR/req.json" \
    -o "$BENCH_DIR/ours.json" >/dev/null
  OUR_ELAPSED=$(( $(date +%s) - START ))
  OUR_RESULT="$(run_tests)"
  OUR_CALLS=$(python3 - "$BENCH_DIR/ours.json" <<'PY' 2>/dev/null || echo '?'
import json, sys
print(len(json.load(open(sys.argv[1])).get("tool_calls") or []))
PY
)
  case "$OUR_RESULT" in
    *"3 passed"*) ok "agent fixed it in ${OUR_ELAPSED}s / $OUR_CALLS calls" ;;
    *)            bad "agent left it failing after ${OUR_ELAPSED}s / $OUR_CALLS calls ($OUR_RESULT)" ;;
  esac
else
  bad "agent server failed to start — see $BENCH_DIR/server.log"
fi
kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null

# ----------------------------------------------------------------- verdict
note "5/5 verdict"
printf '  %-10s %-12s %s\n' "harness" "wall clock" "result"
printf '  %-10s %-12s %s\n' "pi"   "${PI_ELAPSED}s"   "$PI_RESULT"
printf '  %-10s %-12s %s\n' "opencode" "${OC_ELAPSED}s" "$OC_RESULT"
printf '  %-10s %-12s %s\n' "ours" "${OUR_ELAPSED}s"   "$OUR_RESULT (tool calls: $OUR_CALLS)"
echo

if [ "$PI_ELAPSED" -gt 0 ] && [ "$OUR_ELAPSED" -gt 0 ] \
   && [ $(( OUR_ELAPSED > PI_ELAPSED * 2 )) -eq 1 ]; then
  bad "more than 2x pi's wall clock — the loop is not at parity yet"
fi

printf '\nartifacts in %s\n' "$BENCH_DIR"
printf '%s passed, %s failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]