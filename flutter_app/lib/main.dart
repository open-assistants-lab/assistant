import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

import 'core/crash_reporting.dart';
import 'core/router/app_router.dart';
import 'providers/agent_provider.dart';
import 'providers/workspace_provider.dart';
import 'services/instrumented_app.dart';
import 'services/test_instrumentation.dart';
import 'theme/app_theme.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await loadScrollPositionsFromPrefs();
  await initCrashReporting();

  final prefs = await SharedPreferences.getInstance();
  var userId = prefs.getString('ea_user_id');
  if (userId == null || userId.isEmpty) {
    userId = const Uuid().v4();
    await prefs.setString('ea_user_id', userId);
  }
  resolvedUserId = userId;

  runApp(
    InstrumentedApp(
      child: const ProviderScope(child: ExecutiveAssistantApp()),
    ),
  );
}

class ExecutiveAssistantApp extends ConsumerWidget {
  const ExecutiveAssistantApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(appRouterProvider);
    final themeMode = ref.watch(themeModeProvider);
    return MaterialApp.router(
      title: 'Assistant',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: themeMode,
      routerConfig: router,
    );
  }
}
