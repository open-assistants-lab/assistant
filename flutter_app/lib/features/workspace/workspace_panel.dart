import 'dart:convert';
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../providers/workspace_provider.dart';
import 'canvas_tab.dart';
import 'capabilities_tab.dart';
import 'learn_checklist_provider.dart';
import 'learn_checklist_widget.dart' show htmlForChecklistItem;

typedef WorkspaceFileLoader =
    Future<List<Map<String, dynamic>>> Function(WidgetRef ref);

class WorkspacePanel extends ConsumerStatefulWidget {
  final Duration refreshInterval;
  final WorkspaceFileLoader? fileLoader;

  const WorkspacePanel({
    super.key,
    this.refreshInterval = const Duration(seconds: 3),
    this.fileLoader,
  });

  @override
  ConsumerState<WorkspacePanel> createState() => _WorkspacePanelState();
}

class _WorkspacePanelState extends ConsumerState<WorkspacePanel> {
  List<Map<String, dynamic>> _files = [];
  bool _loading = true;
  String? _error;
  Timer? _refreshTimer;
  _WorkspacePanelTab _tab = _WorkspacePanelTab.files;

  @override
  void initState() {
    super.initState();
    _loadFiles();
    _refreshTimer = Timer.periodic(widget.refreshInterval, (_) => _loadFiles());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(agentProvider.notifier).onCanvasUpdate = (event) {
        ref.read(canvasProvider.notifier).onCanvasUpdate(event);
      };
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<List<Map<String, dynamic>>> _fetchFiles() async {
    final host = ref.read(hostProvider);
    final userId = ref.read(userIdProvider);
    final wsId = ref.read(currentWorkspaceIdProvider);
    final url = Uri.parse(
      'http://$host/workspace/json?user_id=$userId&workspace_id=$wsId',
    );
    final response = await http.get(url);
    if (response.statusCode != 200) {
      throw Exception('Failed to load (${response.statusCode})');
    }
    final data = jsonDecode(response.body) as Map<String, dynamic>;
    return List<Map<String, dynamic>>.from(data['files'] ?? []);
  }

  Future<void> _loadFiles() async {
    if (!mounted) return;
    setState(() {
      _loading = _files.isEmpty;
      _error = null;
    });
    try {
      final files = await (widget.fileLoader?.call(ref) ?? _fetchFiles());
      if (!mounted) return;
      setState(() {
        _files = files;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Cannot connect: $e';
        _loading = false;
      });
    }
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    if (bytes < 1024 * 1024 * 1024) {
      return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
    }
    return '${(bytes / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(currentWorkspaceIdProvider, (prev, next) {
      if (prev != next) {
        _loadFiles();
        ref.read(canvasProvider.notifier).setActiveWorkspace(next);
      }
    });

    ref.listen<ChatState>(agentProvider, (prev, next) {
      if (prev?.status == ChatStatus.streaming &&
          next.status == ChatStatus.idle) {
        _loadFiles();
      }
    });

    ref.listen(canvasProvider, (prev, next) {
      final addedSurfaces = next.surfaces.length - (prev?.surfaces.length ?? 0);
      if (addedSurfaces > 0) {
        final newest = next.surfaces.take(addedSurfaces);
        final isTutorial = newest.every((s) => s.surfaceId.startsWith('learn_'));
        if (!isTutorial) {
          setState(() => _tab = _WorkspacePanelTab.canvas);
        }
      }
    });

    final checklist = ref.watch(learnChecklistProvider);
    final showChecklist =
        !checklist.dismissed &&
        checklist.completed.length < checklistItems.length;

