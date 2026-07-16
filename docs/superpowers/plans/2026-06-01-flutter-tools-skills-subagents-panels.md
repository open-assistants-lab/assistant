# Tools, Skills & Subagents Sidebar Panels — Flutter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Tools, Skills, and Subagents as sidebar items with full panels (scope-switchable between User/Workspace), plus a Tools tab in the Workspace panel. Capabilities toggle support via backend API.

**Architecture:** Three new sidebar items in `DesktopSidebarItem`. Each opens a full panel screen routed through `GoRouter`. Panels share a `ScopeSwitcher` widget and use Riverpod providers for state. Workspace panel gets a 4th "Tools" tab as a filtered convenience view. All data comes from backend APIs: `GET /tools`, `PATCH /tools/:name`, `GET /capabilities`.

**Tech Stack:** Flutter 3.x, Dart, Riverpod, GoRouter, Material Symbols, HTTP

---

## File Structure

**Create:**
- `flutter_app/lib/features/tools/tools_panel.dart` — tools sidebar panel (full, with scope)
- `flutter_app/lib/features/tools/tools_provider.dart` — tools state + API calls
- `flutter_app/lib/features/tools/tools_workspace_tab.dart` — tools tab within workspace
- `flutter_app/lib/features/skills/skills_sidebar_panel.dart` — skills sidebar panel
- `flutter_app/lib/features/subagents/subagents_sidebar_panel.dart` — subagents sidebar panel
- `flutter_app/lib/widgets/scope_switcher.dart` — User/Workspace toggle widget

**Modify:**
- `flutter_app/lib/core/layout/desktop_layout.dart` — add Tools/Skills/Subagents sidebar items
- `flutter_app/lib/core/router/app_router.dart` — add routes for panels
- `flutter_app/lib/features/workspace/workspace_panel.dart` — add Tools tab

---

### Task 1: Add Tools, Skills, Subagents to DesktopSidebarItem

**Files:**
- Modify: `flutter_app/lib/core/layout/desktop_layout.dart:18-56`

- [ ] **Step 1: Add three new enum values with Material Symbols icons**

```dart
// Add after 'connectors' enum value (line 32):
  tools(
    icon: Symbols.handyman,
    activeIcon: Symbols.handyman,
    label: 'Tools',
    path: '/tools',
  ),
  skills(
    icon: Symbols.psychology,
    activeIcon: Symbols.psychology,
    label: 'Skills',
    path: '/skills',
  ),
  subagents(
    icon: Symbols.robot_2,
    activeIcon: Symbols.robot_2,
    label: 'Subagents',
    path: '/subagents',
  ),
```

- [ ] **Step 2: Add sidebar items to the bottom section of _Sidebar**

```dart
// Add inside the bottom Column in _Sidebar, before connectors:
// Tools
Padding(
  padding: EdgeInsets.symmetric(
    horizontal: tokens.spacing.md,
    vertical: tokens.spacing.xs,
  ),
  child: _SidebarItem(
    item: DesktopSidebarItem.tools,
    selected: false,
    onTap: () => context.go('/tools'),
  ),
),
// Skills
Padding(
  padding: EdgeInsets.symmetric(
    horizontal: tokens.spacing.md,
    vertical: tokens.spacing.xs,
  ),
  child: _SidebarItem(
    item: DesktopSidebarItem.skills,
    selected: false,
    onTap: () => context.go('/skills'),
  ),
),
// Subagents
Padding(
  padding: EdgeInsets.symmetric(
    horizontal: tokens.spacing.md,
    vertical: tokens.spacing.xs,
  ),
  child: _SidebarItem(
    item: DesktopSidebarItem.subagents,
    selected: false,
    onTap: () => context.go('/subagents'),
  ),
),
```

- [ ] **Step 3: Mark active item from router location**

In `_Sidebar.build()`, read current route:

```dart
final location = GoRouterState.of(context).uri.toString();
```

Then pass `selected` to each `_SidebarItem` based on `location.startsWith(item.path)`.

- [ ] **Step 4: Hot reload and verify sidebar items appear**

```bash
cd flutter_app && flutter run
```

Expected: 3 new sidebar items appear below Workspace list. Tapping navigates to `/tools`, `/skills`, `/subagents` (empty placeholder screens).

- [ ] **Step 5: Commit**

