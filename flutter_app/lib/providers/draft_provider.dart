import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Per-session input drafts. Keyed by session_id.
/// In-memory only — not persisted to disk in this batch.
final draftProvider = StateNotifierProvider<DraftNotifier, Map<String, String>>(
  (ref) => DraftNotifier(),
);

class DraftNotifier extends StateNotifier<Map<String, String>> {
  DraftNotifier() : super(const {});

  String? load(String sessionId) => state[sessionId];

  void save(String sessionId, String text) {
    if (text.isEmpty) {
      clear(sessionId);
      return;
    }
    state = {...state, sessionId: text};
  }

  void clear(String sessionId) {
    if (!state.containsKey(sessionId)) return;
    final next = Map<String, String>.from(state)..remove(sessionId);
    state = next;
  }
}
