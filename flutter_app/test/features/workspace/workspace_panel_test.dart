import 'dart:async';
import 'dart:convert';

import 'package:executive_assistant/features/tools/tools_provider.dart';
import 'package:executive_assistant/features/workspace/subagents_panel.dart';
import 'package:executive_assistant/features/workspace/workspace_panel.dart';
import 'package:executive_assistant/features/workspace/skills_panel.dart';
import 'package:executive_assistant/models/message.dart';
import 'package:executive_assistant/models/subagent.dart';
import 'package:executive_assistant/providers/agent_provider.dart';
import 'package:executive_assistant/providers/subagent_provider.dart';
import 'package:executive_assistant/providers/workspace_provider.dart';
import 'package:executive_assistant/services/api_client.dart';
import 'package:executive_assistant/services/backend_service.dart';
import 'package:executive_assistant/services/ws_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:http/testing.dart';
import 'package:mocktail/mocktail.dart';

class MockApiClient extends Mock implements ApiClient {}

class MockWsClient extends Mock implements WsClient {}

class _NoopBackendService extends BackendService {
  final _statusController = StreamController<BackendStatus>.broadcast();

  @override
  Stream<BackendStatus> get status => _statusController.stream;

  @override
  BackendStatus get currentStatus => BackendStatus.stopped;

  @override
  bool get isRunning => false;

  @override
  Future<void> start() async {}

  @override
  void dispose() {
    _statusController.close();
  }
}

Override _noopBackendOverride() => backendServiceProvider.overrideWith((ref) {
  final service = _NoopBackendService();
  ref.onDispose(service.dispose);
  return service;
});

List<Override> apiClientOverrides(ApiClient api) => [
  apiClientProvider.overrideWithValue(api),
  _noopBackendOverride(),
];