```bash
git add flutter_app/lib/core/layout/desktop_layout.dart
git commit -m "feat: add Tools, Skills, Subagents sidebar items"
```

---

### Task 2: Add GoRouter routes for panels

**Files:**
- Modify: `flutter_app/lib/core/router/app_router.dart`

- [ ] **Step 1: Add route entries for tools, skills, subagents**

```dart
// Inside ShellRoute children, add after workspace route:
GoRoute(
  path: '/tools',
  pageBuilder: (context, state) => CustomTransitionPage(
    key: state.pageKey,
    child: const ToolsPanel(),
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(opacity: animation, child: child);
    },
  ),
),
GoRoute(
  path: '/skills',
  pageBuilder: (context, state) => CustomTransitionPage(
    key: state.pageKey,
    child: const SkillsSidebarPanel(),
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(opacity: animation, child: child);
    },
  ),
),
GoRoute(
  path: '/subagents',
  pageBuilder: (context, state) => CustomTransitionPage(
    key: state.pageKey,
    child: const SubagentsSidebarPanel(),
    transitionsBuilder: (context, animation, secondaryAnimation, child) {
      return FadeTransition(opacity: animation, child: child);
    },
  ),
),
```

- [ ] **Step 2: Create placeholder screens so routes resolve**

```dart
// Create minimal placeholder files:
// flutter_app/lib/features/tools/tools_panel.dart
import 'package:flutter/material.dart';
class ToolsPanel extends StatelessWidget {
  const ToolsPanel({super.key});
  @override
  Widget build(BuildContext context) => const Center(child: Text('Tools'));
}

// flutter_app/lib/features/skills/skills_sidebar_panel.dart
import 'package:flutter/material.dart';
class SkillsSidebarPanel extends StatelessWidget {
  const SkillsSidebarPanel({super.key});
  @override
  Widget build(BuildContext context) => const Center(child: Text('Skills'));
}

// flutter_app/lib/features/subagents/subagents_sidebar_panel.dart
import 'package:flutter/material.dart';
class SubagentsSidebarPanel extends StatelessWidget {
  const SubagentsSidebarPanel({super.key});
  @override
  Widget build(BuildContext context) => const Center(child: Text('Subagents'));
}
```

- [ ] **Step 3: Hot reload and verify navigation works**

Expected: Tapping sidebar items shows placeholder text in the content panel area.

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/core/router/app_router.dart flutter_app/lib/features/tools/ flutter_app/lib/features/skills/ flutter_app/lib/features/subagents/
git commit -m "feat: add GoRouter routes for Tools, Skills, Subagents panels"
```

---

### Task 3: Create ScopeSwitcher widget

**Files:**
- Create: `flutter_app/lib/widgets/scope_switcher.dart`

- [ ] **Step 1: Write ScopeSwitcher widget**

```dart
// flutter_app/lib/widgets/scope_switcher.dart
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

enum CapabilityScope { user, workspace }

class ScopeSwitcher extends StatelessWidget {
  final CapabilityScope scope;
  final ValueChanged<CapabilityScope> onChanged;

  const ScopeSwitcher({
    super.key,
    required this.scope,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return SegmentedButton<CapabilityScope>(
      segments: const [
        ButtonSegment(
          value: CapabilityScope.user,
          label: Text('User'),
          icon: Icon(Symbols.person, size: 16),
        ),
        ButtonSegment(
          value: CapabilityScope.workspace,
          label: Text('Workspace'),
          icon: Icon(Symbols.folder, size: 16),
        ),
      ],
      selected: {scope},
      onSelectionChanged: (selected) => onChanged(selected.first),
      style: SegmentedButton.styleFrom(
        backgroundColor: tokens.colors.bgSurface,
        selectedBackgroundColor: tokens.colors.accent.withAlpha(30),
        selectedForegroundColor: tokens.colors.accent,
      ),
    );
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add flutter_app/lib/widgets/scope_switcher.dart
git commit -m "feat: add ScopeSwitcher widget (User/Workspace toggle)"
```

---

### Task 4: Create ToolsProvider (Riverpod state + API)

**Files:**
- Create: `flutter_app/lib/features/tools/tools_provider.dart`

- [ ] **Step 1: Write ToolsProvider**

```dart
// flutter_app/lib/features/tools/tools_provider.dart
import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../providers/agent_provider.dart';
import '../../providers/workspace_provider.dart';

// Tool model
class ToolItem {
  final String name;
  final String description;
  final String category;
  final Map<String, dynamic> annotations;
  final Map<String, dynamic> parameters;
  final bool enabled;
  final String source;

  const ToolItem({
    required this.name,
    required this.description,
    required this.category,
    required this.annotations,
    required this.parameters,
    required this.enabled,
    required this.source,
  });

  factory ToolItem.fromJson(Map<String, dynamic> json) => ToolItem(
    name: json['name'] ?? '',
    description: json['description'] ?? '',
    category: json['category'] ?? 'core',
    annotations: Map<String, dynamic>.from(json['annotations'] ?? {}),
    parameters: Map<String, dynamic>.from(json['parameters'] ?? {}),
    enabled: json['enabled'] ?? true,
    source: json['source'] ?? 'native',
  );

  bool get isDestructive => annotations['destructive'] == true;
  bool get isReadOnly => annotations['read_only'] == true;
}

// Category summary
class CategorySummary {
  final int count;
  final int enabled;
  const CategorySummary({required this.count, required this.enabled});
}

// State
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
      t.description.toLowerCase().contains(q)
    ).toList();
  }

