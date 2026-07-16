import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/message.dart';
import '../services/backend_service.dart';
import '../services/ws_client.dart';
import '../services/api_client.dart';

String resolvedUserId = 'default_user';

enum ChatStatus { idle, streaming, error, awaitingApproval, disconnected }

final selectedModelProvider = StateProvider<String>(
  (ref) => 'deepseek:deepseek-v4-flash',
);
final providerKeysProvider = StateProvider<Map<String, String>>((ref) => {});

class ChatState {
  final List<ChatMessage> messages;
  final ChatStatus status;
  final String? error;
  final String streamingText;
  final String reasoningText;
  final List<ToolCallDisplay> activeToolCalls;
  final Map<String, ToolCallDisplay> pendingApprovals;
  final String sessionId;
  final bool connected;
  final Set<String> deliveredMessageIds;
  final bool loadingHistory;
  final List<String> skillsLoaded;
  final BackendStatus backendStatus;

  static const _errorSentinel = Object();

  const ChatState({
    this.messages = const [],
    this.status = ChatStatus.disconnected,
    this.error,
    this.streamingText = '',
    this.reasoningText = '',
    this.activeToolCalls = const [],
    this.pendingApprovals = const {},
    this.sessionId = '',
    this.connected = false,
    this.deliveredMessageIds = const {},
    this.loadingHistory = false,
    this.skillsLoaded = const [],
    this.backendStatus = BackendStatus.stopped,
  });

  ChatState copyWith({
    List<ChatMessage>? messages,
    ChatStatus? status,
    Object? error = _errorSentinel,
    String? streamingText,
    String? reasoningText,
    List<ToolCallDisplay>? activeToolCalls,
    Map<String, ToolCallDisplay>? pendingApprovals,
    String? sessionId,
    bool? connected,
    Set<String>? deliveredMessageIds,
    bool? loadingHistory,
    List<String>? skillsLoaded,
    BackendStatus? backendStatus,
  }) {
    return ChatState(
      messages: messages ?? this.messages,
      status: status ?? this.status,
      error: identical(error, _errorSentinel) ? this.error : error as String?,
      streamingText: streamingText ?? this.streamingText,
      reasoningText: reasoningText ?? this.reasoningText,
      activeToolCalls: activeToolCalls ?? this.activeToolCalls,
      pendingApprovals: pendingApprovals ?? this.pendingApprovals,
      sessionId: sessionId ?? this.sessionId,
      connected: connected ?? this.connected,
      deliveredMessageIds: deliveredMessageIds ?? this.deliveredMessageIds,
      loadingHistory: loadingHistory ?? this.loadingHistory,
      skillsLoaded: skillsLoaded ?? this.skillsLoaded,
      backendStatus: backendStatus ?? this.backendStatus,
    );
  }
}

class AgentNotifier extends StateNotifier<ChatState> {
  final WsClient _wsClient;
  final ApiClient _apiClient;
  final BackendService _backendService;
  StreamSubscription? _statusSubscription;
  StreamSubscription? _messageSubscription;
  StreamSubscription? _backendSubscription;
  bool _disposed = false;
  bool _loadingHistory = false;
  final List<WsMessage> _bufferedMessages = [];
  String _workspaceId = 'personal';
  String? _activeStreamWorkspaceId;
  final Map<String, ChatState> _workspaceStates = {};
  final Set<String> _historyLoadedWorkspaces = {};
  final Map<String, StringBuffer> _toolInputAccum = {};
  final Map<String, DateTime> _toolStartTimes = {};
  final Set<String> _seenContentHashes = {};
  bool _transportConnected = false;
  bool _backendReady = false;
  bool _welcomeSent = false;
  void Function(Map<String, dynamic>)? onCanvasUpdate;

  AgentNotifier(this._wsClient, this._apiClient, this._backendService)
      : super(const ChatState()) {
    _statusSubscription = _wsClient.status.listen(_onStatusChange);
    _messageSubscription = _wsClient.messages.listen(_onMessage);
    _backendSubscription = _backendService.status.listen(_onBackendStatus);
    _backendService.start();
  }

