"""
流式响应测试
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.streaming import StreamChunk, StreamingMixin, collect_stream
from engines.base import ChatResponse, EngineError


class MockStreamEngine:
    """Mock engine with streaming support"""

    def __init__(self, response_text="Hello streaming world!"):
        self.response_text = response_text
        self._should_fail = False

    def chat(self, messages, temperature=0.7, max_tokens=2000):
        if self._should_fail:
            raise EngineError("Mock stream error")
        return ChatResponse(
            content=self.response_text,
            engine_name="mock",
            model="mock-v1",
            tokens_used=42,
        )


class StreamableEngine(StreamingMixin, MockStreamEngine):
    pass


class TestStreamChunk:
    def test_basic_chunk(self):
        chunk = StreamChunk(content="Hello", done=False)
        assert chunk.content == "Hello"
        assert not chunk.done

    def test_done_chunk(self):
        chunk = StreamChunk(content="!", done=True, engine_name="test", tokens_used=10)
        assert chunk.done
        assert chunk.tokens_used == 10


class TestStreamingMixin:
    def test_stream_chat(self):
        engine = StreamableEngine()
        chunks = list(engine.stream_chat([{"role": "user", "content": "hi"}]))
        assert len(chunks) > 0
        assert chunks[-1].done
        full_text = "".join(c.content for c in chunks)
        assert full_text == "Hello streaming world!"

    def test_stream_error(self):
        engine = StreamableEngine()
        engine._should_fail = True
        chunks = list(engine.stream_chat([{"role": "user", "content": "hi"}]))
        assert len(chunks) == 1
        assert chunks[0].done
        assert "Error" in chunks[0].content

    def test_stream_to_sse(self):
        engine = StreamableEngine(response_text="SSE test")
        sse_lines = list(engine.stream_to_sse([{"role": "user", "content": "hi"}]))
        assert len(sse_lines) > 1
        assert sse_lines[-1] == "data: [DONE]\n\n"
        for line in sse_lines[:-1]:
            assert line.startswith("data: ")

    def test_stream_short_content(self):
        engine = StreamableEngine(response_text="Hi")
        chunks = list(engine.stream_chat([{"role": "user", "content": "hi"}]))
        assert len(chunks) >= 1
        full = "".join(c.content for c in chunks)
        assert full == "Hi"


class TestCollectStream:
    def test_collect(self):
        engine = StreamableEngine(response_text="Collected!")
        stream = engine.stream_chat([{"role": "user", "content": "hi"}])
        response = collect_stream(stream)
        assert isinstance(response, ChatResponse)
        assert response.content == "Collected!"
        assert response.engine_name == "mock"
        assert response.tokens_used == 42

    def test_collect_empty(self):
        def empty_stream():
            yield StreamChunk(content="", done=True)
        response = collect_stream(empty_stream())
        assert response.content == ""
