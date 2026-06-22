import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../theme/app_theme.dart';
import '../../features/companion/companion_pulse.dart';

class TabletRailLayout extends ConsumerWidget {
  final Widget child;

  const TabletRailLayout({super.key, required this.child});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tokens = context.tokens;

    return Scaffold(
      body: Row(
        children: [
          // 64px rail sidebar
          _TabletRail(tokens: tokens),
          // Divider
          Container(width: 1, color: tokens.colors.borderSubtle),
          // Content area
          Expanded(child: child),
        ],
      ),
    );
  }
}

class _TabletRail extends ConsumerWidget {
  final EaTokens tokens;
  const _TabletRail({required this.tokens});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Container(
      width: 64,
      color: tokens.colors.bgCanvas,
      child: Column(
        children: [
          const SizedBox(height: 8),
          const CompanionPulse(),
          const SizedBox(height: 8),
          const Divider(height: 1),
          const Expanded(child: SizedBox.shrink()),
          const Divider(height: 1),
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(
              children: [
                _RailIcon(
                  icon: Symbols.settings,
                  tooltip: 'Settings',
                  isActive: false,
                  onTap: () => context.go('/settings'),
                  tokens: tokens,
                ),
                const SizedBox(height: 4),
                _RailIcon(
                  icon: tokens.isDark ? Symbols.light_mode : Symbols.dark_mode,
                  tooltip: tokens.isDark ? 'Switch to light mode' : 'Switch to dark mode',
                  isActive: false,
                  onTap: () => ref.read(themeModeProvider.notifier).toggle(),
                  tokens: tokens,
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _RailIcon extends StatelessWidget {
  final IconData? icon;
  final String? tooltip;
  final bool isActive;
  final VoidCallback? onTap;
  final EaTokens tokens;

  const _RailIcon({
    this.icon,
    this.tooltip,
    required this.isActive,
    required this.onTap,
    required this.tokens,
  });

  @override
  Widget build(BuildContext context) {
    final bg = isActive ? tokens.colors.bgElevated : Colors.transparent;
    final fg = isActive ? tokens.colors.accent : tokens.colors.textTertiary;

    final child = Tooltip(
      message: tooltip ?? '',
      waitDuration: const Duration(milliseconds: 300),
      child: Container(
        width: 48,
        height: 48,
        margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: tokens.radius.mdAll,
          border: isActive
              ? Border(left: BorderSide(color: tokens.colors.accent, width: 2))
              : null,
        ),
        child: Center(
          child: icon != null
              ? Icon(icon, size: 22, color: fg)
              : Text(
                  '?',
                  style: tokens.typography.textTheme.bodyMedium?.copyWith(
                    color: fg,
                    fontWeight: FontWeight.w600,
                  ),
                ),
        ),
      ),
    );

    if (onTap != null) {
      return InkWell(
        onTap: onTap,
        borderRadius: tokens.radius.mdAll,
        child: child,
      );
    }
    return child;
  }
}