  void setWorkspaceId(String id) {
    _workspaceStates[_workspaceId] = state;
    _workspaceId = id;
    final saved = _workspaceStates[id];
    if (saved != null) {
      state = _withCurrentTransport(saved);
      _workspaceStates[id] = state;
    }
  }

  bool hasWorkspaceState(String id) => _workspaceStates.containsKey(id);

  bool needsHistory(String id) {
    final workspaceState = id == _workspaceId ? state : _workspaceStates[id];
    if (workspaceState?.messages.isNotEmpty == true) return false;
    return !_historyLoadedWorkspaces.contains(id);
  }

  void removeWorkspaceState(String id) {
    _workspaceStates.remove(id);
  }

  void _setState(ChatState next) {
    state = next;
    _workspaceStates[_workspaceId] = next;
  }

  ChatState _withCurrentTransport(ChatState workspaceState) {
    if (!_transportConnected) return workspaceState.copyWith(connected: false);
    final restoredStatus = workspaceState.status == ChatStatus.disconnected
        ? ChatStatus.idle
        : workspaceState.status;
    return workspaceState.copyWith(
      connected: true,
      status: restoredStatus,
      error: restoredStatus == ChatStatus.idle ? null : workspaceState.error,
    );
  }

  void _flushStreamingTextToMessage() {
    final content = state.streamingText;
    if (content.trim().isEmpty) return;
    final assistantMsg = ChatMessage(
      id: 'ai_segment_${DateTime.now().microsecondsSinceEpoch}',
      role: 'assistant',
      content: content,
      timestamp: DateTime.now(),
    );
    _setState(
      state.copyWith(
        messages: [...state.messages, assistantMsg],
        streamingText: '',
      ),
    );
  }

  Future<void> _maybeSendWelcome() async {
    if (_welcomeSent) return;
    if (state.messages.isNotEmpty) return;
    _welcomeSent = true;
    final welcome = ChatMessage(
      id: 'welcome_${DateTime.now().millisecondsSinceEpoch}',
      role: 'assistant',
      content: 'Hey there! 👋 I\'m your Assistant.\n\n'
          'I\'d love to get to know you — what\'s your name, and what brings you here? '
          'Here are a few things I can help with:\n\n'
          '• **Stay on top of email** — read, draft, and send messages\n'
          '• **Manage your calendar** — check schedule, find free slots\n'
          '• **Track tasks** — create todo lists and stay organized\n'
          '• **Research and learn** — search the web, summarize articles\n'
          '• **Work with files** — read, write, and organize documents\n\n'
          'Tell me a bit about yourself and what you\'d like to accomplish!',
      timestamp: DateTime.now(),
    );
    _setState(
      state.copyWith(
        messages: [welcome],
        status: ChatStatus.idle,
      ),
    );
  }

  void connect() {
    if (!_backendReady) {
      _setState(
        state.copyWith(
          status: ChatStatus.disconnected,
          error: 'Backend starting… waiting for server',
        ),
      );
      return;
    }
    _wsClient.connect();
  }

  void _onBackendStatus(BackendStatus status) {
    if (_disposed) return;
    if (status == BackendStatus.running && !_backendReady) {
      _backendReady = true;
      _setState(state.copyWith(backendStatus: status, error: null));
      _wsClient.connect();
    } else if (status == BackendStatus.crashed) {
      _backendReady = false;
      _setState(
        state.copyWith(
          backendStatus: status,
          connected: false,
          status: ChatStatus.disconnected,
          error: 'Backend crashed — restarting',
        ),
      );
    } else {
      _setState(state.copyWith(backendStatus: status));
    }
  }