  int get totalEnabled => tools.where((t) => t.enabled).length;
}

// Notifier
class ToolsNotifier extends StateNotifier<ToolsState> {
  final http.Client _client;
  ToolsNotifier(this._client) : super(const ToolsState());

  Future<void> loadTools({
    required String host,
    required String userId,
    required String workspaceId,
  }) async {
    state = state._copyWith(loading: true, error: null);
    try {
      final uri = Uri.parse(
        'http://$host/tools?user_id=$userId&workspace_id=$workspaceId',
      );
      final response = await _client.get(uri);
      if (response.statusCode != 200) throw Exception('${response.statusCode}');
      final data = jsonDecode(response.body);
      final tools = (data['tools'] as List).map((t) => ToolItem.fromJson(t)).toList();
      final cats = <String, CategorySummary>{};
      for (final cat in (data['categories'] as Map<String, dynamic>).entries) {
        cats[cat.key] = CategorySummary(
          count: cat.value['count'] ?? 0,
          enabled: cat.value['enabled'] ?? 0,
        );
      }
      state = state._copyWith(tools: tools, categories: cats, loading: false);
    } catch (e) {
      state = state._copyWith(loading: false, error: e.toString());
    }
  }

  void setSearch(String query) {
    state = state._copyWith(searchQuery: query);
  }

  Future<void> toggleTool({
    required String host,
    required String userId,
    required String workspaceId,
    required String toolName,
    required bool enabled,
  }) async {
    // Optimistic update
    final updated = state.tools.map((t) =>
      t.name == toolName ? ToolItem(
        name: t.name, description: t.description, category: t.category,
        annotations: t.annotations, parameters: t.parameters,
        enabled: enabled, source: t.source,
      ) : t
    ).toList();
    state = state._copyWith(tools: updated);

    try {
      final uri = Uri.parse(
        'http://$host/tools/$toolName?user_id=$userId&workspace_id=$workspaceId',
      );
      await _client.patch(
        uri,
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'enabled': enabled}),
      );
    } catch (e) {
      // Revert on failure
      final reverted = state.tools.map((t) =>
        t.name == toolName ? ToolItem(
          name: t.name, description: t.description, category: t.category,
          annotations: t.annotations, parameters: t.parameters,
          enabled: !enabled, source: t.source,
        ) : t
      ).toList();
      state = state._copyWith(tools: reverted);
    }
  }
}

extension _ToolsStateCopy on ToolsState {
  ToolsState _copyWith({
    List<ToolItem>? tools,
    Map<String, CategorySummary>? categories,
    bool? loading,
    String? error,
    String? searchQuery,
  }) => ToolsState(
    tools: tools ?? this.tools,
    categories: categories ?? this.categories,
    loading: loading ?? this.loading,
    error: error ?? this.error,
    searchQuery: searchQuery ?? this.searchQuery,
  );
}

