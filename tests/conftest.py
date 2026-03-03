"""
Shared fixtures for WeChatGPT-Dual test suite
"""

import os
import sys
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from engines.base import BaseEngine, ChatResponse, EngineError
from engines.engine_manager import EngineManager
from context.manager import ContextManager
from knowledge.store import KnowledgeStore
from commands.handler import CommandHandler
from wechat.message import WeChatMessage, MessageType
from wechat.handler import WeChatHandler


class MockEngine(BaseEngine):
    """Mock engine for testing"""

    def __init__(self, name="mock", model="mock-v1", responses=None, should_fail=False, fail_error=None):
        super().__init__(name=name, model=model)
        self.responses = responses or ["Mock response"]
        self._call_count = 0
        self.should_fail = should_fail
        self.fail_error = fail_error or EngineError("Mock failure")
        self.last_messages = None
        self.last_temperature = None
        self.last_max_tokens = None
        self._available = True

    def set_available(self, available: bool):
        self._available = available

    def chat(self, messages, temperature=0.7, max_tokens=2000):
        self.last_messages = messages
        self.last_temperature = temperature
        self.last_max_tokens = max_tokens

        if self.should_fail:
            self.stats.record_failure(str(self.fail_error))
            raise self.fail_error

        response_text = self.responses[self._call_count % len(self.responses)]
        self._call_count += 1
        self.stats.record_success(0.5, 100)

        return ChatResponse(
            content=response_text,
            engine_name=self.name,
            model=self.model,
            tokens_used=100,
            latency=0.5,
        )

    def is_available(self):
        return self._available and not self.stats.is_rate_limited


@pytest.fixture
def mock_config():
    """Basic test configuration"""
    cfg = Config()
    cfg.bot_token = "test-bot-token-123"
    cfg.openai.api_key = "test-openai-key"
    cfg.claude.api_key = "test-claude-key"
    cfg.primary_engine = "openai"
    cfg.failover_enabled = True
    return cfg


@pytest.fixture
def mock_engine():
    return MockEngine(name="primary", model="test-v1")


@pytest.fixture
def mock_secondary():
    return MockEngine(name="secondary", model="test-v2", responses=["Secondary response"])


@pytest.fixture
def engine_manager(mock_engine, mock_secondary):
    return EngineManager(
        primary=mock_engine,
        secondary=mock_secondary,
        failover_enabled=True,
        max_retries=2,
    )


@pytest.fixture
def context_manager():
    return ContextManager(
        max_history=10,
        summary_threshold=8,
        summary_keep_recent=2,
        default_system_prompt="You are a test assistant.",
    )


@pytest.fixture
def knowledge_store(tmp_path):
    store = KnowledgeStore(
        store_dir=str(tmp_path / "kb"),
        chunk_size=100,
        chunk_overlap=20,
        top_k=3,
    )
    return store


@pytest.fixture
def command_handler(engine_manager, context_manager, knowledge_store):
    return CommandHandler(
        engine_manager=engine_manager,
        context_manager=context_manager,
        knowledge_store=knowledge_store,
    )


@pytest.fixture
def wechat_handler(engine_manager, context_manager, knowledge_store, command_handler):
    return WeChatHandler(
        engine_manager=engine_manager,
        context_manager=context_manager,
        knowledge_store=knowledge_store,
        command_handler=command_handler,
    )


@pytest.fixture
def sample_message():
    return WeChatMessage(
        msg_id="msg001",
        sender_id="user123",
        sender_name="TestUser",
        content="Hello AI",
        msg_type=MessageType.TEXT,
    )


@pytest.fixture
def sample_group_message():
    return WeChatMessage(
        msg_id="msg002",
        sender_id="user456",
        sender_name="GroupUser",
        content="@bot What is AI?",
        msg_type=MessageType.TEXT,
        is_group=True,
        group_id="group789",
        group_name="Test Group",
        is_at_me=True,
    )
