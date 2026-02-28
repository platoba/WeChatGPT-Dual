"""Tests for Config module"""

import os
import pytest
from config import Config, OpenAIConfig, ClaudeConfig, ContextConfig, KnowledgeConfig


class TestOpenAIConfig:
    def test_defaults(self):
        cfg = OpenAIConfig()
        assert cfg.api_key == ""
        assert cfg.model == "gpt-4o-mini"
        assert cfg.max_tokens == 2000
        assert cfg.temperature == 0.7
        assert cfg.timeout == 60

    def test_custom(self):
        cfg = OpenAIConfig(api_key="sk-xxx", model="gpt-4o", max_tokens=4000)
        assert cfg.api_key == "sk-xxx"
        assert cfg.model == "gpt-4o"
        assert cfg.max_tokens == 4000


class TestClaudeConfig:
    def test_defaults(self):
        cfg = ClaudeConfig()
        assert cfg.api_key == ""
        assert "claude" in cfg.model
        assert cfg.timeout == 60

    def test_custom(self):
        cfg = ClaudeConfig(api_key="sk-ant-xxx", model="claude-3-opus")
        assert cfg.api_key == "sk-ant-xxx"
        assert cfg.model == "claude-3-opus"


class TestContextConfig:
    def test_defaults(self):
        cfg = ContextConfig()
        assert cfg.max_history == 20
        assert cfg.summary_threshold == 16
        assert cfg.summary_keep_recent == 4


class TestKnowledgeConfig:
    def test_defaults(self):
        cfg = KnowledgeConfig()
        assert cfg.chunk_size == 500
        assert cfg.chunk_overlap == 50
        assert cfg.top_k == 3
        assert cfg.store_dir == "knowledge_store"


class TestConfig:
    def test_defaults(self):
        cfg = Config()
        assert cfg.bot_token == ""
        assert cfg.primary_engine == "openai"
        assert cfg.failover_enabled is True
        assert cfg.failover_max_retries == 2

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "test-token-abc")
        monkeypatch.setenv("PRIMARY_ENGINE", "claude")
        monkeypatch.setenv("FAILOVER_ENABLED", "false")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
        monkeypatch.setenv("CLAUDE_MODEL", "claude-3-sonnet")
        monkeypatch.setenv("MAX_HISTORY", "30")
        monkeypatch.setenv("CHUNK_SIZE", "1000")

        cfg = Config.from_env()

        assert cfg.bot_token == "test-token-abc"
        assert cfg.primary_engine == "claude"
        assert cfg.failover_enabled is False
        assert cfg.openai.api_key == "sk-test"
        assert cfg.claude.api_key == "sk-ant-test"
        assert cfg.openai.model == "gpt-4o"
        assert cfg.claude.model == "claude-3-sonnet"
        assert cfg.context.max_history == 30
        assert cfg.knowledge.chunk_size == 1000

    def test_from_env_defaults(self, monkeypatch):
        # Clear relevant env vars
        for key in ["BOT_TOKEN", "PRIMARY_ENGINE", "OPENAI_API_KEY", "CLAUDE_API_KEY"]:
            monkeypatch.delenv(key, raising=False)

        cfg = Config.from_env()
        assert cfg.bot_token == ""
        assert cfg.primary_engine == "openai"
        assert cfg.failover_enabled is True

    def test_system_prompt_env(self, monkeypatch):
        monkeypatch.setenv("SYSTEM_PROMPT", "Custom AI prompt")
        cfg = Config.from_env()
        assert cfg.system_prompt == "Custom AI prompt"

    def test_rate_limit_cooldown(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_COOLDOWN", "120")
        cfg = Config.from_env()
        assert cfg.rate_limit_cooldown == 120
