import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../router/app_router.dart' show isUtilityRoute;
import '../constants/breakpoints.dart';
import 'desktop_layout.dart';
import 'desktop_utility_layout.dart';
import 'mobile_layout.dart';
import 'tablet_rail.dart';

class ResponsiveShell extends ConsumerWidget {
  final GoRouterState state;
  final Widget child;

  const ResponsiveShell({super.key, required this.state, required this.child});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final utility = isUtilityRoute(state.matchedLocation);
        if (constraints.maxWidth >= Breakpoints.desktop) {
          return utility
              ? DesktopUtilityLayout(
                  sidebar: const Sidebar(),
                  child: child,
                )
              : DesktopLayout(child: child);
        }
        if (constraints.maxWidth >= Breakpoints.mobile) {
          return TabletRailLayout(child: child);
        }
        return MobileLayout(child: child);
      },
    );
  }
}
