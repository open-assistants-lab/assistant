import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:webview_flutter/webview_flutter.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';

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
  final List<CanvasSurface> surfaces;
  final String? lastAction;

  const CanvasState({
    this.surfaces = const [],
    this.lastAction,
  });
}

// ── Persistence ──────────────────────────────────────────────

const _kStorageKey = 'canvas_surfaces';

Future<List<CanvasSurface>> _loadSurfaces() async {
  final prefs = await SharedPreferences.getInstance();
  final raw = prefs.getString(_kStorageKey);
  if (raw == null || raw.isEmpty) return [];
  try {
    final decoded = jsonDecode(raw) as List;
    return decoded
        .map((e) => CanvasSurface.fromJson(e as Map<String, dynamic>))
        .toList();
  } catch (_) {
    return [];
  }
}

Future<void> _saveSurfaces(List<CanvasSurface> data) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.setString(
    _kStorageKey,
    jsonEncode(data.map((s) => s.toJson()).toList()),
  );
}

// ── Provider ─────────────────────────────────────────────────

class CanvasProvider extends StateNotifier<CanvasState> {
  CanvasProvider() : super(const CanvasState()) {
    _init();
  }

  Future<void> _init() async {
    final loaded = await _loadSurfaces();
    if (loaded.isNotEmpty) {
      state = CanvasState(surfaces: loaded);
    }
  }

  void _persist() {
    _saveSurfaces(state.surfaces);
  }

  void destroyTutorialSurfaces() {
    final surfaces = state.surfaces
        .where((s) => !s.surfaceId.startsWith('learn_'))
        .toList();
    state = CanvasState(surfaces: surfaces, lastAction: 'destroy');
    _persist();
  }

  void onCanvasUpdate(Map<String, dynamic> event) {
    final action = event['action'] as String? ?? 'create';
    final surfaceId = event['surface_id'] as String? ?? '';
    final html = event['html'] as String? ?? '';

    var surfaces = List<CanvasSurface>.from(state.surfaces);

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

    state = CanvasState(surfaces: surfaces, lastAction: action);
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

    if (surfaces.isNotEmpty) {
      final allKeys = <String>{};
      for (final s in surfaces) {
        final key = s.surfaceId;
        allKeys.add(key);
        _ensureController(s, key);
      }

      _controllers.keys.where((k) => !allKeys.contains(k)).toList().forEach(_controllers.remove);

      return Stack(
        children: [
          for (final s in surfaces)
            _webViewFor(s, s.surfaceId),
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

  Widget _webViewFor(CanvasSurface surface, String cacheKey) {
    _ensureController(surface, cacheKey);
    return SizedBox.expand(
      child: Container(
        color: const Color(0xFF1e1e2e),
        child: WebViewWidget(controller: _controllers[cacheKey]!),
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
      ..loadHtmlString(_htmlFor(surface));
    _controllers[cacheKey] = controller;
  }
}
