// TODO: Update for new observation/reflection model when re-enabled.
// Old memory endpoints (GET /memories, DELETE /memories/$id) no longer exist.
// New Model: observation stores (id, content, priority, observation_ts) and
// reflection stores (id, content, domain, confidence, linked_observation_ids).

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../widgets/app_input.dart';

class MemoryPanel extends ConsumerStatefulWidget {
  const MemoryPanel({super.key});

  @override
  ConsumerState<MemoryPanel> createState() => _MemoryPanelState();
}

class _MemoryPanelState extends ConsumerState<MemoryPanel> {
  List<Map<String, dynamic>> _memories = [];
  bool _loading = true;
  String? _error;
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _load({String? query}) async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final host = ref.read(hostProvider);
      final userId = ref.read(userIdProvider);
      final url = query != null && query.isNotEmpty
          ? Uri.parse('http://$host/memories/search?q=$query&user_id=$userId')
          : Uri.parse('http://$host/memories?user_id=$userId&limit=100');
      final response = await http.get(url);

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        setState(() {
          _memories = List<Map<String, dynamic>>.from(data['memories'] ?? []);
          _loading = false;
        });
      } else {
        setState(() {
          _error = 'Failed to load memories (${response.statusCode})';
          _loading = false;
        });
      }
    } catch (e) {
      setState(() {
        _error = 'Cannot connect: $e';
        _loading = false;
      });
    }
  }

  Future<void> _deleteMemory(int id) async {
    try {
      final host = ref.read(hostProvider);
      final userId = ref.read(userIdProvider);
      final url = Uri.parse('http://$host/memories/$id?user_id=$userId');
      final response = await http.delete(url);
      if (response.statusCode == 200) {
        setState(() {
          _memories.removeWhere((m) => m['id'] == id);
        });
      }
    } catch (e) {
      // Silently ignore delete errors
    }
  }

  String _formatDate(String ts) {
    final dt = DateTime.tryParse(ts);
    if (dt == null) return '';
    final now = DateTime.now();
    final diff = now.difference(dt);
    if (diff.inMinutes < 1) return 'Just now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    if (diff.inDays < 7) return '${diff.inDays}d ago';
    return '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')}';
  }

  IconData _typeIcon(String type) {
    switch (type.toLowerCase()) {
      case 'fact':
        return Symbols.lightbulb;
      case 'preference':
        return Symbols.tune;
      case 'insight':
        return Symbols.insights;
      case 'event':
        return Symbols.event;
      default:
        return Symbols.psychology;
    }
  }

  Color _typeColor(String type) {
    switch (type.toLowerCase()) {
      case 'fact':
        return AppColors.accent;
      case 'preference':
        return AppColors.warning;
      case 'insight':
        return AppColors.success;
      case 'event':
        return const Color(0xFF8B5CF6);
      default:
        return AppColors.textSecondary;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.background,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Container(
            height: 52,
            padding: const EdgeInsets.symmetric(horizontal: AppSpacing.cardPadding),
            decoration: const BoxDecoration(
              border: Border(bottom: BorderSide(color: AppColors.divider)),
            ),
            child: Row(
              children: [
                Icon(Symbols.psychology, size: 18, color: AppColors.accent),
                const SizedBox(width: 8),
                Text(
                  'Memory',
                  style: AppTypography.sectionTitle.copyWith(fontSize: 15),
                ),
                const Spacer(),
                Text(
                  '${_memories.length} items',
                  style: AppTypography.caption.copyWith(color: AppColors.textDim),
                ),
                const SizedBox(width: 8),
                Tooltip(
                  message: 'Refresh',
                  child: InkWell(
                    onTap: () => _load(),
                    borderRadius: BorderRadius.circular(4),
                    child: Padding(
                      padding: const EdgeInsets.all(4),
                      child: Icon(Symbols.refresh, size: 16, color: AppColors.textSecondary),
                    ),
                  ),
                ),
              ],
            ),
          ),

          // Search bar
          Padding(
            padding: const EdgeInsets.all(10),
            child: AppSearchField(
              hint: 'Search memories...',
              controller: _searchController,
              onSubmitted: (q) => _load(query: q),
            ),
          ),

          // Content
          Expanded(child: _buildBody()),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }

    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.cardPadding),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Symbols.cloud_off, size: 40, color: AppColors.textDim),
              const SizedBox(height: 12),
              Text(_error!, textAlign: TextAlign.center,
                  style: AppTypography.caption.copyWith(color: AppColors.danger)),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () => _load(),
                icon: const Icon(Symbols.refresh, size: 16),
                label: const Text('Retry'),
              ),
            ],
          ),
        ),
      );
    }

    if (_memories.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Symbols.psychology, size: 48, color: AppColors.textDim),
            const SizedBox(height: 12),
            Text('No memories yet',
                style: AppTypography.body.copyWith(color: AppColors.textDim)),
            const SizedBox(height: 4),
            Text('The agent remembers facts and\npreferences as it learns about you.',
                textAlign: TextAlign.center,
                style: AppTypography.caption.copyWith(color: AppColors.textDim)),
          ],
        ),
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      itemCount: _memories.length,
      itemBuilder: (context, index) {
        final m = _memories[index];
        final trigger = m['trigger']?.toString() ?? '';
        final action = m['action']?.toString() ?? '';
        final mType = m['memory_type']?.toString() ?? '';
        final ts = m['updated_at']?.toString() ?? '';
        final id = m['id'];
        final displayText = action.isNotEmpty ? '$trigger → $action' : trigger;

        return Card(
          margin: const EdgeInsets.symmetric(vertical: 3),
          color: AppColors.surface,
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.chip),
            side: const BorderSide(color: AppColors.divider),
          ),
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(_typeIcon(mType), size: 16, color: _typeColor(mType)),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(displayText,
                          style: AppTypography.body.copyWith(fontSize: 13)),
                      const SizedBox(height: 2),
                      Row(
                        children: [
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 1),
                            decoration: BoxDecoration(
                              color: _typeColor(mType).withAlpha(25),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(mType,
                                style: AppTypography.caption.copyWith(
                                    fontSize: 10,
                                    color: _typeColor(mType))),
                          ),
                          const SizedBox(width: 6),
                          Text(_formatDate(ts),
                              style: AppTypography.caption.copyWith(
                                  fontSize: 10, color: AppColors.textDim)),
                        ],
                      ),
                    ],
                  ),
                ),
                if (id != null)
                  IconButton(
                    icon: Icon(Symbols.delete, size: 16,
                        color: AppColors.textDim),
                    onPressed: () => _deleteMemory(id as int),
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(),
                  ),
              ],
            ),
          ),
        );
      },
    );
  }
}
