import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/agent_provider.dart';
import '../../theme/app_theme.dart';
import '../tools/tools_provider.dart';

class CapabilitiesTab extends ConsumerStatefulWidget {
  const CapabilitiesTab({super.key});

  @override
  ConsumerState<CapabilitiesTab> createState() => _CapabilitiesTabState();
}

class _CapabilitiesTabState extends ConsumerState<CapabilitiesTab> {
  final _searchController = TextEditingController();
  String _query = '';

  @override
  void initState() {
    super.initState();
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

  bool _matches(String name) {
    if (_query.isEmpty) return true;
    return name.toLowerCase().contains(_query.toLowerCase());
  }

  @override
  Widget build(BuildContext context) {
    final toolsState = ref.watch(toolsProvider);
    final tools = toolsState.tools;
    final filteredTools = _query.isEmpty ? tools : tools.where((t) => _matches(t.name)).toList();

    return Column(
      children: [
        _buildSearchBar(),
        Expanded(
          child: Material(
            color: context.tokens.colors.bgCanvas,
            child: ListView(
              padding: EdgeInsets.zero,
              children: [
                _buildSection(
                  icon: Symbols.handyman,
                  title: 'Tools',
                  count: filteredTools.length,
                  total: tools.length,
                  children: filteredTools.map((t) => _buildToolTile(t)).toList(),
                ),
                _buildSection(
                  icon: Symbols.psychology,
                  title: 'Skills',
                  count: 0,
                  total: 0,
                  placeholder: 'Coming soon',
                ),
                _buildSection(
                  icon: Symbols.robot_2,
                  title: 'Subagents',
                  count: 0,
                  total: 0,
                  placeholder: 'Coming soon',
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSearchBar() {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: TextField(
        controller: _searchController,
        onChanged: (v) => setState(() => _query = v),
        style: context.tokens.typography.textTheme.bodyLarge?.copyWith(
          color: context.tokens.colors.textPrimary,
          fontSize: 13,
        ),
        decoration: InputDecoration(
          hintText: 'Search capabilities...',
          hintStyle: context.tokens.typography.textTheme.bodySmall?.copyWith(
            color: context.tokens.colors.textTertiary,
          ),
          prefixIcon: Icon(Symbols.search, size: 18, color: context.tokens.colors.textTertiary),
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        ),
      ),
    );
  }

  Widget _buildSection({
    required IconData icon,
    required String title,
    required int count,
    required int total,
    List<Widget>? children,
    String? placeholder,
  }) {
    final childList = children ?? [];
    final hasPlaceholder = placeholder != null && childList.isEmpty;

    return ExpansionTile(
      initiallyExpanded: false,
      leading: Icon(icon, size: 18, color: context.tokens.colors.accent),
      title: Row(
        children: [
          Text(
            title,
            style: context.tokens.typography.textTheme.bodyLarge?.copyWith(
              color: context.tokens.colors.textPrimary,
              fontSize: 13,
            ),
          ),
          if (_query.isNotEmpty && total > 0) ...[
            const SizedBox(width: 6),
            Text(
              '$count/$total',
              style: context.tokens.typography.textTheme.bodySmall?.copyWith(
                color: context.tokens.colors.textTertiary,
                fontSize: 11,
              ),
            ),
          ],
        ],
      ),
      children: hasPlaceholder
          ? [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                child: Text(
                    placeholder,
                  style: context.tokens.typography.textTheme.bodySmall?.copyWith(
                    color: context.tokens.colors.textTertiary,
                  ),
                ),
              ),
            ]
          : childList,
    );
  }

  Widget _buildToolTile(ToolItem tool) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  tool.name,
                  style: context.tokens.typography.textTheme.bodyLarge?.copyWith(
                    color: context.tokens.colors.textPrimary,
                    fontSize: 13,
                  ),
                ),
                if (tool.description.isNotEmpty)
                  Text(
                    tool.description,
                    style: context.tokens.typography.textTheme.bodySmall?.copyWith(
                      color: context.tokens.colors.textSecondary,
                      fontSize: 11,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          _buildBadges(tool),
        ],
      ),
    );
  }

  Widget _buildBadges(ToolItem tool) {
    final badges = <Widget>[];
    if (tool.isReadOnly) {
      badges.add(_badge('RO', context.tokens.colors.accent));
    }
    if (tool.enabled) {
      badges.add(_badge('on', context.tokens.colors.success));
    } else {
      badges.add(_badge('off', context.tokens.colors.textTertiary));
    }
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: badges,
    );
  }

  Widget _badge(String label, Color color) {
    return Container(
      margin: const EdgeInsets.only(right: 4),
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        color: color.withAlpha(18),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        label,
        style: TextStyle(fontSize: 9, color: color, height: 1.2),
      ),
    );
  }
}