  Future<void> loadHistory({int limit = 100}) async {
    try {
      final targetWorkspaceId = _workspaceId;
      final apiMessages = await _apiClient.getConversation(
        limit: limit,
        workspaceId: targetWorkspaceId,
      );
      if (_disposed || targetWorkspaceId != _workspaceId) return;
      _historyLoadedWorkspaces.add(targetWorkspaceId);
      final now = DateTime.now();
      var idx = 0;
      final chatMessages = <ChatMessage>[];
      for (final msg in apiMessages.whereType<Map>()) {
        final role = msg['role']?.toString();
        if (role == null) {
          // Skip messages without a role field — can't classify them
          continue;
        }
        final content = msg['content']?.toString() ?? '';
        final meta = msg['metadata'];
        final metadata = meta is Map<String, dynamic>
            ? meta
            : <String, dynamic>{};

        if (content.trim().isEmpty && role != 'tool') continue;

        // Deduplicate by content hash to catch messages stored multiple times
        final contentHash = '$role:$content';
        if (_seenContentHashes.contains(contentHash)) continue;
        _seenContentHashes.add(contentHash);

        final ts = msg['timestamp']?.toString();
        final parsed = ts != null ? DateTime.tryParse(ts) : null;
        final timestamp = parsed ?? now.subtract(Duration(minutes: idx));

        String displayRole = role == 'summary' ? 'system' : role;
        String displayContent = content;

        if (displayRole == 'tool') {
          displayRole = 'tool';
          final toolName = metadata['tool_name']?.toString() ?? '';
          displayContent = toolName.isNotEmpty ? toolName : 'unknown tool';
        } else if (displayRole == 'reasoning') {
          displayRole = 'reasoning';
        }

        chatMessages.add(
          ChatMessage(
            id: 'hist_$idx',
            role: displayRole,
            content: displayContent,
            timestamp: timestamp,
            metadata: metadata,
          ),
        );
        idx++;
      }

      if (_disposed) return;
      if (chatMessages.isNotEmpty) {
        final currentIds = state.messages.map((m) => m.id).toSet();
        final newMessages = chatMessages
            .where((m) => !currentIds.contains(m.id))
            .toList();
        if (newMessages.isNotEmpty) {
          newMessages.sort((a, b) => a.timestamp.compareTo(b.timestamp));
          final allMessages = [...state.messages, ...newMessages];
          allMessages.sort((a, b) => a.timestamp.compareTo(b.timestamp));
          final historyIds = chatMessages.map((m) => m.id).toSet();
          _setState(
            state.copyWith(
              messages: allMessages,
              deliveredMessageIds: historyIds,
              loadingHistory: false,
            ),
          );
        }
      } else {
        await _maybeSendWelcome();
        _setState(state.copyWith(loadingHistory: false));
      }
    } catch (e, stack) {
      debugPrint('[AgentNotifier] Failed to load history: $e\n$stack');
    }
  }

  Future<void> _loadHistorySafely() async {
    try {
      await loadHistory();
    } catch (e) {
      if (_disposed) return;
      _setState(state.copyWith(error: 'Failed to load conversation history'));
    } finally {
      _loadingHistory = false;
      if (!_disposed) {
        _setState(state.copyWith(loadingHistory: false));
        final buffered = List<WsMessage>.from(_bufferedMessages);
        _bufferedMessages.clear();
        for (final msg in buffered) {
          _onMessage(msg);
        }
      }
    }
  }

  void sendMessage(String content) {
    if (content.trim().isEmpty) return;

    if (!state.connected) {
      _setState(
        state.copyWith(
          status: ChatStatus.error,
          error: 'Not connected. Tap the cloud icon or banner to reconnect.',
        ),
      );
      return;
    }

    final userMsg = ChatMessage(
      id: 'user_${DateTime.now().millisecondsSinceEpoch}',
      role: 'user',
      content: content,
      timestamp: DateTime.now(),
    );

    _setState(
      state.copyWith(
        messages: [...state.messages, userMsg],
        status: ChatStatus.streaming,
        streamingText: '',
        activeToolCalls: [],
        skillsLoaded: [],
        error: null,
        loadingHistory: false,
      ),
    );

    _activeStreamWorkspaceId = _workspaceId;
    _wsClient.sendMessage(content, workspaceId: _workspaceId);
  }

