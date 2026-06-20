# Canvas 2.0 — Phase 4: Co-Edit Design Spec

**Date:** 2026-06-20
**Status:** Draft
**Target:** Executive Assistant, Session view — Notes, Email body, Text files

---

## 1. Overview

### Problem
The MVP (Phases 1-3) gives users a read-only workspace with live widgets. The agent can view emails, todos, contacts, and files — but cannot propose edits. Users cannot collaborate with the agent on text content (notes, email drafts, files) without leaving the workspace to copy-paste.

### Solution
Two co-edit patterns sharing the same principle — **agent proposes, human decides**:

1. **Diff Card** (chat-surface) — Agent outputs a ```` ```diff:filepath ```` block in chat showing proposed changes. A diff-render card with accept/reject buttons appears inline in the conversation.

2. **Inline Suggestions** (editor-surface) — Agent outputs a ```` ```suggestion ```` block. Backend extracts it, sends it as a WS `suggestion_push` event. The ProseMirror editor renders suggestions as ephemeral decorations (green underline + accept/reject controls inline). The same editor is used for Notes, Email body, and text files.

Both patterns are **turn-based** — the agent responds, the user reviews, then accepts or rejects. There is no real-time collaborative editing.

---

## 2. Agent Block Extraction

### New fence types

#### `diff:filepath` — Diff Card (chat)

```
```diff:path/to/file.md
- Old line
+ New line
+ Another new line
 unchanged line
```
```

Extracted by the backend, rendered as a diff card in chat (not in the workspace panel). The user sees the diff inline and can accept (agent writes the file) or reject.

#### `suggestion` — Inline Suggestions

```
```suggestion
filePath: path/to/note.md
---
Here is the suggested replacement text for the visible portion.
The editor will show this as an overlay with accept/reject.
```
```

Extracted by the backend, sent as `suggestion_push` WS event to the Flutter app, which routes it to the ProseMirror editor instance. The editor renders suggestions as ephemeral decorations — no files are written until the user accepts.

### Block parsers

```python
# In src/http/routers/conversation.py — add to _extract_surfaces

_DIFF_FENCE = re.compile(
    r"```diff:(.+?)\s*\n(.*?)```",
    re.DOTALL,
)

_SUGGESTION_FENCE = re.compile(
    r"```suggestion\s*\nfilePath:\s*(.+?)\n---\n(.*?)```",
    re.DOTALL,
)

def _extract_diff(text: str) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for match in _DIFF_FENCE.finditer(text):
        file_path = match.group(1).strip()
        diff_content = match.group(2).strip()
        if not diff_content:
            continue
        diffs.append({
            "surface_type": "diff_card",
            "file_path": file_path,
            "diff": diff_content,
        })
    return diffs

def _extract_suggestions(text: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for match in _SUGGESTION_FENCE.finditer(text):
        file_path = match.group(1).strip()
        content = match.group(2).strip()
        if not content:
            continue
        suggestions.append({
            "surface_type": "suggestion",
            "file_path": file_path,
            "content": content,
        })
    return suggestions
```

`_strip_canvas_fences()` gained `_DIFF_FENCE.sub()` and `_SUGGESTION_FENCE.sub()`.

---

## 3. WS Protocol Extensions

### Server → Client

#### `diff_card` event

```json
{
  "type": "diff_card",
  "file_path": "path/to/file.md",
  "diff": "- Old line\n+ New line",
  "suggestion_id": "diff_0"
}
```

Sent alongside `done` (and existing `switch_tab`/`widget_refresh` directives). The Flutter chat renders a diff card widget inline.

#### `suggestion_push` event

```json
{
  "type": "suggestion_push",
  "file_path": "path/to/note.md",
  "content": "Suggested replacement text...",
  "suggestion_id": "sug_0",
  "from": 0,
  "to": 120
}
```

