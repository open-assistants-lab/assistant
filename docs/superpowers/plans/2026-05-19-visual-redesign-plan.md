# Visual Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the entire Flutter visual layer with a minimalist-dark design token system, component library, dark/light mode, responsive tablet layout, and polished animations.

**Architecture:** Build a `ThemeExtension` subclass (`EaTokens`) providing mode-aware colors/typography/spacing via `context.tokens.*`. Keep backward-compat static shims during migration. Rebuild `lib/widgets/` component library from scratch. Extract shared `ChatMessageList` to eliminate chat panel duplication. Split 1254-line subagent panel into 6 dialog files. Fix all 37 analyzer issues. Deliver dark mode in Phase 1 before any screen retouch.

**Tech Stack:** Flutter 3.32+, Dart, Riverpod, go_router, google_fonts (Inter + Fira Code).

**Verification commands:**
- Flutter test: `cd flutter_app && flutter test test/`
- Flutter analyze: `cd flutter_app && flutter analyze lib/`
- After each task: `flutter test test/features/workspace/workspace_panel_test.dart`

---

## Phase 1: Foundation (Tasks 1-7)

---

### Task 1: Font Bundling

**Files:**
- Modify: `flutter_app/pubspec.yaml`

- [ ] **Step 1: Add google_fonts dependency and font configuration**

Add to `pubspec.yaml` under `dependencies:`
```yaml
  google_fonts: ^6.2.1
```

Add to `flutter:` section:
```yaml
  fonts:
    - family: FiraCode
      fonts:
        - asset: assets/fonts/FiraCode-Regular.ttf
          weight: 400
        - asset: assets/fonts/FiraCode-Medium.ttf
          weight: 500
```

- [ ] **Step 2: Download Fira Code font files**

```bash
mkdir -p flutter_app/assets/fonts
cd flutter_app/assets/fonts
curl -L "https://github.com/tonsky/FiraCode/raw/master/distr/ttf/FiraCode-Regular.ttf" -o FiraCode-Regular.ttf
curl -L "https://github.com/tonsky/FiraCode/raw/master/distr/ttf/FiraCode-Medium.ttf" -o FiraCode-Medium.ttf
```

- [ ] **Step 3: Verify fonts load correctly**

Run:
```bash
cd flutter_app && flutter test test/features/workspace/workspace_panel_test.dart
```

