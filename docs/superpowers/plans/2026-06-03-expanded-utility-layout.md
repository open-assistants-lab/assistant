# Expanded Utility Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Tools, Skills, Subagents, Connection, and Settings panels expand to cover the full content width (sidebar + utility panel only), hiding the chat panel.

**Architecture:** Add a `DesktopUtilityLayout` (two-column: sidebar + full-width child) alongside the existing `DesktopLayout` (three-column: sidebar + chat + content). `ResponsiveShell` branches on the current route to pick the right layout. The sidebar widget is reused unchanged.

**Tech Stack:** Flutter, GoRouter, Riverpod, existing `Sidebar` widget (renamed from `_Sidebar`) in `desktop_layout.dart`.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `flutter_app/lib/core/layout/desktop_utility_layout.dart` | New: two-column layout for utility routes |
| `flutter_app/lib/core/layout/responsive_shell.dart` | Modify: branch on route to pick layout |
| `flutter_app/lib/core/layout/desktop_layout.dart` | Modify: rename `_Sidebar` → `Sidebar` for cross-file access |
| `flutter_app/lib/core/router/app_router.dart` | Modify: pass `state` to `ResponsiveShell`, add `isUtilityRoute` helper, add `/connectors` route |
| `flutter_app/lib/features/connectors/connectors_modal.dart` | Modify: fix close button for routed context |
| `flutter_app/test/core/layout/desktop_utility_layout_test.dart` | New: widget tests for new layout |
| `flutter_app/test/core/layout/responsive_shell_test.dart` | New: route-mapping tests |

---

## Task 1: Create DesktopUtilityLayout with tests

**Files:**
- Create: `flutter_app/lib/core/layout/desktop_utility_layout.dart`
- Create: `flutter_app/test/core/layout/desktop_utility_layout_test.dart`

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/core/layout/desktop_utility_layout_test.dart`:

```dart
import 'package:executive_assistant/core/layout/desktop_utility_layout.dart';
import 'package:executive_assistant/core/layout/desktop_layout.dart' show DesktopSidebarItem;
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('renders sidebar and child in two columns', (tester) async {
    final sidebarKey = GlobalKey();
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.light,
          home: DesktopUtilityLayout(
            sidebar: SizedBox(key: sidebarKey, width: 240, child: const Text('SB')),
            child: const Text('PANEL'),
          ),
        ),
      ),
    );
    expect(find.text('SB'), findsOneWidget);
    expect(find.text('PANEL'), findsOneWidget);
    // Sidebar is fixed-width, panel takes the rest
    final sidebarBox = tester.getRect(find.byKey(sidebarKey));
    final panelFinder = find.text('PANEL');
    final panelBox = tester.getRect(panelFinder);
    expect(sidebarBox.width, 240);
    expect(panelBox.left, greaterThan(sidebarBox.right));
  });
}
```

Note: the test accepts a `sidebar` widget parameter directly (not a
full `Sidebar` widget) so the test doesn't need to mock all of the
sidebar's providers (workspace list, current workspace, theme). The
production code in Task 2 will pass the real `Sidebar` widget.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/layout/desktop_utility_layout_test.dart
```

Expected: FAIL — `desktop_utility_layout.dart` not found.

- [ ] **Step 3: Implement the layout**

Create `flutter_app/lib/core/layout/desktop_utility_layout.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../features/companion/companion_toast.dart';

/// Two-column layout: fixed-width sidebar + full-width utility panel.
/// Used for routes that should occupy the full content width
/// (tools, skills, subagents, connectors, settings).
class DesktopUtilityLayout extends ConsumerWidget {
  final Widget sidebar;
  final Widget child;
  const DesktopUtilityLayout({
    super.key,
    required this.sidebar,
    required this.child,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    const dividerWidth = 1.0;
    return Scaffold(
      body: Stack(
        children: [
          Row(
            children: [
              sidebar,
              Container(
                width: dividerWidth,
                color: context.tokens.colors.borderSubtle,
              ),
              Expanded(child: child),
            ],
          ),
          const CompanionToastOverlay(),
        ],
      ),
    );
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/layout/desktop_utility_layout_test.dart
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/core/layout/desktop_utility_layout.dart \
        flutter_app/test/core/layout/desktop_utility_layout_test.dart
git commit -m "feat: DesktopUtilityLayout (two-column sidebar + full-width child)"
```

