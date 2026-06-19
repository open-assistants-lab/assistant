import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/message.dart';

enum ConnectionStatus { disconnected, connecting, connected }

class WsClient {
  String _host;
  String _userId;
  String? _sessionId;
  String? _model;
  Map<String, String>? _providerKeys;
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  final _messageController = StreamController<WsMessage>.broadcast();
  final _statusController = StreamController<ConnectionStatus>.broadcast();
  final _pendingApprovals = <String, ToolCallDisplay>{};
  bool _disposed = false;

  WsClient({
    String host = '127.0.0.1:8080',
    String userId = 'default',
    String? sessionId,
  }) : _host = host,
       _userId = userId,
       _sessionId = sessionId;

  Stream<WsMessage> get messages => _messageController.stream;
  Stream<ConnectionStatus> get status => _statusController.stream;
  bool get isConnected => _channel != null && !_disposed;
  set model(String? m) => _model = m;
  set providerKeys(Map<String, String>? keys) => _providerKeys = keys;
  Map<String, ToolCallDisplay> get pendingApprovals =>
      Map.unmodifiable(_pendingApprovals);

  int _reconnectAttempts = 0;
  static const int _maxReconnectAttempts = 5;
  static const Duration _reconnectBaseDelay = Duration(seconds: 1);
  Timer? _reconnectTimer;
  Timer? _pingTimer;
  bool _intentionalDisconnect = false;

  /// Clean up existing channel/resources without resetting reconnect counter.
  void _cleanupExisting() {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    _subscription?.cancel();
    _subscription = null;
    try {
      _channel?.sink.close();
    } catch (e) {
      debugPrint('[WsClient] Error closing sink: $e');
    }
    _channel = null;
    _pendingApprovals.clear();
  }

  void _cleanupConnection() {
    _intentionalDisconnect = false;
    _cleanupExisting();
    _reconnectAttempts = 0;
  }

  /// Auto-detect ws:// vs wss:// based on host string.
  /// Supports: localhost (ws), http:// (ws), https:// (wss), :443 (wss).
  String get wsScheme {
    if (_host.startsWith('https://')) return 'wss';
    if (_host.startsWith('http://')) return 'ws';
    if (_host.contains(':443')) return 'wss';
    return 'ws';
  }

  /// Strip any http(s):// prefix so we can build a clean ws URI.
  String get cleanHost {
    return _host.replaceFirst(RegExp(r'^https?://'), '');
  }

