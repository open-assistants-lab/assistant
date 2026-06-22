import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../widgets/app_input.dart';
import '../../features/chat/widgets/chat_message_list.dart';
import '../../features/chat/widgets/chat_input.dart';
import '../../features/chat/widgets/error_bar.dart';
import '../../features/chat/widgets/connection_banner.dart';
import '../../features/chat/widgets/jump_to_bottom_button.dart';
import '../../features/companion/companion_pulse.dart';
import '../../features/companion/companion_context_pill.dart';
import '../../features/companion/companion_toast.dart';

enum DesktopSidebarItem {
  chat(
    icon: Symbols.chat,
    activeIcon: Symbols.chat,
    label: 'Chat',
    path: '/workspace',
  ),
  tools(
    icon: Symbols.handyman,
    activeIcon: Symbols.handyman,
    label: 'Tools',
    path: '/tools',
  ),
  skills(
    icon: Symbols.psychology,
    activeIcon: Symbols.psychology,
    label: 'Skills',
    path: '/skills',
  ),
  subagents(
    icon: Symbols.robot_2,
    activeIcon: Symbols.robot_2,
    label: 'Subagents',
    path: '/subagents',
  ),
  settings(
    icon: Symbols.settings,
    activeIcon: Symbols.settings,
    label: 'Settings',
    path: '/settings',
  );

  final IconData icon;
  final IconData activeIcon;
  final String label;
  final String path;

  const DesktopSidebarItem({
    required this.icon,
    required this.activeIcon,
    required this.label,
    required this.path,
  });
}

final desktopSidebarProvider = StateProvider<DesktopSidebarItem>(
  (ref) => DesktopSidebarItem.chat,
);

class DesktopLayout extends ConsumerWidget {
  final Widget child;
  const DesktopLayout({super.key, required this.child});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    const sidebarWidth = 240.0;
    const dividerCount = 2;

    return Scaffold(
      body: Stack(
        children: [
          LayoutBuilder(
            builder: (context, constraints) {
              final totalWidth = constraints.maxWidth;
              final remainingWidth = totalWidth - sidebarWidth - dividerCount;
              final chatPanelWidth = remainingWidth * 0.28;
              final contentWidth = remainingWidth * 0.72;
              return Row(
                children: [
                  const Sidebar(width: sidebarWidth),
                  Container(
                    width: 1,
                    color: context.tokens.colors.borderSubtle,
                  ),
                  SizedBox(width: chatPanelWidth, child: const _ChatPanel()),
                  Container(
                    width: 1,
                    color: context.tokens.colors.borderSubtle,
                  ),
                  SizedBox(width: contentWidth, child: child),
                ],
              );
            },
          ),
          const CompanionToastOverlay(),
        ],
      ),
    );
  }
}

class Sidebar extends ConsumerWidget {
  final double width;
  const Sidebar({super.key, required this.width});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tokens = context.tokens;