final toolsProvider = StateNotifierProvider<ToolsNotifier, ToolsState>((ref) {
  return ToolsNotifier(http.Client());
});
```

- [ ] **Step 2: Commit**

```bash
git add flutter_app/lib/features/tools/tools_provider.dart
git commit -m "feat: add ToolsProvider (Riverpod state + HTTP API)"
```

---

### Task 5: Build ToolsPanel (sidebar panel with scope)

**Files:**
- Modify: `flutter_app/lib/features/tools/tools_panel.dart` (replace placeholder)

- [ ] **Step 1: Write ToolsPanel**

```dart
// flutter_app/lib/features/tools/tools_panel.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../providers/workspace_provider.dart';
import '../../widgets/scope_switcher.dart';
import 'tools_provider.dart';

class ToolsPanel extends ConsumerStatefulWidget {
  const ToolsPanel({super.key});

  @override
  ConsumerState<ToolsPanel> createState() => _ToolsPanelState();
}

class _ToolsPanelState extends ConsumerState<ToolsPanel> {
  CapabilityScope _scope = CapabilityScope.workspace;
  final _searchController = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    final host = ref.read(hostProvider);
    final userId = ref.read(userIdProvider);
    final wsId = _scope == CapabilityScope.workspace
        ? ref.read(currentWorkspaceIdProvider)
        : 'personal';
    ref.read(toolsProvider.notifier).loadTools(
      host: host, userId: userId, workspaceId: wsId,
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
          // Header
          Padding(
            padding: EdgeInsets.all(tokens.spacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text('Tools', style: tokens.typography.textTheme.titleLarge
                        ?.copyWith(color: tokens.colors.textPrimary)),
                    const Spacer(),
                    Text('${state.totalEnabled} / ${tools.length} enabled',
                      style: tokens.typography.textTheme.labelSmall
                          ?.copyWith(color: tokens.colors.textTertiary)),
                  ],
                ),
                SizedBox(height: tokens.spacing.sm),
                ScopeSwitcher(
                  scope: _scope,
                  onChanged: (s) => setState(() { _scope = s; _load(); }),
                ),
                SizedBox(height: tokens.spacing.sm),
                TextField(
                  controller: _searchController,
                  decoration: const InputDecoration(
                    hintText: 'Search tools...',
                    prefixIcon: Icon(Symbols.search, size: 18),
                    isDense: true,
                  ),
                  onChanged: (v) => ref.read(toolsProvider.notifier).setSearch(v),
                ),
              ],
            ),
          ),
          // Tool list grouped by category
          Expanded(
            child: state.loading
                ? const Center(child: CircularProgressIndicator())
                : state.error != null
                    ? Center(child: Text('Error: ${state.error}', style: TextStyle(color: tokens.colors.textSecondary)))
                    : ListView.builder(
                        padding: EdgeInsets.symmetric(horizontal: tokens.spacing.md),
                        itemCount: sorted.length,
                        itemBuilder: (_, i) {
                          final cat = sorted[i].key;
                          final items = sorted[i].value;
                          final catSummary = state.categories[cat];
                          return _CategorySection(
                            title: cat.toUpperCase(),
                            count: '${catSummary?.enabled ?? 0} / ${catSummary?.count ?? items.length}',
                            tools: items,
                            tokens: tokens,
                            onToggle: (tool, enabled) {
                              final host = ref.read(hostProvider);
                              final userId = ref.read(userIdProvider);
                              final wsId = _scope == CapabilityScope.workspace
                                  ? ref.read(currentWorkspaceIdProvider)
                                  : 'personal';
                              ref.read(toolsProvider.notifier).toggleTool(
                                host: host, userId: userId, workspaceId: wsId,
                                toolName: tool.name, enabled: enabled,
                              );
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

class _CategorySection extends StatelessWidget {
  final String title;
  final String count;
  final List<ToolItem> tools;
  final AppTokens tokens;
  final Function(ToolItem, bool) onToggle;

  const _CategorySection({
    required this.title,
    required this.count,
    required this.tools,
    required this.tokens,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(height: tokens.spacing.sm),
        Row(
          children: [
            Text(title,
              style: tokens.typography.textTheme.labelSmall
                  ?.copyWith(color: tokens.colors.textTertiary)),
            const SizedBox(width: 8),
            Text(count,
              style: tokens.typography.textTheme.labelSmall
                  ?.copyWith(color: tokens.colors.textTertiary)),
          ],
        ),
        const SizedBox(height: 4),
        Container(
          decoration: BoxDecoration(
            color: tokens.colors.bgElevated,
            borderRadius: tokens.radius.smAll,
          ),
          child: Column(
            children: tools.map((tool) => _ToolRow(
              tool: tool,
              tokens: tokens,
              onToggle: (enabled) => onToggle(tool, enabled),
            )).toList(),
          ),
        ),
        SizedBox(height: tokens.spacing.sm),
      ],
    );
  }
}

class _ToolRow extends StatelessWidget {
  final ToolItem tool;
  final AppTokens tokens;
  final ValueChanged<bool> onToggle;

  const _ToolRow({
    required this.tool,
    required this.tokens,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: () {},
      child: Padding(
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
                      Text(tool.name,
                        style: tool.enabled
                            ? tokens.typography.textTheme.bodyMedium
                                ?.copyWith(color: tokens.colors.textPrimary)
                            : tokens.typography.textTheme.bodyMedium
                                ?.copyWith(color: tokens.colors.textTertiary)),
                      const SizedBox(width: 8),
                      if (tool.isReadOnly)
                        _AnnotationBadge(label: 'read-only', color: tokens.colors.accent, tokens: tokens),
                      if (tool.isDestructive)
                        _AnnotationBadge(label: 'destructive', color: const Color(0xFFE74C3C), tokens: tokens),
                    ],
                  ),
                  Text(tool.description,
                    style: tokens.typography.textTheme.labelSmall
                        ?.copyWith(color: tokens.colors.textTertiary),
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                ],
              ),
            ),
            Switch(
              value: tool.enabled,
              onChanged: onToggle,
              activeColor: tokens.colors.accent,
            ),
          ],
        ),
      ),
    );
  }
}

