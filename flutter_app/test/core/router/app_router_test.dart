import 'package:executive_assistant/core/router/app_router.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('isUtilityRoute', () {
    test('returns true for utility routes', () {
      for (final path in const [
        '/tools', '/skills', '/subagents', '/settings',
      ]) {
        expect(isUtilityRoute(path), isTrue, reason: '$path should be utility');
      }
    });

    test('returns false for non-utility routes', () {
      for (final path in const [
        '/workspace', '/chat',
      ]) {
        expect(isUtilityRoute(path), isFalse, reason: '$path should NOT be utility');
      }
    });

    test('returns false for unknown routes', () {
      expect(isUtilityRoute('/unknown'), isFalse);
      expect(isUtilityRoute(''), isFalse);
    });
  });
}