    return Container(
      width: width,
      color: tokens.colors.bgCanvas,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const CompanionPulse(),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: AppSearchField(hint: 'Search chats...'),
          ),
          const SizedBox(height: 10),
          const Divider(height: 1),
          Expanded(
            child: Padding(
              padding: EdgeInsets.symmetric(
                horizontal: tokens.spacing.md,
                vertical: tokens.spacing.xs,
              ),
              child: SidebarItem(
                item: DesktopSidebarItem.chat,
                selected: true,
                onTap: () => context.go('/workspace'),
              ),
            ),
          ),
          const Divider(height: 1),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: EdgeInsets.symmetric(
                  horizontal: tokens.spacing.md,
                  vertical: tokens.spacing.xs,
                ),
                child: SidebarItem(
                  item: DesktopSidebarItem.tools,
                  selected: false,
                  onTap: () => context.go('/tools'),
                ),
              ),
              Padding(
                padding: EdgeInsets.symmetric(
                  horizontal: tokens.spacing.md,
                  vertical: tokens.spacing.xs,
                ),
                child: SidebarItem(
                  item: DesktopSidebarItem.skills,
                  selected: false,
                  onTap: () => context.go('/skills'),
                ),
              ),
              Padding(
                padding: EdgeInsets.symmetric(
                  horizontal: tokens.spacing.md,
                  vertical: tokens.spacing.xs,
                ),
                child: SidebarItem(
                  item: DesktopSidebarItem.subagents,
                  selected: false,
                  onTap: () => context.go('/subagents'),
                ),
              ),
              Padding(
                padding: EdgeInsets.only(
                  left: tokens.spacing.md,
                  right: tokens.spacing.sm,
                  bottom: tokens.spacing.sm,
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: SidebarItem(
                        item: DesktopSidebarItem.settings,
                        selected: false,
                        onTap: () => context.go('/settings'),
                      ),
                    ),
                    IconButton(
                      icon: Icon(
                        tokens.isDark ? Symbols.light_mode : Symbols.dark_mode,
                        size: 18,
                        color: tokens.colors.textSecondary,
                      ),
                      onPressed: () =>
                          ref.read(themeModeProvider.notifier).toggle(),
                      tooltip: tokens.isDark
                          ? 'Switch to light mode'
                          : 'Switch to dark mode',
                    ),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class SidebarItem extends StatefulWidget {
  final DesktopSidebarItem item;
  final bool selected;
  final VoidCallback onTap;
  const SidebarItem({
    super.key,
    required this.item,
    required this.selected,
    required this.onTap,
  });

  @override
  State<SidebarItem> createState() => _SidebarItemState();
}

class _SidebarItemState extends State<SidebarItem> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final showBg = widget.selected || _hover;
    final textColor = (widget.selected || _hover)
        ? tokens.colors.textPrimary
        : tokens.colors.textSecondary;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: tokens.motion.fast,
          curve: tokens.motion.curveStandard,
          padding: EdgeInsets.symmetric(
            horizontal: tokens.spacing.md,
            vertical: tokens.spacing.sm + 2,
          ),
          decoration: BoxDecoration(
            color: showBg ? tokens.colors.bgSurface : Colors.transparent,
            borderRadius: tokens.radius.smAll,
            border: widget.selected
                ? Border(
                    left: BorderSide(
                      color: tokens.colors.accent,
                      width: 3,
                    ),
                  )
                : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                widget.selected ? widget.item.activeIcon : widget.item.icon,
                size: 18,
                color: textColor,
              ),
              SizedBox(width: tokens.spacing.sm + 2),
              Flexible(
                child: Text(
                  widget.item.label,
                  style: tokens.typography.textTheme.bodyMedium?.copyWith(
                    color: textColor,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ChatPanel extends ConsumerStatefulWidget {
  const _ChatPanel();
  @override
  ConsumerState<_ChatPanel> createState() => _ChatPanelState();
}

class _ChatPanelState extends ConsumerState<_ChatPanel> {
  final _scrollController = ScrollController();
  final Map<String, GlobalKey> _messageKeys = {};
  bool _pendingScrollToBottom = false;
  bool _showJumpToBottom = false;
  bool _atBottom = true;
  bool _hasAutoScrolled = false;
  int _unreadCount = 0;
  int _lastSeenMessageCount = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(agentProvider.notifier).connect();
    });
    _scrollController.addListener(_onScrollPositionChanged);
    _lastSeenMessageCount = ref.read(agentProvider).messages.length;
  }

  void _onScrollPositionChanged() {
    if (!_scrollController.hasClients) return;
    _atBottom = _scrollController.position.extentBefore < 16;
    final shouldShow = !_atBottom;
    if (_showJumpToBottom != shouldShow) {
      setState(() => _showJumpToBottom = shouldShow);
    }
    if (_atBottom) {
      _unreadCount = 0;
    }
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScrollPositionChanged);
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!mounted) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (!_scrollController.hasClients) return;
      _scrollController.jumpTo(0);
    });
  }

  void _jumpToBottom() {
    if (!mounted) return;
    _unreadCount = 0;
    if (_scrollController.hasClients) {
      _scrollController.jumpTo(0);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final state = ref.watch(agentProvider);

    ref.listen<ChatState>(agentProvider, (prev, next) {
      final newMessageCount = next.messages.length - _lastSeenMessageCount;
      if (newMessageCount > 0 && _showJumpToBottom) {
        _unreadCount += newMessageCount;
      }
      _lastSeenMessageCount = next.messages.length;

      final shouldScrollToBottom = _pendingScrollToBottom &&
          next.messages.isNotEmpty;
      final shouldScrollOnLoad = next.messages.isNotEmpty &&
          prev?.messages.isEmpty == true &&
          next.status == ChatStatus.idle &&
          !_hasAutoScrolled;
      if (shouldScrollToBottom) {
        _pendingScrollToBottom = false;
        _scrollToBottom();
        return;
      }
      if (shouldScrollOnLoad) {
        _hasAutoScrolled = true;
        _scrollToBottom();
        return;
      }
      if (next.status == ChatStatus.streaming && _atBottom) {
        _scrollToBottom();
      }
    });

    return Container(
      color: tokens.colors.bgCanvas,
      child: Column(
        children: [
          ConnectionBanner(
            connected: state.connected,
            isDisconnected: state.status == ChatStatus.disconnected,
            onReconnect: () => ref.read(agentProvider.notifier).connect(),
            backendStatus: state.backendStatus,
          ),
          Expanded(
            child: Stack(
              children: [
                _PanelMessageList(
                  state: state,
                  scrollController: _scrollController,
                  messageKeys: _messageKeys,
                ),
                if (_showJumpToBottom)
                  Positioned(
                    right: 16,
                    bottom: 8,
                    child: JumpToBottomButton(
                      newCount: _unreadCount,
                      onPressed: _jumpToBottom,
                    ),
                  ),
              ],
            ),
          ),
          if (state.status == ChatStatus.error && state.error != null)
            ErrorBar(error: state.error!),
          const ChatInput(),
        ],
      ),
    );
  }
}

class _PanelMessageList extends ConsumerWidget {
  final ChatState state;
  final ScrollController scrollController;
  final Map<String, GlobalKey> messageKeys;
  const _PanelMessageList({
    required this.state,
    required this.scrollController,
    required this.messageKeys,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tokens = context.tokens;
    return ChatMessageList(
        key: const ValueKey('desktop-chat-message-list'),
        messages: state.messages,
        isStreaming: state.status == ChatStatus.streaming,
        streamingText: state.streamingText,
        reasoningText: state.reasoningText,
        activeToolCalls: state.activeToolCalls,
        scrollController: scrollController,
        isLoading: state.loadingHistory,
        messageKeys: messageKeys,
        header: state.messages.isNotEmpty
            ? const CompanionContextPill()
            : null,
        emptyBuilder: (_) => Center(
          child: Padding(
            padding: const EdgeInsets.all(AppSpacing.screenEdge),
            child: Text(
              'Ask anything...',
              style: tokens.typography.textTheme.bodyMedium?.copyWith(
                color: tokens.colors.textTertiary,
              ),
            ),
          ),
        ),
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.cardPadding,
          vertical: AppSpacing.itemGap,
        ),
    );
  }
}

