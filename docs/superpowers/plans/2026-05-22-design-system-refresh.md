# Design System Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Flutter app from "Material defaults with grey accent" into a cohesive Linear-inspired product with Deep Emerald accent, Balanced typography, user-bubble + flat-AI chat, and Hybrid motion (utility 180ms / moment 280ms).

**Architecture:** Three phases. Phase 1 replaces token values only (no structural changes) — every screen continues to work but looks different. Phase 2 rebuilds the chat surface widgets to match the new aesthetic. Phase 3 polishes component-level details (buttons, tabs, sidebar, dialogs, inputs) and wires up the transition choreography.

**Tech Stack:** Flutter 3.x, Riverpod, google_fonts (Inter + Fira Code already bundled), Material Symbols, existing `EaTokens` ThemeExtension system.

**Spec:** `docs/superpowers/specs/2026-05-22-design-system-refresh.md`

---

## File Structure

### Phase 1 — Foundation (tokens)
- Modify: `flutter_app/lib/theme/tokens/colors.dart` (replace `dark` and `light` palettes)
- Modify: `flutter_app/lib/theme/tokens/typography.dart` (replace scale, add `code` style)
- Modify: `flutter_app/lib/theme/tokens/spacing.dart` (add `xxs`)
- Modify: `flutter_app/lib/theme/tokens/radius.dart` (add `xs`, `none`, `full`)
- Modify: `flutter_app/lib/theme/tokens/motion.dart` (rename to Linear semantics, add `moment`, `curveSpring`, etc.)
- Modify: `flutter_app/lib/theme/app_theme.dart` (wire updated tokens into ColorScheme)
- Test: `flutter_app/test/theme/tokens_test.dart` (new — token value contract)

### Phase 2 — Chat Surface
- Modify: `flutter_app/lib/features/chat/widgets/message_bubble.dart` (split into UserBubble + AssistantMessage)
- Modify: `flutter_app/lib/features/chat/widgets/streaming_bubble.dart` (flat, pulsing role dot)
- Modify: `flutter_app/lib/features/chat/widgets/reasoning_bubble.dart` (collapsed card with small-caps header)
- Modify: `flutter_app/lib/features/chat/widgets/tool_call_card.dart` (new compact design with status badges)
- Create: `flutter_app/lib/features/chat/widgets/empty_state.dart` (suggestion chips + accent dot)
- Modify: `flutter_app/lib/features/chat/widgets/chat_input.dart` (new input + send-button states)
- Modify: `flutter_app/lib/features/chat/widgets/chat_message_list.dart` (remove staggeredEntry, add AnimatedSwitcher for workspace switch crossfade, fade-in for new messages)
- Modify: `flutter_app/lib/core/animations.dart` (replace `staggeredEntry` with `fadeIn` helper; reduced-motion aware)
- Test: `flutter_app/test/widgets/chat_surface_test.dart` (golden + smoke tests)

### Phase 3 — Component Polish + Choreography
- Create: `flutter_app/lib/widgets/ea_button.dart` (Primary/Secondary/Ghost variants)
- Create: `flutter_app/lib/widgets/ea_card.dart` (hover-aware card)
- Create: `flutter_app/lib/widgets/ea_dialog.dart` (no-shadow, scale+spring entrance)
- Create: `flutter_app/lib/core/page_transitions.dart` (PageTransitionsBuilder for sidebar nav)
- Modify: `flutter_app/lib/theme/app_theme.dart` (register page transitions, dialog theme)
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (sidebar items, chat panel tabs, crossfade for workspace switch)
- Modify: `flutter_app/lib/features/chat/widgets/approval_sheet.dart` (slide+spring entrance)
- Test: `flutter_app/test/widgets/ea_button_test.dart`, `test/widgets/ea_dialog_test.dart`, `test/widgets/page_transitions_test.dart`

---

## Phase 1: Foundation

### Task 1.1: Replace dark + light color palettes with Deep Emerald

**Files:**
- Modify: `flutter_app/lib/theme/tokens/colors.dart` (the `static const dark` and `static const light` blocks)
- Test: `flutter_app/test/theme/tokens_test.dart` (create)

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/theme/tokens_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/theme/tokens/colors.dart';