Expected: All tests pass (fonts don't affect widget tests).

- [ ] **Step 4: Commit**

```bash
git add flutter_app/pubspec.yaml flutter_app/assets/fonts/
git commit -m "feat: add Inter (google_fonts) and Fira Code (asset) typography dependencies"
```

---

### Task 2: Build EaTokens ThemeExtension

**Files:**
- Create: `flutter_app/lib/theme/tokens/colors.dart`
- Create: `flutter_app/lib/theme/tokens/typography.dart`
- Create: `flutter_app/lib/theme/tokens/spacing.dart`
- Create: `flutter_app/lib/theme/tokens/radius.dart`
- Create: `flutter_app/lib/theme/tokens/motion.dart`
- Create: `flutter_app/lib/theme/app_theme.dart` (replaces existing)

**Goal:** Build a `ThemeExtension<EaTokens>` that holds all design tokens with dark/light mode maps. Also provide backward-compat static shims (`AppColors.dark`, `AppColors.light`, `AppTypography`) so existing code compiles during migration.

- [ ] **Step 1: Create `colors.dart`**

```dart
import 'package:flutter/material.dart';

@immutable
class EaColors {
  final Color bgCanvas;
  final Color bgSurface;
  final Color bgElevated;
  final Color bgField;
  final Color textPrimary;
  final Color textSecondary;
  final Color textTertiary;
  final Color textInverse;
  final Color accent;
  final Color accentHover;
  final Color accentMuted;
  final Color success;
  final Color warning;
  final Color error;
  final Color info;
  final Color borderSubtle;
  final Color borderDefault;
  final Color borderAccent;

  const EaColors({
    required this.bgCanvas,
    required this.bgSurface,
    required this.bgElevated,
    required this.bgField,
    required this.textPrimary,
    required this.textSecondary,
    required this.textTertiary,
    required this.textInverse,
    required this.accent,
    required this.accentHover,
    required this.accentMuted,
    required this.success,
    required this.warning,
    required this.error,
    required this.info,
    required this.borderSubtle,
    required this.borderDefault,
    required this.borderAccent,
  });

  static const dark = EaColors(
    bgCanvas: Color(0xFF0A0A0F),
    bgSurface: Color(0xFF12121A),
    bgElevated: Color(0xFF1A1A26),
    bgField: Color(0xFF161620),
    textPrimary: Color(0xFFEEEEF0),
    textSecondary: Color(0xFF8B8BA0),
    textTertiary: Color(0xFF5C5C6E),
    textInverse: Color(0xFF0A0A0F),
    accent: Color(0xFF6C5CE7),
    accentHover: Color(0xFF7C6CF7),
    accentMuted: Color(0xFF3D3580),
    success: Color(0xFF2ED573),
    warning: Color(0xFFFFA502),
    error: Color(0xFFFF4757),
    info: Color(0xFF54A0FF),
    borderSubtle: Color(0xFF1E1E2E),
    borderDefault: Color(0xFF2A2A3C),
    borderAccent: Color(0x6C5CE780),
  );

  static const light = EaColors(
    bgCanvas: Color(0xFFF8F8FA),
    bgSurface: Color(0xFFFFFFFF),
    bgElevated: Color(0xFFF0F0F4),
    bgField: Color(0xFFEBEBF0),
    textPrimary: Color(0xFF12121A),
    textSecondary: Color(0xFF5C5C6E),
    textTertiary: Color(0xFF9C9CB0),
    textInverse: Color(0xFFFFFFFF),
    accent: Color(0xFF5E4ED6),
    accentHover: Color(0xFF4E3EC6),
    accentMuted: Color(0xFFE8E5FF),
    success: Color(0xFF1DB954),
    warning: Color(0xFFE89400),
    error: Color(0xFFE8404F),
    info: Color(0xFF3B8EFF),
    borderSubtle: Color(0xFFE4E4EC),
    borderDefault: Color(0xFFD0D0DC),
    borderAccent: Color(0x5E4ED680),
  );

  // TODO(deprecated): Remove shims when migration complete
  // Backward-compat shims for existing AppColors references
  static Color get background => dark.bgCanvas;
  static Color get surface => dark.bgSurface;
  static Color get primary => dark.accent;
  static Color get accent => dark.accent;
  static Color get success => dark.success;
  static Color get warning => dark.warning;
  static Color get danger => dark.error;
  static Color get textPrimary => dark.textPrimary;
  static Color get textSecondary => dark.textSecondary;
  static Color get textDim => dark.textTertiary;
  static Color get border => dark.borderDefault;
  static Color get divider => dark.borderSubtle;
  static Color get userBubble => dark.accentMuted;
  static Color get assistantBubble => dark.bgSurface;
  static Color get toolChipBg => dark.bgField;
}
```

- [ ] **Step 2: Create `typography.dart`**

```dart
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

@immutable
class EaTypography {
  final TextTheme textTheme;
  final TextTheme monoTheme;

  const EaTypography({
    required this.textTheme,
    required this.monoTheme,
  });

  factory EaTypography.build(Brightness brightness) {
    final inter = GoogleFonts.interTextTheme();
    final firaCode = GoogleFonts.firaCodeTextTheme();
    return EaTypography(
      textTheme: inter.copyWith(
        displayLarge: inter.displayLarge?.copyWith(
          fontSize: 32, fontWeight: FontWeight.w600, height: 1.2, letterSpacing: -0.5,
        ),
        headlineLarge: inter.headlineLarge?.copyWith(
          fontSize: 24, fontWeight: FontWeight.w600, height: 1.3, letterSpacing: -0.3,
        ),
        headlineMedium: inter.headlineMedium?.copyWith(
          fontSize: 18, fontWeight: FontWeight.w600, height: 1.3, letterSpacing: -0.2,
        ),
        bodyLarge: inter.bodyLarge?.copyWith(
          fontSize: 16, fontWeight: FontWeight.w400, height: 1.5,
        ),
        bodyMedium: inter.bodyMedium?.copyWith(
          fontSize: 14, fontWeight: FontWeight.w400, height: 1.5,
        ),
        bodySmall: inter.bodySmall?.copyWith(
          fontSize: 13, fontWeight: FontWeight.w400, height: 1.4, letterSpacing: 0.1,
        ),
        labelSmall: inter.labelSmall?.copyWith(
          fontSize: 11, fontWeight: FontWeight.w500, height: 1.3, letterSpacing: 0.2,
        ),
      ),
      monoTheme: firaCode.copyWith(
        bodyLarge: firaCode.bodyLarge?.copyWith(
          fontSize: 14, fontWeight: FontWeight.w400, height: 1.6,
        ),
        bodyMedium: firaCode.bodyMedium?.copyWith(
          fontSize: 13, fontWeight: FontWeight.w400, height: 1.5,
        ),
        bodySmall: firaCode.bodySmall?.copyWith(
          fontSize: 11, fontWeight: FontWeight.w400, height: 1.4,
        ),
      ),
    );
  }
}
```

- [ ] **Step 3: Create `spacing.dart`**

```dart
import 'package:flutter/material.dart';

@immutable
class EaSpacing {
  final double xs;
  final double sm;
  final double md;
  final double lg;
  final double xl;
  final double xxl;
  final double xxxl;

  const EaSpacing({
    required this.xs,
    required this.sm,
    required this.md,
    required this.lg,
    required this.xl,
    required this.xxl,
    required this.xxxl,
  });

  static const standard = EaSpacing(
    xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32, xxxl: 48,
  );
}
```

- [ ] **Step 4: Create `radius.dart`**

```dart
import 'package:flutter/material.dart';

@immutable
class EaRadius {
  final double sm;
  final double md;
  final double lg;
  final double xl;

  const EaRadius({required this.sm, required this.md, required this.lg, required this.xl});

  static const standard = EaRadius(sm: 6, md: 10, lg: 14, xl: 20);

  BorderRadius get smAll => BorderRadius.circular(sm);
  BorderRadius get mdAll => BorderRadius.circular(md);
  BorderRadius get lgAll => BorderRadius.circular(lg);
  BorderRadius get xlAll => BorderRadius.circular(xl);
}
```

- [ ] **Step 5: Create `motion.dart`** (aligns to existing EaMotion names)

```dart
/// Motion tokens aligned with existing EaMotion naming convention.
///
/// Keeps `snappy`/`fluid`/`intuitive` from the existing motion system.
@immutable
class EaMotion {
  final Duration snappy;    // 200ms — press scale, focus ring
  final Duration fluid;     // 300ms — page transitions, dialog open
  final Duration intuitive; // 400ms — staggered entry, hero animation
  final Duration instant;   // 100ms — hover feedback
  final Duration graceful;  // 600ms — empty state, onboarding

  const EaMotion({
    required this.snappy,
    required this.fluid,
    required this.intuitive,
    required this.instant,
    required this.graceful,
  });

  static const standard = EaMotion(
    snappy: Duration(milliseconds: 200),
    fluid: Duration(milliseconds: 300),
    intuitive: Duration(milliseconds: 400),
    instant: Duration(milliseconds: 100),
    graceful: Duration(milliseconds: 600),
  );
}
```

- [ ] **Step 6: Create EaTokens ThemeExtension wrapper**

Add to `app_theme.dart`:

```dart
import 'package:flutter/material.dart';

import 'tokens/colors.dart';
import 'tokens/typography.dart';
import 'tokens/spacing.dart';
import 'tokens/radius.dart';
import 'tokens/motion.dart';

@immutable
class EaTokens extends ThemeExtension<EaTokens> {
  final EaColors colors;
  final EaTypography typography;
  final EaSpacing spacing;
  final EaRadius radius;
  final EaMotion motion;
  final Brightness brightness;

  const EaTokens({
    required this.colors,
    required this.typography,
    required this.spacing,
    required this.radius,
    required this.motion,
    required this.brightness,
  });

  bool get isDark => brightness == Brightness.dark;

  factory EaTokens.dark() {
    return EaTokens(
      colors: EaColors.dark,
      typography: EaTypography.build(Brightness.dark),
      spacing: EaSpacing.standard,
      radius: EaRadius.standard,
      motion: EaMotion.standard,
      brightness: Brightness.dark,
    );
  }

  factory EaTokens.light() {
    return EaTokens(
      colors: EaColors.light,
      typography: EaTypography.build(Brightness.light),
      spacing: EaSpacing.standard,
      radius: EaRadius.standard,
      motion: EaMotion.standard,
      brightness: Brightness.light,
    );
  }

  @override
  EaTokens copyWith({
    EaColors? colors,
    EaTypography? typography,
    EaSpacing? spacing,
    EaRadius? radius,
    EaMotion? motion,
    Brightness? brightness,
  }) {
    return EaTokens(
      colors: colors ?? this.colors,
      typography: typography ?? this.typography,
      spacing: spacing ?? this.spacing,
      radius: radius ?? this.radius,
      motion: motion ?? this.motion,
      brightness: brightness ?? this.brightness,
    );
  }

  @override
  EaTokens lerp(EaTokens? other, double t) => this;
}

extension EaTokensBuildContext on BuildContext {
  EaTokens get tokens => Theme.of(this).extension<EaTokens>()!;
}
```

- [ ] **Step 7: Run Flutter analyze**

```bash
cd flutter_app && flutter analyze lib/theme/
```

Expected: Clean (no errors, no warnings).

- [ ] **Step 8: Commit**

```bash
git add flutter_app/lib/theme/
git commit -m "feat: add EaTokens ThemeExtension with dark/light colors, typography, spacing, radius, motion"
```

---

### Task 3: Add Dark Theme to AppTheme

**Files:**
- Modify: `flutter_app/lib/theme/app_theme.dart`
- Modify: `flutter_app/lib/main.dart`

- [ ] **Step 1: Build ThemeData factories with EaTokens**

In `app_theme.dart`, add:

```dart
class AppTheme {
  static ThemeData _build(Brightness brightness) {
    final tokens = brightness == Brightness.dark ? EaTokens.dark() : EaTokens.light();

    return ThemeData(
      brightness: brightness,
      useMaterial3: true,
      scaffoldBackgroundColor: tokens.colors.bgCanvas,
      colorScheme: ColorScheme(
        brightness: brightness,
        primary: tokens.colors.accent,
        onPrimary: tokens.colors.textInverse,
        surface: tokens.colors.bgSurface,
        onSurface: tokens.colors.textPrimary,
        error: tokens.colors.error,
        onError: Colors.white,
      ),
      textTheme: tokens.typography.textTheme,
      cardTheme: CardThemeData(
        color: tokens.colors.bgSurface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: tokens.radius.lgAll,
          side: BorderSide(color: tokens.colors.borderSubtle),
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: tokens.colors.bgCanvas,
        foregroundColor: tokens.colors.textPrimary,
        elevation: 0,
        scrolledUnderElevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: tokens.colors.bgField,
        border: OutlineInputBorder(
          borderRadius: tokens.radius.smAll,
          borderSide: BorderSide(color: tokens.colors.borderDefault),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: tokens.radius.smAll,
          borderSide: BorderSide(color: tokens.colors.borderAccent),
        ),
        contentPadding: EdgeInsets.symmetric(horizontal: tokens.spacing.md, vertical: 10),
        labelStyle: tokens.typography.textTheme.bodyMedium,
      ),
      bottomNavigationBarTheme: BottomNavigationBarThemeData(
        backgroundColor: tokens.colors.bgSurface,
        selectedItemColor: tokens.colors.accent,
        unselectedItemColor: tokens.colors.textTertiary,
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: tokens.colors.bgSurface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(tokens.radius.lg)),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: tokens.colors.borderSubtle,
        thickness: 1,
      ),
      extensions: [tokens],
    );
  }

  static ThemeData get dark => _build(Brightness.dark);
  static ThemeData get light => _build(Brightness.light);
}
```

- [ ] **Step 2: Wire ThemeMode into main.dart**

In `main.dart`, wrap the app in a theme-mode state:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

final themeModeProvider = StateProvider<ThemeMode>((ref) => ThemeMode.dark);

void main() {
  runApp(const ProviderScope(child: AssistantApp()));
}

class AssistantApp extends ConsumerWidget {
  const AssistantApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final themeMode = ref.watch(themeModeProvider);

    return MaterialApp.router(
      title: 'Assistant',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: themeMode,
      routerConfig: appRouter,
    );
  }
}
```

- [ ] **Step 3: Verify app launches in dark mode**

```bash
cd flutter_app && flutter test test/features/workspace/workspace_panel_test.dart
```

Expected: Tests pass. App now renders in dark mode by default (ThemeMode.dark).

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/theme/app_theme.dart flutter_app/lib/main.dart
git commit -m "feat: add dark theme, EaTokens extension, themeModeProvider defaulting to dark"
```

