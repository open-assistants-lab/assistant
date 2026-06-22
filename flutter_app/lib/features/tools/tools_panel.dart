import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import 'tools_provider.dart';

class ToolsPanel extends ConsumerStatefulWidget {
  const ToolsPanel({super.key});

  @override
  ConsumerState<ToolsPanel> createState() => _ToolsPanelState();
}

class _ToolsPanelState extends ConsumerState<ToolsPanel> {
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    final existing = ref.read(toolsProvider).searchQuery;
    if (existing.isNotEmpty) {
      _searchController.text = existing;
    }
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
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final state = ref.watch(toolsProvider);
    final tools = state.filteredTools;
    final grouped = <String, List<ToolItem>>{};
    for (final t in tools) {
      grouped.putIfAbsent(t.category, () => []).add(t);
    }
    final sorted = grouped.entries.toList()
      ..sort((a, b) => a.key.compareTo(b.key));

    return Container(
      color: tokens.colors.bgCanvas,
      child: Column(
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(
                tokens.spacing.md,
                tokens.spacing.lg,
                tokens.spacing.md,
                tokens.spacing.md,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Symbols.handyman,
                        size: 18, color: tokens.colors.accent),
                    const SizedBox(width: 8),
                    Text(
                      'Tools',
                      style: tokens.typography.textTheme.titleLarge
                          ?.copyWith(color: tokens.colors.textPrimary),
                    ),
                    const Spacer(),
                    Text(
                      '${state.totalEnabled} / ${tools.length} enabled',
                      style: tokens.typography.textTheme.labelSmall
                          ?.copyWith(color: tokens.colors.textTertiary),
                    ),
                  ],
                ),
                SizedBox(height: tokens.spacing.sm),
                TextField(
                  controller: _searchController,
                  decoration: const InputDecoration(
                    hintText: 'Search tools...',
                    prefixIcon: Icon(Symbols.search, size: 18),
                    isDense: true,
                  ),
                  onChanged: (v) =>
                      ref.read(toolsProvider.notifier).setSearch(v),
                ),
                SizedBox(height: tokens.spacing.sm),
              ],
            ),
          ),
          Expanded(
            child: state.loading
                ? const Center(child: CircularProgressIndicator())
                : state.error != null
                    ? Center(
                        child: Text(
                          'Error: ${state.error}',
                          style: TextStyle(color: tokens.colors.textSecondary),
                        ),
                      )
                    : ListView.builder(
                        padding: EdgeInsets.symmetric(
                            horizontal: tokens.spacing.md),
                        itemCount: sorted.length,
                        itemBuilder: (_, i) {
                          final cat = sorted[i].key;
                          final items = sorted[i].value;
                          final catSummary = state.categories[cat];
                          return _CategorySection(
                            title: cat.toUpperCase(),
                            count:
                                '${catSummary?.enabled ?? 0} / ${catSummary?.count ?? items.length}',
                            tools: items,
                            tokens: tokens,
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}

class _CategorySection extends StatelessWidget {
  final String title;
  final String count;
  final List<ToolItem> tools;
  final EaTokens tokens;

  const _CategorySection({
    required this.title,
    required this.count,
    required this.tools,
    required this.tokens,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(height: tokens.spacing.sm),
        Row(
          children: [
            Icon(Symbols.handyman,
                size: 18, color: tokens.colors.accent),
            const SizedBox(width: 8),
            Text(
              title,
              style: tokens.typography.textTheme.labelSmall
                  ?.copyWith(color: tokens.colors.textTertiary),
            ),
            const SizedBox(width: 8),
            Text(
              count,
              style: tokens.typography.textTheme.labelSmall
                  ?.copyWith(color: tokens.colors.textTertiary),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Container(
          decoration: BoxDecoration(
            color: tokens.colors.bgElevated,
            borderRadius: tokens.radius.smAll,
          ),
          child: Column(
            children: tools
                .map((tool) => _ToolRow(
                      tool: tool,
                      tokens: tokens,
                    ))
                .toList(),
          ),
        ),
        SizedBox(height: tokens.spacing.sm),
      ],
    );
  }
}

class _ToolRow extends ConsumerWidget {
  final ToolItem tool;
  final EaTokens tokens;

  const _ToolRow({
    required this.tool,
    required this.tokens,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Padding(
      padding: EdgeInsets.symmetric(
        horizontal: tokens.spacing.md,
        vertical: tokens.spacing.sm,
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      tool.name,
                      style: tool.enabled
                          ? tokens.typography.textTheme.bodyMedium
                              ?.copyWith(color: tokens.colors.textPrimary)
                          : tokens.typography.textTheme.bodyMedium
                              ?.copyWith(color: tokens.colors.textTertiary),
                    ),
                    const SizedBox(width: 8),
                    if (tool.isReadOnly)
                      _AnnotationBadge(
                        label: 'read-only',
                        color: tokens.colors.accent,
                        tokens: tokens,
                      ),
                    if (tool.isDestructive)
                      _AnnotationBadge(
                        label: 'destructive',
                        color: const Color(0xFFE74C3C),
                        tokens: tokens,
                      ),
                  ],
                ),
                Text(
                  tool.description,
                  style: tokens.typography.textTheme.labelSmall
                      ?.copyWith(color: tokens.colors.textTertiary),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _AnnotationBadge extends StatelessWidget {
  final String label;
  final Color color;
  final EaTokens tokens;

  const _AnnotationBadge({
    required this.label,
    required this.color,
    required this.tokens,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
      decoration: BoxDecoration(
        border: Border.all(color: color.withAlpha(120)),
        borderRadius: tokens.radius.smAll,
      ),
      child: Text(
        label,
        style: tokens.typography.textTheme.labelSmall
            ?.copyWith(color: color, fontSize: 9),
      ),
    );
  }
}
