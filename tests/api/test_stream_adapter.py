from src.sdk.messages import StreamChunk


def test_adapt_stream_chunk_uses_canonical_text_alias():
    from src.http.stream_adapter import adapt_stream_chunk

    event = adapt_stream_chunk(StreamChunk.ai_token("hello"))

    assert event.kind == "text_delta"
    assert event.content == "hello"


def test_adapt_stream_chunk_uses_canonical_tool_start_alias():
    from src.http.stream_adapter import adapt_stream_chunk

    event = adapt_stream_chunk(StreamChunk.tool_start("time_get", "call-1", {"tz": "UTC"}))

    assert event.kind == "tool_input_start"
    assert event.tool == "time_get"
    assert event.call_id == "call-1"
    assert event.args == {"tz": "UTC"}


def test_adapt_stream_chunk_uses_canonical_reasoning_alias():
    from src.http.stream_adapter import adapt_stream_chunk

    event = adapt_stream_chunk(StreamChunk.reasoning("thinking"))

    assert event.kind == "reasoning_delta"
    assert event.content == "thinking"


def test_adapt_stream_chunk_includes_tool_result_preview():
    from src.http.stream_adapter import adapt_stream_chunk

    event = adapt_stream_chunk(StreamChunk.tool_result_event("time_get", "call-1", "noon"))

    assert event.kind == "tool_result"
    assert event.tool == "time_get"
    assert event.call_id == "call-1"
    assert event.result_preview == "noon"
