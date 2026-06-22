import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import 'tools_provider.dart';

class ToolsWorkspaceTab extends ConsumerStatefulWidget {
  const ToolsWorkspaceTab({super.key});

  @override
  ConsumerState<ToolsWorkspaceTab> createState() => _ToolsWorkspaceTabState();
}

class _ToolsWorkspaceTabState extends ConsumerState<ToolsWorkspaceTab> {
  @override
  void initState() {
    super.initState();
    // Defer provider mutation until after the current frame builds.
    // Modifying a provider inside initState is not allowed in Riverpod.
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  void _load() {
    final host = ref.read(hostProvider);
    final userId = ref.read(userIdProvider);
    ref.read(toolsProvider.notifier).loadTools(
          host: host,
          userId: userId,
        );
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final state = ref.watch(toolsProvider);
    final tools = state.tools;

    if (state.loading) {
      return const Center(child: CircularProgressIndicator());
    }

    final enabled = tools.where((t) => t.enabled).toList();
    final disabled = tools.where((t) => !t.enabled).toList();

    return ListView(
      padding: EdgeInsets.all(tokens.spacing.md),
      children: [
        if (enabled.isNotEmpty) ...[
          Text('Enabled (${enabled.length})',
              style: tokens.typography.textTheme.labelSmall
                  ?.copyWith(color: tokens.colors.textTertiary)),
          const SizedBox(height: 4),
          ...enabled.map((t) => _compactRow(t, tokens)),
          const SizedBox(height: 16),
        ],
        if (disabled.isNotEmpty) ...[
          Text('Disabled (${disabled.length})',
              style: tokens.typography.textTheme.labelSmall
                  ?.copyWith(color: tokens.colors.textTertiary)),
          const SizedBox(height: 4),
          ...disabled.map((t) => _compactRow(t, tokens)),
        ],
      ],
    );
  }

  Widget _compactRow(ToolItem tool, EaTokens tokens) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Icon(
            tool.enabled ? Symbols.check_circle : Symbols.cancel,
            size: 14,
            color: tool.enabled
                ? tokens.colors.accent
                : tokens.colors.textTertiary,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              tool.name,
              style: tokens.typography.textTheme.bodySmall?.copyWith(
                color: tool.enabled
                    ? tokens.colors.textPrimary
                    : tokens.colors.textTertiary,
              ),
            ),
          ),
          if (tool.isDestructive)
            Text('⚠',
                style: TextStyle(
                    color: const Color(0xFFE74C3C), fontSize: 12)),
        ],
      ),
    );
  }
}