class _AnnotationBadge extends StatelessWidget {
  final String label;
  final Color color;
  final AppTokens tokens;

  const _AnnotationBadge({
    required this.label,
    required this.color,
    required this.tokens,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: 4, vertical: 1),
      decoration: BoxDecoration(
        border: Border.all(color: color.withAlpha(120)),
        borderRadius: tokens.radius.smAll,
      ),
      child: Text(label,
        style: tokens.typography.textTheme.labelSmall
            ?.copyWith(color: color, fontSize: 9)),
    );
  }
}
```

- [ ] **Step 2: Hot reload and verify Tools panel works**

Expected: Shows categories, toggle switches work, search filters, scope switcher changes loaded data.

- [ ] **Step 3: Commit**

```bash
git add flutter_app/lib/features/tools/tools_panel.dart
git commit -m "feat: build ToolsPanel with search, categories, toggles, scope switcher"
```

---

### Task 6: Add Tools tab in WorkspacePanel

**Files:**
- Create: `flutter_app/lib/features/tools/tools_workspace_tab.dart`
- Modify: `flutter_app/lib/features/workspace/workspace_panel.dart`

- [ ] **Step 1: Write ToolsWorkspaceTab**

```dart
// flutter_app/lib/features/tools/tools_workspace_tab.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../providers/workspace_provider.dart';
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
    _load();
  }

  void _load() {
    final host = ref.read(hostProvider);
    final userId = ref.read(userIdProvider);
    final wsId = ref.read(currentWorkspaceIdProvider);
    ref.read(toolsProvider.notifier).loadTools(
      host: host, userId: userId, workspaceId: wsId,
    );
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final state = ref.watch(toolsProvider);
    final tools = state.tools;

    if (state.loading) return const Center(child: CircularProgressIndicator());

    final enabled = tools.where((t) => t.enabled).toList();
    final disabled = tools.where((t) => !t.enabled).toList();

    return ListView(
      padding: EdgeInsets.all(tokens.spacing.md),
      children: [
        Text('Enabled (${enabled.length})',
          style: tokens.typography.textTheme.labelSmall
              ?.copyWith(color: tokens.colors.textTertiary)),
        const SizedBox(height: 4),
        ...enabled.map((t) => _compactRow(t, tokens)),
        const SizedBox(height: 16),
        Text('Disabled (${disabled.length})',
          style: tokens.typography.textTheme.labelSmall
              ?.copyWith(color: tokens.colors.textTertiary)),
        const SizedBox(height: 4),
        ...disabled.map((t) => _compactRow(t, tokens)),
      ],
    );
  }

  Widget _compactRow(ToolItem tool, AppTokens tokens) {
    return Padding(
      padding: EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Icon(
            tool.enabled ? Symbols.check_circle : Symbols.cancel,
            size: 14,
            color: tool.enabled ? tokens.colors.accent : tokens.colors.textTertiary,
          ),
          const SizedBox(width: 8),
          Text(tool.name,
            style: tokens.typography.textTheme.bodySmall
                ?.copyWith(color: tool.enabled ? tokens.colors.textPrimary : tokens.colors.textTertiary)),
          const Spacer(),
          if (tool.isDestructive)
            Text('⚠', style: TextStyle(color: const Color(0xFFE74C3C), fontSize: 12)),
        ],
      ),
    );
  }
}
```

- [ ] **Step 2: Add Tools tab to WorkspacePanel**

In `flutter_app/lib/features/workspace/workspace_panel.dart`:

Add `tools` to the `_WorkspacePanelTab` enum:
```dart
enum _WorkspacePanelTab { files, skills, subagents, tools }
```

Add tab builder case:
```dart
case _WorkspacePanelTab.tools:
  return const ToolsWorkspaceTab();
