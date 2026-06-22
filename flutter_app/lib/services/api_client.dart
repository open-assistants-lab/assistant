import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/companion.dart';

class ApiClient {
  String _host;
  String _userId;
  String? _apiKey;
  String? _model;
  Map<String, String>? _providerKeys;
  final http.Client _httpClient;

  ApiClient({
    String host = '127.0.0.1:8080',
    String userId = 'default_user',
    String? apiKey,
    String? model,
    http.Client? httpClient,
  }) : _host = host,
       _userId = userId,
       _apiKey = apiKey,
       _model = model,
       _httpClient = httpClient ?? http.Client();

  set apiKey(String? key) => _apiKey = key;
  set model(String? m) => _model = m;
  set providerKeys(Map<String, String>? keys) => _providerKeys = keys;

  Map<String, String> get _headers => {
    if (_apiKey != null && _apiKey!.isNotEmpty)
      'Authorization': 'Bearer $_apiKey',
  };

  Future<http.Response> _get(Uri url) => _httpClient
      .get(url, headers: _headers)
      .timeout(const Duration(seconds: 30));

  Future<http.Response> _post(Uri url, {Object? body}) => _httpClient
      .post(
        url,
        headers: {..._headers, 'Content-Type': 'application/json'},
        body: body,
      )
      .timeout(const Duration(seconds: 30));

  Future<http.Response> _put(Uri url, {Object? body}) => _httpClient
      .put(
        url,
        headers: {..._headers, 'Content-Type': 'application/json'},
        body: body,
      )
      .timeout(const Duration(seconds: 30));

  Future<http.Response> _patch(Uri url, {Object? body}) => _httpClient
      .patch(
        url,
        headers: {..._headers, 'Content-Type': 'application/json'},
        body: body,
      )
      .timeout(const Duration(seconds: 30));

  Future<http.Response> _delete(Uri url) => _httpClient
      .delete(url, headers: _headers)
      .timeout(const Duration(seconds: 30));

  String get _baseUrl {
    final host = _host.replaceFirst(RegExp(r'/+$'), '');
    if (host.startsWith('http://') || host.startsWith('https://')) return host;
    if (host.contains(':443')) return 'https://$host';
    return 'http://$host';
  }

  Map<String, String> get _queryParams => {'user_id': _userId};

  void updateHost(String host) => _host = host;
  void updateUserId(String userId) => _userId = userId;

  String _buildUrl(String path, [Map<String, String>? extra]) {
    final params = Map<String, String>.from(_queryParams);
    if (extra != null) params.addAll(extra);
    final query = params.entries
        .map((e) => '${e.key}=${Uri.encodeComponent(e.value)}')
        .join('&');
    return '$_baseUrl$path?$query';
  }

  // ─── Health ───

  Future<Map<String, dynamic>> healthCheck() async {
    final response = await _get(Uri.parse('$_baseUrl/health'));
    return _handleResponse(response);
  }

  // ─── Memories (Observations) ───

  Future<List<dynamic>> listMemories({String? domain, int limit = 20}) async {
    final extra = <String, String>{'limit': limit.toString()};
    if (domain != null) extra['domain'] = domain;
    final response = await _get(Uri.parse(_buildUrl('/memories/observations', extra)));
    return _handleListResponse(response, 'observations');
  }

  Future<Map<String, dynamic>> searchMemories(String query) async {
    final response = await _post(
      Uri.parse(_buildUrl('/memories/observations/search')),
      body: jsonEncode({'query': query, 'user_id': _userId}),
    );
    return _handleResponse(response);
  }

  // ─── Contacts ───

  Future<List<dynamic>> listContacts() async {
    final response = await _get(Uri.parse(_buildUrl('/contacts')));
    return _handleListResponse(response, 'contacts');
  }

