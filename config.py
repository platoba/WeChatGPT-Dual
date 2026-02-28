"""
WeChatGPT-Dual v2.0 配置中心
所有配置从环境变量读取，支持 .env 文件
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OpenAIConfig:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    max_tokens: int = 2000
    temperature: float = 0.7
    timeout: int = 60


@dataclass
class ClaudeConfig:
    api_key: str = ""
    base_url: str = "https://api.anthropic.com"
    model: str = "claude-3-haiku-20240307"
    max_tokens: int = 2000
    temperature: float = 0.7
    timeout: int = 60


@dataclass
class ContextConfig:
    max_history: int = 20
    summary_threshold: int = 16
    summary_keep_recent: int = 4
    max_tokens_estimate: int = 8000


@dataclass
class KnowledgeConfig:
    chunk_size: int = 500
    chunk_overlap: int = 50
    top_k: int = 3
    store_dir: str = "knowledge_store"


@dataclass
class Config:
    # Telegram
    bot_token: str = ""
    system_prompt: str = "你是一个有用的AI助手。回答简洁、准确、有帮助。"

    # Engine
    primary_engine: str = "openai"  # "openai" or "claude"
    failover_enabled: bool = True
    failover_max_retries: int = 2
    failover_timeout: float = 30.0
    rate_limit_cooldown: int = 60

    # Sub-configs
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        cfg = cls()
        cfg.bot_token = os.environ.get("BOT_TOKEN", "")
        cfg.system_prompt = os.environ.get(
            "SYSTEM_PROMPT", cfg.system_prompt
        )
        cfg.primary_engine = os.environ.get("PRIMARY_ENGINE", "openai")
        cfg.failover_enabled = os.environ.get(
            "FAILOVER_ENABLED", "true"
        ).lower() == "true"
        cfg.failover_max_retries = int(
            os.environ.get("FAILOVER_MAX_RETRIES", "2")
        )
        cfg.failover_timeout = float(
            os.environ.get("FAILOVER_TIMEOUT", "30.0")
        )
        cfg.rate_limit_cooldown = int(
            os.environ.get("RATE_LIMIT_COOLDOWN", "60")
        )

        # OpenAI
        cfg.openai.api_key = os.environ.get("OPENAI_API_KEY", "")
        cfg.openai.base_url = os.environ.get(
            "OPENAI_BASE_URL", cfg.openai.base_url
        )
        cfg.openai.model = os.environ.get("OPENAI_MODEL", cfg.openai.model)
        cfg.openai.max_tokens = int(
            os.environ.get("OPENAI_MAX_TOKENS", "2000")
        )
        cfg.openai.temperature = float(
            os.environ.get("OPENAI_TEMPERATURE", "0.7")
        )
        cfg.openai.timeout = int(os.environ.get("OPENAI_TIMEOUT", "60"))

        # Claude
        cfg.claude.api_key = os.environ.get("CLAUDE_API_KEY", "")
        cfg.claude.base_url = os.environ.get(
            "CLAUDE_BASE_URL", cfg.claude.base_url
        )
        cfg.claude.model = os.environ.get("CLAUDE_MODEL", cfg.claude.model)
        cfg.claude.max_tokens = int(
            os.environ.get("CLAUDE_MAX_TOKENS", "2000")
        )
        cfg.claude.temperature = float(
            os.environ.get("CLAUDE_TEMPERATURE", "0.7")
        )
        cfg.claude.timeout = int(os.environ.get("CLAUDE_TIMEOUT", "60"))

        # Context
        cfg.context.max_history = int(
            os.environ.get("MAX_HISTORY", "20")
        )
        cfg.context.summary_threshold = int(
            os.environ.get("SUMMARY_THRESHOLD", "16")
        )
        cfg.context.summary_keep_recent = int(
            os.environ.get("SUMMARY_KEEP_RECENT", "4")
        )

        # Knowledge
        cfg.knowledge.chunk_size = int(
            os.environ.get("CHUNK_SIZE", "500")
        )
        cfg.knowledge.chunk_overlap = int(
            os.environ.get("CHUNK_OVERLAP", "50")
        )
        cfg.knowledge.top_k = int(os.environ.get("TOP_K", "3"))
        cfg.knowledge.store_dir = os.environ.get(
            "KNOWLEDGE_STORE_DIR", "knowledge_store"
        )

        return cfg