  void updateModel(String model) {
    _wsClient.model = model;
    _apiClient.model = model;
  }

  void updateProviderKeys(Map<String, String> keys) {
    _wsClient.providerKeys = keys;
    _apiClient.providerKeys = keys;
  }

  void approveToolCall(String callId) {
    _wsClient.approveToolCall(callId);
    final updated = Map<String, ToolCallDisplay>.from(state.pendingApprovals);
    updated.remove(callId);
    _setState(
      state.copyWith(pendingApprovals: updated, status: ChatStatus.streaming),
    );
  }

  void rejectToolCall(String callId, {String reason = ''}) {
    _wsClient.rejectToolCall(callId, reason: reason);
    final updated = Map<String, ToolCallDisplay>.from(state.pendingApprovals);
    updated.remove(callId);
    _setState(
      state.copyWith(
        pendingApprovals: updated,
        status: updated.isEmpty ? ChatStatus.idle : ChatStatus.awaitingApproval,
      ),
    );
  }

  void cancelExecution() {
    _wsClient.cancel();
    _setState(
      state.copyWith(
        status: state.connected ? ChatStatus.idle : ChatStatus.disconnected,
        pendingApprovals: const {},
        activeToolCalls: [],
        streamingText: '',
        reasoningText: '',
        skillsLoaded: [],
      ),
    );
  }

  void clearError() {
    _setState(state.copyWith(error: null));
  }

  void clearHistory({bool loading = false}) {
    _seenContentHashes.clear();
    _welcomeSent = false;
    if (loading) {
      _historyLoadedWorkspaces.remove(_workspaceId);
    }
    _setState(
      state.copyWith(
        messages: [],
        streamingText: '',
        reasoningText: '',
        activeToolCalls: [],
        status: state.connected ? ChatStatus.idle : ChatStatus.disconnected,
        loadingHistory: loading,
      ),
    );
  }

  void updateHost(String host) {
    _wsClient.updateHost(host);
    _apiClient.updateHost(host);
    _backendService.stop();
    _backendReady = false;
    _wsClient.disconnect();
    _backendService.start();
  }

  void updateUserId(String userId) {
    _wsClient.updateUserId(userId);
    _apiClient.updateUserId(userId);
    _wsClient.disconnect();
    _wsClient.connect();
  }

  String _extractTextContent(dynamic content) {
    if (content is String) return content;
    if (content is List) {
      return content
          .whereType<Map>()
          .where((m) => m['type'] == 'text')
          .map((m) => m['text']?.toString() ?? '')
          .join();
    }
    debugPrint(
      '[AgentNotifier] Unexpected text_delta content type: ${content.runtimeType}',
    );
    return '';
  }

  Map<String, dynamic> _safeArgs(dynamic value) {
    if (value is Map<String, dynamic>) return value;
    if (value is Map) return Map<String, dynamic>.from(value);
    return {};
  }

