import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../providers/workspace_provider.dart';
import 'widgets/chat_message_list.dart';
import 'widgets/chat_input.dart';
import 'widgets/error_bar.dart';
import 'widgets/connection_banner.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _scrollController = ScrollController();
  bool _pendingScrollToBottom = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ref.read(agentProvider.notifier).connect();
    });
  }

  void _scrollToBottom() {
    if (!_scrollController.hasClients) return;
    void jump() {
      if (!mounted || !_scrollController.hasClients) return;
      final max = _scrollController.position.maxScrollExtent;
      if (max > 0) _scrollController.jumpTo(max);
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      jump();
      WidgetsBinding.instance.addPostFrameCallback((_) {
        jump();
        WidgetsBinding.instance.addPostFrameCallback((_) => jump());
      });
    });
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(agentProvider);
    final wsId = ref.watch(currentWorkspaceIdProvider);

    ref.listen<ChatState>(agentProvider, (prev, next) {
      if (!mounted) return;
      if (_pendingScrollToBottom && next.messages.isNotEmpty) {
        _pendingScrollToBottom = false;
        _scrollToBottom();
        return;
      }
      if (next.messages.isNotEmpty &&
          prev?.messages.isEmpty == true &&
          next.status == ChatStatus.idle) {
        _scrollToBottom();
        return;
      }
      if (next.status == ChatStatus.streaming) {
        _scrollToBottom();
      }
    });

    ref.listen<String>(currentWorkspaceIdProvider, (prev, next) {
      if (prev == null || prev == next) return;
      _pendingScrollToBottom = true;
      _scrollToBottom();
    });

    final isConnected = state.connected;
    final statusIcon = isConnected
        ? Symbols.cloud_done
        : state.status == ChatStatus.disconnected
        ? Symbols.cloud_off
        : Symbols.cloud_sync;
    final tokens = context.tokens;
    final statusColor = isConnected
        ? tokens.colors.success
        : state.status == ChatStatus.disconnected
        ? tokens.colors.error
        : tokens.colors.warning;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Assistant'),
        actions: [
          IconButton(
            icon: Icon(statusIcon, color: statusColor, size: 20),
            tooltip: isConnected ? 'Connected' : 'Reconnect',
            onPressed: () => ref.read(agentProvider.notifier).connect(),
          ),
        ],
      ),
      body: Column(
        children: [
          ConnectionBanner(
            connected: isConnected,
            isDisconnected: state.status == ChatStatus.disconnected,
            onReconnect: () => ref.read(agentProvider.notifier).connect(),
            backendStatus: state.backendStatus,
          ),
          Expanded(
            child: _MessageList(
              state: state,
              scrollController: _scrollController,
              workspaceId: wsId,
            ),
          ),
          if (state.status == ChatStatus.error && state.error != null)
            ErrorBar(error: state.error!),
          if (state.messages.isEmpty && state.streamingText.isEmpty)
            _SuggestionChips(
              onTap: (text) => ref.read(agentProvider.notifier).sendMessage(text),
            ),
          const ChatInput(),
        ],
      ),
    );
  }
}

class _SuggestionChips extends StatelessWidget {
  final void Function(String text) onTap;

  const _SuggestionChips({required this.onTap});

  static const _suggestions = [
    ('Summarize my inbox', 'Quick overview of recent emails'),
    ('What\'s on my calendar today?', 'Today\'s schedule at a glance'),
    ('Draft a reply to a recent email', 'Respond to the latest message'),
    ('Help me organize my tasks', 'List and prioritize what\'s due'),
  ];

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 12, 4),
      child: Wrap(
        spacing: 6,
        runSpacing: 4,
        alignment: WrapAlignment.center,
        children: _suggestions.map((s) {
          return ActionChip(
            label: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  s.$1,
                  style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w500),
                ),
                Text(
                  s.$2,
                  style: TextStyle(
                    fontSize: 9,
                    color: context.tokens.colors.textTertiary,
                  ),
                ),
              ],
            ),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            onPressed: () => onTap(s.$1),
            visualDensity: VisualDensity.compact,
          );
        }).toList(),
      ),
    );
  }
}

class _MessageList extends StatelessWidget {
  final ChatState state;
  final ScrollController scrollController;
  final String workspaceId;

  const _MessageList({
    required this.state,
    required this.scrollController,
    required this.workspaceId,
  });

  @override
  Widget build(BuildContext context) {
    return ChatMessageList(
      key: ValueKey('chat_list_$workspaceId'),
      messages: state.messages,
      isStreaming: state.status == ChatStatus.streaming,
      streamingText: state.streamingText,
      reasoningText: state.reasoningText,
      activeToolCalls: state.activeToolCalls,
      skillsLoaded: state.skillsLoaded,
      scrollController: scrollController,
      isLoading: state.loadingHistory,
      emptyBuilder: (context) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Symbols.chat_bubble, size: 48, color: context.tokens.colors.textTertiary),
            SizedBox(height: context.tokens.spacing.lg),
            Text(
              'Send a message to start',
              style: context.tokens.typography.textTheme.bodyLarge?.copyWith(
                color: context.tokens.colors.textTertiary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
