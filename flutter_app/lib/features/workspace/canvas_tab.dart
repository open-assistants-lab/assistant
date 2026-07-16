import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:webview_flutter/webview_flutter.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../providers/workspace_provider.dart';


// ── Data Model ───────────────────────────────────────────────

class CanvasSurface {
  final String surfaceId;
  final String action;
  final String html;

  const CanvasSurface({
    required this.surfaceId,
    required this.action,
    required this.html,
  });

  Map<String, dynamic> toJson() => {
    'surface_id': surfaceId,
    'action': action,
    'html': html,
  };

  factory CanvasSurface.fromJson(Map<String, dynamic> json) {
    return CanvasSurface(
      surfaceId: json['surface_id']?.toString() ?? '',
      action: json['action']?.toString() ?? '',
      html: json['html']?.toString() ?? '',
    );
  }
}

class CanvasState {
  final Map<String, List<CanvasSurface>> _surfacesByWs;
  final String activeWorkspaceId;
  final String? lastAction;

  const CanvasState({
    Map<String, List<CanvasSurface>>? surfacesByWs,
    this.activeWorkspaceId = 'personal',
    this.lastAction,
  }) : _surfacesByWs = surfacesByWs ?? const {};

  List<CanvasSurface> get surfaces =>
      _surfacesByWs[activeWorkspaceId] ?? const [];

  Map<String, List<CanvasSurface>> get allSurfaces =>
      Map.unmodifiable(_surfacesByWs);
}

// ── Persistence ──────────────────────────────────────────────

const _kStorageKey = 'canvas_surfaces';