---

### Task 4: Fix All 37 Analyzer Issues

**Files:**
- Modify: 9 files with unused imports
- Modify: 2 files with RadioListTile deprecation
- Create: Test assertion for clean analyzer

- [ ] **Step 1: Remove 9 unused imports**

Files and lines to fix:
```
desktop_layout.dart:3      — remove 'import ... go_router'
desktop_layout.dart:8      — remove 'import ... companion_provider.dart'
desktop_layout.dart:20     — remove 'import ... ea_widgets.dart'
chat_screen.dart:6         — remove 'import ... message.dart'
companion_context_pill.dart:5 — remove 'import ... app_radius.dart'
companion_feed.dart:7      — remove 'import ... app_radius.dart'
companion_feed.dart:11     — remove 'import ... app_input.dart'
email_list_screen.dart:3   — remove 'import ... api_client.dart'
companion_provider.dart:2  — remove 'import ... dart:convert'
chat_screen_test.dart:11   — remove 'import ... go_router'
```

Manual edits — delete each import line.

- [ ] **Step 2: Fix RadioListTile deprecation (8 instances)**

In `settings_screen.dart` lines 257, 259 — replace `RadioListTile` with `Radio` + `ListTile`:
```dart
ListTile(
  title: Text('Model Name'),
  leading: Radio<String>(
    value: modelId,
    groupValue: selectedModel,
    onChanged: (v) => setState(() => selectedModel = v!),
  ),
)
```