```

Add tab button in the header row:
```dart
_TabButton(
  label: 'Tools',
  selected: _tab == _WorkspacePanelTab.tools,
  onTap: () => setState(() => _tab = _WorkspacePanelTab.tools),
),
```

- [ ] **Step 3: Hot reload and verify Workspace Tools tab**

Expected: 4 tabs in workspace panel. "Tools" tab shows enabled/disabled tools for current workspace.

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/features/tools/tools_workspace_tab.dart flutter_app/lib/features/workspace/workspace_panel.dart
git commit -m "feat: add Tools tab to WorkspacePanel"
```

---

### Task 7: Skills & Subagents sidebar panels (placeholders with scope)

**Files:**
- Modify: `flutter_app/lib/features/skills/skills_sidebar_panel.dart` (replace placeholder)
- Modify: `flutter_app/lib/features/subagents/subagents_sidebar_panel.dart` (replace placeholder)

- [ ] **Step 1: Write SkillsSidebarPanel with scope switcher**

```dart
// flutter_app/lib/features/skills/skills_sidebar_panel.dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../widgets/scope_switcher.dart';

class SkillsSidebarPanel extends ConsumerStatefulWidget {
  const SkillsSidebarPanel({super.key});

  @override
  ConsumerState<SkillsSidebarPanel> createState() => _SkillsSidebarPanelState();
}

class _SkillsSidebarPanelState extends ConsumerState<SkillsSidebarPanel> {
  CapabilityScope _scope = CapabilityScope.workspace;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return Container(
      color: tokens.colors.bgCanvas,
      child: Column(
        children: [
          Padding(
            padding: EdgeInsets.all(tokens.spacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Skills',
                  style: tokens.typography.textTheme.titleLarge
                      ?.copyWith(color: tokens.colors.textPrimary)),
                SizedBox(height: tokens.spacing.sm),
                ScopeSwitcher(
                  scope: _scope,
                  onChanged: (s) => setState(() => _scope = s),
                ),
              ],
            ),
          ),
          Expanded(
            child: Center(
              child: Text('Skill management coming soon',
                style: tokens.typography.textTheme.bodyMedium
                    ?.copyWith(color: tokens.colors.textTertiary)),
            ),
          ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 2: Write SubagentsSidebarPanel with scope switcher**

Same pattern — replace with scope-enabled placeholder.

- [ ] **Step 3: Hot reload and verify**

Expected: Skills/Subagents sidebar panels show with scope switcher, placeholder content.

- [ ] **Step 4: Commit**

```bash
git add flutter_app/lib/features/skills/skills_sidebar_panel.dart flutter_app/lib/features/subagents/subagents_sidebar_panel.dart
git commit -m "feat: Skills and Subagents sidebar panels with scope switcher"
```

---

### Task 8: Full integration test (manual)

- [ ] **Step 1: Start Assistant backend and Flutter app**

```bash
uv run assistant http &
cd flutter_app && flutter run -d macos
```

- [ ] **Step 2: Verify Tools panel**

- Toggle a tool on/off — verify `PATCH /tools/:name` is called
- Switch scope to User — verify tools reload
- Search for "files" — verify filtered list

- [ ] **Step 3: Verify Workspace Tools tab**

- Open workspace, click Tools tab — verify enabled/disabled list
- Toggle in sidebar panel, switch to workspace tab — verify change reflected

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: integration verification — tools management working end-to-end"
```
