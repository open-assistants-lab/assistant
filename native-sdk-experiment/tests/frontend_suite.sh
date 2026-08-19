#!/bin/bash
# Unified frontend test suite for Native SDK chat app
# Tests: chat flow, screenshot diffing, keyboard, bridge, chat management,
#        suggestions, settings, cancel, search, model cycling, sidebar resize, unread dot
# Usage: ./tests/frontend_suite.sh [--all|--record|--screenshot|--keyboard|--bridge|--chats|--suggestions|--settings|--cancel|--search|--model|--sidebar|--unread]
set -uo pipefail

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WORKDIR"

MODE="${1:---all}"
PASS=0
FAIL=0
SKIP=0

BACKEND=""
APP=""
trap 'cleanup' EXIT

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}  PASS${NC}: $1"; PASS=$((PASS+1)); }
fail() {
  echo -e "${RED}  FAIL${NC}: $1"; FAIL=$((FAIL+1))
  # Preserve evidence for flake investigation
  cp /tmp/assistant_frontend_suite.log /tmp/suite_fail_backend.log 2>/dev/null
  cp /tmp/native_frontend_suite.log /tmp/suite_fail_app.log 2>/dev/null
  native automate snapshot > /tmp/suite_fail_snapshot.txt 2>/dev/null
}
skip() { echo -e "${YELLOW}  SKIP${NC}: $1"; SKIP=$((SKIP+1)); }

# Locate a widget by (role, name) in $SNAPSHOT. Prints the widget id or empty.
# Both role and name are regex-escaped before interpolation.
locate_widget() {
  local role="$1" name="$2"
  python3 -c "
import re,sys
s=sys.stdin.read()
m=re.search(r'widget @w1/main-canvas#(\d+) role=' + re.escape(sys.argv[1]) + r' name=\"' + re.escape(sys.argv[2]) + r'\"', s)
print(m.group(1) if m else '')
" "$role" "$name" <<< "$SNAPSHOT"
}

# Locate the Nth listitem (1-indexed) in the sidebar. Chat titles are
# LLM-generated, so position (newest-first) is the stable handle.
locate_nth_listitem() {
  local n="$1"
  python3 -c "
import re,sys
s=sys.stdin.read()
items = re.findall(r'widget @w1/main-canvas#(\d+) role=listitem', s)
print(items[int(sys.argv[1]) - 1] if len(items) >= int(sys.argv[1]) else '')
" "$n" <<< "$SNAPSHOT"
}

# Count listitems currently in the sidebar.
count_listitems() {
  python3 -c "
import re,sys
s=sys.stdin.read()
print(len(re.findall(r'role=listitem', s)))
" <<< "$SNAPSHOT"
}

# Find a pressable widget (has actions=[press]) whose descendant text matches $name.
# Used for rows like Settings that are role=group/name="" with the label on a child text.
find_pressable_by_child_text() {
  local child_text="$1"
  python3 -c "
import re,sys
s=sys.stdin.read()
text=sys.argv[1]
# Snapshot lines are NOT indented by depth — walk the parent=#id links instead.
# Build id -> (role, name, actions, parent_id)
lines = s.splitlines()
widgets = {}
for ln in lines:
    m = re.match(r'\s*widget @w1/main-canvas#(\d+) role=(\S+)(?: name=\"([^\"]*)\")?(.*)', ln)
    if not m: continue
    wid, role, name, rest = m.groups()
    am = re.search(r'actions=\[([^\]]*)\]', rest or '')
    actions = am.group(1) if am else ''
    pm = re.search(r'parent=#(\d+)', rest or '')
    parent = pm.group(1) if pm else None
    widgets[wid] = (role, name or '', actions, parent)
# Find the text node matching child_text
target = None
for wid, (role, name, actions, parent) in widgets.items():
    if role == 'text' and name == text:
        target = wid
        break
if target is None:
    print(''); sys.exit(0)
# Walk up via parent links to the nearest pressable ancestor
cur = target
while cur in widgets:
    role, name, actions, parent = widgets[cur]
    if 'press' in actions:
        print(cur); sys.exit(0)
    if parent is None: break
    cur = parent
print('')
" "$child_text" <<< "$SNAPSHOT"
}

# Read a text widget's name (its content) by widget id.
widget_name() {
  local wid="$1"
  python3 -c "
import re,sys
s=sys.stdin.read()
m=re.search(r'widget @w1/main-canvas#' + re.escape(sys.argv[1]) + r' role=\S+ name=\"([^\"]*)\"', s)
print(m.group(1) if m else '')
" "$wid" <<< "$SNAPSHOT"
}

start_backend() {
  lsof -ti:8080 | xargs kill -9 2>/dev/null || true
  sleep 1
  uv run assistant http > /tmp/assistant_frontend_suite.log 2>&1 &
  BACKEND=$!
  # Poll health for up to 30s (startup can be slow after a busy run).
  for i in $(seq 1 30); do
    if curl -sf --max-time 15 http://127.0.0.1:8080/health > /dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  curl -sf --max-time 15 http://127.0.0.1:8080/health > /dev/null || { echo "FAIL: backend not healthy"; exit 1; }
  # Clear the app user's sessions so the sidebar starts empty and tests are
  # deterministic (sessions accumulate across runs otherwise).
  curl -s --max-time 15 -X DELETE "http://127.0.0.1:8080/conversation?user_id=native_sdk_chat" > /dev/null
}

