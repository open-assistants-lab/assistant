# Assistant UX: Jump-to-Bottom, Draft Persistence, Fade Removal

2026-06-02

## Context

After the scroll-to-bottom refactor (`reverse: true` on `ChatMessageList`), three
UX gaps remain in the chat panel:

1. **No "jump to bottom" affordance.** When the user scrolls up to read history
   and new messages stream in, the chat silently grows above the fold. The user
   has no way to know there are new messages or jump back to them. Standard chat
   apps (Slack, Discord, iMessage, WhatsApp) show a floating "↓ N new" button
   anchored above the input when the viewport is not at the bottom.

2. **Input draft is lost on workspace switch.** `AppChatField` owns its own
   `TextEditingController` in `_AppChatFieldState`. Because the parent
   `ChatInput` is below the `KeyedSubtree` keyed by `activeWs` (in
   `_PanelMessageList`), the `TextField` state is destroyed and recreated on
   every workspace switch. The user loses any text they were typing. Standard
   behavior in modern chat apps: draft persists per conversation.

3. **Dead opacity fade on workspace switch.** The `TweenAnimationBuilder`
   wrapping `_PanelMessageList` (in `desktop_layout.dart:712`) animates opacity
   0→1 over `tokens.motion.base` (~200ms). This was added in commit `33a37bd`
   to mask the old scroll-jump glitch. With `reverse: true` the scroll is
   correct on the first frame, so the fade is dead complexity that delays
   content visibility.

## Summary

Three changes, all in `flutter_app/`:

1. **Jump-to-bottom button** — `JumpToBottomButton` widget. Floats above the
   input. Hidden when viewport is at bottom (with `reverse: true`:
   `extentBefore == 0`). Visible with a "↓ N new" badge when
   `extentBefore > threshold`. Clicking it calls `scrollController.jumpTo(0)`.

2. **Per-workspace input draft** — lift the `TextEditingController` and
   `FocusNode` from `_AppChatFieldState` into a Riverpod `StateNotifier`
   keyed by workspace ID. On workspace switch, the new controller loads the
   saved draft (if any). On send, the draft is cleared. Drafts persist in
   memory only (no disk persistence in this batch).

3. **Remove the opacity fade** — delete the `TweenAnimationBuilder` from
   `desktop_layout.dart:712`. Content is visible immediately on workspace
   switch.

## Files

### New
- `flutter_app/lib/features/chat/widgets/jump_to_bottom_button.dart` — the
  floating button widget
- `flutter_app/lib/providers/draft_provider.dart` — Riverpod state for
  per-workspace input drafts
- `flutter_app/test/features/chat/widgets/jump_to_bottom_button_test.dart` —
  widget tests for the button

### Modify
- `flutter_app/lib/core/layout/desktop_layout.dart` — remove
  `TweenAnimationBuilder`; add `JumpToBottomButton` above input; wire
  `ScrollController` position to button visibility
- `flutter_app/lib/features/chat/widgets/chat_input.dart` — read draft from
  `draftProvider` instead of owning controller
- `flutter_app/lib/widgets/app_input.dart` — accept external
  `TextEditingController` + `FocusNode` via constructor; remove internal
  state for them
- `flutter_app/test/core/layout/desktop_layout_test.dart` — add test for
  jump-to-bottom button visibility

## Design

### 1. Jump-to-bottom button

**Placement:** `Positioned` inside a `Stack` that wraps the message list and
the input. Anchored `bottom: 8`, horizontally centered or right-aligned
(matches the chat input's horizontal padding).

**Visibility logic:**
- `ScrollController.position` exists
- `position.extentBefore > 16` (with `reverse: true`, this means scrolled
  away from bottom by more than 16px)
- Animated in/out using `AnimatedOpacity` + `AnimatedSlide` (200ms)

**Badge count:** track the number of new messages received while scrolled
away. Increment when `state.messages.length` increases AND
`extentBefore > threshold`. Reset to 0 when button is tapped or viewport
returns to bottom.

**Widget sketch:**
```
┌────────────────────────────┐
│                            │
│   (message list)           │
│                            │
│              ┌──────────┐  │  ← floating, right-aligned
│              │  ↓ 3 new │  │
│              └──────────┘  │
├────────────────────────────┤
│ (chat input)               │
└────────────────────────────┘
```

### 2. Per-workspace input draft

**Provider shape:**
```dart
final draftProvider = StateNotifierProvider<DraftNotifier, Map<String, String>>(
  (ref) => DraftNotifier(),
);

class DraftNotifier extends StateNotifier<Map<String, String>> {
  // state: { workspaceId: draftText }
  void save(String workspaceId, String text) { ... }
  String? load(String workspaceId) => state[workspaceId];
  void clear(String workspaceId) { ... }
}
```

**Integration:**
- `ChatInput` reads `ref.watch(currentWorkspaceIdProvider)` to get the active
  workspace
- On workspace change, the `TextEditingController` is updated to the saved
  draft (via a `ref.listen` in the parent or `useEffect` equivalent in
  `ConsumerStatefulWidget`)
- On send, `_send()` calls `ref.read(draftProvider.notifier).clear(workspaceId)`
  before `widget.onSend(text)`
- `AppChatField` accepts `controller` and `focusNode` as required
  constructor parameters; internal `_controller` and `_focusNode` removed

### 3. Remove opacity fade

In `desktop_layout.dart`, the `_PanelMessageList.build` currently returns:
```dart
TweenAnimationBuilder<double>(
  key: ValueKey('chat_list_$activeWs'),
  tween: Tween(begin: 0.0, end: 1.0),
  builder: (_, t, child) => Opacity(opacity: t.clamp(0.0, 1.0), child: child),
  child: KeyedSubtree(...),
)
```

Change to:
```dart
KeyedSubtree(
  key: ValueKey('chat_list_inner_$activeWs'),
  child: ChatMessageList(...),
)
```

The outer `KeyedSubtree` key (which forces full rebuild on workspace switch)
stays — it's still needed to reset the `ListView`'s scroll position and
rebuild the `messageKeys` map.

## Testing

### Widget tests (TDD)
- `JumpToBottomButton`: renders when `extentBefore > threshold`, hidden when
  at bottom, tap calls `onPressed`
- `desktop_layout_test.dart`: new test — scroll up, verify button appears;
  tap button, verify scroll position returns to 0

### Integration check (manual)
- Switch to `personal` → text scrolls to bottom immediately (no fade)
- Type a draft in workspace A, switch to B, switch back → draft restored
- Scroll up in a long conversation → button appears
- Send a message while scrolled up → button badge increments

## Out of scope (deferred)
- Persisting drafts to disk (SharedPreferences) — this batch is in-memory only
- "Jump to first unread" behavior — just "jump to bottom" for now
- Animated list insertion (new messages sliding in) — current behavior is
  fine