  void _onMessage(WsMessage msg) {
    if (_disposed) return;
    final eventWorkspaceId = msg['workspace_id']?.toString();
    final streamWorkspaceId = eventWorkspaceId?.isNotEmpty == true
        ? eventWorkspaceId
        : _activeStreamWorkspaceId;
    if (streamWorkspaceId != null && streamWorkspaceId != _workspaceId) {
      if (msg.type == 'done' || msg.type == 'error') {
        _activeStreamWorkspaceId = null;
      }
      return;
    }
    if (_loadingHistory) {
      _bufferedMessages.add(msg);
      return;
    }
    final type = msg.type;
    // Helper: canonical type for block-structured events.
    final canonical = _canonicalType(type);

    // --- Text blocks (backward-compat + block-structured) ---
    if (canonical == 'text_delta' || type == 'ai_token') {
      _setState(
        state.copyWith(
          streamingText:
              state.streamingText + _extractTextContent(msg['content']),
          status: state.status == ChatStatus.awaitingApproval
              ? ChatStatus.awaitingApproval
              : ChatStatus.streaming,
        ),
      );
      return;
    }
    if (canonical == 'text_start') return;
    if (canonical == 'text_end') return;

    // --- Reasoning blocks ---
    if (canonical == 'reasoning_delta' || type == 'reasoning') {
      _setState(
        state.copyWith(
          reasoningText:
              state.reasoningText + (msg['content']?.toString() ?? ''),
          status: state.status == ChatStatus.awaitingApproval
              ? ChatStatus.awaitingApproval
              : ChatStatus.streaming,
        ),
      );
      return;
    }
    if (canonical == 'reasoning_start') return;
    if (canonical == 'reasoning_end') return;

    // --- Tool input blocks ---
    if (canonical == 'tool_input_start' || type == 'tool_start') {
      _flushStreamingTextToMessage();
      final callId = msg['call_id']?.toString() ?? '';
      final tool = msg['tool']?.toString() ?? '';
      final args = _safeArgs(msg['args']);
      final tc = ToolCallDisplay(callId: callId, toolName: tool, args: args);
      _toolStartTimes[callId] = DateTime.now();
      _setState(
        state.copyWith(
          activeToolCalls: [...state.activeToolCalls, tc],
          status: state.status == ChatStatus.awaitingApproval
              ? ChatStatus.awaitingApproval
              : ChatStatus.streaming,
        ),
      );
      return;
    }
    if (canonical == 'tool_call') {
      final callId = msg['call_id']?.toString() ?? '';
      final tool = msg['tool']?.toString() ?? '';
      final args = _safeArgs(msg['args']);
      _upsertActiveTool(callId: callId, toolName: tool, args: args);
      return;
    }
    if (canonical == 'tool_input_delta') {
      final callId = msg['call_id']?.toString() ?? '';
      final delta =
          msg['content']?.toString() ?? msg['args_delta']?.toString() ?? '';
      if (callId.isNotEmpty && delta.isNotEmpty) {
        _toolInputAccum.putIfAbsent(callId, () => StringBuffer());
        _toolInputAccum[callId]!.write(delta);
      }
      return;
    }
    if (canonical == 'tool_input_end') {
      final callId = msg['call_id']?.toString() ?? '';
      final accumulated = _toolInputAccum.remove(callId);
      if (accumulated != null && accumulated.isNotEmpty && callId.isNotEmpty) {
        final parsed = _safeArgs(tryParseJson(accumulated.toString()));
        _upsertActiveToolByCallId(callId, parsed);
      }
      return;
    }

    // --- Tool result / end ---
    if (canonical == 'tool_result' || type == 'tool_end') {
      final callId = msg['call_id']?.toString() ?? '';
      final preview = msg['result_preview']?.toString() ?? '';
      final startTime = _toolStartTimes.remove(callId);
      final duration = startTime != null
          ? DateTime.now().difference(startTime)
          : null;
      final updated = state.activeToolCalls.map((tc) {
        if (tc.callId == callId) {
          return tc.copyWith(resultPreview: preview, duration: duration);
        }
        return tc;
      }).toList();
      _setState(state.copyWith(activeToolCalls: updated));
      return;
    }

    // --- HITL interrupt ---
    if (type == 'interrupt') {
      final callId = msg['call_id']?.toString() ?? '';
      final tool = msg['tool']?.toString() ?? '';
      final args = _safeArgs(msg['args']);
      final pending = Map<String, ToolCallDisplay>.from(state.pendingApprovals);
      pending[callId] = ToolCallDisplay(
        callId: callId,
        toolName: tool,
        args: args,
        isPending: true,
      );
      _setState(
        state.copyWith(
          pendingApprovals: pending,
          status: ChatStatus.awaitingApproval,
        ),
      );
      return;
    }

    // --- Usage events ---
    if (canonical == 'usage') {
      // TODO(phase-14): Track session token/cost estimates.
      // final usage = msg['usage'] as Map<String, dynamic>?;
      return;
    }

    // --- Skills Load event ---
    if (type == 'skills_load') {
      final name = msg['name']?.toString() ?? 'unknown';
      _setState(
        state.copyWith(
          skillsLoaded: [...state.skillsLoaded, name],
          status: state.status == ChatStatus.awaitingApproval
              ? ChatStatus.awaitingApproval
              : ChatStatus.streaming,
        ),
      );
      return;
    }

    // --- Canvas Update event ---
    if (type == 'canvas_update') {
      onCanvasUpdate?.call(msg.data);
      return;
    }

    // --- Completion ---
    if (type == 'done') {
      final messageId = msg['message_id']?.toString() ?? '';
      if (messageId.isNotEmpty &&
          state.deliveredMessageIds.contains(messageId)) {
        return;
      }
      final updatedIds = Set<String>.from(state.deliveredMessageIds);
      if (messageId.isNotEmpty) updatedIds.add(messageId);

      final response = msg['response']?.toString() ?? '';
      final finalText = state.streamingText;
      final now = DateTime.now();

      // Collect tool messages as separate entries, before the assistant response
      final toolMessages = state.activeToolCalls.map((tc) {
        return ChatMessage(
          id: 'tool_${now.millisecondsSinceEpoch}_${tc.callId}',
          role: 'tool',
          content: tc.toolName,
          metadata: {
            'tool_name': tc.toolName,
            'call_id': tc.callId,
            'args': tc.args,
            'result_preview': tc.resultPreview ?? '',
          },
          timestamp: now.subtract(const Duration(milliseconds: 1)),
        );
      }).toList();

      final hasPendingApprovals = state.pendingApprovals.isNotEmpty;

      List<ChatMessage> newMessages = [...state.messages, ...toolMessages];

      if (finalText.isNotEmpty || response.isNotEmpty) {
        final content = finalText.isNotEmpty ? finalText : response;
        newMessages.add(ChatMessage(
          id: 'ai_${now.millisecondsSinceEpoch}',
          role: 'assistant',
          content: content,
          timestamp: now,
        ));
      }

      _setState(
        state.copyWith(
          messages: newMessages,
          status: hasPendingApprovals
              ? ChatStatus.awaitingApproval
              : ChatStatus.idle,
          streamingText: '',
          reasoningText: '',
          activeToolCalls: hasPendingApprovals ? state.activeToolCalls : [],
          skillsLoaded: [],
          deliveredMessageIds: updatedIds,
          loadingHistory: false,
        ),
      );
      _toolInputAccum.clear();
      _activeStreamWorkspaceId = null;
      return;
    }

    // --- Error ---
    if (type == 'error') {
      _setState(
        state.copyWith(
          status: ChatStatus.error,
          error: msg['message']?.toString() ?? 'Unknown error',
          streamingText: '',
          loadingHistory: false,
        ),
      );
      _activeStreamWorkspaceId = null;
      return;
    }

    // --- Heartbeat ---
    if (type == 'pong') return;
    if (type == 'middleware') return;

    // --- Unknown type — silently ignore for forward compatibility.
    // If you need to debug unknown events, add a logger here.
  }