- `from`/`to` are optional ProseMirror document positions (not character offsets — PM uses a node-counting `doc.resolve()` coordinate system). Default (or `-1`) = entire document.
- Coordinates are computed by the backend when extracting the fence: for full-file suggestions, `from=0, to=-1`. For precise ranges, the agent specifies positions in the ```` ```suggestion at:42-56 ```` syntax, and the backend proxies them through. The JS bridge in the editor converts these to PM `doc.resolve()` positions.
- Sent alongside `done`, extracted in the same handler as existing directives and canvas surfaces (see Event timing below).

### Event timing

All diff/suggestion events are extracted and sent inside the `done` chunk handler, after streaming completes. The flow is:

1. Agent text streams → WS `text_delta` events
2. Agent finishes → WS `done` event handler fires
3. Handler extracts all block types from the response text:
   - Directives (`[SWITCH_TAB]`, `[WIDGET_REFRESH]`, `[NEW_SESSION]`, `[OPEN_EDITOR]`) — sent as bare dict events (no Pydantic wrapper)
   - Canvas fences (` ```html:canvas ``, `` ```html:editor ``) — sent as `canvas_update` events
   - Diff fences (` ```diff:path ``) — sent as `diff_card` events (via `DiffCardMessage`)
   - Suggestion fences (` ```suggestion ``) — sent as `suggestion_push` events (via `SuggestionPushMessage`), content stored in `_suggestion_store`
4. Handler strips all fence blocks from display text
5. Handler stores the cleaned response in conversation history
6. WS `done` event sent with final response text

Extraction and emission order within the handler: directives first (tab switching before other events), then suggestion/diff/canvas events. This matches the existing pattern in `ws.py:_run_agent_stream()` where directives and `canvas_update` events are sent before `done`.

### Pydantic models

Following the existing `CanvasUpdateMessage` pattern in `ws_protocol.py`:

```python
class DiffCardMessage(BaseModel):
    """Server sends a diff card to display inline in chat."""
    type: str = "diff_card"
    file_path: str
    diff: str
    suggestion_id: str

class SuggestionPushMessage(BaseModel):
    """Server pushes an inline suggestion to the ProseMirror editor."""
    type: str = "suggestion_push"
    file_path: str
    content: str
    suggestion_id: str
    from_: int = Field(-1, alias="from")
    to: int = -1

class SuggestionAcceptMessage(BaseModel):
    """Client accepts an inline suggestion."""
    type: str = "suggestion_accept"
    suggestion_id: str
    file_path: str

class SuggestionRejectMessage(BaseModel):
    """Client rejects an inline suggestion."""
    type: str = "suggestion_reject"
    suggestion_id: str
    file_path: str

class SuggestionAcceptAllMessage(BaseModel):
    """Client accepts all pending suggestions for a file."""
    type: str = "suggestion_accept_all"
    file_path: str

class DiffCardAcceptMessage(BaseModel):
    """Client accepts a diff card proposal."""
    type: str = "diff_card_accept"
    suggestion_id: str
    file_path: str

class DiffCardRejectMessage(BaseModel):
    """Client rejects a diff card proposal."""
    type: str = "diff_card_reject"
    suggestion_id: str
    file_path: str = ""

class DiffCardErrorMessage(BaseModel):
    """Server reports an error applying a diff or suggestion."""
    type: str = "diff_card_error"
    code: str  # DIFF_CONFLICT, FILE_NOT_FOUND, WRITE_FAILED
    message: str
    suggestion_id: str
    file_path: str
```

Register in the message type maps:

```python
CLIENT_MESSAGE_TYPES = {
    ...
    "suggestion_accept": SuggestionAcceptMessage,
    "suggestion_reject": SuggestionRejectMessage,
    "suggestion_accept_all": SuggestionAcceptAllMessage,
    "diff_card_accept": DiffCardAcceptMessage,
    "diff_card_reject": DiffCardRejectMessage,
}

SERVER_MESSAGE_TYPES = {
    ...
    "diff_card": DiffCardMessage,
    "suggestion_push": SuggestionPushMessage,
    "diff_card_error": DiffCardErrorMessage,
}
```

