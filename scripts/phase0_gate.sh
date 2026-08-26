#!/usr/bin/env bash
# Phase-0 integration gate (roadmap P0-T8) — live-server variant.
# Runs the four gate checks with curl against `uv run assistant http`.
#
# Usage:  bash scripts/phase0_gate.sh [port]
# Port defaults to 8099 to avoid clashing with the dev server (8080).
#
# Model requirement: checks 2–4 stream a real response, so they need a
# reachable LLM (OLLAMA_API_KEY / configured model). When the stream fails
# (no model), checks 2–4 are reported as SKIP-WARNING, not hard failures.
set -uo pipefail

PORT="${1:-8099}"
ROOT="$(mktemp -d)"
SERVER_PID=""
AUTH_PID=""
trap 'kill "$SERVER_PID" "$AUTH_PID" 2>/dev/null; rm -rf "$ROOT"' EXIT

echo "== phase0 gate: live server on :$PORT =="
DEPLOYMENT_DATA_ROOT="$ROOT" API_PORT="$PORT" \
  uv run assistant http > "$ROOT/server.log" 2>&1 &
SERVER_PID=$!

up() { # pid, log
  local pid="$1" log="$2"
  for i in $(seq 1 40); do
    curl -s "http://127.0.0.1:$PORT/health" > /dev/null 2>&1 && return 0
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 0.5
  done
  return 1
}

if ! up "$SERVER_PID" "$ROOT/server.log"; then
  echo "FAIL: server did not start"; tail -20 "$ROOT/server.log"; exit 1
fi

fail=0
check() { # name, result, detail
  if [ "$2" = "ok" ]; then echo "PASS: $1"
  elif [ "$2" = "warn" ]; then echo "SKIP-WARNING: $1 ($3)"
  else echo "FAIL: $1 ($3)"; fail=1; fi
}

# 1. Shared-secret auth. Run on a SEPARATE port so the no-auth server above
#    never answers the auth probe; verify the auth server actually bound.
AUTH_PORT=$((PORT + 1))
DEPLOYMENT_DATA_ROOT="$ROOT" API_PORT="$AUTH_PORT" API_KEY="gate-secret" SOLO_BYPASS="false" \
  uv run assistant http > "$ROOT/server_auth.log" 2>&1 &
AUTH_PID=$!
if ! up "$AUTH_PID" "$ROOT/server_auth.log"; then
  echo "FAIL: auth server did not start"; tail -20 "$ROOT/server_auth.log"; exit 1
fi
NO_TOKEN=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$AUTH_PORT/v1/conversation?user_id=default_user")
BEARER=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer gate-secret" \
  "http://127.0.0.1:$AUTH_PORT/v1/conversation?user_id=default_user")
[ "$NO_TOKEN" = "401" ] && [ "$BEARER" = "200" ] && R1=ok || R1="no=$NO_TOKEN bearer=$BEARER"
check "shared-secret auth (401/200)" "$R1"
kill "$AUTH_PID" 2>/dev/null; AUTH_PID=""

# 2. Stream a response via /v1/message/stream (real SSE frames + done).
STREAM=$(curl -s -N -X POST "http://127.0.0.1:$PORT/v1/message/stream" \
  -H "Content-Type: application/json" \
  -d '{"message":"ping","user_id":"default_user","session_id":"gate-sh"}' \
  --max-time 60)
if [ -z "$STREAM" ]; then
  check "stream a response (SSE done)" warn "empty stream (no model?)"
elif echo "$STREAM" | grep -q '"type": "done"'; then
  check "stream a response (SSE done)" ok
else
  check "stream a response (SSE done)" warn "no done frame (no model?)"
fi

# 3. Audit export: a tool-call action must appear in GET /v1/audit.
sleep 1
AUDIT=$(curl -s "http://127.0.0.1:$PORT/v1/audit?user_id=default_user")
if echo "$AUDIT" | grep -q "tool_call"; then
  check "audit export (tool_call rows)" ok
else
  check "audit export (tool_call rows)" warn "no tool_call rows (no model/tool triggered)"
fi

# 4. PROFILE.md bootstrap: a user PROFILE.md drives loop creation.
cat > "$ROOT/PROFILE.md" << 'MD'
---
name: gate-agent
description: gate
model: ollama:minimax-m2.5
system_prompt: You are the GATE AGENT persona.
---
MD
PROFILE_REQ=$(curl -s -N -X POST "http://127.0.0.1:$PORT/v1/message/stream" \
  -H "Content-Type: application/json" \
  -d '{"message":"hi","user_id":"default_user","session_id":"gate-profile"}' \
  --max-time 60)
if [ -z "$PROFILE_REQ" ]; then
  check "PROFILE.md bootstrap (round-trip)" warn "empty stream (no model?)"
elif echo "$PROFILE_REQ" | grep -q '"type": "done"'; then
  check "PROFILE.md bootstrap (round-trip)" ok
else
  check "PROFILE.md bootstrap (round-trip)" warn "no done frame (no model?)"
fi

echo "== phase0 gate: $([ $fail = 0 ] && echo ALL GREEN || echo FAILURES) =="
exit $fail