Future<Map<String, List<CanvasSurface>>> _loadSurfaces() async {
  final prefs = await SharedPreferences.getInstance();
  final raw = prefs.getString(_kStorageKey);
  if (raw == null || raw.isEmpty) return {};
  try {
    final decoded = jsonDecode(raw) as Map<String, dynamic>;
    final result = <String, List<CanvasSurface>>{};
    for (final entry in decoded.entries) {
      result[entry.key] = (entry.value as List)
          .map((e) => CanvasSurface.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return result;
  } catch (_) {
    return {};
  }
}

Future<void> _saveSurfaces(Map<String, List<CanvasSurface>> data) async {
  final prefs = await SharedPreferences.getInstance();
  final encoded = data.map(
    (k, v) => MapEntry(k, v.map((s) => s.toJson()).toList()),
  );
  await prefs.setString(_kStorageKey, jsonEncode(encoded));
}

// ── Provider ─────────────────────────────────────────────────

class CanvasProvider extends StateNotifier<CanvasState> {
  CanvasProvider() : super(const CanvasState()) {
    _init();
  }

  Future<void> _init() async {
    final loaded = await _loadSurfaces();
    if (loaded.isNotEmpty) {
      state = CanvasState(surfacesByWs: loaded, activeWorkspaceId: 'personal');
    }
  }

  void _persist() {
    _saveSurfaces(state._surfacesByWs);
  }

  void setActiveWorkspace(String id) {
    state = CanvasState(
      surfacesByWs: state._surfacesByWs,
      activeWorkspaceId: id,
      lastAction: state.lastAction,
    );
  }

  void destroyTutorialSurfaces() {
    final wsId = state.activeWorkspaceId;
    final updated = Map<String, List<CanvasSurface>>.from(state._surfacesByWs);
    final surfaces = List<CanvasSurface>.from(updated[wsId] ?? [])
      ..removeWhere((s) => s.surfaceId.startsWith('learn_'));
    updated[wsId] = surfaces;
    state = CanvasState(
      surfacesByWs: updated,
      activeWorkspaceId: state.activeWorkspaceId,
      lastAction: 'destroy',
    );
    _persist();
  }

  void onCanvasUpdate(Map<String, dynamic> event, {String? workspaceId}) {
    final wsId = workspaceId ?? event['workspace_id']?.toString() ?? 'personal';
    final action = event['action'] as String? ?? 'create';
    final surfaceId = event['surface_id'] as String? ?? '';
    final html = event['html'] as String? ?? '';

    final updated = Map<String, List<CanvasSurface>>.from(state._surfacesByWs);
    var surfaces = List<CanvasSurface>.from(updated[wsId] ?? []);

    if (action == 'destroy') {
      surfaces.removeWhere((s) => s.surfaceId == surfaceId);
    } else if (action == 'update') {
      final idx = surfaces.indexWhere((s) => s.surfaceId == surfaceId);
      if (idx >= 0) {
        surfaces[idx] = CanvasSurface(
          surfaceId: surfaceId,
          action: action,
          html: html,
        );
      }
    } else {
      surfaces.removeWhere((s) => s.surfaceId == surfaceId);
      surfaces.add(CanvasSurface(
        surfaceId: surfaceId,
        action: action,
        html: html,
      ));
    }

    updated[wsId] = surfaces;

    state = CanvasState(
      surfacesByWs: updated,
      activeWorkspaceId: state.activeWorkspaceId,
      lastAction: action,
    );
    _persist();
  }
}

final canvasProvider =
    StateNotifierProvider<CanvasProvider, CanvasState>((ref) => CanvasProvider());

// ── Widget ───────────────────────────────────────────────────

class CanvasTab extends ConsumerStatefulWidget {
  const CanvasTab({super.key});

  @override
  ConsumerState<CanvasTab> createState() => _CanvasTabState();
}

class _CanvasTabState extends ConsumerState<CanvasTab> {
  final Map<String, WebViewController> _controllers = {};

  @override
  void dispose() {
    _controllers.clear();
    super.dispose();
  }

  String _htmlFor(CanvasSurface surface) {
    return '''
      <html>
      <head>$_baseStyle</head>
      <body>
        ${surface.html}
        <script>
          function postMessage(data) {
            canvasBridge.postMessage(JSON.stringify(data));
          }
        </script>
        <script>
        (function() {
          function track(type, data) {
            try {
              uiBridge.postMessage(JSON.stringify({type: type, ...data}));
            } catch(e) {}
          }
          document.addEventListener('click', function(e) {
            var el = e.target;
            track('click', {
              target: el.id || (el.textContent ? el.textContent.trim().slice(0, 50) : '') || el.tagName,
              tag: el.tagName.toLowerCase()
            });
          }, true);
          document.addEventListener('mouseup', function() {
            var sel = window.getSelection();
            if (sel && sel.toString().trim()) {
              track('select', {text: sel.toString().trim().slice(0, 200)});
            }
          });
          document.addEventListener('focus', function(e) {
            var el = e.target;
            if (el.tagName.match(/^(INPUT|TEXTAREA|SELECT)\$/i)) {
              track('focus', {target: el.id || el.name || el.placeholder || 'field'});
            }
          }, true);
          document.addEventListener('blur', function(e) {
            var el = e.target;
            if (el.tagName.match(/^(INPUT|TEXTAREA|SELECT)\$/i)) {
              track('blur', {target: el.id || el.name || 'field', value: (el.value || '').slice(0, 100)});
            }
          }, true);
          var scrollTimer;
          document.addEventListener('scroll', function() {
            clearTimeout(scrollTimer);
            scrollTimer = setTimeout(function() {
              track('scroll', {scrollTop: window.scrollY, scrollHeight: document.body.scrollHeight});
            }, 300);
          }, true);
          document.addEventListener('dragstart', function(e) {
            track('drag_start', {target: e.target.id || (e.target.textContent ? e.target.textContent.trim().slice(0, 50) : '')});
          }, true);
          document.addEventListener('drop', function(e) {
            track('drop', {target: e.target.id || (e.target.textContent ? e.target.textContent.trim().slice(0, 50) : '')});
          }, true);
        })();
        </script>
      </body>
      </html>
    ''';
  }

  static const _baseStyle = '''
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
      :root {
        --primary: #3b82f6; --bg: #1e1e2e;
        --text: #cdd6f4; --border: #45475a;
      }
      body {
        margin: 0; padding: 16px;
        font-family: system-ui, -apple-system, sans-serif;
        color: var(--text); background: var(--bg);
      }
      input, textarea, select {
        background: #313244; color: var(--text);
        border: 1px solid var(--border); border-radius: 6px;
        padding: 8px; font-size: 14px; width: 100%;
        margin-bottom: 8px; box-sizing: border-box;
      }
      button {
        background: var(--primary); color: white;
        border: none; border-radius: 6px; padding: 10px 16px;
        cursor: pointer;
      }
      h2 { margin-top: 0; }
    </style>
  ''';

  void _onInteractionEvent(String message) {
    try {
      final data = jsonDecode(message) as Map<String, dynamic>;
      ref.read(apiClientProvider).post('/ui/track', {
        'user_id': ref.read(userIdProvider),
        'tab': 'canvas',
        'event': data,
      });
    } catch (_) {}
  }

  void _onCanvasAction(String message) {
    try {
      final data = jsonDecode(message) as Map<String, dynamic>;
      final action = data['action'] as String? ?? '';
      final ref = this.ref;

      if (action == 'save' && data.containsKey('fields')) {
        final fields = Map<String, String>.from(
          (data['fields'] as Map).map((k, v) => MapEntry(k, v.toString())),
        );
        final desc = fields.entries
            .map((e) => '  ${e.key}: ${e.value}')
            .join('\n');
        final msg = '[Canvas submit] ${data['form'] ?? 'form'}:\n$desc\n\nCreate this.';

        ref.read(agentProvider.notifier).sendMessage(msg);
      } else if (action == 'cancel') {
        final msg = '[Canvas] User cancelled the form.';
        ref.read(agentProvider.notifier).sendMessage(msg);
      } else {
        final msg = '[Canvas] ${jsonEncode(data)}';
        ref.read(agentProvider.notifier).sendMessage(msg);
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(canvasProvider);
    final surfaces = state.surfaces;
    final wsId = ref.watch(currentWorkspaceIdProvider);

    if (surfaces.isNotEmpty) {
      final allKeys = <String>{};
      for (final ws in state.allSurfaces.entries) {
        for (final s in ws.value) {
          final key = '${ws.key}::${s.surfaceId}';
          allKeys.add(key);
          _ensureController(s, key);
        }
      }

      final activeKeys = surfaces.map((s) => '$wsId::${s.surfaceId}').toSet();
      _controllers.keys.where((k) => !allKeys.contains(k)).toList().forEach(_controllers.remove);

      return Stack(
        children: [
          for (final entry in state.allSurfaces.entries)
            for (final s in entry.value)
              _webViewFor(s, '${entry.key}::${s.surfaceId}', activeKeys),
        ],
      );
    }

    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Symbols.dashboard, size: 48,
              color: context.tokens.colors.textTertiary),
          const SizedBox(height: 16),
          Text('Agent-generated content appears here',
              style: context.tokens.typography.textTheme.bodySmall),
        ],
      ),
    );
  }

  Widget _webViewFor(CanvasSurface surface, String cacheKey, Set<String> activeKeys) {
    _ensureController(surface, cacheKey);
    return Offstage(
      offstage: !activeKeys.contains(cacheKey),
      child: SizedBox.expand(
        child: Container(
          color: const Color(0xFF1e1e2e),
          child: WebViewWidget(controller: _controllers[cacheKey]!),
        ),
      ),
    );
  }

  void _ensureController(CanvasSurface surface, String cacheKey) {
    final existing = _controllers[cacheKey];
    if (existing != null) {
      if (surface.action == 'update') {
        existing.loadHtmlString(_htmlFor(surface));
      }
      return;
    }
    final controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..addJavaScriptChannel(
        'canvasBridge',
        onMessageReceived: (msg) => _onCanvasAction(msg.message),
      )
      ..addJavaScriptChannel(
        'uiBridge',
        onMessageReceived: (msg) => _onInteractionEvent(msg.message),
      )
      ..loadHtmlString(_htmlFor(surface));
    _controllers[cacheKey] = controller;
  }
}
