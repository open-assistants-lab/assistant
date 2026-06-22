import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:webview_flutter/webview_flutter.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import 'canvas_tab.dart';
import 'learn_checklist_provider.dart';
import 'learn_checklist_widget.dart' show htmlForChecklistItem;

class WorkspacePanel extends ConsumerStatefulWidget {
  const WorkspacePanel({super.key});

  @override
  ConsumerState<WorkspacePanel> createState() => _WorkspacePanelState();
}

class _WorkspacePanelState extends ConsumerState<WorkspacePanel> {
  _WorkspacePanelTab _tab = _WorkspacePanelTab.canvas;
  final Map<String, WebViewController> _widgetControllers = {};
  WebViewController? _editorController;
  String? _activeEditorFilePath;
  final Map<String, List<Map<String, dynamic>>> _suggestionQueue = {};
  static const int _maxSuggestionsPerFile = 100;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final agent = ref.read(agentProvider.notifier);
      agent.onCanvasUpdate = (event) {
        ref.read(canvasProvider.notifier).onCanvasUpdate(event);
      };
      agent.onTabUpdate = (tab) {
        final mapped = _tabFromString(tab);
        if (mapped != null && mounted) {
          setState(() => _tab = mapped);
        }
      };
      agent.onWidgetRefresh = (widget) {
        _widgetControllers[widget]?.reload();
      };
      agent.onSuggestionPush = (event) {
        final filePath = event['file_path']?.toString() ?? '';
        if (filePath.isEmpty) return;
        if (_activeEditorFilePath == filePath && _editorController != null) {
          _editorController!.runJavaScript(
            'window.__addSuggestion(${jsonEncode(event)})',
          );
        } else {
          _enqueueSuggestion(filePath, event);
        }
      };
      agent.onEditorOpen = (event) {
        final filePath = event['file_path']?.toString() ?? '';
        if (filePath.isEmpty) return;
        if (mounted) {
          setState(() {
            _activeEditorFilePath = filePath;
            _tab = _WorkspacePanelTab.notes;
          });
          _loadEditorForFile(filePath);
          _drainSuggestionQueue(filePath);
        }
      };
    });
  }

  @override
  void dispose() {
    _widgetControllers.clear();
    _editorController = null;
    _suggestionQueue.clear();
    super.dispose();
  }

  _WorkspacePanelTab? _tabFromString(String tab) {
    return switch (tab.toLowerCase()) {
      'canvas' => _WorkspacePanelTab.canvas,
      'email' => _WorkspacePanelTab.email,
      'todos' => _WorkspacePanelTab.todos,
      'contacts' => _WorkspacePanelTab.contacts,
      'files' => _WorkspacePanelTab.files,
      'notes' => _WorkspacePanelTab.notes,
      _ => null,
    };
  }

  void _enqueueSuggestion(String filePath, Map<String, dynamic> suggestion) {
    _suggestionQueue.putIfAbsent(filePath, () => []);
    if (_suggestionQueue[filePath]!.length >= _maxSuggestionsPerFile) return;
    _suggestionQueue[filePath]!.add(suggestion);
  }

  void _drainSuggestionQueue(String filePath) {
    final queue = _suggestionQueue.remove(filePath);
    if (queue == null || _editorController == null) return;
    for (final sug in queue) {
      _editorController!.runJavaScript(
        'window.__addSuggestion(${jsonEncode(sug)})',
      );
    }
  }

  Future<void> _loadEditorForFile(String filePath) async {
    try {
      final host = ref.read(hostProvider);
      final response = await http.get(
        Uri.parse('http://$host/workspace/read/${Uri.encodeFull(filePath)}?user_id=default_user'),
      );
      String content = '';
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        content = data['response']?.toString() ?? '';
      }
      final encodedPath = Uri.encodeComponent(filePath);
      final encodedContent = Uri.encodeComponent(content);
      final editorUrl = 'http://$host/editor?file_path=$encodedPath&content=$encodedContent';
      _ensureEditorController().loadRequest(Uri.parse(editorUrl));
    } catch (e) {
      debugPrint('[WorkspacePanel] Failed to load editor for $filePath: $e');
    }
  }

  void _closeEditor() {
    setState(() {
      _activeEditorFilePath = null;
      _editorController = null;
      _tab = _WorkspacePanelTab.files;
    });
  }

  WebViewController _ensureEditorController() {
    if (_editorController != null) return _editorController!;
    _editorController = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..addJavaScriptChannel(
        'flutterChannel',
        onMessageReceived: _onEditorMessage,
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageFinished: (_) {
            _editorController!.runJavaScript('''
window.parent.postMessage = function(msg) {
  window.flutterChannel.postMessage(JSON.stringify(msg));
};
''');
          },
        ),
      );
    return _editorController!;
  }

  void _onEditorMessage(JavaScriptMessage message) {
    try {
      final data = jsonDecode(message.message) as Map<String, dynamic>;
      if (data['type'] == 'editor_action') {
        _handleEditorAction(data);
      }
    } catch (e) {
      debugPrint('[WorkspacePanel] Failed to parse editor message: $e');
    }
  }

  void _handleEditorAction(Map<String, dynamic> data) {
    final action = data['action']?.toString() ?? '';
    switch (action) {
      case 'suggestion_accept':
        final suggestionId = data['suggestion_id']?.toString() ?? '';
        final filePath = data['file_path']?.toString() ?? _activeEditorFilePath ?? '';
        if (suggestionId.isNotEmpty) {
          ref.read(agentProvider.notifier).acceptSuggestion(suggestionId, filePath);
        }
      case 'suggestion_reject':
        final suggestionId = data['suggestion_id']?.toString() ?? '';
        final filePath = data['file_path']?.toString() ?? _activeEditorFilePath ?? '';
        if (suggestionId.isNotEmpty) {
          ref.read(agentProvider.notifier).rejectSuggestion(suggestionId, filePath);
        }
      case 'save':
        final content = data['content']?.toString() ?? '';
        if (_activeEditorFilePath != null && content.isNotEmpty) {
          _saveFile(_activeEditorFilePath!, content);
        }
      case 'cancel':
        _closeEditor();
    }
  }

  Future<void> _saveFile(String filePath, String content) async {
    try {
      final host = ref.read(hostProvider);
      await http.post(
        Uri.parse('http://$host/workspace/${Uri.encodeFull(filePath)}?user_id=default_user'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'content': content}),
      );
    } catch (e) {
      debugPrint('[WorkspacePanel] Failed to save file: $e');
    }
  }

  String _widgetUrl(String type) {
    final host = ref.read(hostProvider);
    return 'http://$host/widget/$type';
  }

  WebViewController _ensureWidgetController(String type) {
    if (_widgetControllers.containsKey(type)) {
      return _widgetControllers[type]!;
    }
    final controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageFinished: (_) {},
        ),
      )
      ..loadRequest(Uri.parse(_widgetUrl(type)));
    _widgetControllers[type] = controller;
    return controller;
  }

  @override
  Widget build(BuildContext context) {
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
                _buildWidgetWebView('email'),
                _buildWidgetWebView('todos'),
                _buildWidgetWebView('contacts'),
                _buildWidgetWebView('files'),
                _buildEditorWebView(),
              ],
            ),
          ),
          _BottomTabs(
            selected: _tab,
            onSelected: (tab) => setState(() {
              _tab = tab;
              if (tab == _WorkspacePanelTab.notes && _activeEditorFilePath != null) {
                _drainSuggestionQueue(_activeEditorFilePath!);
              }
            }),
          ),
        ],
      ),
    );
  }

  Widget _buildWidgetWebView(String type) {
    final controller = _ensureWidgetController(type);
    return SizedBox.expand(
      child: WebViewWidget(controller: controller),
    );
  }

  Widget _buildEditorWebView() {
    if (_editorController == null) {
      return const SizedBox.shrink();
    }
    return SizedBox.expand(
      child: WebViewWidget(controller: _editorController!),
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
                'Learn Executive Assistant',
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

enum _WorkspacePanelTab { canvas, email, todos, contacts, files, notes }

class _BottomTabs extends StatelessWidget {
  final _WorkspacePanelTab selected;
  final ValueChanged<_WorkspacePanelTab> onSelected;

  const _BottomTabs({required this.selected, required this.onSelected});

  static const _tabs = <({IconData icon, IconData activeIcon, String tooltip, _WorkspacePanelTab tab})>[
    (icon: Symbols.dashboard_customize, activeIcon: Symbols.dashboard_customize, tooltip: 'Canvas', tab: _WorkspacePanelTab.canvas),
    (icon: Symbols.mail, activeIcon: Symbols.mail, tooltip: 'Email', tab: _WorkspacePanelTab.email),
    (icon: Symbols.checklist, activeIcon: Symbols.checklist, tooltip: 'Todos', tab: _WorkspacePanelTab.todos),
    (icon: Symbols.contacts, activeIcon: Symbols.contacts, tooltip: 'Contacts', tab: _WorkspacePanelTab.contacts),
    (icon: Symbols.folder, activeIcon: Symbols.folder, tooltip: 'Files', tab: _WorkspacePanelTab.files),
    (icon: Symbols.edit_note, activeIcon: Symbols.edit_note, tooltip: 'Notes', tab: _WorkspacePanelTab.notes),
  ];

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 44,
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: context.tokens.colors.borderSubtle),
        ),
      ),
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: EdgeInsets.zero,
        children: _tabs.map((t) {
          return _BottomTabButton(
            icon: t.icon,
            activeIcon: t.activeIcon,
            selected: selected == t.tab,
            tooltip: t.tooltip,
            onTap: () => onSelected(t.tab),
          );
        }).toList(),
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