  Future<Map<String, dynamic>> addContact({
    required String email,
    String? name,
    String? phone,
    String? company,
  }) async {
    final response = await _post(
      Uri.parse(_buildUrl('/contacts')),
      body: jsonEncode({
        'email': email,
        'name': name,
        'phone': phone,
        'company': company,
        'user_id': _userId,
      }),
    );
    return _handleResponse(response);
  }

  // ─── Todos ───

  Future<List<dynamic>> listTodos() async {
    final response = await _get(Uri.parse(_buildUrl('/todos')));
    return _handleListResponse(response, 'todos');
  }

  Future<Map<String, dynamic>> addTodo({
    required String content,
    String priority = 'medium',
  }) async {
    final response = await _post(
      Uri.parse(_buildUrl('/todos')),
      body: jsonEncode({
        'content': content,
        'priority': priority,
        'user_id': _userId,
      }),
    );
    return _handleResponse(response);
  }

  Future<Map<String, dynamic>> updateTodo(
    String todoId, {
    String? status,
    String? content,
    String? priority,
  }) async {
    final response = await _put(
      Uri.parse('$_baseUrl/todos/$todoId?user_id=$_userId'),
      body: jsonEncode({
        'status': status,
        'content': content,
        'priority': priority,
      }),
    );
    return _handleResponse(response);
  }

  // ─── Email ───

  Future<Map<String, dynamic>> listEmails({
    int limit = 50,
    int offset = 0,
    bool? isRead,
  }) async {
    final extra = <String, String>{
      'limit': limit.toString(),
      'offset': offset.toString(),
      'user_id': _userId,
    };
    if (isRead != null) extra['is_read'] = isRead.toString();
    final response = await _get(Uri.parse(_buildUrl('/emails', extra)));
    final data = await _handleResponse(response);
    return data;
  }

  Future<Map<String, dynamic>> getEmail(String emailId) async {
    final response = await _get(
      Uri.parse('$_baseUrl/emails/$emailId?user_id=$_userId'),
    );
    return _handleResponse(response);
  }

  Future<Map<String, dynamic>> searchEmails(
    String query, {
    int limit = 20,
  }) async {
    final extra = <String, String>{
      'q': query,
      'limit': limit.toString(),
      'user_id': _userId,
    };
    final response = await _get(Uri.parse(_buildUrl('/emails/search', extra)));
    return _handleResponse(response);
  }

  // ─── Skills ───

  Future<List<dynamic>> listSkills() async {
    final response = await _get(Uri.parse(_buildUrl('/skills')));
    return _handleListResponse(response, 'skills');
  }

  Future<Map<String, dynamic>> getSkillDetail(String name) async {
    final encodedName = Uri.encodeComponent(name);
    final response = await _get(
      Uri.parse(_buildUrl('/skills/$encodedName')),
    );
    return _handleResponse(response);
  }

  Future<Map<String, dynamic>> createSkill(
    String name,
    String description,
    String content, {
    String scope = 'user',
  }) async {
    final response = await _post(
      Uri.parse(_buildUrl('/skills')),
      body: jsonEncode({
        'name': name,
        'description': description,
        'content': content,
        'scope': scope,
      }),
    );
    return _handleResponse(response);
  }

  Future<Map<String, dynamic>> updateSkill(
    String name, {
    String? description,
    String? content,
    String scope = 'user',
  }) async {
    final encodedName = Uri.encodeComponent(name);
    final body = <String, dynamic>{'scope': scope};
    if (description != null) body['description'] = description;
    if (content != null) body['content'] = content;
    final response = await _put(
      Uri.parse(_buildUrl('/skills/$encodedName')),
      body: jsonEncode(body),
    );
    return _handleResponse(response);
  }

