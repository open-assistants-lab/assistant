import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/agent_provider.dart';
import '../../theme/app_theme.dart';

class SubagentsSidebarPanel extends ConsumerStatefulWidget {
  const SubagentsSidebarPanel({super.key});

  @override
  ConsumerState<SubagentsSidebarPanel> createState() =>
      _SubagentsSidebarPanelState();
}

class _SubagentsSidebarPanelState
    extends ConsumerState<SubagentsSidebarPanel> {
  final _searchController = TextEditingController();
  List<Map<String, dynamic>> _agents = [];
  bool _loading = true;
  String? _error;
  int _loadSeq = 0;

  @override
  void initState() {
    super.initState();
    _loadAgents();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadAgents() async {
    if (!mounted) return;
    final seq = ++_loadSeq;
    setState(() {
      _loading = _agents.isEmpty;
      _error = null;
    });
    try {
      final agents = await ref
          .read(apiClientProvider)
          .listSubagents();
      if (!mounted || seq != _loadSeq) return;
      setState(() {
        _agents = agents.whereType<Map<String, dynamic>>().toList();
        _loading = false;
      });
    } catch (e) {
      if (!mounted || seq != _loadSeq) return;
      setState(() {
        _error = 'Cannot load subagents: $e';
        _loading = false;
      });
    }
  }

  List<Map<String, dynamic>> get _filtered {
    final q = _searchController.text.toLowerCase();
    if (q.isEmpty) return _agents;
    return _agents.where((a) {
      final name = (a['name']?.toString() ?? '').toLowerCase();
      final desc = (a['description']?.toString() ?? '').toLowerCase();
      return name.contains(q) || desc.contains(q);
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final filtered = _filtered;

    return Container(
      color: tokens.colors.bgCanvas,
      child: Column(
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(tokens.spacing.md, tokens.spacing.lg,
                tokens.spacing.md, tokens.spacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Symbols.robot_2,
                        size: 18, color: tokens.colors.accent),
                    const SizedBox(width: 8),
                    Text('Subagents',
                        style: tokens.typography.textTheme.titleLarge
                            ?.copyWith(color: tokens.colors.textPrimary)),
                    const Spacer(),
                    if (_agents.isNotEmpty)
                      Text('${_agents.length}',
                          style: tokens.typography.textTheme.labelSmall
                              ?.copyWith(color: tokens.colors.textTertiary)),
                  ],
                ),
                SizedBox(height: tokens.spacing.sm),
                TextField(
                  controller: _searchController,
                  decoration: const InputDecoration(
                    hintText: 'Search subagents...',
                    prefixIcon: Icon(Symbols.search, size: 18),
                    isDense: true,
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ],
            ),
          ),
          Expanded(
            child: _loading && _agents.isEmpty
                ? const Center(child: CircularProgressIndicator(strokeWidth: 2))
                : _error != null && _agents.isEmpty
                    ? _buildError(tokens)
                    : filtered.isEmpty
                        ? _buildEmpty(tokens)
                        : ListView.builder(
                            padding:
                                EdgeInsets.symmetric(horizontal: tokens.spacing.md),
                            itemCount: filtered.length,
                            itemBuilder: (_, i) =>
                                _buildRow(filtered[i], tokens),
                          ),
          ),
        ],
      ),
    );
  }

  Widget _buildError(EaTokens tokens) {
    return Center(
      child: Padding(
        padding: EdgeInsets.all(tokens.spacing.md),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(_error!, style: TextStyle(color: tokens.colors.error)),
          SizedBox(height: tokens.spacing.sm),
          OutlinedButton(
              onPressed: _loadAgents, child: const Text('Retry')),
        ]),
      ),
    );
  }

  Widget _buildEmpty(EaTokens tokens) {
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Icon(Symbols.robot_2,
            size: 40, color: tokens.colors.textTertiary),
        const SizedBox(height: 8),
        Text('No subagents yet',
            style: tokens.typography.textTheme.bodySmall
                ?.copyWith(color: tokens.colors.textTertiary)),
      ]),
    );
  }

  Widget _buildRow(Map<String, dynamic> agent, EaTokens tokens) {
    final name = agent['name']?.toString() ?? '';
    final desc = agent['description']?.toString() ?? '';
    final status = agent['status']?.toString() ?? '';
    return Container(
      margin: EdgeInsets.only(bottom: tokens.spacing.sm),
      padding: EdgeInsets.all(tokens.spacing.sm),
      decoration: BoxDecoration(
        color: tokens.colors.bgElevated,
        borderRadius: tokens.radius.smAll,
      ),
      child: Row(
        children: [
          Icon(Symbols.robot_2, size: 18, color: tokens.colors.accent),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(name,
                    style: tokens.typography.textTheme.bodyMedium
                        ?.copyWith(color: tokens.colors.textPrimary)),
                if (desc.isNotEmpty)
                  Text(desc,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: tokens.typography.textTheme.labelSmall
                          ?.copyWith(color: tokens.colors.textTertiary)),
              ],
            ),
          ),
          if (status.isNotEmpty)
            _badge(status, tokens.colors.accent, tokens),
        ],
      ),
    );
  }

  Widget _badge(String label, Color color, EaTokens tokens) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(18),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(label,
          style: tokens.typography.textTheme.bodySmall
              ?.copyWith(fontSize: 10, color: color)),
    );
  }
}