  /// Map WS event type to its canonical form (handles backward-compat aliases).
  String _canonicalType(String type) {
    return const {
          'ai_token': 'text_delta',
          'tool_start': 'tool_input_start',
          'tool_end': 'tool_result',
          'reasoning': 'reasoning_delta',
        }[type] ??
        type;
  }

  void _upsertActiveTool({
    required String callId,
    required String toolName,
    required Map<String, dynamic> args,
  }) {
    final existing = state.activeToolCalls.indexWhere(
      (tc) => tc.callId == callId,
    );
    if (existing >= 0) {
      final updated = List<ToolCallDisplay>.from(state.activeToolCalls);
      updated[existing] = ToolCallDisplay(
        callId: callId,
        toolName: toolName,
        args: args,
        resultPreview: updated[existing].resultPreview,
      );
      _setState(state.copyWith(activeToolCalls: updated));
    } else {
      _setState(
        state.copyWith(
          activeToolCalls: [
            ...state.activeToolCalls,
            ToolCallDisplay(callId: callId, toolName: toolName, args: args),
          ],
          status: state.status == ChatStatus.awaitingApproval
              ? ChatStatus.awaitingApproval
              : ChatStatus.streaming,
        ),
      );
    }
  }

  void _upsertActiveToolByCallId(String callId, Map<String, dynamic> args) {
    final idx = state.activeToolCalls.indexWhere((tc) => tc.callId == callId);
    if (idx >= 0) {
      final updated = List<ToolCallDisplay>.from(state.activeToolCalls);
      updated[idx] = updated[idx].copyWith(args: args);
      _setState(state.copyWith(activeToolCalls: updated));
    }
  }

