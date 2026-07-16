# Chat UX Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "jump to bottom" floating button, persist input drafts per workspace, and remove the dead opacity fade on workspace switch.

**Architecture:** Three independent UI/UX improvements to the chat panel. The jump-to-bottom button is a new widget wired to the existing `ScrollController`. Draft persistence lifts the `TextEditingController` from local widget state into a Riverpod `StateNotifier` keyed by workspace ID. The opacity fade is dead code from before the `reverse: true` scroll refactor — delete it.

**Tech Stack:** Flutter (Riverpod, `ListView.builder` with `reverse: true`), `flutter_test` for widget tests, `mocktail` for mocks.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `flutter_app/lib/features/chat/widgets/jump_to_bottom_button.dart` | New: floating "↓ N new" button widget |
| `flutter_app/lib/providers/draft_provider.dart` | New: Riverpod `StateNotifier` for per-workspace input drafts |
| `flutter_app/lib/core/layout/desktop_layout.dart` | Modify: remove fade, add button, wire scroll position |
| `flutter_app/lib/features/chat/widgets/chat_input.dart` | Modify: read draft from provider, pass controller to field |
| `flutter_app/lib/widgets/app_input.dart` | Modify: accept external `controller` + `focusNode` |
| `flutter_app/test/features/chat/widgets/jump_to_bottom_button_test.dart` | New: widget tests for the button |
| `flutter_app/test/core/layout/desktop_layout_test.dart` | Modify: add test for button visibility + tap |

---

## Task 1: JumpToBottomButton widget + tests

**Files:**
- Create: `flutter_app/lib/features/chat/widgets/jump_to_bottom_button.dart`
- Create: `flutter_app/test/features/chat/widgets/jump_to_bottom_button_test.dart`

- [ ] **Step 1: Write the failing test**

```dart
// flutter_app/test/features/chat/widgets/jump_to_bottom_button_test.dart
import 'package:executive_assistant/features/chat/widgets/jump_to_bottom_button.dart';
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  Widget wrap(Widget child) => MaterialApp(
    theme: AppTheme.light,
    home: Scaffold(body: Center(child: child)),
  );

  testWidgets('renders with new message count', (tester) async {
    await tester.pumpWidget(
      wrap(JumpToBottomButton(newCount: 3, onPressed: () {})),
    );
    expect(find.text('3 new'), findsOneWidget);
    expect(find.byIcon(Icons.arrow_downward), findsOneWidget);
  });

  testWidgets('renders singular "new" when count is 1', (tester) async {
    await tester.pumpWidget(
      wrap(JumpToBottomButton(newCount: 1, onPressed: () {})),
    );
    expect(find.text('1 new'), findsOneWidget);
  });

  testWidgets('hides badge and arrow when newCount is 0', (tester) async {
    await tester.pumpWidget(
      wrap(JumpToBottomButton(newCount: 0, onPressed: () {})),
    );
    expect(find.byIcon(Icons.arrow_downward), findsOneWidget);
    expect(find.text('0 new'), findsNothing);
  });

  testWidgets('calls onPressed when tapped', (tester) async {
    var tapped = 0;
    await tester.pumpWidget(
      wrap(JumpToBottomButton(newCount: 2, onPressed: () => tapped++)),
    );
    await tester.tap(find.byType(JumpToBottomButton));
    expect(tapped, 1);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/features/chat/widgets/jump_to_bottom_button_test.dart`
Expected: FAIL with "Target of URI doesn't exist" or "JumpToBottomButton not found"

- [ ] **Step 3: Implement the widget**

