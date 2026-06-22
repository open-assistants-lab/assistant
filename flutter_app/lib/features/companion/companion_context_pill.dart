import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/companion_provider.dart';
import '../../theme/app_theme.dart';

class CompanionContextPill extends ConsumerWidget {
  const CompanionContextPill({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final notifications = ref.watch(companionNotifierProvider);
    final relevant = notifications
        .where((n) => !n.dismissed)
        .toList();

    if (relevant.isEmpty) return const SizedBox.shrink();

    final notif = relevant.first;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: Container(
        decoration: BoxDecoration(
          color: context.tokens.colors.accent.withAlpha(15),
          border: Border(left: BorderSide(color: context.tokens.colors.accent, width: 2)),
          borderRadius: BorderRadius.circular(8),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Row(
          children: [
            Icon(Symbols.lightbulb, size: 14, color: context.tokens.colors.accent),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                notif.message,
                style: context.tokens.typography.textTheme.bodySmall!.copyWith(
                  color: context.tokens.colors.textSecondary,
                  fontSize: 12,
                ),
              ),
            ),
            const SizedBox(width: 8),
            GestureDetector(
              onTap: () => ref.read(companionNotifierProvider.notifier).dismiss(notif.id),
              child: Text('Dismiss', style: context.tokens.typography.textTheme.bodySmall!.copyWith(color: context.tokens.colors.accent, fontSize: 11)),
            ),
          ],
        ),
      ),
    );
  }
}
