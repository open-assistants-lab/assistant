import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'agent_provider.dart';

// Scroll positions are no longer persisted - session switching always
// scrolls to the bottom (latest message), matching Slack/Discord UX.
// We clean up any stale prefs from older versions on startup.
Future<void> loadScrollPositionsFromPrefs() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('scroll_positions');
  } catch (_) {}
}

final effectiveModelProvider = Provider<String>((ref) {
  return ref.watch(selectedModelProvider);
});

final activeSessionIdProvider = StateProvider<String>((ref) => 'default');