### Client → Server

#### `suggestion_accept` event

```json
{
  "type": "suggestion_accept",
  "suggestion_id": "sug_0",
  "file_path": "path/to/note.md"
}
```

Backend writes the suggestion content to the file via `files_write` tool (or appropriate storage API), then broadcasts `widget_refresh` so the widget re-fetches.

**Content persistence:** The `suggestion_accept` event carries only the `suggestion_id`, not the content. The content was already sent in the `suggestion_push` event. The backend stores suggestion content per `suggestion_id` when it sends `suggestion_push`. Storage options:
- **Primary:** In-memory `dict[str, SuggestionStore]` — keyed by `suggestion_id`, value is `{"content": str, "file_path": str, "created_at": float, "from": int, "to": int}`. This is simple and sufficient since suggestions are ephemeral (survive only for the session).
- **Fallback:** If the backend process restarts mid-session, pending suggestions are lost — the Flutter side shows "Session expired" on the suggestion decoration and prompts the user to re-ask.

```python
# In ws.py or a new suggestions.py module:

_suggestion_store: dict[str, dict[str, Any]] = {}

def _store_suggestion(suggestion_id: str, content: str, file_path: str, from_: int = -1, to: int = -1) -> None:
    _suggestion_store[suggestion_id] = {
        "content": content,
        "file_path": file_path,
        "from": from_,
        "to": to,
        "created_at": time.time(),
    }

def _get_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    return _suggestion_store.get(suggestion_id)

def _remove_suggestion(suggestion_id: str) -> None:
    _suggestion_store.pop(suggestion_id, None)
```

#### `suggestion_reject` event

```json
{
  "type": "suggestion_reject",
  "suggestion_id": "sug_0",
  "file_path": "path/to/note.md"
}
```

Backend does nothing. The suggestion decoration is removed from the editor.

#### `suggestion_accept_all` event

```json
{
  "type": "suggestion_accept_all",
  "file_path": "path/to/note.md"
}
```

Accepts all pending suggestions for the given file. Backend applies them in order.

#### `diff_card_accept` event

```json
{
  "type": "diff_card_accept",
  "suggestion_id": "diff_0",
  "file_path": "path/to/file.md"
}
```

Backend applies the diff to the file (reads current content, applies unified diff, writes result).

#### `diff_card_reject` event

```json
{
  "type": "diff_card_reject",
  "suggestion_id": "diff_0",
  "file_path": "path/to/file.md"
}
```

Backend does nothing. The diff card is dismissed in the UI. The `file_path` field is included for routing/disambiguation (multiple diff cards may target different files).

---

## 4. ProseMirror Editor

### Why ProseMirror (replacing TipTap)

| Factor | TipTap (current) | ProseMirror |
|--------|------------------|-------------|
| License | MIT | MIT |
| Bundle size | ~500KB (bundled) | ~150KB (core only) |
| AI co-edit API | None (must build on top of PM) | Native decoration API |
| Inline suggestions | Custom plugin needed | `DecorationSet` + `Decoration` widget |
| Position tracking | Manual | `Transaction.map()` built-in |
| Paid tiers | No | No |
| Active maintenance | Yes (based on PM) | Yes |