  void _scheduleReconnect() {
    if (_disposed || _intentionalDisconnect) return;
    if (_reconnectAttempts >= _maxReconnectAttempts) {
      _safeAddStatus(ConnectionStatus.disconnected);
      return;
    }
    _reconnectAttempts++;
    final delay = _reconnectBaseDelay * _reconnectAttempts;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, () {
      if (!_disposed) connect();
    });
  }

  void _safeAddStatus(ConnectionStatus status) {
    if (!_disposed && !_statusController.isClosed) {
      _statusController.add(status);
    }
  }

  void _safeAddMessage(WsMessage msg) {
    if (!_disposed && !_messageController.isClosed) {
      _messageController.add(msg);
    }
  }

  void connect() {
    if (_disposed) return;
    _cleanupExisting();
    _safeAddStatus(ConnectionStatus.connecting);
    final scheme = wsScheme;
    final host = cleanHost;
    try {
      final queryParams = <String, String>{};
      if (_sessionId != null && _sessionId!.isNotEmpty) {
        queryParams['session_id'] = _sessionId!;
      }
      final uri = Uri(
        scheme: scheme,
        host: host,
        path: '/ws/conversation',
        queryParameters: queryParams.isNotEmpty ? queryParams : null,
      );
      debugPrint('[WsClient] Connecting to $uri');
      _channel = WebSocketChannel.connect(uri);
      _channel!.ready.then((_) {
        if (!_disposed && _channel != null) {
          debugPrint('[WsClient] WebSocket ready, sending ping');
          try {
            _send({'type': 'ping'});
          } catch (e) {
            debugPrint('[WsClient] Ping send failed: $e');
          }
        }
      }).catchError((error) {
        debugPrint('[WsClient] WebSocket ready failed: $error');
        if (!_disposed) {
          _channel = null;
          _subscription = null;
          _scheduleReconnect();
        }
      });
      _subscription = _channel!.stream.listen(
        _onData,
        onError: (error) {
          debugPrint('[WsClient] Stream error: $error');
          _onError(error);
        },
        onDone: () {
          debugPrint('[WsClient] Stream done');
          _onDone();
        },
        cancelOnError: false,
      );
      _pingTimer?.cancel();
      _pingTimer = Timer.periodic(const Duration(seconds: 30), (_) {
        if (!_disposed && _channel != null) ping();
      });
    } catch (e) {
      debugPrint('[WsClient] Connection failed: $e');
      if (!_disposed) {
        _channel = null;
        _subscription = null;
        _scheduleReconnect();
      }
    }
  }

  final _pendingMessages = <Map<String, dynamic>>[];

  void sendMessage(String content, {bool verbose = false, String workspaceId = 'personal'}) {
    final payload = <String, dynamic>{
      'type': 'user_message',
      'content': content,
      'user_id': _userId,
      'workspace_id': workspaceId,
      'verbose': verbose,
      if (_sessionId != null && _sessionId!.isNotEmpty) 'session_id': _sessionId,
      if (_model != null) 'model': _model,
      if (_providerKeys != null && _providerKeys!.isNotEmpty) 'provider_keys': _providerKeys,
    };
    if (_channel != null && !_disposed && _reconnectAttempts == 0) {
      _send(payload);
    } else {
      _pendingMessages.add(payload);
    }
  }

  void approveToolCall(String callId) {
    _send({'type': 'approve', 'call_id': callId});
    _pendingApprovals.remove(callId);
  }

  void rejectToolCall(String callId, {String reason = ''}) {
    _send({'type': 'reject', 'call_id': callId, 'reason': reason});
    _pendingApprovals.remove(callId);
  }

  void editAndApprove(String callId, Map<String, dynamic> editedArgs) {
    _send({
      'type': 'edit_and_approve',
      'call_id': callId,
      'edited_args': editedArgs,
    });
    _pendingApprovals.remove(callId);
  }

  void cancel() {
    _send({'type': 'cancel'});
  }

  void ping() {
    _send({'type': 'ping'});
  }

  void updateHost(String host) {
    _host = host;
  }

  void updateUserId(String userId) {
    _userId = userId;
  }

  void updateSessionId(String? sessionId) {
    _sessionId = sessionId;
  }

  void disconnect() {
    _intentionalDisconnect = true;
    _cleanupConnection();
    _safeAddStatus(ConnectionStatus.disconnected);
  }

  void _send(Map<String, dynamic> data) {
    if (_channel != null && !_disposed) {
      try {
        _channel!.sink.add(jsonEncode(data));
      } catch (e) {
        debugPrint('[WsClient] Send error: $e');
      }
    }
  }

  void _onData(dynamic data) {
    if (_disposed) return;
    if (data is! String) return;
    try {
      final json = jsonDecode(data) as Map<String, dynamic>;
      final msg = WsMessage.fromJson(json);
      if (msg.type == 'pong') {
        _reconnectAttempts = 0;
        _safeAddStatus(ConnectionStatus.connected);
        while (_pendingMessages.isNotEmpty) {
          try {
            _send(_pendingMessages.first);
            _pendingMessages.removeAt(0);
          } catch (e) {
            debugPrint('[WsClient] Failed to send queued message: $e');
            break; // Keep remaining messages for next reconnect
          }
        }
        return;
      }
      _handleMessage(msg);
    } on FormatException catch (e) {
      debugPrint('[WsClient] Malformed JSON: $e');
    } catch (e) {
      debugPrint('[WsClient] Unexpected error processing message: $e');
    }
  }

  Map<String, dynamic> _safeArgs(dynamic value) {
    if (value is Map<String, dynamic>) return value;
    if (value is Map) return Map<String, dynamic>.from(value);
    return {};
  }

  void _handleMessage(WsMessage msg) {
    if (_disposed) return;
    switch (msg.type) {
      case 'interrupt':
        final callId = msg['call_id']?.toString() ?? '';
        final tool = msg['tool']?.toString() ?? '';
        final args = _safeArgs(msg['args']);
        if (callId.isNotEmpty) {
          _pendingApprovals[callId] = ToolCallDisplay(
            callId: callId,
            toolName: tool,
            args: args,
            isPending: true,
          );
        }
        _safeAddMessage(msg);
        break;
      case 'tool_end':
        _safeAddMessage(msg);
        break;
      default:
        _safeAddMessage(msg);
    }
  }

  void _onError(dynamic error) {
    if (_disposed) return;
    _channel = null;
    _subscription = null;
    _scheduleReconnect();
  }

  void _onDone() {
    if (_disposed) return;
    _channel = null;
    _subscription = null;
    _pingTimer?.cancel();
    _pingTimer = null;
    _scheduleReconnect();
  }

  void dispose() {
    _disposed = true;
    _reconnectTimer?.cancel();
    _pingTimer?.cancel();
    _subscription?.cancel();
    disconnect();
    _messageController.close();
    _statusController.close();
  }
}