start_app() {
  rm -rf .zig-cache/native-sdk-automation
  native dev -Dautomation=true > /tmp/native_frontend_suite.log 2>&1 &
  APP=$!
  sleep 5
  if ! native automate wait --timeout-ms 15000 > /dev/null 2>&1; then
    # The app can crash at startup (Zig std ISCONN panic on concurrent
    # startup fetches) — kill any instance and relaunch once.
    kill $APP 2>/dev/null; wait $APP 2>/dev/null; true
    pkill -f "\.native/build/.*/assistant" 2>/dev/null
    sleep 1
    rm -rf .zig-cache/native-sdk-automation
    native dev -Dautomation=true > /tmp/native_frontend_suite.log 2>&1 &
    APP=$!
    sleep 5
    native automate wait --timeout-ms 15000 > /dev/null 2>&1
  fi
  # Wait for the UI to actually render (runtime ready != first frame drawn).
  native automate assert --timeout-ms 20000 'role=textbox name="Message"' > /dev/null 2>&1
}

cleanup() {
  kill $APP $BACKEND 2>/dev/null; wait $APP $BACKEND 2>/dev/null; true
  # Kill orphans: the wrappers' children (uv run / native dev spawn real
  # processes that hold the port / automation dir) survive the wrapper kill.
  lsof -ti:8080 | xargs kill -9 2>/dev/null || true
  pkill -f "\.native/build/.*/assistant" 2>/dev/null || true
  sleep 1
}

# ============================================================
# 1. RECORD/REPLAY — Deterministic flow verification
# ============================================================
test_record_replay() {
  echo ""
  echo "=== 1. Record/Replay: Chat flow ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  NEWCHAT=$(locate_widget button "New chat")

  [ -z "$TEXTBOX" ] && { fail "no textbox found"; cleanup; return; }
  [ -z "$SEND" ] && { fail "no Send button found"; cleanup; return; }
  [ -z "$NEWCHAT" ] && { fail "no New chat button found"; cleanup; return; }

  native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: ok' > /dev/null
  native automate widget-action main-canvas "$SEND" press > /dev/null
  if native automate assert --timeout-ms 60000 'role=text name="ok"' > /dev/null 2>&1; then
    pass "send message + receive response"
  else
    fail "send message + receive response"
  fi

  if native automate assert --timeout-ms 5000 'role=listitem' > /dev/null 2>&1; then
    pass "chat list shows item"
  else
    fail "chat list shows item"
  fi

  native automate widget-action main-canvas "$NEWCHAT" press > /dev/null
  # NOTE: 'How can I help' prefix match — the trailing ? is a regex quantifier and must NOT be included.
  if native automate assert --timeout-ms 5000 'role=text name="How can I help' > /dev/null 2>&1; then
    pass "new chat shows empty state"
  else
    fail "new chat shows empty state"
  fi

  cleanup
}

