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
            "type": "diff_card",
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
            "type": "suggestion",
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

- `from`/`to` are optional document positions (character offsets). Default (or `-1`) = entire document.
- Sent alongside `done`. Flutter routes to the ProseMirror editor showing the file. If no editor is open for that file, the suggestion is queued — the Flutter agent provider maintains `_suggestionQueue: Map<String, List<Map>>` keyed by file path. When the editor opens a file, it drains the queue and applies all queued suggestions via `window.__addSuggestion()`.

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
  "suggestion_id": "diff_0"
}
```

Backend does nothing. The diff card is dismissed in the UI.

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

---

## 6. Inline Suggestion Widget (Flutter)

The editor is a ProseMirror-powered WebView (reusing the `WidgetWebView` pattern from Phase 3). The `suggestion_push` event is forwarded to the editor via JavaScript evaluation:

```dart
// Queue management in agent_provider.dart:
Map<String, List<Map<String, dynamic>>> _suggestionQueue = {};

void _onSuggestionPush(Map<String, dynamic> data) {
  final filePath = data['file_path'] as String;
  // Only forward if this editor is showing the same file
  if (_isEditorOpenFor(filePath)) {
    _routeSuggestionToEditor(data);
  } else {
    // Queue suggestion by file path
    _suggestionQueue.putIfAbsent(filePath, () => []).add(data);
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
          onAccept: (id) => postMessage({ type: 'suggestion_accept', id }),
          onReject: (id) => postMessage({ type: 'suggestion_reject', id }),
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

For diff card acceptance, the backend parses the unified diff format and applies it:

```python
def apply_unified_diff(original: str, diff_text: str) -> str:
    """Apply a unified diff to original content.
    
    Supports standard +/- unified diff format:
      - line to remove
      + line to add
       unchanged context line
    """
    lines = original.split("\n")
    result: list[str] = []
    diff_lines = diff_text.split("\n")
    i = 0  # index into original lines
    in_hunk = False
    
    for dline in diff_lines:
        if dline.startswith("+"):
            result.append(dline[1:])  # add new line
            in_hunk = True
        elif dline.startswith("-"):
            i += 1  # skip original line
            in_hunk = True
        elif dline.startswith(" "):
            result.append(lines[i]) if i < len(lines) else None
            i += 1
            in_hunk = True
        # blank lines or context separators: copy through
        elif not dline.strip() and not in_hunk:
            result.append("")
        elif not dline.strip():
            result.append("")
    
    # Append remaining original lines
    while i < len(lines):
        result.append(lines[i])
        i += 1
    
    return "\n".join(result)
```

This is applied when the user accepts a `diff_card`. The backend tool call carries the file path and the diff-text.

---

## 8. Agent Integration

### Agent directives (new)

| Directive | Effect |
|-----------|--------|
| `[OPEN_EDITOR:path/to/file.md]` | Open the ProseMirror editor for the given file |

The agent can open the editor on any text file accessible via `files_read`. The editor loads the file content and displays it for co-edit.

This directive is parsed by `parse_directives()` (same as `SWITCH_TAB`/`WIDGET_REFRESH`) and sent as a `switch_tab:notes` WS event plus a payload carrying the file path. The Flutter workspace panel opens the Notes tab, which loads the editor WebView with the specified file.

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

### Task 4.2: Backend block extraction
- Add `_DIFF_FENCE` and `_SUGGESTION_FENCE` regex patterns to `conversation.py`
- Add `_extract_diff()`, `_extract_suggestions()` functions
- Update `_extract_surfaces()` and `_strip_canvas_fences()` to include new fence types
- Wire diff/suggestion extraction into `ws.py` alongside existing directive/canvas extraction in the `done` chunk handler

### Task 4.3: WS protocol types
- Add `DiffCardMessage`, `SuggestionPushMessage`, `SuggestionAcceptMessage`, `SuggestionRejectMessage`, `SuggestionAcceptAllMessage`, `DiffCardAcceptMessage`, `DiffCardRejectMessage` to `ws_protocol.py`
- Register in `CLIENT_MESSAGE_TYPES` and `SERVER_MESSAGE_TYPES`
- Add `apply_unified_diff()` utility in a new `src/http/diff_utils.py`

### Task 4.4: Flutter diff card widget
- Create `flutter_app/lib/widgets/diff_card.dart` — a Flutter widget for inline diff rendering
- States: `pending`, `accepted`, `rejected`
- WS event handling in `agent_provider.dart`: `diff_card`, `suggestion_push`
- Render diff cards in chat message list

### Task 4.5: Flutter inline suggestion integration
- Wire `suggestion_push` events from `agent_provider.dart` to the active editor `WebViewWidget`
- Add JavaScript bridge in the editor WebView to call `window.__addSuggestion()`
- Handle suggestion queues (suggestions for files not currently open)
- Handle `suggestion_accept` and `suggestion_reject` from editor JS to WS

### Task 4.6: Backend suggestion/diff apply handlers
- In `ws.py`, handle `suggestion_accept`, `suggestion_accept_all`, `diff_card_accept` client messages
- Apply diff/suggestion to file content and call `files_write` (or appropriate storage API)
- `files_write` already handles writing to any path under ea_root
- On success: send `widget_refresh` + confirmation in `done` event

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