  Future<void> deleteSkill(
    String name, {
    String scope = 'user',
  }) async {
    final extra = <String, String>{'scope': scope};
    final encodedName = Uri.encodeComponent(name);
    final response = await _delete(
      Uri.parse(_buildUrl('/skills/$encodedName', extra)),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, response.body);
    }
  }

  // ─── Subagents ───

  Future<List<dynamic>> listSubagents() async {
    final response = await _get(Uri.parse(_buildUrl('/subagents')));
    final data = await _handleResponse(response);
    return data['agents'] as List<dynamic>? ?? [];
  }

  Future<Map<String, dynamic>> createSubagent({
    required String name,
    required String description,
    String? model,
    String scope = 'user',
    List<String>? tools,
    List<String>? skills,
    String? systemPrompt,
    int maxLlmCalls = 50,
    double costLimitUsd = 1.0,
    int timeoutSeconds = 300,
    Map<String, dynamic>? providerOptions,
    Map<String, dynamic>? outputSchema,
    String? handoffInstructions,
    String? artifactPolicy,
  }) async {
    final response = await _post(
      Uri.parse(_buildUrl('/subagents')),
      body: jsonEncode({
        'name': name,
        'description': description,
        'model': ?model,
        'scope': scope,
        'tools': ?tools,
        'skills': ?skills,
        'system_prompt': ?systemPrompt,
        'max_llm_calls': maxLlmCalls,
        'cost_limit_usd': costLimitUsd,
        'timeout_seconds': timeoutSeconds,
        'provider_options': ?providerOptions,
        'output_schema': ?outputSchema,
        'handoff_instructions': ?handoffInstructions,
        'artifact_policy': ?artifactPolicy,
      }),
    );
    return _handleResponse(response);
  }

  Future<void> updateSubagent(
    String name, {
    String? description,
    String? model,
    List<String>? tools,
    List<String>? skills,
    String? systemPrompt,
    int? maxLlmCalls,
    double? costLimitUsd,
    int? timeoutSeconds,
  }) async {
    final encodedName = Uri.encodeComponent(name);
    final response = await _patch(
      Uri.parse(_buildUrl('/subagents/$encodedName')),
      body: jsonEncode({
        'description': ?description,
        'model': ?model,
        'tools': ?tools,
        'skills': ?skills,
        'system_prompt': ?systemPrompt,
        'max_llm_calls': ?maxLlmCalls,
        'cost_limit_usd': ?costLimitUsd,
        'timeout_seconds': ?timeoutSeconds,
      }),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, response.body);
    }
  }

  Future<void> deleteSubagent(String name) async {
    final encodedName = Uri.encodeComponent(name);
    final response = await _delete(
      Uri.parse(_buildUrl('/subagents/$encodedName')),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, response.body);
    }
  }

  Future<Map<String, dynamic>> startSubagent(
    String name, {
    required String task,
    String? parentId,
  }) async {
    final encodedName = Uri.encodeComponent(name);
    final response = await _post(
      Uri.parse(_buildUrl('/subagents/$encodedName/start')),
      body: jsonEncode({
        'task': task,
        'parent_id': ?parentId,
      }),
    );
    return _handleResponse(response);
  }

  Future<List<dynamic>> listSubagentJobs({
    String? status,
  }) async {
    final extra = <String, String>{};
    if (status != null) extra['status'] = status;
    final response = await _get(Uri.parse(_buildUrl('/subagents/jobs', extra)));
    final data = await _handleResponse(response);
    return data['jobs'] as List<dynamic>? ?? [];
  }

  Future<Map<String, dynamic>> getSubagentJob(String jobId) async {
    final response = await _get(
      Uri.parse(_buildUrl('/subagents/jobs/$jobId')),
    );
    return _handleResponse(response);
  }

  Future<void> cancelSubagentJob(String jobId) async {
    await _post(
      Uri.parse(_buildUrl('/subagents/jobs/$jobId/cancel')),
    );
  }

  Future<void> instructSubagentJob(String jobId, {
    required String instruction,
  }) async {
    await _post(
      Uri.parse(_buildUrl('/subagents/jobs/$jobId/instructions')),
      body: jsonEncode({'instruction': instruction}),
    );
  }

  Future<List<String>> listToolNames() async {
    final response = await _get(Uri.parse(_buildUrl('/tools/names')));
    final data = await _handleResponse(response);
    return List<String>.from(data['tools'] ?? []);
  }

  // ─── Sessions ───

  Future<List<dynamic>> listSessions() async {
    final response = await _get(Uri.parse(_buildUrl('/sessions')));
    final data = await _handleResponse(response);
    return data['sessions'] as List<dynamic>? ?? [];
  }

  Future<void> deleteSession(String sessionId) async {
    final extra = <String, String>{'session_id': sessionId};
    await _delete(Uri.parse(_buildUrl('/conversation', extra)));
  }

  // ─── Conversation ───

  Future<List<dynamic>> getConversation({
    int limit = 20,
    String? sessionId,
  }) async {
    final extra = <String, String>{
      'limit': limit.toString(),
      'user_id': _userId,
      if (sessionId != null && sessionId.isNotEmpty) 'session_id': sessionId,
    };
    final response = await _get(Uri.parse(_buildUrl('/conversation', extra)));
    return _handleListResponse(response, 'messages');
  }

  /// Send a non-streaming message to the agent.
  Future<Map<String, dynamic>> sendMessage(String content) async {
    final response = await _post(
      Uri.parse('$_baseUrl/message'),
      body: jsonEncode({
        'message': content,
        'user_id': _userId,
        if (_model != null) 'model': _model,
        if (_providerKeys != null && _providerKeys!.isNotEmpty)
          'provider_keys': _providerKeys,
      }),
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(response.statusCode, response.body);
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  // ─── Companion ───

  Future<CompanionStatus> getCompanionStatus() async {
    final response = await _get(Uri.parse(_buildUrl('/companion/status')));
    final decoded = await _handleResponse(response);
    return CompanionStatus.fromJson(decoded);
  }

  Future<List<CompanionNotification>> getCompanionNotifications({
    int limit = 50,
  }) async {
    final extra = <String, String>{'limit': limit.toString()};
    final response = await _get(
      Uri.parse(_buildUrl('/companion/notifications', extra)),
    );
    final decoded = await _handleResponse(response);
    final list = decoded['notifications'];
    if (list is List) {
      return list
          .map((e) => CompanionNotification.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }

  Future<void> dismissCompanionNotification(String id) async {
    await _post(
      Uri.parse(
        '$_baseUrl/companion/notifications/$id/dismiss?user_id=$_userId',
      ),
    );
  }

  Future<void> pauseCompanion() async {
    await _post(Uri.parse('$_baseUrl/companion/pause?user_id=$_userId'));
  }

  Future<void> resumeCompanion() async {
    await _post(Uri.parse('$_baseUrl/companion/resume?user_id=$_userId'));
  }

  Future<List<CompanionMemoryFact>> getCompanionMemory() async {
    final response = await _get(Uri.parse(_buildUrl('/companion/memory')));
    final decoded = await _handleResponse(response);
    final list = decoded['facts'];
    if (list is List) {
      return list
          .map((e) => CompanionMemoryFact.fromJson(e as Map<String, dynamic>))
          .toList();
    }
    return [];
  }

  Future<void> deleteCompanionMemory(int id) async {
    await _delete(Uri.parse('$_baseUrl/companion/memory/$id?user_id=$_userId'));
  }

  Future<List<dynamic>> _handleListResponse(
    http.Response response,
    String key,
  ) async {
    final decoded = await _handleResponse(response);
    final value = decoded[key];
    return value is List ? value : [];
  }

  Future<Map<String, dynamic>> _handleResponse(http.Response response) async {
    if (response.statusCode >= 200 && response.statusCode < 300) {
      final decoded = jsonDecode(response.body);
      if (decoded is Map<String, dynamic>) return decoded;
      throw ApiException(
        response.statusCode,
        'Expected JSON object, got ${decoded.runtimeType}',
      );
    }
    throw ApiException(response.statusCode, response.body);
  }
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  ApiException(this.statusCode, this.body);
  @override
  String toString() => 'ApiException($statusCode): $body';
}