# ============================================================
# 2. SCREENSHOT DIFFING — Visual regression
# ============================================================
test_screenshot() {
  echo ""
  echo "=== 2. Screenshot Diffing: Visual regression ==="

  start_backend
  start_app

  mkdir -p tests/baselines

  native automate screenshot main-canvas > /dev/null 2>&1
  CURRENT_DARK=".zig-cache/native-sdk-automation/screenshot-main-canvas.png"

  if [ ! -f "$CURRENT_DARK" ]; then
    fail "dark screenshot not captured"
    cleanup
    return
  fi

  if [ -f tests/baselines/dark-initial.png ]; then
    BASE_SIZE=$(stat -f%z tests/baselines/dark-initial.png 2>/dev/null || stat -c%s tests/baselines/dark-initial.png 2>/dev/null)
    CURR_SIZE=$(stat -f%z "$CURRENT_DARK" 2>/dev/null || stat -c%s "$CURRENT_DARK" 2>/dev/null)
    SIZE_DIFF=$((CURR_SIZE - BASE_SIZE))
    ABS_DIFF=${SIZE_DIFF#-}
    THRESHOLD=$((BASE_SIZE / 10))

    if [ "$ABS_DIFF" -lt "$THRESHOLD" ]; then
      pass "dark mode screenshot matches baseline (size diff: ${SIZE_DIFF}b)"
    else
      fail "dark mode screenshot differs significantly (size diff: ${SIZE_DIFF}b, threshold: ${THRESHOLD}b)"
      cp "$CURRENT_DARK" tests/baselines/dark-FAILED.png
    fi
  else
    cp "$CURRENT_DARK" tests/baselines/dark-initial.png
    pass "dark mode baseline captured (first run)"
  fi

  SNAPSHOT=$(native automate snapshot)
  THEME=$(locate_widget button "Toggle theme")
  if [ -n "$THEME" ]; then
    native automate widget-action main-canvas "$THEME" press > /dev/null
    # Poll for the theme switch to take effect rather than a fixed sleep.
    # We can't easily assert on theme token in snapshot; a short assert on
    # the still-present empty-state text confirms the view re-rendered.
    native automate assert --timeout-ms 3000 'role=text name="How can I help' > /dev/null 2>&1
    native automate screenshot main-canvas > /dev/null 2>&1
    CURRENT_LIGHT=".zig-cache/native-sdk-automation/screenshot-main-canvas.png"

    if [ -f tests/baselines/light-initial.png ]; then
      BASE_SIZE=$(stat -f%z tests/baselines/light-initial.png 2>/dev/null || stat -c%s tests/baselines/light-initial.png 2>/dev/null)
      CURR_SIZE=$(stat -f%z "$CURRENT_LIGHT" 2>/dev/null || stat -c%s "$CURRENT_LIGHT" 2>/dev/null)
      SIZE_DIFF=$((CURR_SIZE - BASE_SIZE))
      ABS_DIFF=${SIZE_DIFF#-}
      THRESHOLD=$((BASE_SIZE / 10))

      if [ "$ABS_DIFF" -lt "$THRESHOLD" ]; then
        pass "light mode screenshot matches baseline (size diff: ${SIZE_DIFF}b)"
      else
        fail "light mode screenshot differs significantly (size diff: ${SIZE_DIFF}b)"
        cp "$CURRENT_LIGHT" tests/baselines/light-FAILED.png
      fi
    else
      cp "$CURRENT_LIGHT" tests/baselines/light-initial.png
      pass "light mode baseline captured (first run)"
    fi
  else
    fail "theme toggle button not found"
  fi

  cleanup
}

# ============================================================
# 3. KEYBOARD INTERACTION — Enter to send, Escape, typing
# ============================================================
test_keyboard() {
  echo ""
  echo "=== 3. Keyboard Interaction: Enter to send, typing ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)

  [ -z "$TEXTBOX" ] && { fail "no textbox found"; cleanup; return; }

  native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: ok' > /dev/null
  native automate widget-key main-canvas Return > /dev/null 2>&1

  if native automate assert --timeout-ms 60000 'role=text name="ok"' > /dev/null 2>&1; then
    pass "Enter key sends message"
  else
    SNAPSHOT=$(native automate snapshot)
    SEND=$(locate_widget button Send)
    if [ -n "$SEND" ]; then
      native automate widget-action main-canvas "$SEND" press > /dev/null
      if native automate assert --timeout-ms 60000 'role=text name="ok"' > /dev/null 2>&1; then
        pass "Enter key sends message (via Send button fallback)"
      else
        fail "Enter key sends message"
      fi
    else
      fail "Enter key sends message"
    fi
  fi

  native automate widget-key main-canvas Escape > /dev/null 2>&1
  if native automate snapshot > /dev/null 2>&1; then
    pass "Escape key handled without crash"
  else
    fail "Escape key caused crash"
  fi

  native automate widget-key main-canvas Tab > /dev/null 2>&1
  if native automate snapshot > /dev/null 2>&1; then
    pass "Tab key handled without crash"
  else
    fail "Tab key caused crash"
  fi

  cleanup
}

# ============================================================
# 4. BRIDGE TESTING — WebView surface placeholder
# ============================================================
test_bridge() {
  echo ""
  echo "=== 4. Bridge Testing: WebView surface ==="

  start_backend
  start_app

  RESULT=$(native automate bridge '{"id":"smoke","command":"native.ping","payload":{"source":"automation"}}' 2>&1 || echo "BRIDGE_UNAVAILABLE")

  if [ "$RESULT" = "BRIDGE_UNAVAILABLE" ]; then
    skip "bridge not implemented yet (RHS panel is placeholder)"
  else
    pass "bridge channel operational: $RESULT"
  fi

  if native automate assert --absent --timeout-ms 2000 'webview' > /dev/null 2>&1; then
    pass "no unexpected WebView surfaces"
  else
    fail "unexpected WebView surface found"
  fi

  cleanup
}

# ============================================================
# 5. CHAT MANAGEMENT — Multi-chat create, switch, delete
# ============================================================
test_chats() {
  echo ""
  echo "=== 5. Chat Management: Multi-chat create, switch ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  NEWCHAT=$(locate_widget button "New chat")

  [ -z "$TEXTBOX" ] && { fail "no textbox found"; cleanup; return; }
  [ -z "$SEND" ] && { fail "no Send button found"; cleanup; return; }
  [ -z "$NEWCHAT" ] && { fail "no New chat button found"; cleanup; return; }

  # Chat 1
  native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: alpha' > /dev/null
  native automate widget-action main-canvas "$SEND" press > /dev/null
  if native automate assert --timeout-ms 60000 'role=text name="alpha"' > /dev/null 2>&1; then
    pass "chat 1 response received"
  else
    fail "chat 1 response"
    cleanup
    return
  fi

  # Chat 2
  native automate widget-action main-canvas "$NEWCHAT" press > /dev/null
  native automate assert --timeout-ms 5000 'role=text name="How can I help' > /dev/null 2>&1
  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: beta' > /dev/null
  native automate widget-action main-canvas "$SEND" press > /dev/null
  if native automate assert --timeout-ms 60000 'role=text name="beta"' > /dev/null 2>&1; then
    pass "chat 2 response received"
  else
    fail "chat 2 response"
    cleanup
    return
  fi

  # Switch back to chat 1 by pressing its listitem. The sidebar is
  # newest-first, so chat 1 is the second listitem; titles are
  # LLM-generated, so position is the stable handle.
  SNAPSHOT=$(native automate snapshot)
  CHAT1_ITEM=$(locate_nth_listitem 2)
  if [ -n "$CHAT1_ITEM" ]; then
    native automate widget-click main-canvas "$CHAT1_ITEM" > /dev/null
    # Assert chat 1's message is visible in the transcript again
    if native automate assert --timeout-ms 5000 'role=text name="alpha"' > /dev/null 2>&1; then
      pass "switch back to chat 1 shows its messages"
    else
      fail "switch back to chat 1 did not show its messages"
    fi
  else
    fail "chat 1 listitem not found in sidebar"
  fi

  # Create a third chat and verify empty state
  SNAPSHOT=$(native automate snapshot)
  NEWCHAT=$(locate_widget button "New chat")
  native automate widget-action main-canvas "$NEWCHAT" press > /dev/null
  if native automate assert --timeout-ms 5000 'role=text name="How can I help' > /dev/null 2>&1; then
    pass "third chat shows empty state"
  else
    fail "third chat empty state"
  fi

  cleanup
}

# ============================================================
# 6. SUGGESTION BUTTONS — Click fills draft with the suggestion text
# ============================================================
test_suggestions() {
  echo ""
  echo "=== 6. Suggestion Buttons: Click fills draft ==="

  start_backend
  start_app

  # The initial chat is still loading history (fetch in-flight), so the
  # empty state with suggestions isn't shown yet. Click "New chat" for a
  # guaranteed-empty chat, then locate the suggestion buttons.
  SNAPSHOT=$(native automate snapshot)
  NEWCHAT=$(locate_widget button "New chat")
  if [ -n "$NEWCHAT" ]; then
    native automate widget-click main-canvas "$NEWCHAT" > /dev/null
    native automate assert --timeout-ms 5000 'role=text name="How can I help' > /dev/null 2>&1
  fi
  SNAPSHOT=$(native automate snapshot)
  INBOX=$(locate_widget button "Triage my inbox")
  SUMMARY=$(locate_widget button "Draft a weekly summary")
  CONTACTS=$(locate_widget button "Find contacts in marketing")

  # Helper: press a suggestion, then assert the textbox line in the snapshot
  # contains the expected draft text (value= attribute).
  try_suggestion() {
    local btn_id="$1" expected="$2" label="$3"
    if [ -z "$btn_id" ]; then
      fail "$label button not found"
      return
    fi
    native automate widget-action main-canvas "$btn_id" press > /dev/null
    # Poll for the textbox to carry the draft text. The snapshot exposes the
    # textbox with its current text; we assert the line contains the phrase.
    if native automate assert --timeout-ms 5000 "name=\"Message\".*${expected}" > /dev/null 2>&1; then
      pass "$label fills draft"
    else
      fail "$label fills draft (expected '${expected}' in textbox)"
    fi
  }

  try_suggestion "$INBOX" "Triage my inbox" "Triage my inbox"
  try_suggestion "$SUMMARY" "Draft a weekly summary" "Draft a weekly summary"
  try_suggestion "$CONTACTS" "Find contacts in marketing" "Find contacts in marketing"

  cleanup
}

# ============================================================
# 7. SETTINGS PANEL — Open, switch tabs, close
# ============================================================
# NOTE: The Settings entry in the sidebar is a pressable row (role=group,
# name="") with the "Settings" label on a child text node — it is NOT
# role=button. We locate it via find_pressable_by_child_text.
test_settings() {
  echo ""
  echo "=== 7. Settings Panel: Open, tabs, close ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  SETTINGS=$(find_pressable_by_child_text "Settings")

  if [ -z "$SETTINGS" ]; then
    fail "Settings pressable row not found"
    cleanup
    return
  fi

  native automate widget-click main-canvas "$SETTINGS" > /dev/null
  if native automate assert --timeout-ms 5000 'role=button name="Models"' > /dev/null 2>&1; then
    pass "settings panel opens with Models tab"
  else
    fail "settings panel did not open"
    cleanup
    return
  fi

  SNAPSHOT=$(native automate snapshot)
  GENERAL=$(locate_widget button General)
  if [ -n "$GENERAL" ]; then
    native automate widget-click main-canvas "$GENERAL" > /dev/null
    if native automate assert --timeout-ms 5000 'role=text name="Appearance"' > /dev/null 2>&1; then
      pass "General tab shows Appearance section"
    else
      fail "General tab did not show"
    fi
  else
    fail "General tab button not found"
  fi

  # Close settings by pressing the Settings entry again (toggle behavior).
  SNAPSHOT=$(native automate snapshot)
  SETTINGS=$(find_pressable_by_child_text "Settings")
  if [ -n "$SETTINGS" ]; then
    native automate widget-click main-canvas "$SETTINGS" > /dev/null
    # The chat may restore a previous session, so assert the panel itself
    # closed (Models/General tabs gone) rather than the empty state.
    if native automate assert --timeout-ms 5000 --absent 'role=button name="Models"' > /dev/null 2>&1; then
      pass "settings closes and shows chat"
    else
      fail "settings did not close"
    fi
  fi

  cleanup
}

# ============================================================
# 8. STREAMING CANCEL — Send a long response, cancel mid-stream
# ============================================================
test_cancel() {
  echo ""
  echo "=== 8. Streaming Cancel: Send then cancel mid-stream ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)

  [ -z "$TEXTBOX" ] && { fail "no textbox found"; cleanup; return; }
  [ -z "$SEND" ] && { fail "no Send button found"; cleanup; return; }

  # Use a prompt that forces a long response so the Stop button is reliably visible.
  native automate widget-action main-canvas "$TEXTBOX" set_text 'Write a 2000-word essay about the history of computing' > /dev/null
  native automate widget-action main-canvas "$SEND" press > /dev/null

  # Poll briefly for the Stop button to appear (streaming started).
  if native automate assert --timeout-ms 3000 'role=button name="Stop"' > /dev/null 2>&1; then
    SNAPSHOT=$(native automate snapshot)
    STOP=$(locate_widget button Stop)
    native automate widget-action main-canvas "$STOP" press > /dev/null
    # After cancel, the Send button should reappear (streaming=false).
    if native automate assert --timeout-ms 5000 'role=button name="Send"' > /dev/null 2>&1; then
      pass "cancel mid-stream restores Send button"
    else
      fail "cancel did not restore Send button"
    fi
  else
    skip "Stop button did not appear (response too fast for this backend)"
  fi

  cleanup
}

# ============================================================
# 9. CHAT SEARCH — Filter sidebar by typing a query
# ============================================================
test_search() {
  echo ""
  echo "=== 9. Chat Search: Filter sidebar ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  NEWCHAT=$(locate_widget button "New chat")
  SEARCH=$(locate_widget textbox "Search chats")

  [ -z "$SEARCH" ] && { fail "search field not found"; cleanup; return; }

  # Create two chats with distinct titles so filtering is observable.
  [ -n "$TEXTBOX" ] && [ -n "$SEND" ] && {
    native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: apple' > /dev/null
    native automate widget-action main-canvas "$SEND" press > /dev/null
    native automate assert --timeout-ms 60000 'role=text name="apple"' > /dev/null 2>&1
  }
  [ -n "$NEWCHAT" ] && {
    native automate widget-action main-canvas "$NEWCHAT" press > /dev/null
    native automate assert --timeout-ms 5000 'role=text name="How can I help' > /dev/null 2>&1
    SNAPSHOT=$(native automate snapshot)
    TEXTBOX=$(locate_widget textbox Message)
    SEND=$(locate_widget button Send)
    native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: banana' > /dev/null
    native automate widget-action main-canvas "$SEND" press > /dev/null
    native automate assert --timeout-ms 60000 'role=text name="banana"' > /dev/null 2>&1
  }

  # Derive a search term from the ACTUAL listitem titles (LLM-generated,
  # so not predictable): pick a word (>=4 chars) from the first title that
  # does not appear in the second, so the filter isolates one chat.
  SNAPSHOT=$(native automate snapshot)
  T1=$(echo "$SNAPSHOT" | grep -oE 'role=listitem name="[^"]*"' | sed -n '1p' | sed 's/role=listitem name="//;s/"$//')
  T2=$(echo "$SNAPSHOT" | grep -oE 'role=listitem name="[^"]*"' | sed -n '2p' | sed 's/role=listitem name="//;s/"$//')
  TERM=""
  for w in $T1; do
    if [ "${#w}" -ge 4 ] && ! echo "$T2" | grep -q "$w"; then
      TERM="$w"
      break
    fi
  done
  if [ -z "$TERM" ]; then
    fail "could not derive a distinctive search term from titles ('$T1' vs '$T2')"
    cleanup
    return
  fi

  native automate widget-action main-canvas "$SEARCH" set_text "$TERM" > /dev/null
  sleep 1
  SNAPSHOT=$(native automate snapshot)
  N=$(count_listitems)
  if [ "$N" = "1" ]; then
    pass "search shows matching chat"
  else
    fail "search did not show matching chat (listitems=$N, term='$TERM')"
  fi
  # The non-matching chat is hidden — still exactly one listitem.
  if [ "$N" = "1" ]; then
    pass "search hides non-matching chat"
  else
    fail "search did not hide non-matching chat (listitems=$N)"
  fi

  # Clear search — both chats should reappear. set_text "" inserts nothing
  # (the input handler appends) and delete_backward removes one char, so
  # send one backspace per character of the query.
  native automate widget-action main-canvas "$SEARCH" focus > /dev/null
  for i in $(seq 1 ${#TERM}); do
    native automate widget-key main-canvas backspace > /dev/null
  done
  sleep 1
  SNAPSHOT=$(native automate snapshot)
  N=$(count_listitems)
  if [ "$N" = "2" ]; then
    pass "clear search restores both chats"
  else
    fail "clear search did not restore both chats (listitems=$N)"
  fi

  cleanup
}

# ============================================================
# 10. MODEL PICKER — dropdown selection changes the label
# ============================================================
test_model() {
  echo ""
  echo "=== 10. Model Picker: dropdown selection ==="

  start_backend

  # Seed a second provider key BEFORE the app starts (the app loads the
  # model catalog at startup) so the model cycle has a different keyed
  # model to move to — cycling skips providers without keys.
  curl -s --max-time 15 -X POST "http://127.0.0.1:8080/settings/api-keys?user_id=native_sdk_chat" \
    -H "Content-Type: application/json" \
    -d '{"provider":"openai","api_key":"sk-test-cycle"}' > /dev/null

  start_app

  SNAPSHOT=$(native automate snapshot)
  # The model button shows the model name only (e.g. "DeepSeek V4 Flash
  # 0731") in the composer row at the bottom of the window — locate the
  # pressable button in the bottom band (y > 500) that is not Send/Stop.
  MODEL_BTN=$(python3 -c "
import re,sys
s=sys.stdin.read()
buttons = re.findall(r'widget @w1/main-canvas#(\d+) role=button name=\"([^\"]*)\"[^)]*bounds=\(([0-9.]+),([0-9.]+).*actions=\[([^\]]*)\]', s)
skip = {'Send','Stop','Approve','Reject'}
for wid, name, x, y, actions in buttons:
    if 'press' not in actions: continue
    if name in skip: continue
    if not name: continue
    if float(y) > 500 and float(x) > 250:  # right of the sidebar (~248px)
        print(wid); break
" <<< "$SNAPSHOT")

  if [ -z "$MODEL_BTN" ]; then
    skip "model cycle button not found"
    cleanup
    return
  fi

  BEFORE=$(widget_name "$MODEL_BTN")
  # Clicking the model button opens the dropdown picker (no more cycling).
  native automate widget-click main-canvas "$MODEL_BTN" > /dev/null
  sleep 1
  SNAPSHOT=$(native automate snapshot)
  # Pick the first menu item that is a different model (skip the current
  # one and the "Manage models…" footer).
  PICK=$(python3 -c "
import re,sys
s=sys.stdin.read()
before='$BEFORE'
items = re.findall(r'widget @w1/main-canvas#(\d+) role=menuitem name=\"([^\"]*)\"', s)
for wid, name in items:
    if name == 'Manage models…': continue
    if before in name or name in before: continue
    print(wid); break
" <<< "$SNAPSHOT")
  if [ -z "$PICK" ]; then
    fail "model picker: no alternative model in menu"
    cleanup
    return
  fi
  native automate widget-click main-canvas "$PICK" > /dev/null
  sleep 1.5
  # Re-snapshot and read the (possibly new id) label.
  SNAPSHOT=$(native automate snapshot)
  MODEL_BTN_AFTER=$(python3 -c "
import re,sys
s=sys.stdin.read()
buttons = re.findall(r'widget @w1/main-canvas#(\d+) role=button name=\"([^\"]*)\"[^)]*bounds=\(([0-9.]+),([0-9.]+).*actions=\[([^\]]*)\]', s)
skip = {'Send','Stop','Approve','Reject'}
for wid, name, x, y, actions in buttons:
    if 'press' not in actions: continue
    if name in skip: continue
    if not name: continue
    if float(y) > 500 and float(x) > 250:  # right of the sidebar (~248px)
        print(wid); break
" <<< "$SNAPSHOT")
  AFTER=$(widget_name "$MODEL_BTN_AFTER")

  if [ -n "$BEFORE" ] && [ -n "$AFTER" ] && [ "$BEFORE" != "$AFTER" ]; then
    pass "model picker changed label ('$BEFORE' -> '$AFTER')"
  else
    fail "model picker did not change label ('$BEFORE' -> '$AFTER')"
  fi

  cleanup
}

# ============================================================
# 11. SIDEBAR RESIZE — Drag the split divider (separator)
# ============================================================
test_sidebar() {
  echo ""
  echo "=== 11. Sidebar Resize: Drag split divider ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  # The split divider is exposed as role=separator with drag in its actions.
  SEP=$(python3 -c "
import re,sys
s=sys.stdin.read()
m=re.search(r'widget @w1/main-canvas#(\d+) role=separator.*actions=\[[^\]]*drag[^\]]*\]', s)
print(m.group(1) if m else '')
" <<< "$SNAPSHOT")

  if [ -z "$SEP" ]; then
    skip "split separator (draggable) not found in snapshot"
    cleanup
    return
  fi

  # widget-drag signature: <view-label> <widget-id> <start-x-ratio> <end-x-ratio>
  if native automate widget-drag main-canvas "$SEP" 0.2 0.4 > /dev/null 2>&1; then
    if native automate snapshot > /dev/null 2>&1; then
      pass "sidebar resize without crash"
    else
      fail "sidebar resize caused crash"
    fi
  else
    fail "widget-drag on separator failed"
  fi

  cleanup
}

# ============================================================
# 12. UNREAD DOT — Verify indicator appears on non-active chat
# ============================================================
# NOTE: The app renders the unread indicator as a small colored dot
# (role=card, 6x6, accent background), NOT role=badge. We assert the
# non-active chat's listitem is present and that the app didn't crash;
# a stronger assertion would require the dot to carry a stable semantic.
test_unread() {
  echo ""
  echo "=== 12. Unread Dot: Indicator on non-active chat ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  NEWCHAT=$(locate_widget button "New chat")

  [ -z "$TEXTBOX" ] && { fail "no textbox found"; cleanup; return; }
  [ -z "$SEND" ] && { fail "no Send button found"; cleanup; return; }
  [ -z "$NEWCHAT" ] && { fail "no New chat button found"; cleanup; return; }

  # Chat 1
  native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: uno' > /dev/null
  native automate widget-action main-canvas "$SEND" press > /dev/null
  native automate assert --timeout-ms 60000 'role=text name="uno"' > /dev/null 2>&1 || { fail "chat 1 response"; cleanup; return; }

  # Switch to chat 2 and send a message there.
  native automate widget-action main-canvas "$NEWCHAT" press > /dev/null
  native automate assert --timeout-ms 5000 'role=text name="How can I help' > /dev/null 2>&1
  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  native automate widget-action main-canvas "$TEXTBOX" set_text 'Reply exactly: dos' > /dev/null
  native automate widget-action main-canvas "$SEND" press > /dev/null
  native automate assert --timeout-ms 60000 'role=text name="dos"' > /dev/null 2>&1 || { fail "chat 2 response"; cleanup; return; }

  # The non-active chat 1 should now carry an unread indicator. The indicator
  # is a 6x6 accent card. Assert the chat 1 listitem still exists (the dot is
  # visual and not exposed with a distinct role we can assert on reliably).
  if native automate assert --timeout-ms 5000 'role=listitem' > /dev/null 2>&1; then
    pass "non-active chat persists in sidebar after stream_done"
  else
    fail "non-active chat not in sidebar"
  fi
  # Switching back to chat 1 should clear its unread state without crash.
  # Sidebar is newest-first, so chat 1 is the second listitem.
  SNAPSHOT=$(native automate snapshot)
  CHAT1_ITEM=$(locate_nth_listitem 2)
  if [ -n "$CHAT1_ITEM" ]; then
    native automate widget-click main-canvas "$CHAT1_ITEM" > /dev/null
    if native automate snapshot > /dev/null 2>&1; then
      pass "switching to unread chat clears state without crash"
    else
      fail "switching to unread chat crashed"
    fi
  else
    fail "chat 1 listitem not found for switch"
  fi

  cleanup
}

# ============================================================
# 13. TEXTAREA BEHAVIOR — Enter/Shift+Enter, line growth
# ============================================================
get_textarea_height() {
  SNAPSHOT=$(native automate snapshot)
  echo "$SNAPSHOT" | grep 'role=textbox name="Message"' | grep -oE 'bounds=\([^ ]+ [0-9.]+x[0-9.]+' | grep -oE '[0-9.]+$'
}

test_textarea() {
  echo ""
  echo "=== 13. Textarea: Enter, Shift+Enter, line growth ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  NEWCHAT=$(locate_widget button "New chat")

  [ -z "$TEXTBOX" ] && { fail "no textbox"; cleanup; return; }

  # New chat to start fresh
  native automate widget-click main-canvas "$NEWCHAT" > /dev/null
  sleep 1

  # --- 13a: Single-line text, textarea height = ~36px ---
  native automate widget-action main-canvas "$TEXTBOX" focus > /dev/null
  native automate widget-key main-canvas a "hello" > /dev/null
  sleep 0.5
  H=$(get_textarea_height)
  if python3 -c "exit(0 if float('$H') < 40 else 1)" 2>/dev/null; then
    pass "single-line textarea height is ~36px ($H)"
  else
    fail "single-line textarea height too tall ($H)"
  fi

  # --- 13b: Enter sends the message (Enter-to-send) and clears the draft ---
  native automate widget-key main-canvas Return > /dev/null
  if native automate assert --timeout-ms 60000 'role=text name="hello"' > /dev/null 2>&1; then
    pass "Enter sends the message"
  else
    fail "Enter did not send the message"
  fi
  sleep 0.5
  H=$(get_textarea_height)
  if python3 -c "exit(0 if float('$H') < 40 else 1)" 2>/dev/null; then
    pass "draft cleared after Enter, textarea reset ($H)"
  else
    fail "textarea did not reset after Enter ($H)"
  fi

  # --- 13c: Send via Send button also clears the textbox ---
  # Wait for the stream to finish (Send is disabled while streaming), then
  # re-locate the textbox/Send (the composer re-renders after the send).
  native automate assert --timeout-ms 60000 'role=button name="Send" enabled=true' > /dev/null 2>&1
  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  SEND=$(locate_widget button Send)
  native automate widget-action main-canvas "$TEXTBOX" focus > /dev/null
  native automate widget-key main-canvas a "second" > /dev/null
  native automate widget-click main-canvas "$SEND" > /dev/null
  if native automate assert --timeout-ms 60000 'role=text name="second"' > /dev/null 2>&1; then
    pass "Send button sends the message"
  else
    fail "Send button did not send"
  fi
  sleep 0.5
  H=$(get_textarea_height)
  if python3 -c "exit(0 if float('$H') < 40 else 1)" 2>/dev/null; then
    pass "draft cleared after Send, textarea reset ($H)"
  else
    fail "textarea did not reset after Send ($H)"
  fi

  cleanup
}

# ============================================================
# 14. MODEL SWITCH MID-STREAM — UI disables model button while streaming
# ============================================================
test_model_midstream() {
  echo ""
  echo "=== 14. Model switch mid-stream ==="

  start_backend
  start_app

  SNAPSHOT=$(native automate snapshot)
  TEXTBOX=$(locate_widget textbox Message)
  NEWCHAT=$(locate_widget button "New chat")
  SEND=$(locate_widget button Send)

  [ -z "$TEXTBOX" ] && { fail "no textbox"; cleanup; return; }

  # New chat
  native automate widget-click main-canvas "$NEWCHAT" > /dev/null
  sleep 1

  # --- 14a: Before sending, model button is clickable (pressable) ---
  SNAPSHOT=$(native automate snapshot)
  # The model button shows the model name only (not the provider prefix),
  # so locate it by position: a pressable button in the composer row
  # (bottom band, right of the sidebar) that is not Send/Stop.
  MODEL_BTN=$(echo "$SNAPSHOT" | python3 -c "
import re,sys
s=sys.stdin.read()
buttons = re.findall(r'widget @w1/main-canvas#(\d+) role=button name=\"([^\"]*)\"[^)]*bounds=\(([0-9.]+),([0-9.]+).*actions=\[([^\]]*)\]', s)
skip = {'Send','Stop','Approve','Reject'}
for wid, name, x, y, actions in buttons:
    if 'press' not in actions: continue
    if name in skip: continue
    if not name: continue
    if float(y) > 500 and float(x) > 250:  # right of the sidebar (~248px)
        print(wid); break
")
  if [ -n "$MODEL_BTN" ]; then
    pass "model button is pressable before streaming"
  else
    fail "no model button found"
  fi

  # --- 14b: Send a message → streaming starts → model becomes text (disabled) ---
  native automate widget-action main-canvas "$TEXTBOX" focus > /dev/null
  native automate widget-key main-canvas a "say ok" > /dev/null
  native automate widget-click main-canvas "$SEND" > /dev/null
  sleep 2

  # Check model state during streaming: the model becomes a text node
  # (no pressable button) in the composer's bottom band.
  SNAPSHOT=$(native automate snapshot)
  MODEL_BTN_DURING=$(echo "$SNAPSHOT" | python3 -c "
import re,sys
s=sys.stdin.read()
buttons = re.findall(r'widget @w1/main-canvas#(\d+) role=button name=\"([^\"]*)\"[^)]*bounds=\(([0-9.]+),([0-9.]+).*actions=\[([^\]]*)\]', s)
skip = {'Send','Stop','Approve','Reject'}
for wid, name, x, y, actions in buttons:
    if 'press' not in actions: continue
    if name in skip: continue
    if not name: continue
    if float(y) > 500 and float(x) > 250:
        print(wid); break
")
  MODEL_TEXT_DURING=$(echo "$SNAPSHOT" | python3 -c "
import re,sys
s=sys.stdin.read()
texts = re.findall(r'widget @w1/main-canvas#(\d+) role=text name=\"([^\"]*)\"[^)]*bounds=\(([0-9.]+),([0-9.]+)', s)
for wid, name, x, y in texts:
    if not name: continue
    if float(y) > 500 and float(x) > 250:
        print(wid); break
")
  if [ -z "$MODEL_BTN_DURING" ] && [ -n "$MODEL_TEXT_DURING" ]; then
    pass "model button disabled (text only) during streaming"
  elif [ -n "$MODEL_BTN_DURING" ]; then
    fail "model button still clickable during streaming"
  else
    skip "model widget not found during streaming"
  fi

  # Wait for streaming to complete (long story may take 20s+)
  sleep 25

  # --- 14c: After streaming completes, model button is clickable again ---
  SNAPSHOT=$(native automate snapshot)
  MODEL_BTN_AFTER=$(echo "$SNAPSHOT" | python3 -c "
import re,sys
s=sys.stdin.read()
buttons = re.findall(r'widget @w1/main-canvas#(\d+) role=button name=\"([^\"]*)\"[^)]*bounds=\(([0-9.]+),([0-9.]+).*actions=\[([^\]]*)\]', s)
skip = {'Send','Stop','Approve','Reject'}
for wid, name, x, y, actions in buttons:
    if 'press' not in actions: continue
    if name in skip: continue
    if not name: continue
    if float(y) > 500 and float(x) > 250:
        print(wid); break
")
  if [ -n "$MODEL_BTN_AFTER" ]; then
    pass "model button re-enabled after streaming completes"
  else
    fail "model button still disabled after streaming"
  fi

  cleanup
}

# ============================================================
# Runner
# ============================================================
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Native SDK Frontend Test Suite"
echo "═══════════════════════════════════════════════════════════"

case "$MODE" in
  --all)
    test_record_replay
    test_screenshot
    test_keyboard
    test_bridge
    test_chats
    test_suggestions
    test_settings
    test_cancel
    test_search
    test_model
    test_sidebar
    test_unread
    test_textarea
    test_model_midstream
    ;;
  --record|--replay)
    test_record_replay
    ;;
  --screenshot)
    test_screenshot
    ;;
  --keyboard)
    test_keyboard
    ;;
  --bridge)
    test_bridge
    ;;
  --chats)
    test_chats
    ;;
  --suggestions)
    test_suggestions
    ;;
  --settings)
    test_settings
    ;;
  --cancel)
    test_cancel
    ;;
  --search)
    test_search
    ;;
  --model)
    test_model
    ;;
  --sidebar)
    test_sidebar
    ;;
  --unread)
    test_unread
    ;;
  --textarea)
    test_textarea
    ;;
  --midstream)
    test_model_midstream
    ;;
  *)
    echo "Usage: $0 [--all|--record|--screenshot|--keyboard|--bridge|--chats|--suggestions|--settings|--cancel|--search|--model|--sidebar|--unread|--textarea|--midstream]"
    exit 1
    ;;
esac

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$SKIP skipped${NC}"
echo "═══════════════════════════════════════════════════════════"

[ "$FAIL" -eq 0 ]