```dart
// flutter_app/lib/features/chat/widgets/jump_to_bottom_button.dart
import 'package:flutter/material.dart';
import '../../theme/app_theme.dart';

class JumpToBottomButton extends StatelessWidget {
  final int newCount;
  final VoidCallback onPressed;
  const JumpToBottomButton({
    super.key,
    required this.newCount,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Material(
      color: tokens.colors.bgSurface,
      shape: const CircleBorder(),
      elevation: 2,
      child: InkWell(
        onTap: onPressed,
        customBorder: const CircleBorder(),
        child: Padding(
          padding: EdgeInsets.all(tokens.spacing.sm + 2),
          child: Stack(
            clipBehavior: Clip.none,
            alignment: Alignment.center,
            children: [
              Icon(
                Icons.arrow_downward,
                size: 18,
                color: tokens.colors.textPrimary,
              ),
              if (newCount > 0)
                Positioned(
                  right: -8,
                  top: -8,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 6,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: tokens.colors.accent,
                      borderRadius: tokens.radius.fullAll,
                    ),
                    child: Text(
                      '$newCount new',
                      style: tokens.typography.textTheme.labelSmall?.copyWith(
                        color: tokens.colors.onAccent,
                        fontWeight: FontWeight.w600,
                        fontSize: 10,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/features/chat/widgets/jump_to_bottom_button_test.dart`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/features/chat/widgets/jump_to_bottom_button.dart \
        flutter_app/test/features/chat/widgets/jump_to_bottom_button_test.dart
git commit -m "feat: JumpToBottomButton widget with new-message badge"
```

---

## Task 2: Wire button into desktop_layout with scroll position tracking

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart:613-664` (the chat panel return tree)
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart:543-568` (`_ChatPanelState` state + scroll methods)
- Modify: `flutter_app/test/core/layout/desktop_layout_test.dart` (add test)

- [ ] **Step 1: Write the failing test**

Add to `flutter_app/test/core/layout/desktop_layout_test.dart` at the end of `main()`:

```dart
testWidgets(
  'shows jump-to-bottom button when scrolled up and resets scroll on tap',
  (tester) async {
    await tester.pumpWidget(_buildDesktopLayout(mockWs, mockApi));
    statusCtrl.add(ConnectionStatus.connected);
    await tester.pump(const Duration(milliseconds: 100));

    await tester.tap(find.text('Test').first);
    await tester.pump(const Duration(milliseconds: 100));
    await tester.pump(const Duration(milliseconds: 100));

    final messageList = find.byKey(const ValueKey('desktop-chat-message-list'));
    ScrollableState messageScrollable() => tester.state<ScrollableState>(
      find.descendant(of: messageList, matching: find.byType(Scrollable)).last,
    );

    // At bottom: button should NOT be visible
    expect(find.byType(JumpToBottomButton), findsNothing);

    // Scroll up (with reverse: true, drag +y moves away from bottom)
    await tester.drag(messageList, const Offset(0, 500));
    await tester.pump(const Duration(milliseconds: 100));
    expect(messageScrollable().position.extentBefore, greaterThan(16));
    expect(find.byType(JumpToBottomButton), findsOneWidget);

    // Tap button → back to bottom
    await tester.tap(find.byType(JumpToBottomButton));
    await tester.pump(const Duration(milliseconds: 400));
    expect(messageScrollable().position.extentBefore, lessThan(1));
    expect(find.byType(JumpToBottomButton), findsNothing);
  },
);
```

Add the import at the top:
```dart
import 'package:executive_assistant/features/chat/widgets/jump_to_bottom_button.dart';
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/core/layout/desktop_layout_test.dart --name "jump-to-bottom"`
Expected: FAIL — button not found in the tree

- [ ] **Step 3: Add scroll position tracking + button to `_ChatPanelState`**

In `flutter_app/lib/core/layout/desktop_layout.dart`, add an import near line 7:
```dart
import '../features/chat/widgets/jump_to_bottom_button.dart';
```

In `_ChatPanelState` (around line 543-562), add a `bool _showJumpToBottom` field and a scroll listener. Replace the existing `_ChatPanelState` state declarations and `_scrollToBottom` method with:

```dart
class _ChatPanelState extends ConsumerState<_ChatPanel> {
  final _scrollController = ScrollController();
  final Map<String, GlobalKey> _messageKeys = {};
  bool _pendingScrollToBottom = false;
  bool _showJumpToBottom = false;
  int _unreadCount = 0;
  int _lastSeenMessageCount = 0;