void main() {
  testWidgets('renders 5 bottom tabs', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.dark,
          home: const Scaffold(body: WorkspacePanel()),
        ),
      ),
    );
    await tester.pump();

    expect(find.byIcon(Symbols.dashboard_customize), findsOneWidget);
    expect(find.byIcon(Symbols.mail), findsOneWidget);
    expect(find.byIcon(Symbols.checklist), findsOneWidget);
    expect(find.byIcon(Symbols.contacts), findsOneWidget);
    expect(find.byIcon(Symbols.folder), findsOneWidget);
  });

  testWidgets('defaults to canvas tab', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.dark,
          home: const Scaffold(body: WorkspacePanel()),
        ),
      ),
    );
    await tester.pump();

    expect(
      find.text('Agent-generated content appears here'),
      findsOneWidget,
    );
  });

  testWidgets('switches to email tab', (tester) async {
    await tester.pumpWidget(
      ProviderScope(
        child: MaterialApp(
          theme: AppTheme.dark,
          home: const Scaffold(body: WorkspacePanel()),
        ),
      ),
    );
    await tester.pump();

    await tester.tap(find.byIcon(Symbols.mail));
    await tester.pump();
    await tester.pump();

    expect(
      find.text('Agent-generated content appears here'),
      findsNothing,
    );
  });

  testWidgets('discards stale skills response after workspace changes', (
    tester,
  ) async {
    final api = MockApiClient();
    final salesCompleter = Completer<List<dynamic>>();
    when(
      () => api.listSkills(workspaceId: 'sales'),
    ).thenAnswer((_) => salesCompleter.future);
    when(() => api.listSkills(workspaceId: 'support')).thenAnswer(
      (_) async => [
        {
          'name': 'support-skill',
          'description': 'Support workflow',
          'scope': 'workspace',
        },
      ],
    );

    final container = ProviderContainer(overrides: apiClientOverrides(api));
    addTearDown(container.dispose);
    container.read(currentWorkspaceIdProvider.notifier).state = 'sales';

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AppTheme.dark,
          home: Scaffold(body: SkillsPanel()),
        ),
      ),
    );
    await tester.pump();

    container.read(currentWorkspaceIdProvider.notifier).state = 'support';
    await tester.pump();
    await tester.pump();

    expect(find.text('support-skill'), findsOneWidget);

    salesCompleter.complete([
      {
        'name': 'sales-skill',
        'description': 'Sales workflow',
        'scope': 'workspace',
      },
    ]);
    await tester.pump(const Duration(milliseconds: 10));
    await tester.pump(const Duration(milliseconds: 10));

    expect(find.text('support-skill'), findsOneWidget);
    expect(find.text('sales-skill'), findsNothing);
  });

  testWidgets('discards older overlapping skills response for same workspace', (
    tester,
  ) async {
    final api = MockApiClient();
    final firstCompleter = Completer<List<dynamic>>();
    final secondCompleter = Completer<List<dynamic>>();
    var calls = 0;
    when(() => api.listSkills(workspaceId: 'sales')).thenAnswer((_) {
      calls++;
      return calls == 1 ? firstCompleter.future : secondCompleter.future;
    });

    final container = ProviderContainer(overrides: apiClientOverrides(api));
    addTearDown(container.dispose);
    container.read(currentWorkspaceIdProvider.notifier).state = 'sales';

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AppTheme.dark,
          home: Scaffold(body: SkillsPanel()),
        ),
      ),
    );
    await tester.pump();

    await tester.tap(find.byIcon(Symbols.refresh));
    await tester.pump();
    expect(calls, 2);

    secondCompleter.complete([
      <String, dynamic>{
        'name': 'fresh-skill',
        'description': 'Current workflow',
        'scope': 'workspace',
      },
    ]);
    firstCompleter.complete([
      <String, dynamic>{
        'name': 'stale-skill',
        'description': 'Older workflow',
        'scope': 'workspace',
      },
    ]);
    await tester.pump();
    await tester.pump();

    expect(find.text('fresh-skill'), findsOneWidget);
    expect(find.text('stale-skill'), findsNothing);
  });

  testWidgets('create dialog prevents duplicate submissions', (tester) async {
    final api = MockApiClient();
    final createCompleter = Completer<Map<String, dynamic>>();
    when(
      () => api.listSkills(workspaceId: any(named: 'workspaceId')),
    ).thenAnswer((_) async => []);
    when(
      () => api.createSkill(
        any(),
        any(),
        any(),
        scope: any(named: 'scope'),
        workspaceId: any(named: 'workspaceId'),
      ),
    ).thenAnswer((_) => createCompleter.future);

    final container = ProviderContainer(overrides: apiClientOverrides(api));
    addTearDown(container.dispose);
    container.read(currentWorkspaceIdProvider.notifier).state = 'sales';

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AppTheme.dark,
          home: Scaffold(body: SkillsPanel()),
        ),
      ),
    );
    await tester.pump();

    await tester.tap(find.byIcon(Symbols.add));
    await tester.pump();
    await tester.enterText(find.widgetWithText(TextField, 'Name'), 'triage');
    await tester.enterText(
      find.widgetWithText(TextField, 'Description'),
      'Sort inbox',
    );
    await tester.enterText(
      find.widgetWithText(TextField, 'Content'),
      'Prioritize urgent messages',
    );

    final createButton = find.byType(FilledButton);
    await tester.tap(createButton);
    await tester.pump();
    await tester.tap(createButton);
    await tester.pump();

    verify(
      () => api.createSkill(
        'triage',
        'Sort inbox',
        'Prioritize urgent messages',
        scope: 'user',
        workspaceId: 'sales',
      ),
    ).called(1);

    createCompleter.complete({'ok': true});
  });

  testWidgets(
    'delete is cancelled when workspace changes while dialog is open',
    (tester) async {
      final api = MockApiClient();
      when(() => api.listSkills(workspaceId: 'sales')).thenAnswer(
        (_) async => [
          <String, dynamic>{
            'name': 'triage',
            'description': 'Sort inbox',
            'scope': 'workspace',
          },
        ],
      );
      when(() => api.listSkills(workspaceId: 'support')).thenAnswer(
        (_) async => [
          <String, dynamic>{
            'name': 'support-skill',
            'description': 'Support workflow',
            'scope': 'workspace',
          },
        ],
      );
      when(
        () => api.deleteSkill(
          any(),
          scope: any(named: 'scope'),
          workspaceId: any(named: 'workspaceId'),
        ),
      ).thenAnswer((_) async {});

      final container = ProviderContainer(
        overrides: [apiClientProvider.overrideWithValue(api)],
      );
      addTearDown(container.dispose);
      container.read(currentWorkspaceIdProvider.notifier).state = 'sales';

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            theme: AppTheme.dark,
            home: Scaffold(body: SkillsPanel()),
          ),
        ),
      );
      await tester.pump();
      await tester.pump();

      await tester.tap(find.byIcon(Symbols.delete));
      await tester.pump();
      container.read(currentWorkspaceIdProvider.notifier).state = 'support';
      await tester.pump();
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pump();

      verifyNever(
        () => api.deleteSkill(
          any(),
          scope: any(named: 'scope'),
          workspaceId: any(named: 'workspaceId'),
        ),
      );
      expect(find.text('Workspace changed; delete cancelled.'), findsOneWidget);
    },
  );

  testWidgets(
    'create is cancelled when workspace changes while dialog is open',
    (tester) async {
      final api = MockApiClient();
      when(
        () => api.listSkills(workspaceId: any(named: 'workspaceId')),
      ).thenAnswer((_) async => []);
      when(
        () => api.createSkill(
          any(),
          any(),
          any(),
          scope: any(named: 'scope'),
          workspaceId: any(named: 'workspaceId'),
        ),
      ).thenAnswer((_) async => {'ok': true});

      final container = ProviderContainer(
        overrides: [apiClientProvider.overrideWithValue(api)],
      );
      addTearDown(container.dispose);
      container.read(currentWorkspaceIdProvider.notifier).state = 'sales';

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: MaterialApp(
            theme: AppTheme.dark,
            home: Scaffold(body: SkillsPanel()),
          ),
        ),
      );
      await tester.pump();

      await tester.tap(find.byIcon(Symbols.add));
      await tester.pump();
      await tester.enterText(find.widgetWithText(TextField, 'Name'), 'triage');
      await tester.enterText(
        find.widgetWithText(TextField, 'Description'),
        'Sort inbox',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Content'),
        'Prioritize urgent messages',
      );
      container.read(currentWorkspaceIdProvider.notifier).state = 'support';
      await tester.pump();

      await tester.tap(find.byType(FilledButton));
      await tester.pump();

      verifyNever(
        () => api.createSkill(
          any(),
          any(),
          any(),
          scope: any(named: 'scope'),
          workspaceId: any(named: 'workspaceId'),
        ),
      );
      expect(find.text('Workspace changed; create cancelled.'), findsOneWidget);
    },
  );

  testWidgets('create dialog tool picker shows toggles that work', (
    tester,
  ) async {
    final api = MockApiClient();
    when(
      () => api.listSubagents(workspaceId: any(named: 'workspaceId')),
    ).thenAnswer((_) async => []);
    when(
      () => api.listToolNames(),
    ).thenAnswer((_) async => ['time_get', 'shell_execute', 'files_read']);
    when(
      () => api.createSubagent(
        name: any(named: 'name'),
        description: any(named: 'description'),
        model: any(named: 'model'),
        scope: any(named: 'scope'),
        tools: any(named: 'tools'),
        skills: any(named: 'skills'),
        systemPrompt: any(named: 'systemPrompt'),
        maxLlmCalls: any(named: 'maxLlmCalls'),
        costLimitUsd: any(named: 'costLimitUsd'),
        timeoutSeconds: any(named: 'timeoutSeconds'),
        workspaceId: any(named: 'workspaceId'),
      ),
    ).thenAnswer((_) async => {});

    final container = ProviderContainer(overrides: apiClientOverrides(api));
    addTearDown(container.dispose);
    container.read(currentWorkspaceIdProvider.notifier).state = 'personal';

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AppTheme.dark,
          home: Scaffold(body: SubagentsPanel()),
        ),
      ),
    );
    await tester.pump();

    await tester.tap(find.byIcon(Symbols.add));
    await tester.pumpAndSettle();
    await tester.drag(
      find.byType(SingleChildScrollView),
      const Offset(0, -400),
    );
    await tester.pumpAndSettle();

    expect(find.text('Tools'), findsOneWidget);
    final checkboxes = tester.widgetList<Checkbox>(find.byType(Checkbox));
    expect(checkboxes.isNotEmpty, true);
    final first = checkboxes.first;
    expect(first.onChanged, isNotNull);
  });

  testWidgets('prunes terminal jobs after max threshold', (tester) async {
    final api = MockApiClient();
    when(
      () => api.listSubagents(workspaceId: 'test12345'),
    ).thenAnswer((_) async => []);
    when(
      () =>
          api.cancelSubagentJob(any(), workspaceId: any(named: 'workspaceId')),
    ).thenAnswer((_) async => {});

    final container = ProviderContainer(overrides: apiClientOverrides(api));
    addTearDown(container.dispose);
    container.read(currentWorkspaceIdProvider.notifier).state = 'test12345';

    final notifier = container.read(subagentProvider.notifier);

    final seeded = <String, SubagentJob>{};
    for (var i = 0; i < 15; i++) {
      seeded['job-$i'] = SubagentJob(
        jobId: 'job-$i',
        agentName: 'agent',
        task: 'task',
        status: 'completed',
        workspaceId: 'test12345',
        createdAt: '2026-01-${(i + 1).toString().padLeft(2, '0')}T00:00:00',
      );
    }
    // Add one running job that we'll cancel to trigger prune
    seeded['job-running'] = SubagentJob(
      jobId: 'job-running',
      agentName: 'agent',
      task: 'task',
      status: 'running',
      workspaceId: 'test12345',
    );
    notifier.state = notifier.state.copyWith(activeJobs: seeded);

    await notifier.cancelJob('job-running', workspaceId: 'test12345');

    // 15 completed + 1 cancelling = 16, pruned to 11 (cancelling + 10 terminal)
    expect(notifier.state.activeJobs.length, 11);
    expect(notifier.state.activeJobs.containsKey('job-0'), isFalse);
    expect(notifier.state.activeJobs.containsKey('job-14'), isTrue);
  });

  testWidgets('create dialog rejects invalid name with inline error', (
    tester,
  ) async {
    final api = MockApiClient();
    when(
      () => api.listSubagents(workspaceId: any(named: 'workspaceId')),
    ).thenAnswer((_) async => []);
    when(() => api.listToolNames()).thenAnswer((_) async => ['time_get']);
    when(
      () => api.createSubagent(
        name: any(named: 'name'),
        description: any(named: 'description'),
        model: any(named: 'model'),
        scope: any(named: 'scope'),
        tools: any(named: 'tools'),
        skills: any(named: 'skills'),
        systemPrompt: any(named: 'systemPrompt'),
        maxLlmCalls: any(named: 'maxLlmCalls'),
        costLimitUsd: any(named: 'costLimitUsd'),
        timeoutSeconds: any(named: 'timeoutSeconds'),
        workspaceId: any(named: 'workspaceId'),
      ),
    ).thenAnswer((_) async => {});

    final container = ProviderContainer(overrides: apiClientOverrides(api));
    addTearDown(container.dispose);
    container.read(currentWorkspaceIdProvider.notifier).state = 'personal';

    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp(
          theme: AppTheme.dark,
          home: Scaffold(body: SubagentsPanel()),
        ),
      ),
    );
    await tester.pump();

    await tester.tap(find.byIcon(Symbols.add));
    await tester.pumpAndSettle();

    // Enter invalid name with spaces
    await tester.enterText(
      find.widgetWithText(TextField, 'Name *'),
      'bad name',
    );
    await tester.enterText(
      find.widgetWithText(TextField, 'Description *'),
      'Description',
    );
    await tester.tap(find.text('Create'));
    await tester.pumpAndSettle();

    // Dialog should still be open with error message
    expect(
      find.text(
        'Name can only contain letters, numbers, hyphens, and underscores',
      ),
      findsOneWidget,
    );
    verifyNever(
      () => api.createSubagent(
        name: any(named: 'name'),
        description: any(named: 'description'),
        workspaceId: any(named: 'workspaceId'),
        model: any(named: 'model'),
        scope: any(named: 'scope'),
        tools: any(named: 'tools'),
        skills: any(named: 'skills'),
        systemPrompt: any(named: 'systemPrompt'),
        maxLlmCalls: any(named: 'maxLlmCalls'),
        costLimitUsd: any(named: 'costLimitUsd'),
        timeoutSeconds: any(named: 'timeoutSeconds'),
      ),
    );
  });
}