    return Container(
      color: context.tokens.colors.bgCanvas,
      child: Column(
        children: [
          if (showChecklist) _buildChecklistBanner(),
          Expanded(
            child: IndexedStack(
              index: _tab.index,
              children: [
                const CanvasTab(),
                _buildFilesPanel(),
                const CapabilitiesTab(),
              ],
            ),
          ),
          _BottomTabs(
            selected: _tab,
            onSelected: (tab) => setState(() => _tab = tab),
          ),
        ],
      ),
    );
  }

  Widget _buildChecklistBanner() {
    final state = ref.read(learnChecklistProvider);
    final remaining = checklistItems
        .where((i) => !state.completed.contains(i.id))
        .toList();
    final tokens = context.tokens;

    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
      decoration: BoxDecoration(
        color: tokens.colors.accent.withAlpha(12),
        border: Border(bottom: BorderSide(color: tokens.colors.borderSubtle)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Symbols.school, size: 16, color: tokens.colors.accent),
              const SizedBox(width: 6),
              Text(
                'Learn Assistant',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: tokens.colors.textPrimary,
                ),
              ),
              const Spacer(),
              Text(
                '${remaining.length} of ${checklistItems.length}',
                style: TextStyle(
                  fontSize: 10,
                  color: tokens.colors.textTertiary,
                ),
              ),
              const SizedBox(width: 6),
              InkWell(
                onTap: () {
                    ref.read(learnChecklistProvider.notifier).dismiss();
                    ref.read(canvasProvider.notifier).destroyTutorialSurfaces();
                  },
                child: Icon(
                  Symbols.close,
                  size: 14,
                  color: tokens.colors.textTertiary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          SizedBox(
            height: 80,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: remaining.length,
              separatorBuilder: (_, __) => const SizedBox(width: 6),
              itemBuilder: (_, i) {
                final item = remaining[i];
                return _ChecklistChip(
                  item: item,
                  onShowMe: () {
                    ref.read(learnChecklistProvider.notifier).complete(item.id);
                    ref.read(canvasProvider.notifier).onCanvasUpdate({
                      'action': 'create',
                      'surface_id': 'learn_${item.id}',
                      'html': htmlForChecklistItem(item.id),
                    });
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilesPanel() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          height: 52,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(color: context.tokens.colors.borderSubtle),
            ),
          ),
          child: Row(
            children: [
              Icon(
                Symbols.folder,
                size: 18,
                color: context.tokens.colors.accent,
              ),
              const SizedBox(width: 8),
              Text(
                'Files',
                style: context.tokens.typography.textTheme.headlineMedium!
                    .copyWith(
                      fontSize: 15,
                      color: context.tokens.colors.textPrimary,
                    ),
              ),
              const Spacer(),
              if (_loading && _files.isNotEmpty)
                const SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              const SizedBox(width: 4),
              Text(
                '${_files.length}',
                style: context.tokens.typography.textTheme.bodySmall!.copyWith(
                  color: context.tokens.colors.textTertiary,
                ),
              ),
              const SizedBox(width: 4),
              InkWell(
                onTap: _loadFiles,
                child: Icon(
                  Symbols.refresh,
                  size: 16,
                  color: context.tokens.colors.textTertiary,
                ),
              ),
            ],
          ),
        ),
        Expanded(child: _buildFilesBody()),
      ],
    );
  }

  Widget _buildFilesBody() {
    if (_loading && _files.isEmpty) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (_error != null && _files.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                _error!,
                style: context.tokens.typography.textTheme.bodySmall!.copyWith(
                  color: context.tokens.colors.error,
                ),
              ),
              const SizedBox(height: 8),
              OutlinedButton(onPressed: _loadFiles, child: const Text('Retry')),
            ],
          ),
        ),
      );
    }
    if (_files.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Symbols.folder_open,
              size: 40,
              color: context.tokens.colors.textTertiary,
            ),
            const SizedBox(height: 8),
            Text(
              'No files',
              style: context.tokens.typography.textTheme.bodySmall!.copyWith(
                color: context.tokens.colors.textTertiary,
              ),
            ),
          ],
        ),
      );
    }
    return ListView.builder(
      padding: EdgeInsets.zero,
      itemCount: _files.length,
      itemBuilder: (_, i) {
        final f = _files[i];
        final name = f['name']?.toString() ?? '';
        final isDir = f['is_dir'] == true;
        final size = f['size'] as int? ?? 0;
        return ListTile(
          dense: true,
          leading: Icon(
            isDir ? Symbols.folder : Symbols.description,
            size: 18,
            color: isDir
                ? context.tokens.colors.warning
                : context.tokens.colors.textSecondary,
          ),
          title: Text(
            name,
            style: context.tokens.typography.textTheme.bodyLarge!.copyWith(
              fontSize: 13,
              color: context.tokens.colors.textPrimary,
            ),
          ),
          subtitle: Text(
            isDir ? 'Folder' : _formatSize(size),
            style: context.tokens.typography.textTheme.bodySmall!.copyWith(
              fontSize: 11,
              color: context.tokens.colors.textSecondary,
            ),
          ),
        );
      },
    );
  }
}

class _ChecklistChip extends StatelessWidget {
  final LearnChecklistItem item;
  final VoidCallback onShowMe;

  const _ChecklistChip({required this.item, required this.onShowMe});

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Card(
      margin: EdgeInsets.zero,
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: onShowMe,
        child: Container(
          constraints: const BoxConstraints(maxWidth: 160),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(item.icon, style: const TextStyle(fontSize: 14)),
              const SizedBox(height: 4),
              Text(
                item.title,
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w500,
                  color: tokens.colors.textPrimary,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: 2),
              Text(
                'Show me',
                style: TextStyle(fontSize: 10, color: tokens.colors.accent),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

enum _WorkspacePanelTab { canvas, files, capabilities }

class _BottomTabs extends StatelessWidget {
  final _WorkspacePanelTab selected;
  final ValueChanged<_WorkspacePanelTab> onSelected;

  const _BottomTabs({required this.selected, required this.onSelected});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 44,
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: context.tokens.colors.borderSubtle),
        ),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          _BottomTabButton(
            icon: Symbols.dashboard_customize,
            activeIcon: Symbols.dashboard_customize,
            selected: selected == _WorkspacePanelTab.canvas,
            tooltip: 'Canvas',
            onTap: () => onSelected(_WorkspacePanelTab.canvas),
          ),
          _BottomTabButton(
            icon: Symbols.folder,
            activeIcon: Symbols.folder,
            selected: selected == _WorkspacePanelTab.files,
            tooltip: 'Files',
            onTap: () => onSelected(_WorkspacePanelTab.files),
          ),
          _BottomTabButton(
            icon: Symbols.tune,
            activeIcon: Symbols.tune,
            selected: selected == _WorkspacePanelTab.capabilities,
            tooltip: 'Capabilities',
            onTap: () => onSelected(_WorkspacePanelTab.capabilities),
          ),
        ],
      ),
    );
  }
}

class _BottomTabButton extends StatelessWidget {
  final IconData icon;
  final IconData activeIcon;
  final bool selected;
  final String tooltip;
  final VoidCallback onTap;

  const _BottomTabButton({
    required this.icon,
    required this.activeIcon,
    required this.selected,
    required this.tooltip,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        borderRadius: BorderRadius.circular(context.tokens.radius.md),
        onTap: onTap,
        child: Container(
          width: 40,
          height: 32,
          decoration: selected
              ? BoxDecoration(
                  color: context.tokens.colors.accentMuted,
                  borderRadius: BorderRadius.circular(context.tokens.radius.md),
                )
              : null,
          child: Icon(
            selected ? activeIcon : icon,
            size: 19,
            color: selected
                ? context.tokens.colors.accent
                : context.tokens.colors.textSecondary,
          ),
        ),
      ),
    );
  }
}