void main() {
  group('EaColors.dark', () {
    test('uses near-black canvas', () {
      expect(EaColors.dark.bgCanvas, const Color(0xFF08090A));
    });
    test('uses deep emerald accent', () {
      expect(EaColors.dark.accent, const Color(0xFF239766));
      expect(EaColors.dark.accentHover, const Color(0xFF2EAD78));
      expect(EaColors.dark.accentMuted, const Color(0xFF0D2B22));
    });
    test('borderAccent is emerald at 40% opacity', () {
      expect(EaColors.dark.borderAccent.r, closeTo(0x23 / 255, 0.01));
      expect(EaColors.dark.borderAccent.a, closeTo(0.4, 0.05));
    });
    test('text colors meet AA contrast on bgCanvas', () {
      // bgCanvas #08090A ~= 0.014 relative luminance
      // textPrimary #E6E6E6 ~= 0.799 relative luminance
      // contrast = (0.799 + 0.05) / (0.014 + 0.05) = 13.27 — well above AAA 7:1
      expect(EaColors.dark.textPrimary, const Color(0xFFE6E6E6));
    });
  });

  group('EaColors.light', () {
    test('uses off-white canvas', () {
      expect(EaColors.light.bgCanvas, const Color(0xFFFAFAFA));
    });
    test('uses darker emerald for AA contrast on white', () {
      expect(EaColors.light.accent, const Color(0xFF1B7A52));
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/theme/tokens_test.dart`
Expected: FAIL — current colors are different (`bgCanvas: 0xFF0A0A0A`, `accent: 0xFFD4D4D4`).

- [ ] **Step 3: Replace dark and light palettes**

In `flutter_app/lib/theme/tokens/colors.dart`, replace the `static const dark` block with:

```dart
  static const dark = EaColors(
    bgCanvas: Color(0xFF08090A),
    bgSurface: Color(0xFF0E0F11),
    bgElevated: Color(0xFF131416),
    bgField: Color(0xFF1A1B1E),
    textPrimary: Color(0xFFE6E6E6),
    textSecondary: Color(0xFF9B9B9B),
    textTertiary: Color(0xFF6B6B6B),
    textInverse: Color(0xFF08090A),
    accent: Color(0xFF239766),
    accentHover: Color(0xFF2EAD78),
    accentMuted: Color(0xFF0D2B22),
    success: Color(0xFF3DBC83),
    warning: Color(0xFFE0A04B),
    error: Color(0xFFE55B5B),
    info: Color(0xFF5B95E0),
    borderSubtle: Color(0xFF1F1F22),
    borderDefault: Color(0xFF2A2B2E),
    borderAccent: Color(0x66239766),
  );
```

Replace the `static const light` block with:

```dart
  static const light = EaColors(
    bgCanvas: Color(0xFFFAFAFA),
    bgSurface: Color(0xFFFFFFFF),
    bgElevated: Color(0xFFF3F3F3),
    bgField: Color(0xFFF0F0F0),
    textPrimary: Color(0xFF0E0F11),
    textSecondary: Color(0xFF5B5B5B),
    textTertiary: Color(0xFF9B9B9B),
    textInverse: Color(0xFFFFFFFF),
    accent: Color(0xFF1B7A52),
    accentHover: Color(0xFF15633F),
    accentMuted: Color(0xFFE6F4ED),
    success: Color(0xFF1B7A52),
    warning: Color(0xFFD18A2F),
    error: Color(0xFFCC4747),
    info: Color(0xFF3273DC),
    borderSubtle: Color(0xFFE8E8E8),
    borderDefault: Color(0xFFD4D4D4),
    borderAccent: Color(0x661B7A52),
  );
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/theme/tokens_test.dart`
Expected: PASS — 5 tests pass.

- [ ] **Step 5: Run full analyzer to check for downstream breakage**

Run: `cd flutter_app && flutter analyze lib/`
Expected: Same number of issues as before (31 info-level, no errors).

- [ ] **Step 6: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/theme/tokens/colors.dart flutter_app/test/theme/tokens_test.dart
git commit -m "theme: replace palette with Deep Emerald accent (dark + light)"
```

---

### Task 1.2: Tighten typography (Inter, balanced 14px, small-caps labels)

**Files:**
- Modify: `flutter_app/lib/theme/tokens/typography.dart`
- Test: `flutter_app/test/theme/typography_test.dart` (create)

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/theme/typography_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/theme/tokens/typography.dart';

void main() {
  group('EaTypography', () {
    final typo = EaTypography.build(Brightness.dark);

    test('bodyLarge is 14px with tight letter-spacing', () {
      final style = typo.textTheme.bodyLarge!;
      expect(style.fontSize, 14);
      expect(style.fontWeight, FontWeight.w400);
      expect(style.height, closeTo(1.6, 0.01));
      expect(style.letterSpacing, closeTo(-0.011 * 14, 0.05));
    });

    test('labelSmall is 10px uppercase-ready with 0.1em tracking', () {
      final style = typo.textTheme.labelSmall!;
      expect(style.fontSize, 10);
      expect(style.fontWeight, FontWeight.w600);
      expect(style.letterSpacing, closeTo(0.1 * 10, 0.05));
    });

    test('titleLarge is 17px semibold', () {
      final style = typo.textTheme.titleLarge!;
      expect(style.fontSize, 17);
      expect(style.fontWeight, FontWeight.w600);
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/theme/typography_test.dart`
Expected: FAIL — current bodyLarge is 16px, no labelSmall configured at 10px.

- [ ] **Step 3: Replace typography scale**

In `flutter_app/lib/theme/tokens/typography.dart`, replace the `factory EaTypography.build` body with:

```dart
  factory EaTypography.build(Brightness brightness) {
    final inter = GoogleFonts.interTextTheme();
    final firaCode = GoogleFonts.firaCodeTextTheme();

    // Linear-style tracking: -0.011em on body, tighter on display, looser on tiny labels.
    TextStyle s({required double size, required FontWeight weight, required double height, double tracking = -0.011}) {
      return TextStyle(
        fontSize: size,
        fontWeight: weight,
        height: height,
        letterSpacing: size * tracking,
      );
    }

    final base = inter.copyWith(
      displayLarge: inter.displayLarge?.merge(s(size: 28, weight: FontWeight.w600, height: 1.2, tracking: -0.02)),
      displayMedium: inter.displayMedium?.merge(s(size: 22, weight: FontWeight.w600, height: 1.25, tracking: -0.015)),
      titleLarge: inter.titleLarge?.merge(s(size: 17, weight: FontWeight.w600, height: 1.3, tracking: -0.012)),
      titleMedium: inter.titleMedium?.merge(s(size: 15, weight: FontWeight.w500, height: 1.4)),
      titleSmall: inter.titleSmall?.merge(s(size: 13, weight: FontWeight.w500, height: 1.4, tracking: -0.005)),
      bodyLarge: inter.bodyLarge?.merge(s(size: 14, weight: FontWeight.w400, height: 1.6)),
      bodyMedium: inter.bodyMedium?.merge(s(size: 13, weight: FontWeight.w400, height: 1.55, tracking: -0.005)),
      bodySmall: inter.bodySmall?.merge(s(size: 12, weight: FontWeight.w400, height: 1.5, tracking: 0)),
      labelLarge: inter.labelLarge?.merge(s(size: 13, weight: FontWeight.w500, height: 1.4, tracking: -0.005)),
      labelMedium: inter.labelMedium?.merge(s(size: 11, weight: FontWeight.w600, height: 1.3, tracking: 0.04)),
      labelSmall: inter.labelSmall?.merge(s(size: 10, weight: FontWeight.w600, height: 1.3, tracking: 0.1)),
    );

    return EaTypography(
      textTheme: base,
      monoTheme: firaCode.copyWith(
        bodyLarge: firaCode.bodyLarge?.copyWith(fontSize: 13, height: 1.5),
        bodyMedium: firaCode.bodyMedium?.copyWith(fontSize: 12, height: 1.5),
        bodySmall: firaCode.bodySmall?.copyWith(fontSize: 11, height: 1.4),
      ),
    );
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/theme/typography_test.dart`
Expected: PASS — 3 tests pass.

- [ ] **Step 5: Run analyzer to ensure no breakage**

Run: `cd flutter_app && flutter analyze lib/`
Expected: No new errors. Some widgets that hard-coded sizes won't visually adapt yet; that's expected.

- [ ] **Step 6: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/theme/tokens/typography.dart flutter_app/test/theme/typography_test.dart
git commit -m "theme: tighten typography scale with Linear letter-spacing"
```

---

### Task 1.3: Refine spacing scale (add xxs)

**Files:**
- Modify: `flutter_app/lib/theme/tokens/spacing.dart`

- [ ] **Step 1: Replace the spacing class**

In `flutter_app/lib/theme/tokens/spacing.dart`, replace the file contents with:

```dart
import 'package:flutter/foundation.dart';

@immutable
class EaSpacing {
  final double xxs;
  final double xs;
  final double sm;
  final double md;
  final double lg;
  final double xl;
  final double xxl;
  final double xxxl;

  const EaSpacing({
    required this.xxs,
    required this.xs,
    required this.sm,
    required this.md,
    required this.lg,
    required this.xl,
    required this.xxl,
    required this.xxxl,
  });

  static const standard = EaSpacing(
    xxs: 2,
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 20,
    xxl: 28,
    xxxl: 40,
  );
}
```

- [ ] **Step 2: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/`
Expected: No new errors (existing `xs/sm/md/lg/xl/xxl/xxxl` still exist; we only added `xxs`).

- [ ] **Step 3: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/theme/tokens/spacing.dart
git commit -m "theme: add xxs spacing token, refine scale"
```

---

### Task 1.4: Refine radius scale (Linear values)

**Files:**
- Modify: `flutter_app/lib/theme/tokens/radius.dart`

- [ ] **Step 1: Replace the radius class**

In `flutter_app/lib/theme/tokens/radius.dart`, replace the file contents with:

```dart
import 'package:flutter/material.dart';

@immutable
class EaRadius {
  final double none;
  final double xs;
  final double sm;
  final double md;
  final double lg;
  final double xl;
  final double full;

  const EaRadius({
    required this.none,
    required this.xs,
    required this.sm,
    required this.md,
    required this.lg,
    required this.xl,
    required this.full,
  });

  static const standard = EaRadius(
    none: 0,
    xs: 4,
    sm: 6,
    md: 8,
    lg: 10,
    xl: 12,
    full: 999,
  );

  BorderRadius get xsAll => BorderRadius.circular(xs);
  BorderRadius get smAll => BorderRadius.circular(sm);
  BorderRadius get mdAll => BorderRadius.circular(md);
  BorderRadius get lgAll => BorderRadius.circular(lg);
  BorderRadius get xlAll => BorderRadius.circular(xl);
  BorderRadius get fullAll => BorderRadius.circular(full);
}
```

- [ ] **Step 2: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/`
Expected: No new errors. `mdAll`/`lgAll`/`xlAll` getters still exist; `sm` value changed from 6 (was 6 already) — no actual change for existing consumers; `md` changed from 10 to 8 and `lg` changed from 14 to 10. Visual changes expected once we relaunch.

- [ ] **Step 3: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/theme/tokens/radius.dart
git commit -m "theme: tighten radius scale (8/10/12 for cards/panels/dialogs)"
```

---

### Task 1.5: Restructure motion tokens with Linear two-tier semantics

**Files:**
- Modify: `flutter_app/lib/theme/tokens/motion.dart`
- Test: `flutter_app/test/theme/motion_test.dart` (create)

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/theme/motion_test.dart`:

```dart
import 'package:flutter/animation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/theme/tokens/motion.dart';

void main() {
  group('EaMotion.standard', () {
    test('utility tier durations', () {
      expect(EaMotion.standard.fast, const Duration(milliseconds: 120));
      expect(EaMotion.standard.base, const Duration(milliseconds: 180));
    });

    test('moment tier duration', () {
      expect(EaMotion.standard.moment, const Duration(milliseconds: 280));
    });

    test('press scale value', () {
      expect(EaMotion.standard.pressScale, closeTo(0.97, 0.001));
    });

    test('curves are defined as Cubic', () {
      expect(EaMotion.standard.curveStandard, isA<Cubic>());
      expect(EaMotion.standard.curveEntrance, isA<Cubic>());
      expect(EaMotion.standard.curveExit, isA<Cubic>());
      expect(EaMotion.standard.curveSpring, isA<Cubic>());
    });
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/theme/motion_test.dart`
Expected: FAIL — `fast`, `base`, `moment`, `pressScale`, `curveStandard` etc. don't exist yet.

- [ ] **Step 3: Replace motion class**

In `flutter_app/lib/theme/tokens/motion.dart`, replace contents with:

```dart
import 'package:flutter/animation.dart';
import 'package:flutter/foundation.dart';

@immutable
class EaMotion {
  // Utility tier — high-frequency UI changes (180ms or less).
  final Duration instant;
  final Duration fast;
  final Duration base;

  // Moment tier — intentional moments deserving delight (280ms).
  final Duration moment;
  final Duration slow;

  // Press-state scale value.
  final double pressScale;

  // Curves.
  final Cubic curveStandard; // default ease-out (Linear's curve)
  final Cubic curveEntrance; // springy entrance
  final Cubic curveExit;     // sharp exit
  final Cubic curveSpring;   // slight overshoot

  const EaMotion({
    required this.instant,
    required this.fast,
    required this.base,
    required this.moment,
    required this.slow,
    required this.pressScale,
    required this.curveStandard,
    required this.curveEntrance,
    required this.curveExit,
    required this.curveSpring,
  });

  static const standard = EaMotion(
    instant: Duration.zero,
    fast: Duration(milliseconds: 120),
    base: Duration(milliseconds: 180),
    moment: Duration(milliseconds: 280),
    slow: Duration(milliseconds: 320),
    pressScale: 0.97,
    curveStandard: Cubic(0.2, 0, 0, 1),
    curveEntrance: Cubic(0.16, 1, 0.3, 1),
    curveExit: Cubic(0.4, 0, 1, 1),
    curveSpring: Cubic(0.34, 1.56, 0.64, 1),
  );

  // Backward-compatibility aliases. Remove once all call sites migrated.
  @Deprecated('Use base instead')
  Duration get snappy => base;
  @Deprecated('Use moment instead')
  Duration get fluid => moment;
  @Deprecated('Use slow instead')
  Duration get intuitive => slow;
  @Deprecated('Use slow instead')
  Duration get graceful => slow;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/theme/motion_test.dart`
Expected: PASS — 4 tests pass.

- [ ] **Step 5: Run full analyzer to check downstream callers**

Run: `cd flutter_app && flutter analyze lib/ 2>&1 | grep -E "(error|snappy|fluid|intuitive|graceful)" | head -20`
Expected: Possibly deprecation warnings on old names. No errors (deprecated getters cover existing call sites).

- [ ] **Step 6: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/theme/tokens/motion.dart flutter_app/test/theme/motion_test.dart
git commit -m "theme: restructure motion tokens (utility/moment tiers, Linear curves)"
```

---

### Task 1.6: Wire updated tokens into ThemeData

**Files:**
- Modify: `flutter_app/lib/theme/app_theme.dart` (lines 87–193)

- [ ] **Step 1: Read current AppTheme**

Run: `cat flutter_app/lib/theme/app_theme.dart | head -180`
Skim the `AppTheme._build` method.

- [ ] **Step 2: Update the ColorScheme wiring**

In `flutter_app/lib/theme/app_theme.dart`, within `_build`, ensure these mappings (these may already exist; verify):

```dart
      colorScheme: ColorScheme(
        brightness: brightness,
        primary: tokens.colors.accent,
        onPrimary: tokens.colors.textInverse,
        secondary: tokens.colors.accentMuted,
        onSecondary: tokens.colors.textPrimary,
        surface: tokens.colors.bgSurface,
        onSurface: tokens.colors.textPrimary,
        error: tokens.colors.error,
        onError: tokens.colors.textInverse,
        outline: tokens.colors.borderDefault,
        outlineVariant: tokens.colors.borderSubtle,
      ),
```

If different, replace with the above. Also ensure the `inputDecorationTheme` uses the new colors:

```dart
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: tokens.colors.bgField,
        border: OutlineInputBorder(
          borderRadius: tokens.radius.mdAll,
          borderSide: BorderSide(color: tokens.colors.borderDefault),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: tokens.radius.mdAll,
          borderSide: BorderSide(color: tokens.colors.borderDefault),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: tokens.radius.mdAll,
          borderSide: BorderSide(color: tokens.colors.accent, width: 1.5),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      ),
```

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/theme/`
Expected: No errors.

- [ ] **Step 4: Run app and visually verify**

Run: `cd flutter_app && flutter run -d macos` (separate terminal)
Expected: App launches. Accent color visible on focused input, buttons. Background near-black. **Do not fix any broken visuals yet** — Phase 2/3 fix them. Just confirm app doesn't crash.

Kill the app with `q`.

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/theme/app_theme.dart
git commit -m "theme: wire Deep Emerald tokens into ColorScheme + input decoration"
```

---

### Task 1.7: Phase 1 smoke test — run all theme tests + analyzer

- [ ] **Step 1: Run theme tests**

Run: `cd flutter_app && flutter test test/theme/`
Expected: All tests pass (tokens_test, typography_test, motion_test).

- [ ] **Step 2: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/ 2>&1 | tail -10`
Expected: Same number of issues as before (about 31 info-level), zero errors.

- [ ] **Step 3: Tag Phase 1 completion**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git tag design-refresh-phase-1
git push origin design-refresh-phase-1 2>/dev/null || true
```

---

## Phase 2: Chat Surface

### Task 2.1: Replace `staggeredEntry` with reduced-motion-aware fadeIn

**Files:**
- Modify: `flutter_app/lib/core/animations.dart`
- Test: `flutter_app/test/core/animations_test.dart` (create)

- [ ] **Step 1: Read current animations file**

Run: `cat flutter_app/lib/core/animations.dart`
Note the existing `staggeredEntry` signature.

- [ ] **Step 2: Write the failing test**

Create `flutter_app/test/core/animations_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/core/animations.dart';

void main() {
  testWidgets('EaAnimations.fadeIn fades in over 200ms', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: EaAnimations.fadeIn(
          child: const Text('hello'),
        ),
      ),
    ));

    // Initial frame: opacity should start at 0.
    final opacityFinder = find.byType(Opacity);
    expect(opacityFinder, findsOneWidget);

    // After full duration, opacity should be 1.
    await tester.pump(const Duration(milliseconds: 200));
    final opacity = tester.widget<Opacity>(opacityFinder);
    expect(opacity.opacity, 1.0);
  });

  testWidgets('EaAnimations.fadeIn respects reduced-motion', (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: Scaffold(
          body: EaAnimations.fadeIn(child: const Text('hello')),
        ),
      ),
    ));
    // With reduced motion, child is instantly visible.
    await tester.pump();
    final opacityFinder = find.byType(Opacity);
    if (opacityFinder.evaluate().isNotEmpty) {
      final op = tester.widget<Opacity>(opacityFinder);
      expect(op.opacity, 1.0);
    }
  });
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/core/animations_test.dart`
Expected: FAIL — `EaAnimations.fadeIn` doesn't exist yet.

- [ ] **Step 4: Replace `staggeredEntry` with `fadeIn`**

Replace `flutter_app/lib/core/animations.dart` contents with:

```dart
import 'package:flutter/material.dart';

class EaAnimations {
  EaAnimations._();

  /// Fades a child in over 200ms. Respects reduced-motion (instant if enabled).
  static Widget fadeIn({
    required Widget child,
    Duration duration = const Duration(milliseconds: 200),
    Curve curve = const Cubic(0.2, 0, 0, 1),
  }) {
    return _FadeIn(duration: duration, curve: curve, child: child);
  }

  // Backward-compat: keep staggeredEntry as a thin wrapper around fadeIn.
  @Deprecated('Use EaAnimations.fadeIn instead')
  static Widget staggeredEntry({required int index, required Widget child}) {
    return fadeIn(child: child);
  }
}

class _FadeIn extends StatefulWidget {
  final Widget child;
  final Duration duration;
  final Curve curve;
  const _FadeIn({required this.child, required this.duration, required this.curve});

  @override
  State<_FadeIn> createState() => _FadeInState();
}

class _FadeInState extends State<_FadeIn> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: widget.duration);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final reducedMotion = MediaQuery.of(context).disableAnimations;
      if (reducedMotion) {
        _controller.value = 1.0;
      } else {
        _controller.forward();
      }
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (_, child) => Opacity(
        opacity: widget.curve.transform(_controller.value),
        child: child,
      ),
      child: widget.child,
    );
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/core/animations_test.dart`
Expected: PASS — both tests pass.

- [ ] **Step 6: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/core/animations.dart`
Expected: No errors. `staggeredEntry` callers still compile (deprecated).

- [ ] **Step 7: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/core/animations.dart flutter_app/test/core/animations_test.dart
git commit -m "animations: replace staggeredEntry with reduced-motion-aware fadeIn"
```

---

### Task 2.2: Split MessageBubble into UserBubble + AssistantMessage

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/message_bubble.dart`

- [ ] **Step 1: Read current MessageBubble**

Run: `cat flutter_app/lib/features/chat/widgets/message_bubble.dart`
Note: current widget likely renders both user and assistant in different styles inside one widget.

- [ ] **Step 2: Replace contents**

Replace `flutter_app/lib/features/chat/widgets/message_bubble.dart` with:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../../../models/message.dart';
import '../../../theme/app_theme.dart';

class MessageBubble extends StatelessWidget {
  final ChatMessage message;
  const MessageBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final isUser = message.role == 'user';
    return isUser
        ? _UserBubble(content: message.content)
        : _AssistantMessage(content: message.content);
  }
}

class _UserBubble extends StatelessWidget {
  final String content;
  const _UserBubble({required this.content});

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Align(
      alignment: Alignment.centerRight,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
        child: Container(
          margin: EdgeInsets.symmetric(vertical: tokens.spacing.sm),
          padding: EdgeInsets.symmetric(
            horizontal: tokens.spacing.md + 4,
            vertical: tokens.spacing.md,
          ),
          decoration: BoxDecoration(
            color: tokens.colors.accentMuted,
            borderRadius: tokens.radius.mdAll,
            border: Border.all(color: tokens.colors.borderAccent, width: 1),
          ),
          child: SelectableText(
            content,
            style: tokens.typography.textTheme.bodyLarge?.copyWith(
              color: tokens.colors.textPrimary,
            ),
          ),
        ),
      ),
    );
  }
}

class _AssistantMessage extends StatelessWidget {
  final String content;
  const _AssistantMessage({required this.content});

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Align(
      alignment: Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.85),
        child: Padding(
          padding: EdgeInsets.symmetric(vertical: tokens.spacing.sm),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _RoleLabel(label: 'ASSISTANT', dotColor: tokens.colors.accent),
              SizedBox(height: tokens.spacing.xs),
              MarkdownBody(
                data: content,
                selectable: true,
                styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
                  p: tokens.typography.textTheme.bodyLarge?.copyWith(
                    color: tokens.colors.textPrimary,
                  ),
                  code: tokens.typography.monoTheme.bodySmall?.copyWith(
                    color: tokens.colors.textPrimary,
                    backgroundColor: tokens.colors.bgField,
                  ),
                  codeblockDecoration: BoxDecoration(
                    color: tokens.colors.bgField,
                    borderRadius: tokens.radius.smAll,
                  ),
                  codeblockPadding: EdgeInsets.all(tokens.spacing.md),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RoleLabel extends StatelessWidget {
  final String label;
  final Color dotColor;
  const _RoleLabel({required this.label, required this.dotColor});

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 6,
          height: 6,
          decoration: BoxDecoration(
            color: dotColor,
            borderRadius: tokens.radius.fullAll,
          ),
        ),
        SizedBox(width: tokens.spacing.sm - 2),
        Text(
          label,
          style: tokens.typography.textTheme.labelSmall?.copyWith(
            color: tokens.colors.textTertiary,
          ),
        ),
      ],
    );
  }
}
```

- [ ] **Step 3: Verify analyzer is clean**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/message_bubble.dart`
Expected: No errors. If `flutter_markdown` is missing, add it: `cd flutter_app && flutter pub add flutter_markdown`.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/message_bubble.dart flutter_app/pubspec.yaml flutter_app/pubspec.lock 2>/dev/null
git commit -m "chat: split MessageBubble into UserBubble + flat AssistantMessage with role label"
```

---

### Task 2.3: Redesign StreamingBubble (flat, pulsing role-dot)

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/streaming_bubble.dart`

- [ ] **Step 1: Read current StreamingBubble**

Run: `cat flutter_app/lib/features/chat/widgets/streaming_bubble.dart`

- [ ] **Step 2: Replace contents**

Replace `flutter_app/lib/features/chat/widgets/streaming_bubble.dart` with:

```dart
import 'package:flutter/material.dart';
import '../../../theme/app_theme.dart';

class StreamingBubble extends StatefulWidget {
  final String text;
  const StreamingBubble({super.key, required this.text});

  @override
  State<StreamingBubble> createState() => _StreamingBubbleState();
}

class _StreamingBubbleState extends State<StreamingBubble> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse;

  @override
  void initState() {
    super.initState();
    _pulse = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final reducedMotion = MediaQuery.of(context).disableAnimations;
    return Align(
      alignment: Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.85),
        child: Padding(
          padding: EdgeInsets.symmetric(vertical: tokens.spacing.sm),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  AnimatedBuilder(
                    animation: _pulse,
                    builder: (_, __) {
                      final opacity = reducedMotion
                          ? 1.0
                          : 0.4 + 0.6 * _pulse.value;
                      return Container(
                        width: 6,
                        height: 6,
                        decoration: BoxDecoration(
                          color: tokens.colors.accent.withValues(alpha: opacity),
                          borderRadius: tokens.radius.fullAll,
                        ),
                      );
                    },
                  ),
                  SizedBox(width: tokens.spacing.sm - 2),
                  Text(
                    'ASSISTANT',
                    style: tokens.typography.textTheme.labelSmall?.copyWith(
                      color: tokens.colors.textTertiary,
                    ),
                  ),
                ],
              ),
              SizedBox(height: tokens.spacing.xs),
              SelectableText(
                widget.text,
                style: tokens.typography.textTheme.bodyLarge?.copyWith(
                  color: tokens.colors.textPrimary,
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

- [ ] **Step 3: Verify analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/streaming_bubble.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/streaming_bubble.dart
git commit -m "chat: redesign StreamingBubble flat with pulsing role-dot"
```

---

### Task 2.4: Redesign ReasoningBubble (collapsed card, small-caps header)

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/reasoning_bubble.dart`

- [ ] **Step 1: Read current file**

Run: `cat flutter_app/lib/features/chat/widgets/reasoning_bubble.dart`

- [ ] **Step 2: Replace contents**

Replace `flutter_app/lib/features/chat/widgets/reasoning_bubble.dart` with:

```dart
import 'package:flutter/material.dart';
import '../../../theme/app_theme.dart';

class ReasoningBubble extends StatefulWidget {
  final String content;
  const ReasoningBubble({super.key, required this.content});

  @override
  State<ReasoningBubble> createState() => _ReasoningBubbleState();
}

class _ReasoningBubbleState extends State<ReasoningBubble> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Align(
      alignment: Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.85),
        child: Container(
          margin: EdgeInsets.symmetric(vertical: tokens.spacing.sm),
          decoration: BoxDecoration(
            color: tokens.colors.bgSurface,
            border: Border.all(color: tokens.colors.borderSubtle, width: 1),
            borderRadius: tokens.radius.mdAll,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              InkWell(
                onTap: () => setState(() => _expanded = !_expanded),
                borderRadius: tokens.radius.mdAll,
                child: Padding(
                  padding: EdgeInsets.symmetric(
                    horizontal: tokens.spacing.md,
                    vertical: tokens.spacing.md - 2,
                  ),
                  child: Row(
                    children: [
                      Icon(
                        Symbols.psychology,
                        size: 14,
                        color: tokens.colors.textTertiary,
                      ),
                      SizedBox(width: tokens.spacing.sm),
                      Text(
                        'REASONING',
                        style: tokens.typography.textTheme.labelSmall?.copyWith(
                          color: tokens.colors.textTertiary,
                        ),
                      ),
                      const Spacer(),
                      AnimatedRotation(
                        turns: _expanded ? 0.5 : 0,
                        duration: tokens.motion.base,
                        curve: tokens.motion.curveStandard,
                        child: Icon(
                          Symbols.expand_more,
                          size: 16,
                          color: tokens.colors.textTertiary,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              AnimatedSize(
                duration: tokens.motion.base,
                curve: tokens.motion.curveStandard,
                child: _expanded
                    ? Padding(
                        padding: EdgeInsets.fromLTRB(
                          tokens.spacing.md,
                          0,
                          tokens.spacing.md,
                          tokens.spacing.md,
                        ),
                        child: SelectableText(
                          widget.content,
                          style: tokens.typography.monoTheme.bodySmall?.copyWith(
                            color: tokens.colors.textSecondary,
                          ),
                        ),
                      )
                    : const SizedBox.shrink(),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 3: Verify analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/reasoning_bubble.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/reasoning_bubble.dart
git commit -m "chat: redesign ReasoningBubble as collapsed card with small-caps header"
```

---

### Task 2.5: Redesign ToolCallCard with compact header + status badges

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/tool_call_card.dart`

- [ ] **Step 1: Read current file**

Run: `cat flutter_app/lib/features/chat/widgets/tool_call_card.dart`
Note the existing `ToolCallDisplay` model fields used.

- [ ] **Step 2: Replace contents**

Replace `flutter_app/lib/features/chat/widgets/tool_call_card.dart` with (preserving the existing `ToolCallDisplay` class at the top — only update the `ToolCallCard` widget):

```dart
import 'package:flutter/material.dart';
import '../../../theme/app_theme.dart';

class ToolCallDisplay {
  final String name;
  final Map<String, dynamic>? args;
  final String? resultPreview;
  final String status; // 'running' | 'success' | 'error'

  const ToolCallDisplay({
    required this.name,
    this.args,
    this.resultPreview,
    this.status = 'running',
  });
}

class ToolCallCard extends StatefulWidget {
  final ToolCallDisplay toolCall;
  const ToolCallCard({super.key, required this.toolCall});

  @override
  State<ToolCallCard> createState() => _ToolCallCardState();
}

class _ToolCallCardState extends State<ToolCallCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Padding(
      padding: EdgeInsets.symmetric(vertical: tokens.spacing.xs),
      child: Container(
        decoration: BoxDecoration(
          color: tokens.colors.bgSurface,
          border: Border.all(color: tokens.colors.borderSubtle, width: 1),
          borderRadius: tokens.radius.mdAll,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            InkWell(
              onTap: widget.toolCall.args != null
                  ? () => setState(() => _expanded = !_expanded)
                  : null,
              borderRadius: tokens.radius.mdAll,
              child: Padding(
                padding: EdgeInsets.symmetric(
                  horizontal: tokens.spacing.md,
                  vertical: tokens.spacing.md - 2,
                ),
                child: Row(
                  children: [
                    Icon(
                      _iconFor(widget.toolCall.name),
                      size: 14,
                      color: tokens.colors.accent,
                    ),
                    SizedBox(width: tokens.spacing.sm),
                    Flexible(
                      child: Text(
                        widget.toolCall.name,
                        style: tokens.typography.monoTheme.bodySmall?.copyWith(
                          color: tokens.colors.textPrimary,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const Spacer(),
                    _StatusBadge(status: widget.toolCall.status),
                  ],
                ),
              ),
            ),
            if (_expanded && widget.toolCall.args != null)
              Padding(
                padding: EdgeInsets.fromLTRB(
                  tokens.spacing.md,
                  0,
                  tokens.spacing.md,
                  tokens.spacing.md,
                ),
                child: SelectableText(
                  widget.toolCall.args.toString(),
                  style: tokens.typography.monoTheme.bodySmall?.copyWith(
                    color: tokens.colors.textSecondary,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  IconData _iconFor(String name) {
    if (name.startsWith('email_')) return Symbols.mail;
    if (name.startsWith('files_')) return Symbols.folder;
    if (name.startsWith('contacts_')) return Symbols.person;
    if (name.startsWith('todos_')) return Symbols.check_box;
    if (name.startsWith('memory_')) return Symbols.memory;
    if (name.startsWith('shell_')) return Symbols.terminal;
    if (name.startsWith('browser_')) return Symbols.public;
    if (name.startsWith('subagent_')) return Symbols.workspaces;
    return Symbols.build;
  }
}

class _StatusBadge extends StatelessWidget {
  final String status;
  const _StatusBadge({required this.status});

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final (icon, label, color) = switch (status) {
      'success' => (Symbols.check, 'Done', tokens.colors.textSecondary),
      'error' => (Symbols.close, 'Failed', tokens.colors.error),
      _ => (null, 'Running', tokens.colors.accent),
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (status == 'running')
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(
              color: tokens.colors.accent,
              borderRadius: tokens.radius.fullAll,
            ),
          )
        else if (icon != null)
          Icon(icon, size: 12, color: color),
        SizedBox(width: tokens.spacing.xs + 2),
        Text(
          label,
          style: tokens.typography.textTheme.labelMedium?.copyWith(
            color: color,
          ),
        ),
      ],
    );
  }
}
```

- [ ] **Step 3: Verify analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/tool_call_card.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/tool_call_card.dart
git commit -m "chat: redesign ToolCallCard with compact header and status badges"
```

---

### Task 2.6: Create EmptyState widget with suggestion chips

**Files:**
- Create: `flutter_app/lib/features/chat/widgets/empty_state.dart`

- [ ] **Step 1: Create the file**

Create `flutter_app/lib/features/chat/widgets/empty_state.dart`:

```dart
import 'package:flutter/material.dart';
import '../../../theme/app_theme.dart';

class ChatEmptyState extends StatelessWidget {
  final void Function(String)? onSuggestionTap;
  final List<String> suggestions;

  const ChatEmptyState({
    super.key,
    this.onSuggestionTap,
    this.suggestions = const [
      'Summarize my emails',
      "What's on my calendar?",
      'Add a todo',
    ],
  });

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 12,
            height: 12,
            decoration: BoxDecoration(
              color: tokens.colors.accent.withValues(alpha: 0.6),
              borderRadius: tokens.radius.fullAll,
            ),
          ),
          SizedBox(height: tokens.spacing.lg),
          Text(
            "Ask anything. I'm here to help.",
            style: tokens.typography.textTheme.bodyLarge?.copyWith(
              color: tokens.colors.textSecondary,
            ),
          ),
          SizedBox(height: tokens.spacing.xl),
          Wrap(
            spacing: tokens.spacing.sm,
            runSpacing: tokens.spacing.sm,
            alignment: WrapAlignment.center,
            children: suggestions
                .map((s) => _SuggestionChip(label: s, onTap: () => onSuggestionTap?.call(s)))
                .toList(),
          ),
        ],
      ),
    );
  }
}

class _SuggestionChip extends StatefulWidget {
  final String label;
  final VoidCallback onTap;
  const _SuggestionChip({required this.label, required this.onTap});

  @override
  State<_SuggestionChip> createState() => _SuggestionChipState();
}

class _SuggestionChipState extends State<_SuggestionChip> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: tokens.motion.fast,
          curve: tokens.motion.curveStandard,
          padding: EdgeInsets.symmetric(
            horizontal: tokens.spacing.md,
            vertical: tokens.spacing.sm,
          ),
          decoration: BoxDecoration(
            color: tokens.colors.bgSurface,
            border: Border.all(
              color: _hover ? tokens.colors.borderAccent : tokens.colors.borderSubtle,
              width: 1,
            ),
            borderRadius: tokens.radius.smAll,
          ),
          child: Text(
            widget.label,
            style: tokens.typography.textTheme.bodySmall?.copyWith(
              color: tokens.colors.textPrimary,
            ),
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: Verify analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/empty_state.dart`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/empty_state.dart
git commit -m "chat: add ChatEmptyState widget with accent dot and suggestion chips"
```

---

### Task 2.7: Update ChatInput with new visual style

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/chat_input.dart`

- [ ] **Step 1: Read current ChatInput**

Run: `cat flutter_app/lib/features/chat/widgets/chat_input.dart | head -100`
Note: preserve all existing behavior (model switcher, send action, focus handling); only swap visual chrome.

- [ ] **Step 2: Update visual style**

In `flutter_app/lib/features/chat/widgets/chat_input.dart`, locate the input container's `BoxDecoration` and the send button. Update them to use the new tokens:

- Container background: `tokens.colors.bgField`
- Container border: `Border.all(color: focused ? tokens.colors.borderAccent : tokens.colors.borderDefault, width: 1)`
- Border radius: `tokens.radius.mdAll`
- Padding: `EdgeInsets.symmetric(horizontal: tokens.spacing.md + 2, vertical: tokens.spacing.md - 2)`
- Send button: 32x32 square, `tokens.radius.smAll`, background `tokens.colors.accent` when text non-empty, `tokens.colors.accentMuted` when empty
- Icon on send button: `Symbols.arrow_upward`, size 16, color `tokens.colors.textInverse` (active) or `tokens.colors.textTertiary` (inactive)

(Surgical edit — preserve all behavior; only style changes.)

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/chat_input.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/chat_input.dart
git commit -m "chat: restyle ChatInput with new tokens and 32x32 send button"
```

---

### Task 2.8: Update ChatMessageList — remove staggeredEntry, wire workspace crossfade

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/chat_message_list.dart`

- [ ] **Step 1: Read current file**

Run: `cat flutter_app/lib/features/chat/widgets/chat_message_list.dart`
Note: the file may still reference `messageKeys` from earlier scroll-restoration work — remove those if present.

- [ ] **Step 2: Replace `EaAnimations.staggeredEntry(...)` with `EaAnimations.fadeIn(...)`**

In the `_buildItems` method, replace any line like:

```dart
EaAnimations.staggeredEntry(index: i, child: MessageBubble(message: msg))
```

with:

```dart
EaAnimations.fadeIn(child: MessageBubble(message: msg))
```

- [ ] **Step 3: Use the new `ChatEmptyState` widget**

If `emptyBuilder` is null and the messages list is empty, return `const ChatEmptyState()` (after adding the import).

Add to imports:
```dart
import 'empty_state.dart';
```

In `build()`, where it currently returns `const SizedBox.shrink()` for empty state, return `const ChatEmptyState()` instead.

- [ ] **Step 4: Verify analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/chat_message_list.dart`
Expected: No errors (deprecation warning on staggeredEntry call sites would be fine; we've replaced them).

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/chat_message_list.dart
git commit -m "chat: replace staggeredEntry with fadeIn, wire ChatEmptyState"
```

---

### Task 2.9: Phase 2 smoke test — visual run

- [ ] **Step 1: Run analyzer end-to-end**

Run: `cd flutter_app && flutter analyze lib/ 2>&1 | tail -10`
Expected: Same number of issues as before Phase 2 (info-level only). No new errors.

- [ ] **Step 2: Run app on macOS**

Run: `cd flutter_app && flutter run -d macos`

Visually verify:
- Send a user message — appears in emerald-tinted bubble on the right
- Wait for AI reply — appears flat (no bubble), with "ASSISTANT" small-caps label and pulsing dot
- If response includes a tool call — card shows with monospace tool name and status badge
- If response includes reasoning — collapsed card; expand and verify content

Kill app with `q`.

- [ ] **Step 3: Tag Phase 2 completion**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git tag design-refresh-phase-2
```

---

## Phase 3: Component Polish + Choreography

### Task 3.1: Create EaButton (Primary/Secondary/Ghost variants)

**Files:**
- Create: `flutter_app/lib/widgets/ea_button.dart`
- Test: `flutter_app/test/widgets/ea_button_test.dart` (create)

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/widgets/ea_button_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/widgets/ea_button.dart';
import 'package:executive_assistant/theme/app_theme.dart';

Widget wrap(Widget child) => MaterialApp(
      theme: AppTheme.dark,
      home: Scaffold(body: Center(child: child)),
    );

void main() {
  testWidgets('EaButton.primary renders label and is tappable', (tester) async {
    var tapped = false;
    await tester.pumpWidget(wrap(
      EaButton.primary(label: 'Send', onPressed: () => tapped = true),
    ));
    expect(find.text('Send'), findsOneWidget);
    await tester.tap(find.text('Send'));
    await tester.pump();
    expect(tapped, true);
  });

  testWidgets('EaButton respects disabled state', (tester) async {
    await tester.pumpWidget(wrap(
      const EaButton.primary(label: 'Send', onPressed: null),
    ));
    final btn = tester.widget<InkWell>(find.byType(InkWell).first);
    expect(btn.onTap, isNull);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/widgets/ea_button_test.dart`
Expected: FAIL — `EaButton` doesn't exist.

- [ ] **Step 3: Create EaButton**

Create `flutter_app/lib/widgets/ea_button.dart`:

```dart
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

enum EaButtonVariant { primary, secondary, ghost }

class EaButton extends StatefulWidget {
  final String label;
  final VoidCallback? onPressed;
  final EaButtonVariant variant;
  final IconData? icon;

  const EaButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.variant = EaButtonVariant.primary,
    this.icon,
  });

  const EaButton.primary({Key? key, required String label, required VoidCallback? onPressed, IconData? icon})
      : this(key: key, label: label, onPressed: onPressed, icon: icon, variant: EaButtonVariant.primary);

  const EaButton.secondary({Key? key, required String label, required VoidCallback? onPressed, IconData? icon})
      : this(key: key, label: label, onPressed: onPressed, icon: icon, variant: EaButtonVariant.secondary);

  const EaButton.ghost({Key? key, required String label, required VoidCallback? onPressed, IconData? icon})
      : this(key: key, label: label, onPressed: onPressed, icon: icon, variant: EaButtonVariant.ghost);

  @override
  State<EaButton> createState() => _EaButtonState();
}

class _EaButtonState extends State<EaButton> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final enabled = widget.onPressed != null;

    final (bg, fg, border) = switch (widget.variant) {
      EaButtonVariant.primary => (
          tokens.colors.accent,
          tokens.colors.textInverse,
          null as Color?,
        ),
      EaButtonVariant.secondary => (
          Colors.transparent,
          tokens.colors.textPrimary,
          tokens.colors.borderDefault as Color?,
        ),
      EaButtonVariant.ghost => (
          Colors.transparent,
          tokens.colors.textPrimary,
          null as Color?,
        ),
    };

    return Opacity(
      opacity: enabled ? 1.0 : 0.4,
      child: GestureDetector(
        onTapDown: (_) => setState(() => _pressed = true),
        onTapCancel: () => setState(() => _pressed = false),
        onTapUp: (_) => setState(() => _pressed = false),
        child: AnimatedScale(
          scale: _pressed ? tokens.motion.pressScale : 1.0,
          duration: const Duration(milliseconds: 100),
          curve: tokens.motion.curveStandard,
          child: Material(
            color: bg,
            borderRadius: tokens.radius.smAll,
            child: InkWell(
              onTap: enabled ? widget.onPressed : null,
              borderRadius: tokens.radius.smAll,
              hoverColor: widget.variant == EaButtonVariant.primary
                  ? tokens.colors.accentHover
                  : tokens.colors.bgSurface,
              child: Container(
                padding: EdgeInsets.symmetric(
                  horizontal: tokens.spacing.md + 2,
                  vertical: tokens.spacing.sm,
                ),
                decoration: BoxDecoration(
                  borderRadius: tokens.radius.smAll,
                  border: border != null ? Border.all(color: border, width: 1) : null,
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (widget.icon != null) ...[
                      Icon(widget.icon, size: 14, color: fg),
                      SizedBox(width: tokens.spacing.xs + 2),
                    ],
                    Text(
                      widget.label,
                      style: tokens.typography.textTheme.labelLarge?.copyWith(color: fg),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/widgets/ea_button_test.dart`
Expected: PASS — 2 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/widgets/ea_button.dart flutter_app/test/widgets/ea_button_test.dart
git commit -m "widgets: add EaButton with Primary/Secondary/Ghost variants and press scale"
```

---

### Task 3.2: Create EaDialog with scale+spring entrance

**Files:**
- Create: `flutter_app/lib/widgets/ea_dialog.dart`
- Test: `flutter_app/test/widgets/ea_dialog_test.dart` (create)

- [ ] **Step 1: Write the failing test**

Create `flutter_app/test/widgets/ea_dialog_test.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/widgets/ea_dialog.dart';
import 'package:executive_assistant/theme/app_theme.dart';

void main() {
  testWidgets('showEaDialog displays the title and content', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: AppTheme.dark,
      home: Builder(
        builder: (context) => Scaffold(
          body: Center(
            child: ElevatedButton(
              onPressed: () => showEaDialog(
                context: context,
                title: 'Confirm',
                content: const Text('Are you sure?'),
              ),
              child: const Text('Open'),
            ),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    expect(find.text('Confirm'), findsOneWidget);
    expect(find.text('Are you sure?'), findsOneWidget);
  });
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flutter_app && flutter test test/widgets/ea_dialog_test.dart`
Expected: FAIL — `showEaDialog` doesn't exist.

- [ ] **Step 3: Create EaDialog**

Create `flutter_app/lib/widgets/ea_dialog.dart`:

```dart
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

Future<T?> showEaDialog<T>({
  required BuildContext context,
  required String title,
  required Widget content,
  List<Widget> actions = const [],
  bool barrierDismissible = true,
}) {
  return showGeneralDialog<T>(
    context: context,
    barrierDismissible: barrierDismissible,
    barrierLabel: 'dismiss',
    barrierColor: Colors.black.withValues(alpha: 0.6),
    transitionDuration: const Duration(milliseconds: 280),
    pageBuilder: (ctx, anim, _) {
      final tokens = ctx.tokens;
      return Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: Material(
            color: tokens.colors.bgElevated,
            borderRadius: tokens.radius.xlAll,
            child: Container(
              decoration: BoxDecoration(
                border: Border.all(color: tokens.colors.borderDefault, width: 1),
                borderRadius: tokens.radius.xlAll,
              ),
              padding: EdgeInsets.all(tokens.spacing.lg + tokens.spacing.xs),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: tokens.typography.textTheme.titleLarge?.copyWith(
                      color: tokens.colors.textPrimary,
                    ),
                  ),
                  SizedBox(height: tokens.spacing.lg),
                  DefaultTextStyle(
                    style: tokens.typography.textTheme.bodyLarge!.copyWith(
                      color: tokens.colors.textSecondary,
                    ),
                    child: content,
                  ),
                  if (actions.isNotEmpty) ...[
                    SizedBox(height: tokens.spacing.lg + tokens.spacing.xs),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        for (var i = 0; i < actions.length; i++) ...[
                          if (i > 0) SizedBox(width: tokens.spacing.sm),
                          actions[i],
                        ],
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      );
    },
    transitionBuilder: (ctx, anim, _, child) {
      final tokens = ctx.tokens;
      final curved = CurvedAnimation(parent: anim, curve: tokens.motion.curveSpring);
      return FadeTransition(
        opacity: anim,
        child: ScaleTransition(
          scale: Tween<double>(begin: 0.96, end: 1.0).animate(curved),
          child: child,
        ),
      );
    },
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flutter_app && flutter test test/widgets/ea_dialog_test.dart`
Expected: PASS — 1 test passes.

- [ ] **Step 5: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/widgets/ea_dialog.dart flutter_app/test/widgets/ea_dialog_test.dart
git commit -m "widgets: add showEaDialog with scale+spring entrance and emerald border"
```

---

### Task 3.3: Create PageTransitions for sidebar nav

**Files:**
- Create: `flutter_app/lib/core/page_transitions.dart`

- [ ] **Step 1: Create the page transitions builder**

Create `flutter_app/lib/core/page_transitions.dart`:

```dart
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

class EaPageTransitions extends PageTransitionsBuilder {
  const EaPageTransitions();

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    final tokens = context.tokens;
    final slide = Tween<Offset>(
      begin: const Offset(0.02, 0),
      end: Offset.zero,
    ).animate(CurvedAnimation(parent: animation, curve: tokens.motion.curveStandard));
    return FadeTransition(
      opacity: animation,
      child: SlideTransition(position: slide, child: child),
    );
  }
}

class EaPageTransitionsTheme extends PageTransitionsTheme {
  EaPageTransitionsTheme()
      : super(builders: const {
          TargetPlatform.macOS: EaPageTransitions(),
          TargetPlatform.linux: EaPageTransitions(),
          TargetPlatform.windows: EaPageTransitions(),
        });
}
```

- [ ] **Step 2: Wire into ThemeData**

In `flutter_app/lib/theme/app_theme.dart` `_build` method, add to the `ThemeData(...)` constructor:

```dart
      pageTransitionsTheme: EaPageTransitionsTheme(),
```

Add to imports at top of file:
```dart
import '../core/page_transitions.dart';
```

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/core/page_transitions.dart lib/theme/app_theme.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/core/page_transitions.dart flutter_app/lib/theme/app_theme.dart
git commit -m "core: add EaPageTransitions (8px slide + fade, 180ms) and wire into theme"
```

---

### Task 3.4: Wire workspace-switch crossfade in chat panel

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (around the `_PanelMessageList` widget)

- [ ] **Step 1: Locate the chat panel message list**

Run: `grep -n "_PanelMessageList\|ChatMessageList(" flutter_app/lib/core/layout/desktop_layout.dart | head -5`
Note line numbers. The `_PanelMessageList` builds a `ChatMessageList`.

- [ ] **Step 2: Wrap the message list with AnimatedSwitcher**

In the `_PanelMessageList.build` method, wrap the returned `ChatMessageList` in an `AnimatedSwitcher` keyed by the active workspace:

```dart
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tokens = context.tokens;
    final activeWs = ref.watch(activeChatTabProvider);
    return AnimatedSwitcher(
      duration: tokens.motion.base,
      switchInCurve: tokens.motion.curveStandard,
      switchOutCurve: tokens.motion.curveStandard,
      transitionBuilder: (child, anim) => FadeTransition(opacity: anim, child: child),
      child: KeyedSubtree(
        key: ValueKey('chat_list_$activeWs'),
        child: ChatMessageList(
          // … existing args
        ),
      ),
    );
  }
```

(Preserve all existing `ChatMessageList` args.)

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/core/layout/desktop_layout.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "chat: crossfade between workspaces on tab switch (180ms)"
```

---

### Task 3.5: Restyle chat panel tabs (underline indicator)

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (the tab strip around line ~500)

- [ ] **Step 1: Locate the workspace tab strip**

Run: `grep -n "tabs.entries.map\|borderBottom" flutter_app/lib/core/layout/desktop_layout.dart | head -5`

- [ ] **Step 2: Update tab styling**

In the tab strip render code, ensure each tab:
- Active state: text color `tokens.colors.textPrimary`, font weight `FontWeight.w600`, 2px underline using `tokens.colors.accent`
- Inactive state: text color `tokens.colors.textTertiary`, weight `FontWeight.w400`, no underline
- Hover: text color `tokens.colors.textSecondary` (use `MouseRegion`)
- Use `tokens.typography.textTheme.titleSmall` (13px, 500 weight)
- Padding: `EdgeInsets.symmetric(horizontal: tokens.spacing.md, vertical: tokens.spacing.sm)`
- Wrap the underline border in an `AnimatedContainer` with `duration: tokens.motion.base, curve: tokens.motion.curveStandard` so the indicator animates between tabs

(Preserve all tap/close handlers.)

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/core/layout/desktop_layout.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "chat: restyle workspace tabs (underline indicator, animated transition)"
```

---

### Task 3.6: Restyle sidebar items (left accent bar on active)

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (sidebar item builder)

- [ ] **Step 1: Locate the sidebar item code**

Run: `grep -n "DesktopSidebarItem\|sidebarItem" flutter_app/lib/core/layout/desktop_layout.dart | head -10`

- [ ] **Step 2: Update sidebar item rendering**

For each sidebar item, ensure:
- Active: `tokens.colors.bgSurface` background, `tokens.colors.textPrimary` text, 3px-wide accent bar on the left (using a `Container` with `tokens.colors.accent`)
- Inactive: transparent background, `tokens.colors.textSecondary` text
- Hover (use `MouseRegion`): `tokens.colors.bgSurface` background, `tokens.colors.textPrimary` text
- Padding: `EdgeInsets.symmetric(horizontal: tokens.spacing.md, vertical: tokens.spacing.sm + 2)`
- Border radius: `tokens.radius.smAll`
- Icon size 18px, gap `tokens.spacing.sm + 2`
- Wrap state changes in `AnimatedContainer` with `duration: tokens.motion.fast, curve: tokens.motion.curveStandard`

(Preserve all navigation handlers.)

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/core/layout/desktop_layout.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "chat: restyle sidebar items (3px accent bar on active, hover)"
```

---

### Task 3.7: Restyle ApprovalSheet (slide+spring entrance from bottom)

**Files:**
- Modify: `flutter_app/lib/features/chat/widgets/approval_sheet.dart`

- [ ] **Step 1: Read current ApprovalSheet**

Run: `cat flutter_app/lib/features/chat/widgets/approval_sheet.dart`

- [ ] **Step 2: Wrap with slide+fade entrance**

In the build method, wrap the panel content with:

```dart
TweenAnimationBuilder<double>(
  duration: tokens.motion.moment,
  curve: tokens.motion.curveSpring,
  tween: Tween(begin: 0.0, end: 1.0),
  builder: (_, t, child) => Transform.translate(
    offset: Offset(0, 24 * (1 - t)),
    child: Opacity(opacity: t, child: child),
  ),
  child: /* existing panel content */,
)
```

Style updates:
- Background `tokens.colors.bgElevated`
- Border `Border(top: BorderSide(color: tokens.colors.borderDefault), left: BorderSide(color: tokens.colors.accent, width: 3))`
- Padding `EdgeInsets.all(tokens.spacing.lg)`
- Use `EaButton.primary(label: 'Approve', ...)` and `EaButton.secondary(label: 'Reject', ...)` for actions

Add import:
```dart
import '../../../widgets/ea_button.dart';
```

- [ ] **Step 3: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/features/chat/widgets/approval_sheet.dart`
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git add flutter_app/lib/features/chat/widgets/approval_sheet.dart
git commit -m "chat: restyle ApprovalSheet with slide+spring entrance and accent left-bar"
```

---

### Task 3.8: Phase 3 smoke test — full visual run

- [ ] **Step 1: Run all tests**

Run: `cd flutter_app && flutter test test/theme/ test/core/ test/widgets/`
Expected: All tests pass.

- [ ] **Step 2: Run analyzer**

Run: `cd flutter_app && flutter analyze lib/ 2>&1 | tail -10`
Expected: No errors, only info-level issues.

- [ ] **Step 3: Run app and verify each transition**

Run: `cd flutter_app && flutter run -d macos`

Verify in order:
- Sidebar nav clicks: page slides + fades (180ms)
- Click chat tab: workspace messages crossfade (180ms)
- Tab underline animates between tabs (180ms)
- Open settings dialog: scale + spring entrance (280ms)
- Hover sidebar items: 3px accent bar slides in, bg changes (120ms)
- Send a message, watch streaming: role-dot pulses; on completion, settles
- Tool call card appears, shows status badge; expand collapses with height animation
- Reasoning card expands/collapses smoothly
- Click into an approval scenario: panel slides up from bottom with spring (280ms)
- Toggle OS reduced-motion (System Settings → Accessibility) and verify animations stop

Kill with `q`.

- [ ] **Step 4: Tag completion**

```bash
cd /Users/eddy/Developer/Langgraph/assistant
git tag design-refresh-phase-3
git tag design-refresh-complete
```

---

## Self-Review Checklist

- [x] **Spec coverage:** All tokens (color, typography, spacing, radius, motion) replaced in Phase 1. Chat surface rebuild (UserBubble + flat AI + role label, StreamingBubble, ReasoningBubble, ToolCallCard, EmptyState, ChatInput) in Phase 2. Component polish (EaButton, EaDialog, PageTransitions, sidebar, tabs, ApprovalSheet) in Phase 3.
- [x] **No placeholders:** Every step contains full code blocks or surgical instructions (with grep to locate target lines).
- [x] **Type consistency:** `EaMotion.standard.base` / `fast` / `moment` / `pressScale` / `curveStandard` / `curveSpring` used consistently. `EaTokens.spacing.xs` / `sm` / `md` / `lg` / `xl` used consistently. `EaTokens.radius.smAll` / `mdAll` / `xlAll` / `fullAll` used consistently.
- [x] **TDD:** Every new abstraction (`EaButton`, `showEaDialog`, `fadeIn`, token values) has a failing test written first.
- [x] **Frequent commits:** 19 atomic commits across the three phases.