In `subagents_panel.dart` lines 1061, 1062, 1068, 1069 — same pattern:
```dart
ListTile(
  title: Text(scope),
  leading: Radio<String>(
    value: scopeKey,
    groupValue: _selectedScope,
    onChanged: (v) => setState(() => _selectedScope = v!),
  ),
)
```

- [ ] **Step 3: Verify analyzer is clean**

```bash
cd flutter_app && flutter analyze lib/ 2>&1 | tail -5
```

Expected: "No issues found!" (or only info-level suggestions, no warnings/errors).

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/
git commit -m "fix: resolve 37 analyzer issues — unused imports, RadioListTile deprecation, null-aware warnings"
```

---

### Task 5: Build Component Library

**Files:**
- Create: `flutter_app/lib/widgets/ea_button.dart`
- Create: `flutter_app/lib/widgets/ea_input.dart`
- Create: `flutter_app/lib/widgets/ea_card.dart`
- Create: `flutter_app/lib/widgets/ea_dialog.dart`
- Create: `flutter_app/lib/widgets/ea_sheet.dart`
- Create: `flutter_app/lib/widgets/ea_chip.dart`
- Create: `flutter_app/lib/widgets/ea_avatar.dart`
- Create: `flutter_app/lib/widgets/ea_status_badge.dart`

- [ ] **Step 1: Create `ea_button.dart`**

```dart
import 'package:flutter/material.dart';
import '../theme/tokens/colors.dart';
import '../theme/tokens/radius.dart';
import '../theme/tokens/spacing.dart';
import '../theme/tokens/motion.dart';
import '../core/motion/motion.dart';

enum EaButtonVariant { primary, secondary, ghost, danger }
enum EaButtonSize { sm, md, lg }

class EaButton extends StatefulWidget {
  final VoidCallback? onPressed;
  final String? label;
  final Widget? icon;
  final EaButtonVariant variant;
  final EaButtonSize size;
  final bool isLoading;
  final bool isDisabled;

  const EaButton({
    super.key,
    this.onPressed,
    this.label,
    this.icon,
    this.variant = EaButtonVariant.primary,
    this.size = EaButtonSize.md,
    this.isLoading = false,
    this.isDisabled = false,
  });

  @override
  State<EaButton> createState() => _EaButtonState();
}

