import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mocktail/mocktail.dart';
import 'package:executive_assistant/models/message.dart';
import 'package:executive_assistant/services/ws_client.dart';
import 'package:executive_assistant/services/api_client.dart';
import 'package:executive_assistant/providers/agent_provider.dart';
import 'package:executive_assistant/services/backend_service.dart';
import 'package:executive_assistant/theme/app_theme.dart';
import 'package:executive_assistant/features/chat/chat_screen.dart';

class MockWsClient extends Mock implements WsClient {}

class MockApiClient extends Mock implements ApiClient {}

class MockBackendService extends Mock implements BackendService {}

MockBackendService _createMockBackendService() {
  final svc = MockBackendService();
  final statusCtrl = StreamController<BackendStatus>.broadcast();
  when(() => svc.status).thenAnswer((_) => statusCtrl.stream);
  when(() => svc.health).thenAnswer((_) => Stream<bool>.empty());
  when(() => svc.start()).thenAnswer((_) async {
    statusCtrl.add(BackendStatus.running);
  });
  when(() => svc.stop()).thenAnswer((_) async {});
  when(() => svc.dispose()).thenReturn(null);
  return svc;
}

Widget _buildChatScreen(MockWsClient ws, MockApiClient api) {
  return ProviderScope(
    overrides: [
      wsClientProvider.overrideWithValue(ws),
      apiClientProvider.overrideWithValue(api),
      backendServiceProvider.overrideWith((ref) => _createMockBackendService()),
    ],
    child: MaterialApp(
      theme: AppTheme.dark,
      home: const ChatScreen(),
    ),
  );
}

void main() {
  late MockWsClient mockWs;
  late MockApiClient mockApi;
  late StreamController<WsMessage> msgCtrl;
  late StreamController<ConnectionStatus> statusCtrl;

  setUp(() {
    msgCtrl = StreamController<WsMessage>.broadcast();
    statusCtrl = StreamController<ConnectionStatus>.broadcast();
    mockWs = MockWsClient();
    mockApi = MockApiClient();
    when(() => mockWs.status).thenAnswer((_) => statusCtrl.stream);
    when(() => mockWs.messages).thenAnswer((_) => msgCtrl.stream);
    when(() => mockWs.isConnected).thenReturn(false);
    when(() => mockWs.pendingApprovals).thenReturn({});
  });

  tearDown(() {
    msgCtrl.close();
    statusCtrl.close();
  });

  group('ChatScreen renders', () {
    testWidgets('shows title in AppBar', (WidgetTester tester) async {
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      expect(find.text('Assistant'), findsOneWidget);
    });

    testWidgets('shows empty state when no messages', (WidgetTester tester) async {
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));
      expect(find.text('Send a message to start'), findsOneWidget);
    });

    testWidgets('shows connection banner when disconnected', (WidgetTester tester) async {
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));
      expect(find.textContaining('Not connected'), findsOneWidget);
    });

    testWidgets('shows text input field', (WidgetTester tester) async {
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      statusCtrl.add(ConnectionStatus.connected);
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));
      expect(find.byType(TextField), findsOneWidget);
    });
  });

  group('ChatScreen interactions', () {
    testWidgets('send button triggers sendMessage', (WidgetTester tester) async {
      when(() => mockWs.sendMessage(any())).thenReturn(null);
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      statusCtrl.add(ConnectionStatus.connected);
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      final input = find.byType(TextField);
      expect(input, findsOneWidget);

      await tester.enterText(input, 'Hello world');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      verify(() => mockWs.sendMessage('Hello world')).called(1);
    });

    testWidgets('cancel button appears during streaming', (WidgetTester tester) async {
      when(() => mockWs.sendMessage(any())).thenReturn(null);
      when(() => mockWs.cancel()).thenReturn(null);

      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      statusCtrl.add(ConnectionStatus.connected);
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      await tester.enterText(find.byType(TextField), 'stream me');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      // Trigger streaming via a text_delta message.
      msgCtrl.add(WsMessage(type: 'text_delta', data: {
        'type': 'text_delta',
        'content': 'typing',
      }));
      await tester.pump();

      // The suffix icon should switch to stop button.
      expect(find.byIcon(Symbols.stop), findsOneWidget);

      await tester.tap(find.byIcon(Symbols.stop));
      await tester.pump();
      verify(() => mockWs.cancel()).called(1);
    });

    testWidgets('message bubble renders after done event', (WidgetTester tester) async {
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      statusCtrl.add(ConnectionStatus.connected);
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      await tester.enterText(find.byType(TextField), 'trigger');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();

      msgCtrl.add(WsMessage(type: 'text_delta', data: {
        'type': 'text_delta',
        'content': 'Assistant reply',
      }));
      msgCtrl.add(WsMessage(type: 'done', data: {
        'type': 'done',
        'response': '',
      }));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      expect(find.text('Assistant reply'), findsOneWidget);
    });

    testWidgets('reconnect icon is tappable', (WidgetTester tester) async {
      when(() => mockWs.connect()).thenReturn(null);
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      final reconnect = find.byTooltip('Reconnect').first;
      expect(reconnect, findsOneWidget);
      await tester.tap(reconnect);
      await tester.pump();
      verify(() => mockWs.connect()).called(greaterThanOrEqualTo(1));
    });
  });

  group('ChatScreen approval sheet', () {
    testWidgets('shows approval sheet on interrupt', (WidgetTester tester) async {
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      statusCtrl.add(ConnectionStatus.connected);
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      msgCtrl.add(WsMessage(type: 'interrupt', data: {
        'type': 'interrupt',
        'call_id': 'c42',
        'tool': 'email_send',
        'args': {'to': 'a@b.com'},
      }));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      expect(find.text('Tool requires approval'), findsOneWidget);
      expect(find.text('Approve'), findsOneWidget);
      expect(find.text('Reject'), findsOneWidget);
    });

    testWidgets('approve tap delegates to wsClient', (WidgetTester tester) async {
      when(() => mockWs.approveToolCall(any())).thenReturn(null);
      await tester.pumpWidget(_buildChatScreen(mockWs, mockApi));
      statusCtrl.add(ConnectionStatus.connected);
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));

      msgCtrl.add(WsMessage(type: 'interrupt', data: {
        'type': 'interrupt',
        'call_id': 'c42',
        'tool': 'email_send',
        'args': {'to': 'a@b.com'},
      }));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 200));

      await tester.tap(find.text('Approve'));
      await tester.pump(); await tester.pump(const Duration(milliseconds: 100));
      verify(() => mockWs.approveToolCall('c42')).called(1);
    });
  });
}