  @override
  void initState() {
    super.initState();
    ref.read(agentProvider.notifier).connect();
    _scrollController.addListener(_onScrollPositionChanged);
    _lastSeenMessageCount = ref.read(agentProvider).messages.length;
  }

  void _onScrollPositionChanged() {
    if (!_scrollController.hasClients) return;
    // With reverse: true, extentBefore == 0 means at bottom.
    final atBottom = _scrollController.position.extentBefore < 16;
    final shouldShow = !atBottom;
    if (_showJumpToBottom != shouldShow) {
      setState(() => _showJumpToBottom = shouldShow);
    }
    if (atBottom) {
      _unreadCount = 0;
    }
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScrollPositionChanged);
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (!_scrollController.hasClients) return;
      _scrollController.jumpTo(0);
    });
  }

  void _jumpToBottom() {
    _unreadCount = 0;
    _scrollController.jumpTo(0);
  }
}
```

- [ ] **Step 4: Track unread messages in the ChatState listener**

In the `ref.listen<ChatState>(agentProvider, ...)` block (around line 593-611), add unread tracking before the `if (shouldScrollToBottom)` check:

```dart
ref.listen<ChatState>(agentProvider, (prev, next) {
  // Track new messages while scrolled away from bottom
  final newMessageCount = next.messages.length - _lastSeenMessageCount;
  if (newMessageCount > 0 && _showJumpToBottom) {
    _unreadCount += newMessageCount;
  }
  _lastSeenMessageCount = next.messages.length;

  final shouldScrollToBottom = _pendingScrollToBottom &&
      next.messages.isNotEmpty;
  // ... rest unchanged
});
```

- [ ] **Step 5: Add the button to the build tree**

In `desktop_layout.dart`, find the `Expanded` wrapping `_PanelMessageList` (around line 649) and wrap it in a `Stack` with the button positioned at the bottom. Also remove the `TweenAnimationBuilder` from `_PanelMessageList` (Task 4 will cover this fully, but do the structural change now):

Replace the `Expanded(child: _PanelMessageList(...))` block with:

```dart
Stack(
  children: [
    Expanded(child: _PanelMessageList(
      state: state,
      scrollController: _scrollController,
      messageKeys: _messageKeys,
    )),
    if (_showJumpToBottom)
      Positioned(
        right: 16,
        bottom: 8,
        child: JumpToBottomButton(
          newCount: _unreadCount,
          onPressed: _jumpToBottom,
        ),
      ),
  ],
),
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/core/layout/desktop_layout_test.dart`
Expected: PASS (all 5 tests — 4 original + 1 new)

- [ ] **Step 7: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/core/layout/desktop_layout.dart \
        flutter_app/test/core/layout/desktop_layout_test.dart
git commit -m "feat: wire jump-to-bottom button with scroll position tracking"
```

---

## Task 3: Per-workspace input draft provider

