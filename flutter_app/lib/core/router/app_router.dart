import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/chat/chat_screen.dart';
import '../../features/onboarding/onboarding_screen.dart';
import '../../features/onboarding/onboarding_provider.dart';
import '../../features/workspace/workspace_panel.dart';
import '../../features/tools/tools_panel.dart';
import '../../features/skills/skills_sidebar_panel.dart';
import '../../features/subagents/subagents_sidebar_panel.dart';
import '../../features/settings/settings_screen.dart';
import '../layout/responsive_shell.dart';
import '../../theme/tokens/motion.dart';
import '../../theme/app_theme.dart';
import '../../services/instrumented_app.dart';

final _rootNavigatorKey = GlobalKey<NavigatorState>();
final _shellNavigatorKey = GlobalKey<NavigatorState>();

const _utilityRoutes = {
  '/tools',
  '/skills',
  '/subagents',
  '/settings',
};

bool isUtilityRoute(String path) => _utilityRoutes.contains(path);

final appRouterProvider = Provider<GoRouter>((ref) {
  final onboardingComplete = ref.watch(onboardingCompleteProvider);

  return GoRouter(
    navigatorKey: _rootNavigatorKey,
    observers: [EaRouteObserver()],
    initialLocation: '/onboarding',
    redirect: (context, state) {
      if (onboardingComplete == null) return null; // still loading

      final path = state.uri.path;
      final isOnboarding = path == '/onboarding';

      if (!onboardingComplete && !isOnboarding) {
        return '/onboarding';
      }
      if (onboardingComplete && (isOnboarding || path == '/chat')) {
        return '/workspace';
      }
      return null;
    },
    routes: [
      // Redirect root to workspace
      GoRoute(
        path: '/',
        redirect: (_, __) => '/workspace',
      ),
      GoRoute(
        path: '/onboarding',
        name: 'onboarding',
        builder: (context, state) => const OnboardingScreen(),
      ),
      ShellRoute(
        navigatorKey: _shellNavigatorKey,
        builder: (context, state, child) {
          return ResponsiveShell(state: state, child: child);
        },
        routes: [
          GoRoute(
            path: '/workspace',
            name: 'files',
            pageBuilder: (context, state) => CustomTransitionPage(
              key: state.pageKey,
              child: const WorkspacePanel(),
              transitionsBuilder: (context, animation, secondaryAnimation, child) {
                return FadeTransition(opacity: animation, child: child);
              },
              transitionDuration: EaMotion.standard.fluid,
            ),
          ),
          GoRoute(
            path: '/tools',
            name: 'tools',
            pageBuilder: (context, state) => CustomTransitionPage(
              key: state.pageKey,
              child: const ToolsPanel(),
              transitionsBuilder: (context, animation, secondaryAnimation, child) {
                return FadeTransition(opacity: animation, child: child);
              },
              transitionDuration: EaMotion.standard.fluid,
            ),
          ),
          GoRoute(
            path: '/skills',
            name: 'skills',
            pageBuilder: (context, state) => CustomTransitionPage(
              key: state.pageKey,
              child: const SkillsSidebarPanel(),
              transitionsBuilder: (context, animation, secondaryAnimation, child) {
                return FadeTransition(opacity: animation, child: child);
              },
              transitionDuration: EaMotion.standard.fluid,
            ),
          ),
          GoRoute(
            path: '/subagents',
            name: 'subagents',
            pageBuilder: (context, state) => CustomTransitionPage(
              key: state.pageKey,
              child: const SubagentsSidebarPanel(),
              transitionsBuilder: (context, animation, secondaryAnimation, child) {
                return FadeTransition(opacity: animation, child: child);
              },
              transitionDuration: EaMotion.standard.fluid,
            ),
          ),
          GoRoute(
            path: '/settings',
            name: 'settings',
            pageBuilder: (context, state) => CustomTransitionPage(
              key: state.pageKey,
              child: const SettingsScreen(),
              transitionsBuilder: (context, animation, secondaryAnimation, child) {
                return FadeTransition(opacity: animation, child: child);
              },
              transitionDuration: EaMotion.standard.fluid,
            ),
          ),
        ],
      ),
      GoRoute(
        path: '/chat',
        name: 'chat',
        builder: (context, state) => const ChatScreen(),
      ),
    ],
  );
});