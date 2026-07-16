import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../theme/app_theme.dart';
import '../../providers/agent_provider.dart';
import '../../models/message.dart';
import '../chat/widgets/message_bubble.dart';
import '../chat/widgets/streaming_bubble.dart';
import '../chat/widgets/tool_call_card.dart';
import '../chat/widgets/chat_input.dart';
import '../chat/widgets/error_bar.dart';
import '../chat/widgets/connection_banner.dart';

class DemoScreen extends ConsumerStatefulWidget {
  const DemoScreen({super.key});

  @override
  ConsumerState<DemoScreen> createState() => _DemoScreenState();
}

class _DemoScreenState extends ConsumerState<DemoScreen> {
  final _scrollController = ScrollController();

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
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (_scrollController.hasClients) {
        _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
      }
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

    ref.listen<ChatState>(agentProvider, (prev, next) {
      if (next.messages.length != prev?.messages.length) {
        _scrollToBottom();
      }
    });

    return Scaffold(
      body: Row(
        children: [
          // ── Chat (center) ──
          Expanded(
            flex: 3,
            child: Column(
              children: [
                _ChatHeader(connected: state.connected),
                ConnectionBanner(
                  connected: state.connected,
                  isDisconnected: state.status == ChatStatus.disconnected,
                  onReconnect: () => ref.read(agentProvider.notifier).connect(),
                  backendStatus: state.backendStatus,
                ),
                Expanded(
                  child: state.messages.isEmpty && state.streamingText.isEmpty
                      ? Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Symbols.smart_toy,
                                size: 56,
                                color: AppColors.accent.withAlpha(80),
                              ),
                              const SizedBox(height: 16),
                              Text(
                                'Assistant',
                                style: AppTypography.sectionTitle
                                    .copyWith(fontSize: 18),
                              ),
                              const SizedBox(height: 8),
                              Text(
                                'Ask anything — files, email, tasks, and more',
                                style: AppTypography.body.copyWith(
                                  color: AppColors.textDim,
                                ),
                              ),
                            ],
                          ),
                        )
                      : ListView(
                          controller: _scrollController,
                          padding: const EdgeInsets.symmetric(
                            horizontal: AppSpacing.cardPadding,
                            vertical: AppSpacing.itemGap,
                          ),
                          children: [
                            ...state.messages.map(
                              (msg) => MessageBubble(message: msg),
                            ),
                            if (state.status == ChatStatus.streaming &&
                                state.streamingText.isNotEmpty)
                              StreamingBubble(text: state.streamingText),
                            ...state.activeToolCalls
                                .where((tc) => tc.resultPreview == null)
                                .map((tc) => ToolCallCard(toolCall: tc)),
                          ],
                        ),
                ),
                if (state.status == ChatStatus.error && state.error != null)
                  ErrorBar(error: state.error!),
                const ChatInput(),
              ],
            ),
          ),

          Container(width: 1, color: AppColors.divider),

          // ── Context panel (right) ──
          Expanded(
            flex: 2,
            child: _ContextPanel(messages: state.messages),
          ),
        ],
      ),
    );
  }
}

class _ChatHeader extends ConsumerWidget {
  final bool connected;
  const _ChatHeader({required this.connected});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Container(
      height: 52,
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.cardPadding),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AppColors.divider)),
      ),
      child: Row(
        children: [
          Icon(Symbols.smart_toy, size: 20, color: AppColors.accent),
          const SizedBox(width: 8),
          Text(
            'Assistant',
            style: AppTypography.sectionTitle.copyWith(fontSize: 15),
          ),
          const Spacer(),
          Tooltip(
            message: connected ? 'Connected' : 'Disconnected',
            child: Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: connected ? AppColors.success : AppColors.textDim,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ContextPanel extends StatelessWidget {
  final List<ChatMessage> messages;
  const _ContextPanel({required this.messages});

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.background,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            height: 52,
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.cardPadding,
            ),
            decoration: const BoxDecoration(
              border: Border(bottom: BorderSide(color: AppColors.divider)),
            ),
            child: Row(
              children: [
                Text(
                  'Context',
                  style: AppTypography.sectionTitle.copyWith(fontSize: 15),
                ),
                const Spacer(),
                Icon(Symbols.push_pin, size: 16, color: AppColors.textDim),
              ],
            ),
          ),
          Expanded(
            child: _buildContent(),
          ),
        ],
      ),
    );
  }

  Widget _buildContent() {
    if (messages.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Symbols.dashboard, size: 40, color: AppColors.textDim),
            const SizedBox(height: 12),
            Text(
              'Contextual panel',
              style: AppTypography.body.copyWith(color: AppColors.textDim),
            ),
            const SizedBox(height: 4),
            Text(
              'Shows files, contacts, emails, and tool\nresults as the agent works.',
              textAlign: TextAlign.center,
              style: AppTypography.caption.copyWith(color: AppColors.textDim),
            ),
          ],
        ),
      );
    }

    // Show the latest assistant message content as "result"
    final assistantMsgs =
        messages.where((m) => m.role == 'assistant').toList();
    return ListView(
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
      children: [
        // Stats card
        _ContextCard(
          icon: Symbols.analytics,
          title: 'Session Stats',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _statRow('Messages', '${messages.length}'),
              _statRow(
                  'User', '${messages.where((m) => m.role == 'user').length}'),
              _statRow('Assistant', '${assistantMsgs.length}'),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.itemGap),
        // Latest response
        _ContextCard(
          icon: Symbols.psychology,
          title: 'Latest Response',
          child: Text(
            assistantMsgs.isNotEmpty
                ? assistantMsgs.last.content
                : 'No responses yet',
            style: AppTypography.caption.copyWith(
              color: AppColors.textSecondary,
              height: 1.5,
            ),
            maxLines: 12,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const SizedBox(height: AppSpacing.itemGap),
        // Quick hints
        _ContextCard(
          icon: Symbols.lightbulb,
          title: 'Try asking',
          child: Column(
            children: [
              _hintChip('What time is it in Tokyo?'),
              _hintChip('Search my emails for invoices'),
              _hintChip('Create a todo list for today'),
              _hintChip('List files in my project directory'),
            ],
          ),
        ),
      ],
    );
  }

  Widget _statRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label,
              style: AppTypography.caption.copyWith(color: AppColors.textDim)),
          Text(value,
              style: AppTypography.caption.copyWith(
                  color: AppColors.textPrimary,
                  fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  Widget _hintChip(String text) {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.accentLight,
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
      child: Text(
        text,
        style: AppTypography.caption.copyWith(color: AppColors.accent),
      ),
    );
  }
}

class _ContextCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final Widget child;

  const _ContextCard({
    required this.icon,
    required this.title,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(AppRadius.card),
        border: Border.all(color: AppColors.divider),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: AppColors.accent),
              const SizedBox(width: 6),
              Text(
                title,
                style: AppTypography.caption.copyWith(color: AppColors.accent),
              ),
            ],
          ),
          const SizedBox(height: 10),
          child,
        ],
      ),
    );
  }
}
