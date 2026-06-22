import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

class ToolItem {
  final String name;
  final String description;
  final String category;
  final Map<String, dynamic> annotations;
  final Map<String, dynamic> parameters;
  final bool enabled;
  final String source;
  final String scope;
  final List<String> workspaceIds;

  const ToolItem({
    required this.name,
    required this.description,
    required this.category,
    required this.annotations,
    required this.parameters,
    required this.enabled,
    required this.source,
    this.scope = 'all',
    this.workspaceIds = const [],
  });

  factory ToolItem.fromJson(Map<String, dynamic> json) => ToolItem(
        name: json['name'] ?? '',
        description: json['description'] ?? '',
        category: json['category'] ?? 'core',
        annotations: Map<String, dynamic>.from(json['annotations'] ?? {}),
        parameters: Map<String, dynamic>.from(json['parameters'] ?? {}),
        enabled: json['enabled'] ?? true,
        source: json['source'] ?? 'native',
        scope: json['scope'] ?? 'all',
        workspaceIds: (json['workspace_ids'] as List?)
                ?.map((e) => e.toString())
                .toList() ??
            [],
      );

  bool get isDestructive => annotations['destructive'] == true;
  bool get isReadOnly => annotations['read_only'] == true;
}

class CategorySummary {
  final int count;
  final int enabled;
  const CategorySummary({required this.count, required this.enabled});
}

class ToolsState {
  final List<ToolItem> tools;
  final Map<String, CategorySummary> categories;
  final bool loading;
  final String? error;
  final String searchQuery;

  const ToolsState({
    this.tools = const [],
    this.categories = const {},
    this.loading = false,
    this.error,
    this.searchQuery = '',
  });

  List<ToolItem> get filteredTools {
    if (searchQuery.isEmpty) return tools;
    final q = searchQuery.toLowerCase();
    return tools.where((t) =>
        t.name.toLowerCase().contains(q) ||
        t.description.toLowerCase().contains(q)).toList();
  }

  int get totalEnabled => tools.where((t) => t.enabled).length;
}

class ToolsNotifier extends StateNotifier<ToolsState> {
  final http.Client _client;

  ToolsNotifier(this._client) : super(const ToolsState());

  Future<void> loadTools({
    required String host,
    required String userId,
  }) async {
    state = _copyWith(state, loading: true, error: null);
    try {
      final uri = Uri.parse(
        'http://$host/tools?user_id=$userId',
      );
      final response = await _client.get(uri);
      if (response.statusCode != 200) {
        throw Exception('${response.statusCode}');
      }
      final data = jsonDecode(response.body);
      final tools = (data['tools'] as List)
          .map((t) => ToolItem.fromJson(t))
          .toList();
      final cats = <String, CategorySummary>{};
      for (final cat in (data['categories'] as Map<String, dynamic>).entries) {
        cats[cat.key] = CategorySummary(
          count: (cat.value['count'] as num?)?.toInt() ?? 0,
          enabled: (cat.value['enabled'] as num?)?.toInt() ?? 0,
        );
      }
      state = _copyWith(state, tools: tools, categories: cats, loading: false);
    } catch (e) {
      state = _copyWith(state, loading: false, error: e.toString());
    }
  }

  void setSearch(String query) {
    state = _copyWith(state, searchQuery: query);
  }

  Future<void> toggleTool({
    required String host,
    required String userId,
    required String toolName,
    required bool enabled,
  }) async {
    // Optimistic update
    final updated = state.tools.map((t) {
      if (t.name == toolName) {
        return ToolItem(
          name: t.name, description: t.description, category: t.category,
          annotations: t.annotations, parameters: t.parameters,
          enabled: enabled, source: t.source,
          scope: t.scope, workspaceIds: t.workspaceIds,
        );
      }
      return t;
    }).toList();
    state = _copyWith(state, tools: updated);

    try {
      final uri = Uri.parse(
        'http://$host/tools/$toolName?user_id=$userId',
      );
      await _client.patch(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'enabled': enabled}),
      );
    } catch (_) {
      // Revert on failure
      final reverted = state.tools.map((t) {
        if (t.name == toolName) {
          return ToolItem(
            name: t.name, description: t.description, category: t.category,
            annotations: t.annotations, parameters: t.parameters,
            enabled: !enabled, source: t.source,
            scope: t.scope, workspaceIds: t.workspaceIds,
          );
        }
        return t;
      }).toList();
      state = _copyWith(state, tools: reverted);
    }
  }

}

ToolsState _copyWith(
  ToolsState s, {
  List<ToolItem>? tools,
  Map<String, CategorySummary>? categories,
  bool? loading,
  String? error,
  String? searchQuery,
}) {
  return ToolsState(
    tools: tools ?? s.tools,
    categories: categories ?? s.categories,
    loading: loading ?? s.loading,
    error: error ?? s.error,
    searchQuery: searchQuery ?? s.searchQuery,
  );
}

final toolsProvider =
    StateNotifierProvider<ToolsNotifier, ToolsState>((ref) {
  return ToolsNotifier(http.Client());
});