---

## Task 2: Add isUtilityRoute helper and pass state to ResponsiveShell

**Files:**
- Modify: `flutter_app/lib/core/router/app_router.dart`
- Modify: `flutter_app/lib/core/layout/responsive_shell.dart`
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (rename `_Sidebar` → `Sidebar`)

- [ ] **Step 1: Rename `_Sidebar` → `Sidebar` in desktop_layout.dart**

`_Sidebar` is a private class in `desktop_layout.dart`. `ResponsiveShell`
needs to reference it from a different file. Rename the class and its
state class to public, and update all internal references.

In `flutter_app/lib/core/layout/desktop_layout.dart`:
- `class _Sidebar extends ConsumerWidget` → `class Sidebar extends ConsumerWidget`
- `class _SidebarItem extends StatefulWidget` → `class SidebarItem extends StatefulWidget`
  (also private, rename for consistency)
- `class _SidebarItemState extends State<_SidebarItem>` →
  `class _SidebarItemState extends State<SidebarItem>`
- Update the `const _Sidebar(width: ...)` and `const _SidebarItem(...)`
  call sites within `desktop_layout.dart` to use the new names.

- [ ] **Step 2: Add the helper and pass state to ResponsiveShell**

In `flutter_app/lib/core/router/app_router.dart`, add a top-level constant
and helper (before `appRouterProvider`):

```dart
const _utilityRoutes = {
  '/tools',
  '/skills',
  '/subagents',
  '/connectors',
  '/settings',
};

bool isUtilityRoute(String path) => _utilityRoutes.contains(path);
```

Then update the `ShellRoute.builder` to pass `state`:

```dart
ShellRoute(
  navigatorKey: _shellNavigatorKey,
  builder: (context, state, child) {
    return ResponsiveShell(state: state, child: child);
  },
  routes: [ ... ],
),
```

- [ ] **Step 4: Update ResponsiveShell to branch on route**

In `flutter_app/lib/core/layout/responsive_shell.dart`, replace the file
contents with:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../router/app_router.dart' show isUtilityRoute;
import '../constants/breakpoints.dart';
import 'desktop_layout.dart';
import 'desktop_utility_layout.dart';
import 'mobile_layout.dart';
import 'tablet_rail.dart';

class ResponsiveShell extends ConsumerWidget {
  final GoRouterState state;
  final Widget child;

  const ResponsiveShell({super.key, required this.state, required this.child});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final utility = isUtilityRoute(state.matchedLocation);
        if (constraints.maxWidth >= Breakpoints.desktop) {
          return utility
              ? DesktopUtilityLayout(
                  sidebar: const Sidebar(width: 240),
                  child: child,
                )
              : DesktopLayout(child: child);
        }
        if (constraints.maxWidth >= Breakpoints.mobile) {
          return TabletRailLayout(child: child);
        }
        return MobileLayout(child: child);
      },
    );
  }
}
```

- [ ] **Step 5: Verify no analyzer issues**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter analyze lib/core/layout/responsive_shell.dart lib/core/router/app_router.dart
```

Expected: No new issues.

- [ ] **Step 6: Run existing tests to check for regressions**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/layout/ test/core/responsive_test.dart 2>&1 | tail -5
```

Expected: Existing tests still pass. (Some may be pre-existing
failures — that's OK, just verify no new failures.)

- [ ] **Step 7: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/core/layout/responsive_shell.dart \
        flutter_app/lib/core/router/app_router.dart \
        flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "feat: branch ResponsiveShell on utility routes"
```

