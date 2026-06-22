import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/api_client.dart';
import 'agent_provider.dart';

class SessionListState {
  final List<Map<String, dynamic>> sessions;
  final bool loading;
  final String? error;

  SessionListState({
    this.sessions = const [],
    this.loading = false,
    this.error,
  });
}

class SessionListNotifier extends StateNotifier<SessionListState> {
  final ApiClient _apiClient;

  SessionListNotifier(this._apiClient) : super(SessionListState());

  Future<void> loadSessions() async {
    state = SessionListState(loading: true);
    try {
      final sessions = await _apiClient.listSessions();
      state = SessionListState(sessions: sessions.cast<Map<String, dynamic>>());
    } catch (e) {
      state = SessionListState(error: e.toString());
    }
  }

  void removeSession(String sessionId) {
    state = SessionListState(
      sessions: state.sessions.where((s) => s['session_id'] != sessionId).toList(),
    );
  }

  void addSession(Map<String, dynamic> session) {
    state = SessionListState(
      sessions: [session, ...state.sessions],
    );
  }
}

final sessionListProvider = StateNotifierProvider<SessionListNotifier, SessionListState>(
  (ref) => SessionListNotifier(ref.watch(apiClientProvider)),
);