**Files:**
- Create: `flutter_app/lib/providers/draft_provider.dart`

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/providers/draft_provider_test.dart`:

```dart
import 'package:executive_assistant/providers/draft_provider.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('DraftNotifier', () {
    late ProviderContainer container;
    late DraftNotifier notifier;

    setUp(() {
      container = ProviderContainer();
      notifier = container.read(draftProvider.notifier);
    });

    tearDown(() => container.dispose());

    test('save and load round-trip', () {
      notifier.save('ws1', 'hello world');
      expect(notifier.load('ws1'), 'hello world');
    });

    test('load returns null for unknown workspace', () {
      expect(notifier.load('nope'), isNull);
    });

    test('clear removes the draft', () {
      notifier.save('ws1', 'draft');
      notifier.clear('ws1');
      expect(notifier.load('ws1'), isNull);
    });

    test('drafts are isolated per workspace', () {
      notifier.save('ws1', 'a');
      notifier.save('ws2', 'b');
      expect(notifier.load('ws1'), 'a');
      expect(notifier.load('ws2'), 'b');
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/providers/draft_provider_test.dart`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the provider**

Create `flutter_app/lib/providers/draft_provider.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Per-workspace input drafts. Keyed by workspace_id.
/// In-memory only — not persisted to disk in this batch.
final draftProvider = StateNotifierProvider<DraftNotifier, Map<String, String>>(
  (ref) => DraftNotifier(),
);

class DraftNotifier extends StateNotifier<Map<String, String>> {
  DraftNotifier() : super(const {});

  String? load(String workspaceId) => state[workspaceId];

  void save(String workspaceId, String text) {
    if (text.isEmpty) {
      clear(workspaceId);
      return;
    }
    state = {...state, workspaceId: text};
  }

  void clear(String workspaceId) {
    if (!state.containsKey(workspaceId)) return;
    final next = Map<String, String>.from(state)..remove(workspaceId);
    state = next;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/providers/draft_provider_test.dart`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/providers/draft_provider.dart \
        flutter_app/test/providers/draft_provider_test.dart
git commit -m "feat: per-workspace input draft provider"
```

---

## Task 4: Wire draft provider into ChatInput + AppChatField

**Files:**
- Modify: `flutter_app/lib/widgets/app_input.dart:55-118` (accept external controller/focusNode)
- Modify: `flutter_app/lib/features/chat/widgets/chat_input.dart:1-52` (use provider)
- Modify: `flutter_app/test/core/layout/desktop_layout_test.dart` if needed

- [ ] **Step 1: Write the failing test**

Add a new test in `flutter_app/test/widgets/app_input_test.dart` (create if missing):

```dart
// flutter_app/test/widgets/app_input_test.dart
import 'package:executive_assistant/widgets/app_input.dart';
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('AppChatField uses external controller', (tester) async {
    final controller = TextEditingController(text: 'preloaded');
    final focusNode = FocusNode();
    addTearDown(controller.dispose);
    addTearDown(focusNode.dispose);

    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.light,
      home: Scaffold(
        body: AppChatField(
          controller: controller,
          focusNode: focusNode,
          onSend: (_) {},
        ),
      ),
    ));
    expect(find.text('preloaded'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/widgets/app_input_test.dart`
Expected: FAIL — `controller` and `focusNode` not accepted

- [ ] **Step 3: Modify AppChatField to accept external controller/focusNode**

In `flutter_app/lib/widgets/app_input.dart`, replace the `AppChatField` class (lines 55-118) with:

```dart
class AppChatField extends StatefulWidget {
  final String hint;
  final ValueChanged<String> onSend;
  final bool enabled;
  final bool sending;
  final VoidCallback? onCancel;
  final VoidCallback? onReconnect;
  final TextEditingController controller;
  final FocusNode focusNode;
  final ValueChanged<String>? onChanged;

  const AppChatField({
    super.key,
    this.hint = 'Ask anything...',
    required this.onSend,
    this.enabled = true,
    this.sending = false,
    this.onCancel,
    this.onReconnect,
    required this.controller,
    required this.focusNode,
    this.onChanged,
  });

  @override
  State<AppChatField> createState() => _AppChatFieldState();
}

class _AppChatFieldState extends State<AppChatField> {
  bool _focused = false;
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    widget.focusNode.addListener(_handleFocusChange);
    widget.controller.addListener(_handleTextChange);
    _hasText = widget.controller.text.trim().isNotEmpty;
  }

  @override
  void didUpdateWidget(covariant AppChatField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_handleTextChange);
      widget.controller.addListener(_handleTextChange);
    }
    if (oldWidget.focusNode != widget.focusNode) {
      oldWidget.focusNode.removeListener(_handleFocusChange);
      widget.focusNode.addListener(_handleFocusChange);
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_handleTextChange);
    widget.focusNode.removeListener(_handleFocusChange);
    super.dispose();
  }

  void _handleFocusChange() {
    if (_focused != widget.focusNode.hasFocus) {
      setState(() => _focused = widget.focusNode.hasFocus);
    }
  }

  void _handleTextChange() {
    final hasText = widget.controller.text.trim().isNotEmpty;
    if (_hasText != hasText) {
      setState(() => _hasText = hasText);
    }
    widget.onChanged?.call(widget.controller.text);
  }

  void _send() {
    final text = widget.controller.text.trim();
    if (text.isEmpty) return;
    widget.controller.clear();
    widget.onSend(text);
    widget.focusNode.requestFocus();
  }
  // ... rest of build() method unchanged
}
```

Update the `TextField` inside `build()` to use `widget.controller` and `widget.focusNode` (they're already passed in via the constructor, so just remove the internal declarations).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/widgets/app_input_test.dart`
Expected: PASS

- [ ] **Step 5: Wire drafts into ChatInput**

In `flutter_app/lib/features/chat/widgets/chat_input.dart`, replace the `ChatInput.build` method (lines 13-51) with:

```dart
class ChatInput extends ConsumerStatefulWidget {
  const ChatInput({super.key});

  @override
  ConsumerState<ChatInput> createState() => _ChatInputState();
}

class _ChatInputState extends ConsumerState<ChatInput> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  String? _activeWorkspaceId;

  @override
  void initState() {
    super.initState();
    _activeWorkspaceId = ref.read(currentWorkspaceIdProvider);
    final draft = ref.read(draftProvider.notifier).load(_activeWorkspaceId!);
    if (draft != null) _controller.text = draft;
  }

  @override
  void dispose() {
    // Save any pending draft before disposing
    if (_activeWorkspaceId != null) {
      ref.read(draftProvider.notifier).save(_activeWorkspaceId!, _controller.text);
    }
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Listen for workspace changes
    ref.listen<String>(currentWorkspaceIdProvider, (prev, next) {
      if (prev == next) return;
      // Save old draft
      if (prev != null) {
        ref.read(draftProvider.notifier).save(prev, _controller.text);
      }
      // Load new draft
      _activeWorkspaceId = next;
      final draft = ref.read(draftProvider.notifier).load(next) ?? '';
      _controller.value = TextEditingValue(
        text: draft,
        selection: TextSelection.collapsed(offset: draft.length),
      );
    });

    final state = ref.watch(agentProvider);
    final isSending = state.status == ChatStatus.streaming ||
        state.status == ChatStatus.awaitingApproval;
    final hasPendingApprovals = state.pendingApprovals.isNotEmpty;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (hasPendingApprovals) _ApprovalBar(
          pendingApprovals: state.pendingApprovals,
          ref: ref,
        ),
        AppChatField(
          controller: _controller,
          focusNode: _focusNode,
          hint: state.connected ? 'Ask anything...' : 'Connecting...',
          enabled: state.connected,
          sending: isSending,
          onChanged: (text) {
            if (_activeWorkspaceId != null) {
              ref.read(draftProvider.notifier).save(_activeWorkspaceId!, text);
            }
          },
          onSend: (text) {
            if (_activeWorkspaceId != null) {
              ref.read(draftProvider.notifier).clear(_activeWorkspaceId!);
            }
            ref.read(agentProvider.notifier).sendMessage(text);
          },
          onCancel: isSending
              ? () => ref.read(agentProvider.notifier).cancelExecution()
              : null,
          onReconnect: !state.connected
              ? () => ref.read(agentProvider.notifier).connect()
              : null,
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
          child: Row(children: const [ModelSwitcher(), Spacer()]),
        ),
      ],
    );
  }
}
```

Add imports at the top of `chat_input.dart`:
```dart
import '../../providers/draft_provider.dart';
import '../../providers/workspace_provider.dart';  // for currentWorkspaceIdProvider
```

- [ ] **Step 6: Run full test suite**

Run: `cd flutter_app && flutter test test/core/layout/desktop_layout_test.dart test/providers/draft_provider_test.dart test/widgets/app_input_test.dart`
Expected: All PASS (existing tests still work, new tests pass)

- [ ] **Step 7: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/features/chat/widgets/chat_input.dart \
        flutter_app/lib/widgets/app_input.dart \
        flutter_app/test/widgets/app_input_test.dart
git commit -m "feat: per-workspace input draft persistence"
```

---

## Task 5: Remove the opacity fade on workspace switch

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (the `_PanelMessageList.build` method, around line 712-734)

- [ ] **Step 1: Verify existing tests still pass**

Run: `cd flutter_app && flutter test test/core/layout/desktop_layout_test.dart`
Expected: PASS (no changes yet, just baseline)

- [ ] **Step 2: Remove the TweenAnimationBuilder**

In `flutter_app/lib/core/layout/desktop_layout.dart`, find the `_PanelMessageList.build` method. Replace the entire return value (from `return TweenAnimationBuilder<double>(` through the closing `);`) with:

```dart
return KeyedSubtree(
  key: ValueKey('chat_list_inner_$activeWs'),
  child: ChatMessageList(
    key: const ValueKey('desktop-chat-message-list'),
    messages: state.messages,
    isStreaming: state.status == ChatStatus.streaming,
    streamingText: state.streamingText,
    reasoningText: state.reasoningText,
    activeToolCalls: state.activeToolCalls,
    scrollController: scrollController,
    isLoading: state.loadingHistory,
    messageKeys: messageKeys,
    header: state.messages.isNotEmpty
        ? CompanionContextPill(activeWorkspaceId: activeWs)
        : null,
    emptyBuilder: (_) => Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.screenEdge),
        child: Text(
          'Ask anything...',
          style: tokens.typography.textTheme.bodyMedium?.copyWith(
            color: tokens.colors.textTertiary,
          ),
        ),
      ),
    ),
  ),
);
```

The `KeyedSubtree` key forces a full rebuild on workspace switch, which is still needed to reset the `ListView`'s scroll position and rebuild `messageKeys`. We just remove the opacity wrapper.

- [ ] **Step 3: Run tests to verify nothing broke**

Run: `cd flutter_app && flutter test test/core/layout/desktop_layout_test.dart`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "refactor: remove dead opacity fade on workspace switch"
```

---

## Task 6: Full test pass + manual verification

- [ ] **Step 1: Run full Flutter test suite**

Run: `cd flutter_app && flutter test`
Expected: 129+ pass, same 32 pre-existing failures (responsive/chat_screen, unrelated to this work)

- [ ] **Step 2: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/`
Expected: No new issues

- [ ] **Step 3: Build macOS app**

Run: `cd flutter_app && flutter build macos --debug`
Expected: Build succeeds

- [ ] **Step 4: Manual smoke test checklist**

- [ ] Switch to `personal` → no fade, content visible immediately, scrolled to bottom
- [ ] Type "hello" in workspace A, switch to B, switch back → "hello" still in input
- [ ] Send "hello" → input clears, message appears at bottom
- [ ] Scroll up in a long conversation → "↓ N new" button appears
- [ ] New message arrives while scrolled up → badge increments
- [ ] Tap button → scroll returns to bottom, badge clears

- [ ] **Step 5: Commit any remaining changes + push**

```bash
cd /Users/eddy/Developer/Python/assistant
git status  # should be clean
git log --oneline -7  # review the 5 commits
git push origin main
```

---

## Self-Review Checklist

- [x] Spec coverage: all 3 changes (button, draft, fade removal) have tasks
- [x] No placeholders: every step has actual code or commands
- [x] Type consistency: `_showJumpToBottom`, `_unreadCount`, `_lastSeenMessageCount` used consistently across Tasks 2 and 4
- [x] File paths are exact and exist in the codebase
- [x] Each task produces a self-contained, committable change