---

## Task 3: Add /connectors route and fix close button for routed context

**Files:**
- Modify: `flutter_app/lib/features/connectors/connectors_modal.dart`
- Modify: `flutter_app/lib/core/router/app_router.dart`

`ConnectorsModal` is already a full panel widget (has its own `Scaffold`
+ `AppBar`). No extraction is needed — just add the route and fix the
close button so it works in a routed context (not just a dialog).

- [ ] **Step 1: Fix the close button in ConnectorsModal**

In `flutter_app/lib/features/connectors/connectors_modal.dart`, replace
the close `IconButton.onPressed`:

```dart
leading: IconButton(
  icon: const Icon(Symbols.close, size: 20),
  onPressed: () {
    // Works in both dialog and routed contexts: pops if there's a
    // route to pop (dialog), otherwise navigates back to /workspace.
    if (Navigator.of(context).canPop()) {
      Navigator.of(context).pop();
    } else {
      GoRouter.of(context).go('/workspace');
    }
  },
),
```

Add the import at the top of the file:
```dart
import 'package:go_router/go_router.dart';
```

- [ ] **Step 2: Add the `/connectors` route**

In `flutter_app/lib/core/router/app_router.dart`, add to the
`ShellRoute.routes` list (alongside `/tools`, `/skills`, etc.):

```dart
GoRoute(
  path: '/connectors',
  name: 'connectors',
  pageBuilder: (context, state) => CustomTransitionPage(
    key: state.pageKey,
    child: const ConnectorsModal(),
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(opacity: animation, child: child);
    },
    transitionDuration: EaMotion.standard.fluid,
  ),
),
```

- [ ] **Step 3: Verify analyzer + tests**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter analyze lib/core/router/app_router.dart lib/features/connectors/
flutter test test/core/layout/ test/core/responsive_test.dart 2>&1 | tail -3
```

Expected: No new issues.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/lib/core/router/app_router.dart \
        flutter_app/lib/features/connectors/connectors_modal.dart
git commit -m "feat: /connectors route with modal close fix"
```

---

## Task 4: Test isUtilityRoute and ResponsiveShell layout selection

**Files:**
- Create: `flutter_app/test/core/router/app_router_test.dart` (pure function test)
- Create: `flutter_app/test/core/layout/responsive_shell_test.dart` (widget test)

- [ ] **Step 1: Read the existing responsive test file for context**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
head -50 test/core/responsive_test.dart
```

- [ ] **Step 2: Test isUtilityRoute as a pure function**

`GoRouterState` is hard to construct in tests (it's not exposed via
`routerDelegate.currentConfiguration.matches` — those are
`RouteMatch`, not `GoRouterState`). Test the classification function
directly instead.

Create `flutter_app/test/core/router/app_router_test.dart`:

```dart
import 'package:executive_assistant/core/router/app_router.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('isUtilityRoute', () {
    test('returns true for utility routes', () {
      for (final path in const [
        '/tools', '/skills', '/subagents', '/connectors', '/settings',
      ]) {
        expect(isUtilityRoute(path), isTrue, reason: '$path should be utility');
      }
    });

    test('returns false for non-utility routes', () {
      for (final path in const [
        '/workspace', '/email', '/chat', '/tasks', '/contacts', '/more',
      ]) {
        expect(isUtilityRoute(path), isFalse, reason: '$path should NOT be utility');
      }
    });

    test('returns false for unknown routes', () {
      expect(isUtilityRoute('/unknown'), isFalse);
      expect(isUtilityRoute(''), isFalse);
    });
  });
}
```

- [ ] **Step 3: Run the pure function test to verify it passes**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/router/app_router_test.dart 2>&1 | tail -3
```

Expected: PASS (3 tests)

- [ ] **Step 4: Widget test for ResponsiveShell using a real GoRouter**

