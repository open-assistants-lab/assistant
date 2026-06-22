class SubagentAgentDef {
  final String name;
  final String description;
  final String? model;
  final String scope;
  final List<String>? tools;
  final List<String>? skills;
  final String? systemPrompt;
  final int maxLlmCalls;
  final double costLimitUsd;
  final int timeoutSeconds;
  final Map<String, dynamic>? providerOptions;
  final Map<String, dynamic>? outputSchema;
  final String? handoffInstructions;
  final String? artifactPolicy;
  final String? createdAt;

  const SubagentAgentDef({
    required this.name,
    this.description = '',
    this.model,
    this.scope = 'user',
    this.tools,
    this.skills,
    this.systemPrompt,
    this.maxLlmCalls = 50,
    this.costLimitUsd = 1.0,
    this.timeoutSeconds = 300,
    this.providerOptions,
    this.outputSchema,
    this.handoffInstructions,
    this.artifactPolicy,
    this.createdAt,
  });

  factory SubagentAgentDef.fromJson(Map<String, dynamic> json) {
    return SubagentAgentDef(
      name: json['name']?.toString() ?? '',
      description: json['description']?.toString() ?? '',
      model: json['model']?.toString(),
      scope: json['scope']?.toString() ?? 'all',
      tools: json['tools'] != null
          ? List<String>.from(json['tools'] as List)
          : null,
      skills: json['skills'] != null
          ? List<String>.from(json['skills'] as List)
          : null,
      systemPrompt: json['system_prompt']?.toString(),
      maxLlmCalls: json['max_llm_calls'] as int? ?? 50,
      costLimitUsd: (json['cost_limit_usd'] as num?)?.toDouble() ?? 1.0,
      timeoutSeconds: json['timeout_seconds'] as int? ?? 300,
      providerOptions: json['provider_options'] as Map<String, dynamic>?,
      outputSchema: json['output_schema'] as Map<String, dynamic>?,
      handoffInstructions: json['handoff_instructions']?.toString(),
      artifactPolicy: json['artifact_policy']?.toString(),
      createdAt: json['created_at']?.toString(),
    );
  }
}

class SubagentJob {
  final String jobId;
  final String agentName;
  final String task;
  final String status;
  final Map<String, dynamic>? progress;
  final String? result;
  final String? error;
  final List<Map<String, dynamic>> instructions;
  final String? createdAt;
  final String? startedAt;
  final String? completedAt;

  const SubagentJob({
    required this.jobId,
    required this.agentName,
    required this.task,
    required this.status,
    this.progress,
    this.result,
    this.error,
    this.instructions = const [],
    this.createdAt,
    this.startedAt,
    this.completedAt,
  });

  factory SubagentJob.fromJson(Map<String, dynamic> json) {
    return SubagentJob(
      jobId: json['id']?.toString() ?? json['job_id']?.toString() ?? '',
      agentName: json['agent_name']?.toString() ?? '',
      task: json['task']?.toString() ?? '',
      status: json['status']?.toString() ?? 'pending',
      progress: json['progress'] as Map<String, dynamic>?,
      result: json['result']?.toString(),
      error: json['error']?.toString(),
      instructions: json['instructions'] != null
          ? List<Map<String, dynamic>>.from(json['instructions'] as List)
          : [],
      createdAt: json['created_at']?.toString(),
      startedAt: json['started_at']?.toString(),
      completedAt: json['completed_at']?.toString(),
    );
  }

  bool get isTerminal =>
      status == 'completed' || status == 'failed' || status == 'cancelled';
  bool get isRunning => status == 'running' || status == 'cancelling';
}

enum SubagentPanelTab { agents, jobs }