  void _onStatusChange(ConnectionStatus wsStatus) {
    if (_disposed) return;
    if (wsStatus == ConnectionStatus.connected) {
      _transportConnected = true;
      final currentStatus = state.status;
      _setState(
        state.copyWith(
          connected: true,
          status:
              currentStatus == ChatStatus.streaming ||
                  currentStatus == ChatStatus.awaitingApproval
              ? currentStatus
              : ChatStatus.idle,
          error: null,
          loadingHistory: false,
        ),
      );
      if (state.messages.isEmpty) {
        _setState(state.copyWith(streamingText: ''));
        _loadingHistory = true;
        _setState(state.copyWith(loadingHistory: true));
        _loadHistorySafely();
      }
    } else if (wsStatus == ConnectionStatus.disconnected) {
      _transportConnected = false;
      final wasStreaming = state.status == ChatStatus.streaming;
      if (wasStreaming && state.streamingText.isNotEmpty) {
        final partialMsg = ChatMessage(
          id: 'ai_partial_${DateTime.now().millisecondsSinceEpoch}',
          role: 'assistant',
          content: state.streamingText,
          timestamp: DateTime.now(),
        );
        _setState(
          state.copyWith(
            messages: [...state.messages, partialMsg],
            status: ChatStatus.disconnected,
            error: 'Connection lost — tap to reconnect',
            streamingText: '',
            activeToolCalls: [],
            connected: false,
          ),
        );
      } else {
        _setState(
          state.copyWith(status: ChatStatus.disconnected, connected: false),
        );
      }
    } else if (wsStatus == ConnectionStatus.connecting) {
      _transportConnected = false;
      _setState(state.copyWith(connected: false, error: null));
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _statusSubscription?.cancel();
    _messageSubscription?.cancel();
    _backendSubscription?.cancel();
    super.dispose();
  }
}

final hostProvider = StateProvider<String>((ref) => '127.0.0.1:8080');
final userIdProvider = StateProvider<String>((ref) => resolvedUserId);

final backendServiceProvider = Provider<BackendService>((ref) {
  final host = ref.watch(hostProvider);
  final service = BackendService(host: host);
  ref.onDispose(() => service.dispose());
  return service;
});

final wsClientProvider = Provider<WsClient>((ref) {
  final client = WsClient(
    host: ref.watch(hostProvider),
    userId: ref.watch(userIdProvider),
  );
  ref.onDispose(() => client.dispose());
  return client;
});

final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(
    host: ref.watch(hostProvider),
    userId: ref.watch(userIdProvider),
  );
});

final agentProvider = StateNotifierProvider<AgentNotifier, ChatState>((ref) {
  return AgentNotifier(
    ref.watch(wsClientProvider),
    ref.watch(apiClientProvider),
    ref.watch(backendServiceProvider),
  );
});

Map<String, dynamic> tryParseJson(String raw) {
  try {
    final decoded = jsonDecode(raw);
    if (decoded is Map<String, dynamic>) return decoded;
    if (decoded is Map) return Map<String, dynamic>.from(decoded);
    return {};
  } catch (_) {
    return {};
  }
}