ProseMirror is the foundation TipTap itself is built on. By using PM directly we:
- Avoid the TipTap abstraction overhead (~300KB+ of extension code we don't need)
- Get direct access to the decoration API for suggestions
- Have full control over editor behavior
- Eliminate dependency risk (TipTap's paid AI features are irrelevant)

### Migration strategy

The current `editor.html` wraps TipTap's entire bundle. Replace with a minimal ProseMirror setup:

```
editor.html
├── ProseMirror core (prosemirror-view, prosemirror-state, prosemirror-model, prosemirror-schema-basic, prosemirror-schema-list)
├── prosemirror-example-setup (keymaps, input rules, menu)
├── Suggestion plugin (~300 lines, custom)
└── Minimal CSS
```

Bundle: ~200KB vs current ~500KB.

### Suggestion plugin design

```typescript
// prosemirror-suggestion-plugin.ts

interface Suggestion {
  id: string;
  filePath: string;
  suggestedText: string;
  from: number;      // document position (-1 or undefined = entire doc)
  to: number;        // document position (-1 or undefined = entire doc)
}

class SuggestionPlugin {
  private suggestions: Map<string, Suggestion> = new Map();
  
  // Decoration spec — green underline + accept/reject buttons
  get decorations(): DecorationSet { /* ... */ }
  
  // Called when suggestion_push WS event arrives
  addSuggestion(sug: Suggestion): void {
    // Store suggestion, trigger decoration rebuild
  }
  
  // Called on suggestion_accept
  acceptSuggestion(id: string): Transaction {
    // Apply suggestion, remove decoration, return transaction
    // Uses tr.map() for position tracking
  }
  
  // Called on suggestion_reject
  rejectSuggestion(id: string): Transaction {
    // Remove decoration only, return transaction
  }
}
```

### Position tracking

When the user edits text after a suggestion is received, positions shift. ProseMirror handles this automatically — `Decoration` positions are mapped through subsequent transactions via `map()`.

When accepting a suggestion, the plugin maps the stored `from`/`to` positions through all transactions since the suggestion was received, ensuring the correct span is replaced.

```
User types "abc" at position 5
→ Agent suggests replacing "def" at position 10-13
→ User types "xyz" at position 3
→ All subsequent positions shift by +3
→ Accept maps suggestion position: 10→13, 13→16 (auto-mapped)
```

### Accept/reject button rendering

ProseMirror `DecorationType` with `widget` spec places accept/reject buttons near the suggestion range:

```typescript
// Decoration widget at end of suggestion span
acceptRejectWidget = Decoration.widget({
  side: 1, // after the decorated range
  widget: new SuggestionControlWidget(suggestion.id)
});
```

The widget renders a small pill: `[Accept] [Reject]` using green accent / muted red colors from `ea-tokens.css`.

Clicking Accept fires `ws.send({type: "suggestion_accept", suggestion_id, file_path})`.
Clicking Reject fires `ws.send({type: "suggestion_reject", suggestion_id, file_path})`.

### JS → Flutter bridge

The editor WebView uses `postMessage` to send suggestion accept/reject events back to Flutter:
```javascript
// Inside SuggestionControlWidget.onAccept:
window.parent.postMessage({
  type: 'editor_action',
  action: 'suggestion_accept',
  suggestion_id: 'sug_0',
  file_path: '/path/to/file.md'
}, '*');
```

On the Flutter side, the `WebViewWidget` catches these via `onJavaScriptChannel` or `NavigationDelegate` URL interception:
```dart
// Inside the editor WebView builder:
WebViewWidget(
  controller: _controller,
  javascriptMode: JavascriptMode.unrestricted,
  onJavaScriptMessage: (message) {
    final data = jsonDecode(message.message);
    if (data['type'] == 'editor_action') {
      // Route to agent_provider.dart WS send
      _onSuggestionEvent(data['action'], data);
    }
  },
);
```

Note: `postMessage` via `window.parent` works because the editor HTML is loaded as inline content (not an iframe). Alternative: use a custom URL scheme like `app://suggestion_accept?id=sug_0` intercepted by `NavigationDelegate`. The `postMessage` approach is preferred.

---

## 5. Diff Card Widget (Flutter)

The diff card is a Flutter widget rendered inline in the chat message list (not in the workspace panel).

### Widget spec

```
┌─────────────────────────────────┐
│ 📝 Suggested edit: file.md      │
│                                 │
│  ┌───────────────────────────┐  │
│  │ - Old line                │  │
│  │ + New line                │  │
│  │ + Another new line        │  │
│  │  unchanged line           │  │
│  └───────────────────────────┘  │
│                                 │
│    [Accept]  [Reject]           │
└─────────────────────────────────┘
```

- File path shown in header
- Diff rendered with standard unified diff syntax highlighting
  - `-` lines in `--error` / muted red
  - `+` lines in `--success` / green
  - Context lines in `--text-secondary`
- Monospace font (SF Mono / JetBrains Mono, 13px)
- Accept button: green accent background
- Reject button: transparent with error border
- Both buttons send WS events to the backend

### State

- `pending` — awaiting user decision
- `accepted` — user clicked accept, animation (checkmark, fade out after 2s)
- `rejected` — user clicked reject, animation (fade out immediately)
- `failed` — backend returned `diff_card_error` when applying the change. Shows error message from the server + "Re-request" button (re-prompts agent to re-read the file and generate a fresh suggestion).

---

## 6. Inline Suggestion Widget (Flutter)

The editor is a ProseMirror-powered WebView (reusing the `WidgetWebView` pattern from Phase 3). 

### Editor URL routing

The editor is served via `_render_editor_surface()` which takes `file_path` and `content` and fills the `editor.html` template with `__FILE_PATH__` and `__INITIAL_HTML__` placeholders. The editor WebView URL is not a REST endpoint — it's rendered inline in the Notes tab. When the `open_editor` directive fires:

1. Flutter receives `{"type": "open_editor", "file_path": "path/to/file.md", "tab": "notes"}`
2. Flutter switches to Notes tab (or opens it)
3. Flutter calls `GET /workspace/read/path/to/file.md` to get current file content
4. Flutter loads the editor WebView with the file content via `_render_editor_surface()` (server-side) or directly injects content into the ProseMirror template
5. The editor's `_activeEditorFilePath` is set to the file path
6. `_drainSuggestionQueue()` is called to apply any queued suggestions

The `suggestion_push` event is forwarded to the editor via JavaScript evaluation:

```dart
// Queue management in agent_provider.dart:
Map<String, List<Map<String, dynamic>>> _suggestionQueue = {};
const int _maxQueuedPerFile = 100;

bool _isEditorOpenFor(String filePath) {
  // Returns true if the active editor WebView is showing this file path.
  // Set when open_editor event fires or user opens the editor tab.
  return _activeEditorFilePath == filePath;
}

void _onSuggestionPush(Map<String, dynamic> data) {
  final filePath = data['file_path'] as String;
  if (_isEditorOpenFor(filePath)) {
    _routeSuggestionToEditor(data);
  } else {
    // Queue suggestion by file path (cap to prevent memory leak)
    _suggestionQueue.putIfAbsent(filePath, () => []);
    if (_suggestionQueue[filePath]!.length < _maxQueuedPerFile) {
      _suggestionQueue[filePath]!.add(data);
    }
  }
}

// Called when an editor WebView is opened/loaded
void _drainSuggestionQueue(String filePath) {
  final queued = _suggestionQueue.remove(filePath);
  if (queued != null) {
    for (final data in queued) {
      _routeSuggestionToEditor(data);
    }
  }
}

void _routeSuggestionToEditor(Map<String, dynamic> data) {
  _activeEditorController?.runJavaScript('''
    window.__addSuggestion(${jsonEncode(data)});
  ''');
}
```

### Editor initialization

```html
<!-- editor.html (replaces TipTap bundle with ProseMirror) -->
<script src="prosemirror.min.js"></script>
<script src="suggestion-plugin.js"></script>
<script>
  const view = new EditorView(document.querySelector('#editor'), {
    state: EditorState.create({
      doc: DOMParser.fromSchema(mySchema).parse(initialHtml),
      plugins: [
        exampleSetup({ schema: mySchema }),
        suggestionPlugin({
          onAccept: (id) => window.parent.postMessage({
            type: 'editor_action',
            action: 'suggestion_accept',
            suggestion_id: id,
            file_path: currentFilePath,
          }, '*'),
          onReject: (id) => window.parent.postMessage({
            type: 'editor_action',
            action: 'suggestion_reject',
            suggestion_id: id,
            file_path: currentFilePath,
          }, '*'),
        }),
      ],
    }),
  });
  
  window.__addSuggestion = (data) => {
    const from = data.from != null && data.from >= 0 ? data.from : null;
    const to = data.to != null && data.to >= 0 ? data.to : null;
    view.suggestionPlugin.addSuggestion({
      id: data.suggestion_id,
      filePath: data.file_path,
      suggestedText: data.content,
      from: from ?? 0,
      to: to ?? view.state.doc.content.size,
    });
  };
</script>
```

---

## 7. Backend: Apply Diff Logic

For diff card acceptance, the backend applies the diff to the file. This must be safe against concurrent edits — if the original file changed since the agent generated the diff, the backend must detect the mismatch and fail cleanly.

### Algorithm

A simple line-by-line context-validating diff apply that does NOT rely on hunk headers (`@@ ... @@`):

```python
def apply_unified_diff(original: str, diff_lines: list[str]) -> str | None:
    """Apply a unified diff with context validation.
    
    Returns the new content on success, or None if context lines don't match
    (meaning the file was edited since the diff was generated).
    
    Expected format (agent-generated, no hunk headers needed):
      - old line
      + new line  
       unchanged context line
    """
    orig = original.split("\n")
    result: list[str] = []
    i = 0
    
    for dline in diff_lines:
        stripped = dline.strip()
        
        # Skip blank lines between hunks
        if not stripped:
            continue
            
        if dline.startswith("+"):
            result.append(dline[1:])
        elif dline.startswith("-"):
            # Verify context: the removed line must match
            if i < len(orig) and dline[1:] != orig[i]:
                return None  # context mismatch — file was edited
            i += 1
        elif dline.startswith(" "):
            # Verify context: unchanged line must match
            if i >= len(orig) or dline[1:] != orig[i]:
                return None  # context mismatch
            result.append(orig[i])
            i += 1
        else:
            return None  # malformed diff line
    
    # Append remaining original lines
    while i < len(orig):
        result.append(orig[i])
        i += 1
    
    return "\n".join(result)
```

**Key constraints:**
- Returns `None` (not silently corrupts) if context lines don't match the original
- Agent-generated diffs are simple consecutive ± lines (no hunk headers needed)
- For complex diffs (multiple hunks, renames): use the `diff-match-patch` library (already available in Python ecosystem) via `diff_match_patch.patch_apply()`

The agent workflow should prefer full-file replacement over patch diffs when feasible (```` ```suggestion ```` with full content), reserving `diff:path` for small targeted changes.

### Error handling

If `apply_unified_diff` returns `None` (context mismatch) or the file no longer exists, the backend sends an error response:

```json
{
  "type": "diff_card_error",
  "code": "DIFF_CONFLICT",
  "message": "File was edited since the suggestion was made. The diff could not be applied.",
  "suggestion_id": "diff_0",
  "file_path": "path/to/file.md"
}
```

The Flutter diff card transitions to the `failed` state, showing the error message and offering a "Re-request" button that prompts the agent to re-read and re-suggest.

---

## 8. Agent Integration

### Agent directives (new)

| Directive | Effect |
|-----------|--------|
| `[OPEN_EDITOR:path/to/file.md]` | Open the ProseMirror editor for the given file |

The agent can open the editor on any text file accessible via `files_read`. The editor loads the file content and displays it for co-edit.

This directive is parsed by `parse_directives()` (same as `SWITCH_TAB`/`WIDGET_REFRESH`) and sends:

```python
{"type": "open_editor", "file_path": "path/to/file.md", "tab": "notes"}
```

The `tab` field is inferred from the file extension:
- `.md`, `.txt`, `.json`, `.yaml`, `.py`, `.js`, `.ts`, `.css`, `.html` → `notes`
- Email-related (no extension or `.eml`) → `email` (future)

The Flutter workspace panel opens the specified tab, which loads the editor WebView with the file path. The `parse_directives()` function gains a new regex pattern `r'\[OPEN_EDITOR:([^\]]+)\]'` and emits the above event dict with `file_path` and inferred `tab`.

### Agent workflow for diff card

1. User asks "can you improve this paragraph in my notes?"
2. Agent calls `files_read("path/to/notes.md")` to get current content
3. Agent responds with ```` ```diff:path/to/notes.md ``` ```` block showing changes
4. Backend extracts diff, sends `diff_card` WS event + strips fence from display text
5. Flutter renders diff card in chat with Accept/Reject
6. User clicks Accept → `diff_card_accept` WS event → backend calls `files_write` with new content
7. Backend sends `widget_refresh → files` so the Files widget shows updated content

### Agent workflow for inline suggestions

1. User opens a note in the editor tab
2. User writes some content, asks agent "what do you think?"
3. Agent calls `files_read(...)` to see content, responds with ```` ```suggestion ```` block
4. Backend extracts suggestion, sends `suggestion_push` WS event + strips fence
5. Editor shows green-highlighted suggestion overlay with accept/reject per suggestion
6. User clicks Accept → `suggestion_accept` WS event → backend writes file
7. Widget refreshes

---

## 9. Implementation Plan

### Task 4.1: ProseMirror migration
- Replace TipTap bundle in `editor.html` with ProseMirror core + suggestion plugin
- Write the 300-line suggestion plugin (decoration set, widget rendering, accept/reject JS handlers)
- Update `_render_editor_surface()` to use new ProseMirror template
- Bundle: ~200KB (ProseMirror + suggestion plugin + minimal CSS)

### Task 4.2: Backend block extraction + directive parsing
- Add `_DIFF_FENCE` and `_SUGGESTION_FENCE` regex patterns to `conversation.py`
- Add `_extract_diff()`, `_extract_suggestions()` functions
- Update `_extract_surfaces()` and `_strip_canvas_fences()` to include new fence types
- Wire diff/suggestion extraction into `ws.py` alongside existing directive/canvas extraction in the `done` chunk handler
- Add `[OPEN_EDITOR]` directive to `parse_directives()` in `ws.py`: regex `r'\[OPEN_EDITOR:([^\]]+)\]'` emits `{"type": "open_editor", "file_path": "...", "tab": "notes"}`

### Task 4.3: WS protocol types + diff utility
- Add all 8 Pydantic models to `ws_protocol.py`: `DiffCardMessage`, `SuggestionPushMessage`, `SuggestionAcceptMessage`, `SuggestionRejectMessage`, `SuggestionAcceptAllMessage`, `DiffCardAcceptMessage`, `DiffCardRejectMessage`, `DiffCardErrorMessage`
- Register client types in `CLIENT_MESSAGE_TYPES` and server types in `SERVER_MESSAGE_TYPES`
- Add `apply_unified_diff()` utility in a new `src/http/diff_utils.py`
- Add `_suggestion_store` (in-memory dict) with `_store_suggestion()` / `_get_suggestion()` / `_remove_suggestion()` in a new `src/http/suggestion_store.py`

### Task 4.4: Flutter diff card widget
- Create `flutter_app/lib/widgets/diff_card.dart` — a Flutter widget for inline diff rendering
- States: `pending`, `accepted`, `rejected`, `failed` (with error message from `diff_card_error` + "Re-request" button)
- WS event handling in `agent_provider.dart`: `diff_card`, `diff_card_error`, `suggestion_push`
- Render diff cards in chat message list

### Task 4.5: Flutter inline suggestion integration
- Wire `suggestion_push` events from `agent_provider.dart` to the active editor `WebViewWidget`
- Add JavaScript bridge in the editor WebView to call `window.__addSuggestion()`
- Handle suggestion queues (suggestions for files not currently open, capped at 100 per file)
- Handle `suggestion_accept` and `suggestion_reject` from editor JS to WS
- Handle `open_editor` directive event: switch to Notes tab, load editor WebView with specified file path, drain suggestion queue for that file

### Task 4.6: Backend suggestion/diff apply handlers
- In `ws.py`, handle `suggestion_accept`, `suggestion_accept_all`, `diff_card_accept` client messages
- Apply diff/suggestion to file content and call `files_write` (or appropriate storage API)
- `files_write` already handles writing to any path under ea_root
- On success: send `widget_refresh` + confirmation in `done` event
- On failure: send `diff_card_error` event with appropriate code (`DIFF_CONFLICT`, `FILE_NOT_FOUND`, `WRITE_FAILED`)

### Test strategy

Each task includes verification:

| Task | Tests |
|------|-------|
| 4.1 ProseMirror | Manual: editor loads, renders content, saves. JS unit: suggestion plugin add/accept/reject. |
| 4.2 Block extraction | Unit: `test_block_extraction.py` — verify `_extract_diff`, `_extract_suggestions` parse correct fences, ignore malformed ones, strip from text. Follow existing pattern in `tests/api/test_ws_protocol.py:12 parse_directives unit tests`. |
| 4.3 WS protocol | Unit: `test_ws_protocol.py` — verify new message types serialize/deserialize, register in type maps. |
| 4.4 Diff card widget | Flutter widget test: render diff card, test all 4 states (`pending`→`accepted`/`rejected`/`failed`), WS event firing on accept/reject. |
| 4.5 Suggestion integration | Integration: send `suggestion_push` from server, verify JS bridge called in WebView. Unit: queue management (queue + drain). |
| 4.6 Backend handlers | Unit: `test_diff_utils.py` — `apply_unified_diff` with matching context, mismatched context (±), malformed input, empty diff, \ No newline at end of file. Integration: `diff_card_accept` → file written, `DIFF_CONFLICT` error sent on mismatch. |

---

## 10. Security & UX Boundaries

| Constraint | Rationale |
|------------|-----------|
| Agent never writes without explicit user accept | Core co-edit principle; prevents unwanted modifications |
| Editor is read-only during agent turn | No concurrent edits; avoids position tracking edge cases |
| Suggestions are ephemeral | If the editor is closed, pending suggestions are lost (agent can re-propose) |
| Diff card is part of chat history | Provides audit trail of what was proposed and decided |
| Agent-created files still require `files_write` tool call | Standard tool permission model applies (read-only auto-approve, destructive requires HITL) |
| No CRDT / OT sync | Out of scope; turn-based is sufficient for MVP co-edit |

---

## 11. Non-Goals

- Real-time collaborative editing (OT/CRDT)
- Multi-user editing
- Rich text formatting toolbar (future)
- Drag-and-drop file attachment in editor
- Offline editing
- Editor persistence across app restarts (future)

---

## 12. Open Questions

1. Should `suggestion_push` target the Notes tab specifically, or any tab that has an open editor?
   - **Proposal:** Any file-editing surface (Notes, Email compose, text file viewer). The editor WebView route includes the file path; suggestions route to whichever editor shows that file.
2. For diff cards: should acceptance trigger a notification in the workspace tab?
   - **Proposal:** Yes — after accept, send `widget_refresh:files` so the Files widget re-fetches and shows updated content.
3. Should suggestions auto-position at cursor location or at a smart-insert point?
   - **Resolved:** The `from`/`to` fields in `suggestion_push` support precise positioning. Initially they default to full-document. Future: agent can specify positions via ```` ```suggestion at:42-56 ``` ```` syntax or programmatic `from`/`to` in the push payload.