class _EaButtonState extends State<EaButton> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _scaleAnimation;
  bool _isHovered = false;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      duration: const Duration(milliseconds: 200),
      vsync: this,
    );
    _scaleAnimation = Tween<double>(begin: 1.0, end: 0.97).animate(
      CurvedAnimation(parent: _controller, curve: Curves.easeOut),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Color _backgroundColor(EaTokens tokens) {
    if (_isHovered && !_isDisabledOrLoading) {
      return switch (widget.variant) {
        EaButtonVariant.primary => tokens.colors.accentHover,
        EaButtonVariant.secondary || EaButtonVariant.ghost => tokens.colors.bgElevated,
        EaButtonVariant.danger => const Color(0xFFFF5E6E),
      };
    }
    return switch (widget.variant) {
      EaButtonVariant.primary => tokens.colors.accent,
      EaButtonVariant.secondary => Colors.transparent,
      EaButtonVariant.ghost => Colors.transparent,
      EaButtonVariant.danger => tokens.colors.error,
    };
  }

  Color _textColor(EaTokens tokens) {
    return switch (widget.variant) {
      EaButtonVariant.primary || EaButtonVariant.danger => tokens.colors.textInverse,
      EaButtonVariant.secondary => tokens.colors.textPrimary,
      EaButtonVariant.ghost => _isHovered ? tokens.colors.textPrimary : tokens.colors.textSecondary,
    };
  }

  bool get _isDisabledOrLoading => widget.isDisabled || widget.isLoading;

  double get _height => switch (widget.size) {
    EaButtonSize.sm => 32, EaButtonSize.md => 40, EaButtonSize.lg => 48
  };

  double get _padding => switch (widget.size) {
    EaButtonSize.sm => EaSpacing.standard.sm,
    EaButtonSize.md => EaSpacing.standard.md,
    EaButtonSize.lg => EaSpacing.standard.lg
  };

  double get _radius => switch (widget.size) {
    EaButtonSize.sm => EaRadius.standard.sm,
    EaButtonSize.md => EaRadius.standard.md,
    EaButtonSize.lg => EaRadius.standard.md
  };

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    if (widget.icon != null && widget.label == null) {
      return _buildIconOnly(tokens);
    }
    return _buildWithLabel(tokens);
  }

  Widget _buildIconOnly(EaTokens tokens) {
    final icon = widget.icon!;
    if (widget.isLoading) {
      return SizedBox(
        width: _height,
        height: _height,
        child: Center(child: SizedBox(
          width: 16, height: 16,
          child: CircularProgressIndicator(strokeWidth: 1.5, color: _textColor(tokens)),
        )),
      );
    }
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: _isDisabledOrLoading ? null : widget.onPressed,
        onTapDown: _isDisabledOrLoading ? null : (_) => _controller.forward(),
        onTapUp: _isDisabledOrLoading ? null : (_) => _controller.reverse(),
        onTapCancel: _isDisabledOrLoading ? null : () => _controller.reverse(),
        child: ScaleTransition(
          scale: _scaleAnimation,
          child: AnimatedContainer(
            duration: EaMotion.standard.snappy,
            width: _height,
            height: _height,
            decoration: BoxDecoration(
              color: _backgroundColor(tokens),
              borderRadius: BorderRadius.circular(_radius),
              border: widget.variant == EaButtonVariant.secondary
                  ? Border.all(color: tokens.colors.borderDefault)
                  : null,
            ),
            child: IconTheme(data: IconThemeData(color: _textColor(tokens), size: 20), child: icon),
          ),
        ),
      ),
    );
  }

  Widget _buildWithLabel(EaTokens tokens) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: _isDisabledOrLoading ? null : widget.onPressed,
        onTapDown: _isDisabledOrLoading ? null : (_) => _controller.forward(),
        onTapUp: _isDisabledOrLoading ? null : (_) => _controller.reverse(),
        onTapCancel: _isDisabledOrLoading ? null : () => _controller.reverse(),
        child: ScaleTransition(
          scale: _scaleAnimation,
          child: AnimatedContainer(
            duration: EaMotion.standard.snappy,
            height: _height,
            padding: EdgeInsets.symmetric(horizontal: _padding),
            decoration: BoxDecoration(
              color: _backgroundColor(tokens),
              borderRadius: BorderRadius.circular(_radius),
              border: widget.variant == EaButtonVariant.secondary
                  ? Border.all(color: tokens.colors.borderDefault)
                  : null,
            ),
            child: Opacity(
              opacity: widget.isDisabled ? 0.4 : 1.0,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (widget.isLoading) ...[
                    SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 1.5, color: _textColor(tokens))),
                    const SizedBox(width: 6),
                  ] else if (widget.icon != null) ...[
                    IconTheme(data: IconThemeData(color: _textColor(tokens), size: 18), child: widget.icon!),
                    const SizedBox(width: 6),
                  ],
                  if (widget.label != null)
                    Text(widget.label!, style: tokens.typography.textTheme.bodyMedium?.copyWith(color: _textColor(tokens))),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: Create remaining component files**

Create `ea_card.dart`, `ea_dialog.dart`, `ea_sheet.dart`, `ea_chip.dart`, `ea_status_badge.dart` following the spec's component style rules. Each file uses `context.tokens` for all styling, supports dark/light mode automatically via the ThemeExtension.

Due to space constraints, the full code for each is in the spec §2. Follow the exact property tables and state definitions there.

- [ ] **Step 3: Run Flutter analyze**

```bash
cd flutter_app && flutter analyze lib/widgets/
```

Expected: Clean.

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/widgets/
git commit -m "feat: build component library — EaButton, EaCard, EaDialog, EaSheet, EaChip, EaStatusBadge"
```

---

### Task 6: Delete Old Theme and Widget Files

**Files:**
- Delete: `flutter_app/lib/core/motion/motion.dart` (moved to `tokens/motion.dart`)
- Delete: `flutter_app/lib/core/widgets/ea_widgets.dart` (replaced by `lib/widgets/`)
- Keep: `flutter_app/lib/theme/` old files (they co-exist during migration, removed in Phase 4)

- [ ] **Step 1: Delete old motion.dart**

```bash
rm flutter_app/lib/core/motion/motion.dart
```
Update all imports that referenced it — replace with `import 'package:executive_assistant/theme/tokens/motion.dart';`.

- [ ] **Step 2: Delete ea_widgets.dart**

```bash
rm flutter_app/lib/core/widgets/ea_widgets.dart
```
Update all imports — none should exist as the widget was unused.

- [ ] **Step 3: Verify analyzer clean + tests pass**

```bash
cd flutter_app && flutter analyze lib/ && flutter test test/features/workspace/workspace_panel_test.dart
```

- [ ] **Step 4: Commit**

```bash
git add -A flutter_app/lib/
git commit -m "chore: delete old motion.dart and unused ea_widgets.dart, update imports"
```

---

### Task 7: Write Theme Extension Tests

**Files:**
- Create: `flutter_app/test/theme/tokens_test.dart`

- [ ] **Step 1: Write token tests**

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:executive_assistant/theme/app_theme.dart';

void main() {
  testWidgets('EaTokens registers on ThemeData for dark mode', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.dark,
          home: Builder(builder: (context) {
            final tokens = context.tokens;
            expect(tokens.isDark, true);
            expect(tokens.colors.bgCanvas, const Color(0xFF0A0A0F));
            expect(tokens.colors.accent, const Color(0xFF6C5CE7));
            expect(tokens.typography.textTheme.bodyLarge?.fontSize, 16);
            expect(tokens.spacing.lg, 16);
            expect(tokens.radius.md, 10);
            expect(tokens.motion.fluid, const Duration(milliseconds: 300));
            return const SizedBox();
          }),
        ),
      ),
    );
    await tester.pump();
  });

  testWidgets('EaTokens registers on ThemeData for light mode', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.light,
          home: Builder(builder: (context) {
            final tokens = context.tokens;
            expect(tokens.isDark, false);
            expect(tokens.colors.bgCanvas, const Color(0xFFF8F8FA));
            expect(tokens.colors.accent, const Color(0xFF5E4ED6));
            return const SizedBox();
          }),
        ),
      ),
    );
    await tester.pump();
  });

  testWidgets('EaTokens backward-compat shims compile', (tester) async {
    expect(EaColors.background, const Color(0xFF0A0A0F));
    expect(EaColors.primary, const Color(0xFF6C5CE7));
    expect(EaColors.success, const Color(0xFF2ED573));
    expect(EaColors.danger, const Color(0xFFFF4757));
    expect(EaColors.textPrimary, const Color(0xFFEEEEF0));
    expect(EaColors.textSecondary, const Color(0xFF8B8BA0));
    expect(EaColors.userBubble, const Color(0xFF3D3580));
    expect(EaColors.assistantBubble, const Color(0xFF12121A));
    expect(EaColors.toolChipBg, const Color(0xFF161620));
  });
}
```

- [ ] **Step 2: Run token tests**

```bash
cd flutter_app && flutter test test/theme/tokens_test.dart
```

Expected: 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add flutter_app/test/theme/
git commit -m "test: add EaTokens ThemeExtension registration tests"
```

---

## Phase 2: Shell and Navigation (Tasks 8-11)

---

### Task 8: Extract Shared ChatMessageList

**Files:**
- Create: `flutter_app/lib/features/chat/widgets/chat_message_list.dart`
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart` (replace inline _ChatPanel message list)
- Modify: `flutter_app/lib/features/chat/chat_screen.dart` (replace inline message list)

- [ ] **Step 1: Create `chat_message_list.dart`**

Extract the message list rendering logic that is duplicated between `_ChatPanel` (desktop_layout.dart lines ~260-450) and `ChatScreen` (chat_screen.dart lines ~80-150). The widget:

- Takes `List<Message> messages`, `bool isStreaming`, `String? streamingText`, `ScrollController` as parameters
- Renders messages with `MessageBubble`, `ToolCallCard`, `StreamingBubble` widgets
- Handles scroll-to-bottom on new messages
- Uses `context.tokens` for background color
- Returns a `ListView.builder`

- [ ] **Step 2: Update desktop_layout.dart _ChatPanel**

Replace the inline message list with:
```dart
ChatMessageList(
  messages: chatState.messages,
  isStreaming: chatState.isStreaming,
  streamingText: chatState.streamingText,
  scrollController: _scrollController,
)
```

- [ ] **Step 3: Update chat_screen.dart**

Replace the inline message list with same `ChatMessageList` widget.

- [ ] **Step 4: Run tests and analyzer**

```bash
cd flutter_app && flutter analyze lib/features/chat/ lib/core/layout/ && flutter test test/features/workspace/workspace_panel_test.dart
```

- [ ] **Step 5: Commit**

```bash
git add -A flutter_app/lib/
git commit -m "refactor: extract shared ChatMessageList — eliminate chat panel duplication"
```

---

### Task 9: Retouch Desktop Layout with New Theme

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart`

Goal: Replace all hardcoded colors, sizes, and inline styles with `context.tokens.*`. Style sidebar with new dark aesthetic.

Key changes:
- Sidebar background: `context.tokens.colors.bgCanvas`
- Workspace items: ghost-style, hover = `bgElevated`, active = `bgElevated` + 2px accent left border
- Chat panel background: `context.tokens.colors.bgCanvas`
- Content panel background: `context.tokens.colors.bgSurface`, 1px `borderSubtle` left border
- Tab bar: `context.tokens.colors.bgSurface` background, `borderSubtle` bottom border
- Scrollbar: `context.tokens.colors.borderDefault` thumb
- Companion pulse: keep existing CustomPaint, use `context.tokens.colors.accent`

- [ ] **Step 1: Apply new styles to sidebar section**

Replace the `_Sidebar` widget. Update all Container/Column decorations.

- [ ] **Step 2: Apply new styles to chat/content panels**

Replace `_ChatPanel` and content panel Container decorations.

- [ ] **Step 3: Verify analyzer**

```bash
cd flutter_app && flutter analyze lib/core/layout/desktop_layout.dart
```

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "style: retouch desktop layout with new dark theme tokens"
```

---

### Task 10: Retouch Mobile Layout with New Theme

**Files:**
- Modify: `flutter_app/lib/core/layout/mobile_layout.dart`

- [ ] **Step 1: Apply new styles to bottom nav**

```dart
bottomNavigationBar: BottomNavigationBar(
  backgroundColor: context.tokens.colors.bgSurface,
  selectedItemColor: context.tokens.colors.accent,
  unselectedItemColor: context.tokens.colors.textTertiary,
  ...
)
```

- [ ] **Step 2: Commit**

```bash
git add flutter_app/lib/core/layout/mobile_layout.dart
git commit -m "style: retouch mobile layout with new dark theme tokens"
```

---

### Task 11: Wire Up Page Transitions

**Files:**
- Modify: `flutter_app/lib/core/router/app_router.dart`

- [ ] **Step 1: Add consistent page transitions**

Replace `NoTransitionPage` for `/email`, `/workspace` with fade-through transitions:

```dart
GoRoute(
  path: '/email',
  pageBuilder: (context, state) => CustomTransitionPage(
    key: state.pageKey,
    child: const EmailListScreen(),
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(opacity: animation, child: child);
    },
    transitionDuration: const Duration(milliseconds: 300),
  ),
),
```

- [ ] **Step 2: Add dialog route transitions**

No changes — `showDialog` and `showModalBottomSheet` already use system transitions. Keep as is.

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/core/router/app_router.dart
git commit -m "style: add fade-through page transitions to routed screens"
```

---

## Phase 3: Screens (Tasks 12-17)

---

### Task 12: Retouch Chat Screen + Widgets

**Files:**
- Modify: `flutter_app/lib/features/chat/chat_screen.dart`
- Modify: `flutter_app/lib/features/chat/widgets/message_bubble.dart`
- Modify: `flutter_app/lib/features/chat/widgets/streaming_bubble.dart`
- Modify: `flutter_app/lib/features/chat/widgets/reasoning_bubble.dart`
- Modify: `flutter_app/lib/features/chat/widgets/tool_call_card.dart`
- Modify: `flutter_app/lib/features/chat/widgets/chat_input.dart`
- Modify: `flutter_app/lib/features/chat/widgets/connection_banner.dart`
- Modify: `flutter_app/lib/features/chat/widgets/error_bar.dart`

Goal: Migrate all chat widgets from raw `AppColors.xxx` to `context.tokens.*`. Apply new message bubble styling (asymmetric radii, Inter font, 85% width). Tool cards get accent/success/error left-border status + Fira Code mono font.

- [ ] **Step 1: Update MessageBubble**

Replace `AppColors.userBubble` with `context.tokens.colors.accentMuted` (dark) / `context.tokens.colors.accent` (light). Apply `radius.xl` top + `radius.sm` bottom.

- [ ] **Step 2: Update ToolCallCard**

Replace old 62-line widget with new spec styling: `bgField` background, `borderSubtle` border, 3px status-colored left border, mono-medium tool name, mono-small args.

- [ ] **Step 3: Update chat_input.dart**

Extract `_ApprovalBar` into `approval_bar.dart`.

- [ ] **Step 4: Run analyzer + tests**

```bash
cd flutter_app && flutter analyze lib/features/chat/ && flutter test test/features/workspace/workspace_panel_test.dart
```

- [ ] **Step 5: Commit**

```bash
git add flutter_app/lib/features/chat/
git commit -m "style: retouch all chat widgets with new dark theme tokens"
```

---

### Task 13: Split and Retouch Subagents Panel

**Files:**
- Modify: `flutter_app/lib/features/workspace/widgets/subagents/subagents_panel.dart` (reduced to ~400 lines)
- Create: `flutter_app/lib/features/workspace/widgets/subagents/create_dialog.dart`
- Create: `flutter_app/lib/features/workspace/widgets/subagents/edit_dialog.dart`
- Create: `flutter_app/lib/features/workspace/widgets/subagents/start_dialog.dart`
- Create: `flutter_app/lib/features/workspace/widgets/subagents/detail_sheet.dart`
- Create: `flutter_app/lib/features/workspace/widgets/subagents/instruct_dialog.dart`
- Create: `flutter_app/lib/features/workspace/widgets/subagents/job_card.dart`

Goal: Extract 6 dialog/widget functions from the 1254-line file into separate files. Each uses `EaDialog`/`EaSheet` wrappers and `context.tokens` styling.

- [ ] **Step 1: Extract create_dialog.dart** — `_showCreateDialog()` function

- [ ] **Step 2: Extract edit_dialog.dart** — `_showEditDialog()` function

- [ ] **Step 3: Extract start_dialog.dart** — `_showStartDialog()` function

- [ ] **Step 4: Extract detail_sheet.dart** — detail view with agent info + job cards

- [ ] **Step 5: Extract instruct_dialog.dart** — `_showInstructDialog()` function

- [ ] **Step 6: Extract job_card.dart** — `_JobCard` widget

- [ ] **Step 7: Reduce subagents_panel.dart** to ~400 lines (panel layout + create/edit/delete triggers)

- [ ] **Step 8: Verify analyzer + tests**

```bash
cd flutter_app && flutter analyze lib/features/workspace/widgets/subagents/ && flutter test test/features/workspace/workspace_panel_test.dart
```

- [ ] **Step 9: Commit**

```bash
git add flutter_app/lib/features/workspace/widgets/subagents/
git commit -m "refactor: split 1254-line subagents_panel into 6 dialog files, apply new theme"
```

---

### Task 14: Retouch Settings Screen

**Files:**
- Modify: `flutter_app/lib/features/settings/settings_screen.dart`

Goal: Replace raw `Colors.grey`, `Colors.red`, inline `TextStyle`s with `context.tokens.*` and `EaButton`/`EaInput` components. Migrate RadioListTile deprecations.

- [ ] **Step 1: Replace colors with tokens**

`AppColors.xxx` → `context.tokens.colors.xxx`

- [ ] **Step 2: Replace inline TextStyles**

Raw `TextStyle(fontSize: ..., fontWeight: ...)` → `context.tokens.typography.textTheme.bodyMedium`

- [ ] **Step 3: Rebuild settings sections with EaCard**

Wrap each settings section in `EaCard` with "interactive" variant.

- [ ] **Step 4: Run analyzer**

```bash
cd flutter_app && flutter analyze lib/features/settings/settings_screen.dart
```

- [ ] **Step 5: Commit**

```bash
git add flutter_app/lib/features/settings/settings_screen.dart
git commit -m "style: retouch settings screen with new theme tokens and EaCard components"
```

---

### Task 15: Retouch Email Screen

**Files:**
- Modify: `flutter_app/lib/features/email/email_list_screen.dart`

Goal: Replace `Colors.grey`, `Colors.black87` with `context.tokens.colors.textSecondary`, use `EaCard` for email list items, apply staggered entry animation from `EaMotion`.

- [ ] **Step 1: Replace raw colors with tokens**

- [ ] **Step 2: Wrap email items in EaCard**

- [ ] **Step 3: Add staggered list entry animation**

```dart
ListView.builder(
  itemCount: emails.length,
  itemBuilder: (context, index) {
    return EaMotion.staggeredEntry(
      index: index,
      child: EaCard(...),
    );
  },
)
```

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/features/email/email_list_screen.dart
git commit -m "style: retouch email screen with tokens, EaCard, and staggered animations"
```

---

### Task 16: Retouch Home Screen

**Files:**
- Modify: `flutter_app/lib/features/home/home_screen.dart`
- Modify: `flutter_app/lib/features/home/widgets/smart_greeting.dart`
- Modify: `flutter_app/lib/features/home/widgets/status_cards.dart`
- Modify: `flutter_app/lib/features/home/widgets/quick_actions.dart`

Goal: Migrate to `context.tokens`, use `EaChip` for quick actions, `EaCard` for status cards.

- [ ] **Step 1: Migrate smart_greeting.dart** — heading-medium style for greeting, caption for date

- [ ] **Step 2: Migrate status_cards.dart** — EaCard wrapping, hoverable variant

- [ ] **Step 3: Migrate quick_actions.dart** — EaChip for each action, selected variant on tap

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/features/home/
git commit -m "style: retouch home screen with tokens, EaCard, and EaChip components"
```

---

### Task 17: Retouch Companion Feed

**Files:**
- Modify: `flutter_app/lib/features/companion/companion_feed.dart`
- Modify: `flutter_app/lib/features/companion/widget/companion_pulse.dart`
- Modify: `flutter_app/lib/features/companion/widget/companion_toast.dart`

Goal: Migrate to `context.tokens`, keep CustomPaint pulse animation, add fade-in for feed entries.

- [ ] **Step 1: Migrate companion_feed.dart** — tokens for backgrounds, spacings

- [ ] **Step 2: Migrate companion_toast.dart** — bg-surface, border-default, radius.lg

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/features/companion/
git commit -m "style: retouch companion feed with new theme tokens"
```

---

## Phase 4: Polish (Tasks 18-21)

---

### Task 18: Add Staggered Animations + Micro-interactions

Goal: Apply `EaMotion.staggeredEntry` to all list views (messages, workspaces, subagents, emails, settings). Apply `EaButton` press-scale to all buttons.

- [ ] **Step 1: Stagger list entries everywhere**

Wrap `ListView.builder` items with `EaMotion.staggeredEntry(index: index, child: ...)` in:
- `chat_message_list.dart` (message bubbles)
- `workspace_panel.dart` (file/skill/subagent lists)
- `email_list_screen.dart`
- `settings_screen.dart`

- [ ] **Step 2: Replace all ElevatedButton/TextButton with EaButton**

- [ ] **Step 3: Add page transition to workspace switching**

When changing workspace, fade out current content (200ms), fade in new (200ms).

- [ ] **Step 4: Verify analyzer + tests**

```bash
cd flutter_app && flutter analyze lib/ && flutter test test/features/workspace/workspace_panel_test.dart
```

- [ ] **Step 5: Commit**

```bash
git add -A flutter_app/lib/
git commit -m "style: add staggered animations to all lists, EaButton to all actions, workspace transition"
```

---

### Task 19: Responsive Gap Fix (768-1023 Tablet Rail)

**Files:**
- Modify: `flutter_app/lib/core/constants/breakpoints.dart` (add tablet breakpoint)
- Modify: `flutter_app/lib/core/layout/responsive_shell.dart` (add tablet layout selection)
- Create: `flutter_app/lib/core/layout/tablet_rail.dart`

- [ ] **Step 1: Add tablet breakpoint constant**

In `breakpoints.dart`:
```dart
const double tablet = 768;
const double desktop = 1024;
```

- [ ] **Step 2: Add tablet rail layout**

Create `tablet_rail.dart` — 64px sidebar with icons only, tooltips, rail expand button.

- [ ] **Step 3: Wire into responsive_shell.dart**

```dart
if (width >= desktop) return DesktopLayout(child: child);
if (width >= tablet) return TabletRailLayout(child: child);
return MobileLayout(child: child);
```

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/core/layout/ flutter_app/lib/core/constants/
git commit -m "feat: add tablet rail layout for 768-1023px, close responsive gap"
```

---

### Task 20: Final Analyzer Pass

Goal: Zero warnings, zero errors. Run `flutter analyze lib/` and fix any regressions.

- [ ] **Step 1: Run full analyzer**

```bash
cd flutter_app && flutter analyze lib/ 2>&1
```

- [ ] **Step 2: Fix any remaining issues**

- [ ] **Step 3: Run all tests**

```bash
cd flutter_app && flutter test test/
```

- [ ] **Step 4: Commit**

```bash
git add -A flutter_app/
git commit -m "chore: final analyzer pass — zero warnings, zero errors"
```

---

### Task 21: Dark/Light Mode Persistence

**Files:**
- Modify: `flutter_app/lib/main.dart`

- [ ] **Step 1: Persist theme mode to SharedPreferences**

```dart
import 'package:shared_preferences/shared_preferences.dart';

final themeModeProvider = StateNotifierProvider<ThemeModeNotifier, ThemeMode>((ref) {
  return ThemeModeNotifier();
});

class ThemeModeNotifier extends StateNotifier<ThemeMode> {
  ThemeModeNotifier() : super(ThemeMode.dark) {
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    final mode = prefs.getString('themeMode') ?? 'dark';
    state = mode == 'light' ? ThemeMode.light : ThemeMode.dark;
  }

  Future<void> toggle() async {
    state = state == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('themeMode', state == ThemeMode.dark ? 'dark' : 'light');
  }
}
```

- [ ] **Step 2: Add theme toggle button to sidebar**

Bottom section of sidebar — icon button cycles themeModeProvider.

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/main.dart flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "feat: persist dark/light mode to SharedPreferences, add sidebar toggle"
```

---

## Final Verification

- [ ] Run all Flutter tests: `cd flutter_app && flutter test test/`
- [ ] Run Flutter analyzer: `cd flutter_app && flutter analyze lib/`
- [ ] Run workspace panel tests: `cd flutter_app && flutter test test/features/workspace/workspace_panel_test.dart`
- [ ] Commit final state if needed