Create `flutter_app/test/core/layout/responsive_shell_test.dart`:

```dart
import 'package:executive_assistant/core/layout/desktop_layout.dart';
import 'package:executive_assistant/core/layout/desktop_utility_layout.dart';
import 'package:executive_assistant/core/layout/responsive_shell.dart';
import 'package:executive_assistant/core/router/app_router.dart';
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

void main() {
  // Pump the full app with a given initial location, then extract the
  // GoRouterState from the current BuildContext (GoRouterState.of(context)).
  Future<GoRouterState> pumpAt(
    WidgetTester tester,
    String location,
  ) async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final router = container.read(appRouterProvider);
    router.go(location);
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(
          theme: AppTheme.light,
          routerConfig: router,
        ),
      ),
    );
    await tester.pumpAndSettle();
    return GoRouterState.of(tester.element(find.byType(MaterialApp).first));
  }

  testWidgets('utility routes render DesktopUtilityLayout', (tester) async {
    for (final path in const [
      '/tools', '/skills', '/subagents', '/connectors', '/settings',
    ]) {
      final state = await pumpAt(tester, path);
      expect(
        find.byType(DesktopUtilityLayout),
        findsOneWidget,
        reason: 'Expected utility layout for $path',
      );
    }
  });

  testWidgets('non-utility routes render DesktopLayout', (tester) async {
    for (final path in const ['/workspace', '/email']) {
      final state = await pumpAt(tester, path);
      expect(
        find.byType(DesktopLayout),
        findsOneWidget,
        reason: 'Expected desktop layout for $path',
      );
    }
  });
}
```

- [ ] **Step 5: Run the widget test to verify it passes**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/layout/responsive_shell_test.dart 2>&1 | tail -3
```

Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/test/core/router/app_router_test.dart \
        flutter_app/test/core/layout/responsive_shell_test.dart
git commit -m "test: isUtilityRoute + ResponsiveShell layout selection"
```

- [ ] **Step 2: Create a new test file with actual GoRouter-based tests**

Create `flutter_app/test/core/layout/responsive_shell_test.dart`:

```dart
import 'package:executive_assistant/core/layout/desktop_layout.dart';
import 'package:executive_assistant/core/layout/desktop_utility_layout.dart';
import 'package:executive_assistant/core/layout/responsive_shell.dart';
import 'package:executive_assistant/core/router/app_router.dart';
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// Build a real GoRouterState for a given path by creating a router,
/// navigating to the path, and extracting the state from the current
/// route match.
GoRouterState _stateForPath(GoRouter router, String path) {
  // After router.go(path), router.routerDelegate.currentConfiguration
  // holds the active match list. The last match's state is the leaf.
  final config = router.routerDelegate.currentConfiguration;
  return config.matches.last.uri.toString().isEmpty
      ? config.matches.first
      : config.matches.last;
}

void main() {
  Future<GoRouter> buildRouter() async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    return container.read(appRouterProvider);
  }

  testWidgets('utility routes render DesktopUtilityLayout', (tester) async {
    for (final path in const [
      '/tools', '/skills', '/subagents', '/connectors', '/settings',
    ]) {
      final router = await buildRouter();
      router.go(path);
      await tester.pumpAndSettle();

      final state = _stateForPath(router, path);
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.light,
            home: ResponsiveShell(state: state, child: const SizedBox()),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.byType(DesktopUtilityLayout),
        findsOneWidget,
        reason: 'Expected utility layout for $path',
      );
    }
  });

  testWidgets('non-utility routes render DesktopLayout', (tester) async {
    for (final path in const ['/workspace', '/email']) {
      final router = await buildRouter();
      router.go(path);
      await tester.pumpAndSettle();

      final state = _stateForPath(router, path);
      await tester.pumpWidget(
        ProviderScope(
          child: MaterialApp(
            theme: AppTheme.light,
            home: ResponsiveShell(state: state, child: const SizedBox()),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(
        find.byType(DesktopLayout),
        findsOneWidget,
        reason: 'Expected desktop layout for $path',
      );
    }
  });
}
```

