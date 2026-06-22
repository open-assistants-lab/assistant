import 'package:flutter_riverpod/flutter_riverpod.dart';

/// The active session ID for the current chat tab.
final activeChatTabProvider = StateProvider<String>((ref) => 'default');
