import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../providers/session_list_provider.dart';
import '../../providers/chat_tab_provider.dart';
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

class _Divider extends StatelessWidget {
  final void Function(double delta) onDrag;
  const _Divider({required this.onDrag});

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    return MouseRegion(
      cursor: SystemMouseCursors.resizeColumn,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onHorizontalDragUpdate: (d) => onDrag(d.delta.dx),
        child: Container(
          width: 6,
          color: Colors.transparent,
          child: Center(
            child: Container(width: 2, height: 40, decoration: BoxDecoration(
              color: tokens.colors.borderSubtle, borderRadius: BorderRadius.circular(1),
            )),
          ),
        ),
      ),
    );
  }
}

class DesktopLayout extends ConsumerStatefulWidget {
  final Widget child;
  const DesktopLayout({super.key, required this.child});

  @override
  ConsumerState<DesktopLayout> createState() => _DesktopLayoutState();
}

class _DesktopLayoutState extends ConsumerState<DesktopLayout> {
  double _sidebarWidth = 240;
  double _chatFraction = 0.5;

  static const double _minSidebar = 180;
  static const double _maxSidebar = 400;
  static const double _minChat = 200;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          LayoutBuilder(
            builder: (context, constraints) {
              final total = constraints.maxWidth;
              final remaining = total - _sidebarWidth;
              final chatWidth = (remaining * _chatFraction).clamp(_minChat, remaining - _minChat);
              final contentWidth = (remaining - chatWidth).clamp(_minChat, remaining - _minChat);

              return Row(
                children: [
                  SizedBox(width: _sidebarWidth, child: const Sidebar()),
                  _Divider(onDrag: (dx) {
                    setState(() => _sidebarWidth = (_sidebarWidth + dx).clamp(_minSidebar, _maxSidebar));
                  }),
                  SizedBox(width: chatWidth, child: const _ChatPanel()),
                  _Divider(onDrag: (dx) {
                    final newChat = (chatWidth + dx).clamp(_minChat, remaining - _minChat);
                    setState(() => _chatFraction = newChat / remaining);
                  }),
                  SizedBox(width: contentWidth, child: widget.child),
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

class Sidebar extends ConsumerStatefulWidget {
  const Sidebar({super.key});

  @override
  ConsumerState<Sidebar> createState() => _SidebarState();
}

class _SidebarState extends ConsumerState<Sidebar> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() {
      ref.read(sessionListProvider.notifier).loadSessions();
    });
  }

  String _formatTime(String? iso) {
    if (iso == null) return '';
    final dt = DateTime.tryParse(iso);
    if (dt == null) return '';
    final diff = DateTime.now().toUtc().difference(dt);
    if (diff.inMinutes < 1) return 'now';
    if (diff.inMinutes < 60) return '${diff.inMinutes}m';
    if (diff.inHours < 24) return '${diff.inHours}h';
    if (diff.inDays < 7) return '${diff.inDays}d';
    return '${(diff.inDays / 7).floor()}w';
  }

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final sessionListState = ref.watch(sessionListProvider);
    final activeSessionId = ref.watch(agentProvider).sessionId;

    return Container(
      color: tokens.colors.bgCanvas,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const CompanionPulse(),
          Padding(
            padding: EdgeInsets.fromLTRB(
              tokens.spacing.md,
              tokens.spacing.xs,
              tokens.spacing.sm,
              0,
            ),
            child: Row(
              children: [
                Text(
                  'Chats',
                  style: tokens.typography.textTheme.labelLarge?.copyWith(
                    color: tokens.colors.textSecondary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const Spacer(),
                SizedBox(
                  width: 28,
                  height: 28,
                  child: IconButton(
                    icon: Icon(Symbols.add, size: 16, color: tokens.colors.textSecondary),
                    onPressed: () {
                      final newId = ref.read(agentProvider.notifier).newSession();
                      ref.read(activeChatTabProvider.notifier).state = newId;
                    },
                    padding: EdgeInsets.zero,
                    tooltip: 'New chat',
                    style: IconButton.styleFrom(
                      minimumSize: Size.zero,
                      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          const Divider(height: 1),
          Expanded(
            child: _buildSessionList(tokens, sessionListState, activeSessionId),
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

  Widget _buildSessionList(
    EaTokens tokens,
    SessionListState state,
    String activeSessionId,
  ) {
    if (state.loading) {
      return const Center(child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)));
    }

    if (state.error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Symbols.error, size: 20, color: tokens.colors.error),
              const SizedBox(height: 8),
              Text(
                'Failed to load sessions',
                style: tokens.typography.textTheme.bodySmall?.copyWith(
                  color: tokens.colors.textTertiary,
                ),
              ),
            ],
          ),
        ),
      );
    }

    if (state.sessions.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            'No conversations yet',
            style: tokens.typography.textTheme.bodySmall?.copyWith(
              color: tokens.colors.textTertiary,
            ),
          ),
        ),
      );
    }

    return ListView.builder(
      padding: EdgeInsets.symmetric(vertical: tokens.spacing.xs),
      itemCount: state.sessions.length,
      itemBuilder: (context, index) {
        final session = state.sessions[index];
        final sessionId = session['session_id']?.toString() ?? '';
        final lastMessage = session['last_message']?.toString() ?? '';
        final lastUpdated = session['last_updated']?.toString();
        final timeAgo = _formatTime(lastUpdated);
        final isActive = sessionId == activeSessionId;
        final preview = lastMessage.length > 50
            ? '${lastMessage.substring(0, 50)}…'
            : lastMessage;

        return _SessionListItem(
          sessionId: sessionId,
          preview: preview,
          timeAgo: timeAgo,
          isActive: isActive,
          onTap: () {
            ref.read(agentProvider.notifier).setSessionId(sessionId);
            ref.read(activeChatTabProvider.notifier).state = sessionId;
          },
          onDelete: () {
            showDialog(
              context: context,
              builder: (ctx) => AlertDialog(
                title: const Text('Delete conversation?'),
                content: const Text('This action cannot be undone.'),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(ctx),
                    child: const Text('Cancel'),
                  ),
                  TextButton(
                    onPressed: () {
                      ref.read(agentProvider.notifier).deleteSession(sessionId);
                      ref.read(sessionListProvider.notifier).removeSession(sessionId);
                      Navigator.pop(ctx);
                    },
                    child: Text('Delete',
                        style: TextStyle(color: tokens.colors.error)),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }
}

class _SessionListItem extends StatefulWidget {
  final String sessionId;
  final String preview;
  final String timeAgo;
  final bool isActive;
  final VoidCallback onTap;
  final VoidCallback onDelete;

  const _SessionListItem({
    required this.sessionId,
    required this.preview,
    required this.timeAgo,
    required this.isActive,
    required this.onTap,
    required this.onDelete,
  });

  @override
  State<_SessionListItem> createState() => _SessionListItemState();
}

class _SessionListItemState extends State<_SessionListItem> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final tokens = context.tokens;
    final showBg = widget.isActive || _hover;
    final textColor = (widget.isActive || _hover)
        ? tokens.colors.textPrimary
        : tokens.colors.textSecondary;

    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        onLongPress: widget.onDelete,
        child: AnimatedContainer(
          duration: tokens.motion.fast,
          curve: tokens.motion.curveStandard,
          padding: EdgeInsets.symmetric(
            horizontal: tokens.spacing.md,
            vertical: tokens.spacing.sm + 2,
          ),
          decoration: BoxDecoration(
            color: showBg ? tokens.colors.bgSurface : Colors.transparent,
            borderRadius: BorderRadius.zero,
            border: widget.isActive
                ? Border(
                    left: BorderSide(
                      color: tokens.colors.accent,
                      width: 3,
                    ),
                  )
                : null,
          ),
          child: Row(
            children: [
              Icon(
                Symbols.circle,
                size: 6,
                color: widget.isActive
                    ? tokens.colors.accent
                    : tokens.colors.textTertiary,
              ),
              SizedBox(width: tokens.spacing.sm + 2),
              Expanded(
                child: Text(
                  widget.preview.isNotEmpty ? widget.preview : 'New chat',
                  style: tokens.typography.textTheme.bodySmall?.copyWith(
                    color: textColor,
                    fontSize: 12,
                  ),
                  overflow: TextOverflow.ellipsis,
                  maxLines: 1,
                ),
              ),
              if (widget.timeAgo.isNotEmpty)
                Padding(
                  padding: EdgeInsets.only(left: tokens.spacing.xs),
                  child: Text(
                    widget.timeAgo,
                    style: tokens.typography.textTheme.labelSmall?.copyWith(
                      color: tokens.colors.textTertiary,
                      fontSize: 10,
                    ),
                  ),
                ),
            ],
          ),
        ),
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
