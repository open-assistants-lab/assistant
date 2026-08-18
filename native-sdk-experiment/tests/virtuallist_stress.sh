#!/bin/bash
# Virtual list performance stress test.
#
# Proves the chat transcript virtual list stays viewport-sized at scale:
#   - With a 10,000-message transcript, widget_nodes stays bounded while
#     scrolling the full extent (the list mounts only the visible window).
#   - The scrollbar content extent reflects the FULL virtual transcript
#     (item_count x stride), not the mounted window.
#   - Per-frame pipeline stages (rebuild/layout/plan/emit) stay within the
#     60fps budget while scrolling — catching O(n^2) extent-estimation
#     regressions, which show up as 100ms+ frames.
#   - No widget budget errors (WidgetNodeLimitReached etc.).
#
# No backend required: the app seeds a synthetic transcript locally via
# NATIVE_SDK_STRESS_MESSAGES (stress mode skips all backend fetches).
#
# Usage: ./tests/virtuallist_stress.sh
# Env:   NATIVE_SDK_STRESS_MESSAGES (default 10000)
#        NODE_LIMIT                 (default 300 — measured baseline ~92)
#        P90_LIMIT_US               (default 16000 — 60fps budget; measured ~9700)
#        CONTENT_MIN_PX             (default 100000 — full virtual extent)
set -euo pipefail

cd "$(dirname "$0")/.."

MESSAGES="${NATIVE_SDK_STRESS_MESSAGES:-10000}"
NODE_LIMIT="${NODE_LIMIT:-300}"
P90_LIMIT_US="${P90_LIMIT_US:-16000}"
CONTENT_MIN_PX="${CONTENT_MIN_PX:-100000}"

APP=""
trap 'kill $APP 2>/dev/null; wait $APP 2>/dev/null; true' EXIT

fail() { echo "FAIL: $1"; exit 1; }

echo "=== Building and launching with $MESSAGES-message stress transcript ==="
pkill -f "zig-out/bin/assistant" 2>/dev/null || true
rm -rf .zig-cache/native-sdk-automation
NATIVE_SDK_STRESS_MESSAGES=$MESSAGES native dev -Dautomation=true > /tmp/native_stress_test.log 2>&1 &
APP=$!
native automate wait --timeout-ms 60000 > /dev/null || fail "app did not become ready (see /tmp/native_stress_test.log)"
echo "  app ready"

# Extract the transcript list widget id and a numeric field from its line.
snapshot_field() {
  native automate snapshot | grep 'role=list name=""' | head -1 | grep -o "$1" | head -1
}
LIST_ID=$(snapshot_field 'widget @w1/main-canvas#[0-9]*' | sed 's/.*#//')
[ -n "$LIST_ID" ] || fail "transcript list widget not found in snapshot"
echo "  transcript list widget #$LIST_ID"

echo "=== Baseline (bottom, anchor=trailing) ==="
NODES=$(native automate snapshot | grep -o 'widget_nodes=[0-9]*' | head -1 | cut -d= -f2)
CONTENT=$(snapshot_field 'content=[0-9.]*' | cut -d= -f2)
OFFSET=$(snapshot_field 'offset=[0-9.]*' | cut -d= -f2)
echo "  widget_nodes=$NODES content_extent=$CONTENT offset=$OFFSET"
[ -n "$NODES" ] && [ "$NODES" -lt "$NODE_LIMIT" ] || fail "widget_nodes=$NODES not bounded (<$NODE_LIMIT) at baseline"
python3 -c "
import sys
content = float('$CONTENT')
assert content > $CONTENT_MIN_PX, f'content extent {content} too small — list not virtual'
print(f'  PASS: content extent {content:.0f}px reflects the full $MESSAGES-message transcript')
"

echo "=== Scrolling to the top of the transcript ==="
native automate profile on > /dev/null
for i in $(seq 1 30); do
  native automate widget-wheel main-canvas "$LIST_ID" -50000 > /dev/null 2>&1
done
sleep 1
OFFSET=$(snapshot_field 'offset=[0-9.]*' | cut -d= -f2)
NODES=$(native automate snapshot | grep -o 'widget_nodes=[0-9]*' | head -1 | cut -d= -f2)
echo "  after scroll-up: offset=$OFFSET widget_nodes=$NODES"
[ -n "$NODES" ] && [ "$NODES" -lt "$NODE_LIMIT" ] || fail "widget_nodes=$NODES not bounded (<$NODE_LIMIT) at top"
python3 -c "
import sys
offset = float('$OFFSET')
assert offset < 100000, f'offset {offset} — did not reach the top of the transcript'
print('  PASS: reached the top (offset', offset, ') with bounded nodes')
"

echo "=== Scroll-storm (10 rapid wheels at the top) ==="
for i in $(seq 1 10); do
  native automate widget-wheel main-canvas "$LIST_ID" -50000 > /dev/null 2>&1
done
sleep 1
NODES=$(native automate snapshot | grep -o 'widget_nodes=[0-9]*' | head -1 | cut -d= -f2)
echo "  after scroll-storm: widget_nodes=$NODES"
[ -n "$NODES" ] && [ "$NODES" -lt "$NODE_LIMIT" ] || fail "widget_nodes=$NODES not bounded (<$NODE_LIMIT) after scroll-storm"

echo "=== Scrolling back to the bottom ==="
for i in $(seq 1 30); do
  native automate widget-wheel main-canvas "$LIST_ID" 50000 > /dev/null 2>&1
done
sleep 1
OFFSET=$(snapshot_field 'offset=[0-9.]*' | cut -d= -f2)
CONTENT=$(snapshot_field 'content=[0-9.]*' | cut -d= -f2)
NODES=$(native automate snapshot | grep -o 'widget_nodes=[0-9]*' | head -1 | cut -d= -f2)
echo "  after scroll-down: offset=$OFFSET content=$CONTENT widget_nodes=$NODES"
[ -n "$NODES" ] && [ "$NODES" -lt "$NODE_LIMIT" ] || fail "widget_nodes=$NODES not bounded (<$NODE_LIMIT) at bottom"
python3 -c "
import sys
offset, content = float('$OFFSET'), float('$CONTENT')
assert offset > content - 100000, f'offset {offset} — did not return to the bottom (content {content})'
print('  PASS: returned to the bottom (offset', offset, ') with bounded nodes')
"

echo "=== Frame profile (read while profiling is on) ==="
PROFILE=$(native automate snapshot | grep -o 'frame_profile .*' | head -1)
[ -n "$PROFILE" ] || fail "no frame_profile line in snapshot (profile on?)"
echo "$PROFILE" | tr ' ' '\n' | grep -E "rebuild_p90_us|layout_p90_us|plan_p90_us|emit_p90_us" | while read -r kv; do
  stage=${kv%%_p90_us=*}
  us=${kv##*=}
  echo "  $stage p90: ${us}us"
  python3 -c "
import sys
us = float('$us')
assert us < $P90_LIMIT_US, f'$stage p90 {us}us exceeds budget {$P90_LIMIT_US}us — possible O(n^2) regression'
"
done
native automate profile off > /dev/null

echo "=== No widget budget errors ==="
native automate assert --absent 'error event=' > /dev/null || fail "widget budget errors in snapshot"
echo "  PASS: no error events"

echo ""
echo "ALL PASS: virtual list stays viewport-sized at $MESSAGES messages"
echo "  widget_nodes bounded at $NODE_LIMIT (measured: $NODES)"
echo "  content extent $CONTENT px (full virtual transcript)"
echo "  frame p90 stages under ${P90_LIMIT_US}us"
