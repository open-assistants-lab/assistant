#!/usr/bin/env bash
# Phase-0 integration gate (roadmap P0-T8) — live-server variant.
# Runs the four gate checks with curl against `uv run assistant http`.
#
# Usage:  bash scripts/phase0_gate.sh [port]
# Port defaults to 8099 to avoid clashing with the dev server (8080).
set -uo pipefail

PORT="${1:-8099}"
ROOT="$(mktemp -d)"
trap 'kill "$SERVER_PID" 2>/dev/null; rm -rf "$ROOT"' EXIT

echo "== phase0 gate: live server on :$PORT =="
DEPLOYMENT_DATA_ROOT="$ROOT" API_PORT="$PORT" \
  uv run assistant http > "$ROOT/server.log" 2>&1 &
SERVER_PID=$!

for i in $(seq 1 40); do
  if curl -s "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then break; fi
  sleep 0.5
done
curl -s "http://127.0.0.1:$PORT/health" > /dev/null 2>&1 || {
  echo "FAIL: server did not start"; tail -20 "$ROOT/server.log"; exit 1
}

fail=0
check() { # name, result, detail
  if [ "$2" = "ok" ]; then echo "PASS: $1"; else echo "FAIL: $1 ($3)"; fail=1; fi
}

# 1. Shared-secret auth: with API_KEY set, no token -> 401, Bearer -> 200.
DEPLOYMENT_DATA_ROOT="$ROOT" API_PORT="$PORT" API_KEY="gate-secret" SOLO_BYPASS="false" \
  uv run assistant http > "$ROOT/server_auth.log" 2>&1 &
AUTH_PID=$!
for i in $(seq 1 40); do
  curl -s "http://127.0.0.1:$PORT/health" > /dev/null 2>&1 && break
  sleep 0.5
done
NO_TOKEN=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/conversation?user_id=default_user")
BEARER=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer gate-secret" \
  "http://127.0.0.1:$PORT/v1/conversation?user_id=default_user")
[ "$NO_TOKEN" = "401" ] && [ "$BEARER" = "200" ] && R1=ok || R1="no=$NO_TOKEN bearer=$BEARER"
check "shared-secret auth (401/200)" "$R1"
kill "$AUTH_PID" 2>/dev/null

# 2. Stream a response via /v1/message/stream (real SSE frames + done).
STREAM=$(curl -s -N -X POST "http://127.0.0.1:$PORT/v1/message/stream" \
  -H "Content-Type: application/json" \
  -d '{"message":"ping","user_id":"default_user","session_id":"gate-sh"}' \
  --max-time 60)
echo "$STREAM" | grep -q '"type": "done"' && R2=ok || R2="no done event"
check "stream a response (SSE done)" "$R2"

# 3. Audit export: a state-changing action must appear in GET /v1/audit.
sleep 1
AUDIT=$(curl -s "http://127.0.0.1:$PORT/v1/audit?user_id=default_user")
echo "$AUDIT" | grep -q "tool_call" && R3=ok || R3="no tool_call rows"
check "audit export (tool_call rows)" "$R3"

# 4. PROFILE.md bootstrap: a user PROFILE.md drives loop creation.
mkdir -p "$ROOT"
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
echo "$PROFILE_REQ" | grep -q '"type": "done"' && R4=ok || R4="profile round-trip failed"
check "PROFILE.md bootstrap (round-trip)" "$R4"

echo "== phase0 gate: $([ $fail = 0 ] && echo ALL GREEN || echo FAILURES) =="
exit $fail
