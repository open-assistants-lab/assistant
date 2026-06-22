class CompanionNotification {
  final String id;
  final String userId;
  final String message;
  final String category;
  final bool dismissed;
  final String createdAt;

  const CompanionNotification({
    required this.id,
    required this.userId,
    required this.message,
    this.category = 'general',
    this.dismissed = false,
    required this.createdAt,
  });

  factory CompanionNotification.fromJson(Map<String, dynamic> json) {
    return CompanionNotification(
      id: json['id']?.toString() ?? '',
      userId: json['user_id']?.toString() ?? '',
      message: json['message']?.toString() ?? '',
      category: json['category']?.toString() ?? 'general',
      dismissed: json['dismissed'] == 1 || json['dismissed'] == true,
      createdAt: json['created_at']?.toString() ?? '',
    );
  }

  CompanionNotification copyWith({bool? dismissed}) {
    return CompanionNotification(
      id: id,
      userId: userId,
      message: message,
      category: category,
      dismissed: dismissed ?? this.dismissed,
      createdAt: createdAt,
    );
  }
}

class CompanionMemoryFact {
  final int id;
  final String userId;
  final String key;
  final String value;
  final String source;
  final double confidence;
  final String updatedAt;

  const CompanionMemoryFact({
    required this.id,
    required this.userId,
    required this.key,
    required this.value,
    this.source = 'inferred',
    this.confidence = 0.5,
    required this.updatedAt,
  });

  factory CompanionMemoryFact.fromJson(Map<String, dynamic> json) {
    return CompanionMemoryFact(
      id: (json['id'] as num).toInt(),
      userId: json['user_id']?.toString() ?? '',
      key: json['key']?.toString() ?? '',
      value: json['value']?.toString() ?? '',
      source: json['source']?.toString() ?? 'inferred',
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0.5,
      updatedAt: json['updated_at']?.toString() ?? '',
    );
  }
}

class CompanionStatus {
  final bool running;
  final bool paused;
  final String? lastCheck;

  const CompanionStatus({
    this.running = false,
    this.paused = false,
    this.lastCheck,
  });

  factory CompanionStatus.fromJson(Map<String, dynamic> json) {
    return CompanionStatus(
      running: json['running'] == true,
      paused: json['paused'] == true,
      lastCheck: json['last_check']?.toString(),
    );
  }
}
