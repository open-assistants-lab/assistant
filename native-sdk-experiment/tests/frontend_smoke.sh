#!/bin/bash
# Frontend automation smoke test for Native SDK chat app
# Usage: ./tests/frontend_smoke.sh
set -euo pipefail

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WORKDIR"

echo "=== Starting backend ==="
lsof -ti:8080 | xargs kill -9 2>/dev/null || true
uv run ea http > /tmp/ea_frontend_test.log 2>&1 &
BACKEND=$!
trap "kill $BACKEND $APP 2>/dev/null; wait $BACKEND $APP 2>/dev/null; true" EXIT
sleep 10
curl -sf http://127.0.0.1:8080/health > /dev/null || { echo "FAIL: backend not healthy"; exit 1; }
echo "  backend healthy"

echo "=== Starting app ==="
rm -rf .zig-cache/native-sdk-automation
native dev -Dautomation=true > /tmp/native_frontend_test.log 2>&1 &
APP=$!
sleep 5
native automate wait --timeout-ms 15000 > /dev/null
echo "  app ready"

echo "=== Locating widgets ==="
SNAPSHOT=$(native automate snapshot)
get_id() {
  printf '%s\n' "$SNAPSHOT" | python3 -c "
import re,sys
s=sys.stdin.read()
m=re.search(r'widget @w1/main-canvas#(\d+) role=$1 name=\"$2\"', s)
print(m.group(1) if m else '')
"
}
TEXTBOX=$(get_id textbox Message)
SEND=$(get_id button Send)
NEWCHAT=$(get_id button "New chat")
echo "  textbox=$TEXTBOX send=$SEND newchat=$NEWCHAT"

[ -z "$TEXTBOX" ] && { echo "FAIL: no textbox"; exit 1; }
[ -z "$SEND" ] && { echo "FAIL: no Send button"; exit 1; }

echo "=== Test 1: Send message and receive response ==="
native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: ok' > /dev/null
native automate widget-action main-canvas "$SEND" press > /dev/null
native automate assert --timeout-ms 60000 'role=text name="ok"' > /dev/null
echo "  PASS: response received"

echo "=== Test 2: Sidebar shows chat with title ==="
native automate assert --timeout-ms 5000 'role=listitem name="Reply exactly: ok"' > /dev/null
echo "  PASS: chat list updated"

echo "=== Test 3: New chat button works ==="
native automate widget-action main-canvas "$NEWCHAT" press > /dev/null
native automate assert --timeout-ms 5000 'role=text name="How can I help?"' > /dev/null
echo "  PASS: empty state shown"

echo "=== Test 4: Theme toggle works ==="
THEME_BTN=$(get_id button "Toggle theme")
[ -z "$THEME_BTN" ] && { echo "FAIL: no theme toggle"; exit 1; }
native automate widget-action main-canvas "$THEME_BTN" press > /dev/null
echo "  PASS: theme toggled (no crash)"

echo "=== All frontend tests passed ==="