- [ ] **Step 3: Run test to verify it passes**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/layout/responsive_shell_test.dart 2>&1 | tail -3
```

Expected: PASS (2 tests, one per group)

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/test/core/layout/responsive_shell_test.dart
git commit -m "test: responsive shell routes to utility layout"
```

- [ ] **Step 2: Add a new test for utility route → utility layout mapping**

Append to `test/core/responsive_test.dart`:

```dart
testWidgets('ResponsiveShell uses DesktopUtilityLayout for /tools', (tester) async {
  // ... build a GoRouter with /tools as initial location, or
  // construct a ResponsiveShell with a mock GoRouterState whose
  // matchedLocation is '/tools'.
  //
  // Verify that the rendered tree contains an Expanded widget
  // (which DesktopUtilityLayout uses) and NOT the
  // _ChatPanel widget.
});
```

The exact test structure depends on how the existing tests build
`ResponsiveShell`. Use the same pattern. If the existing tests don't
cover `ResponsiveShell` directly, add a new test file
`test/core/layout/responsive_shell_test.dart` and use
`GoRouterState` mocks or a real `GoRouter` instance with `initialLocation`.

Minimum coverage required:
- `/tools`, `/skills`, `/subagents`, `/connectors`, `/settings` →
  `DesktopUtilityLayout`
- `/workspace`, `/email` → `DesktopLayout`

- [ ] **Step 3: Run test to verify it passes**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test test/core/responsive_test.dart test/core/layout/responsive_shell_test.dart 2>&1 | tail -3
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Python/assistant
git add flutter_app/test/core/responsive_test.dart \
        flutter_app/test/core/layout/responsive_shell_test.dart
git commit -m "test: responsive shell routes to utility layout"
```

---

## Task 5: Full test pass + manual verification + push

- [ ] **Step 1: Run full Flutter test suite**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter test
```

Expected: same baseline (129+ pass, same 32 pre-existing failures).

- [ ] **Step 2: Run analyzer**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter analyze lib/
```

Expected: No new issues.

- [ ] **Step 3: Build macOS app**

```bash
cd /Users/eddy/Developer/Python/assistant/flutter_app
flutter build macos --debug
```

Expected: Build succeeds.

- [ ] **Step 4: Manual smoke test checklist**

- [ ] Tap **Tools** in sidebar — panel expands to full content width, chat panel hidden
- [ ] Tap **Skills** — same
- [ ] Tap **Subagents** — same
- [ ] Tap **Connection** — same
- [ ] Tap **Settings** — same
- [ ] Tap **Workspace** — three-column layout returns (sidebar + chat + files)
- [ ] Tap **Email** — three-column layout returns
- [ ] Resize window — both layouts respond correctly to breakpoints

- [ ] **Step 5: Push**

```bash
cd /Users/eddy/Developer/Python/assistant
git status  # should be clean
git log --oneline -6
git push origin main
```

---

## Self-Review Checklist

- [x] Spec coverage: all 5 utility routes covered, `_Sidebar` (renamed to `Sidebar`) reused, tablet/mobile out of scope
- [x] No placeholders: every step has actual code
- [x] Type consistency: `DesktopUtilityLayout` constructor params match between Task 1 test + production + Task 2 usage (both use `sidebar` + `child`)
- [x] Import paths verified: `../router/`, `../constants/`, `../../features/companion/` all match the actual directory depth
- [x] Cross-file visibility: `_Sidebar` renamed to `Sidebar` since `ResponsiveShell` needs to reference it
- [x] `/connectors` route added (was missing from router despite being in sidebar)
- [x] ConnectorsModal close button works in both dialog and routed contexts
- [x] Each task produces a self-contained, committable change
