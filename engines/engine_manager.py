"""
引擎管理器 - 双引擎调度 + 自动failover
"""

import logging
from typing import List, Dict, Optional

from engines.base import (
    BaseEngine,
    ChatResponse,
    EngineError,
    EngineTimeoutError,
    EngineRateLimitError,
)

logger = logging.getLogger(__name__)


class EngineManager:
    """
    双引擎管理器
    - 主引擎优先
    - 超时/限流自动切换备引擎
    - 支持手动切换
    - 记录引擎统计
    """

    def __init__(
        self,
        primary: BaseEngine,
        secondary: BaseEngine,
        failover_enabled: bool = True,
        max_retries: int = 2,
    ):
        self.engines = {
            primary.name: primary,
            secondary.name: secondary,
        }
        self.primary_name = primary.name
        self.secondary_name = secondary.name
        self.failover_enabled = failover_enabled
        self.max_retries = max_retries
        self._failover_count = 0

    @property
    def primary(self) -> BaseEngine:
        return self.engines[self.primary_name]

    @property
    def secondary(self) -> BaseEngine:
        return self.engines[self.secondary_name]

    def switch_primary(self, engine_name: str) -> bool:
        """手动切换主引擎"""
        if engine_name not in self.engines:
            return False
        if engine_name == self.primary_name:
            return True
        old = self.primary_name
        self.primary_name = engine_name
        # secondary becomes the other one
        for name in self.engines:
            if name != engine_name:
                self.secondary_name = name
                break
        logger.info(f"Switched primary engine: {old} -> {engine_name}")
        return True

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        """
        聊天请求，自动failover
        """
        # 尝试主引擎
        try:
            if self.primary.is_available():
                response = self.primary.chat(
                    messages, temperature, max_tokens
                )
                return response
            else:
                logger.warning(
                    f"Primary engine {self.primary_name} not available"
                )
                raise EngineError("Primary engine not available")
        except (EngineTimeoutError, EngineRateLimitError, EngineError) as e:
            logger.warning(
                f"Primary engine {self.primary_name} failed: {e}"
            )

            if not self.failover_enabled:
                raise

            # 尝试备引擎
            return self._failover(messages, temperature, max_tokens, e)

    def _failover(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        original_error: Exception,
    ) -> ChatResponse:
        """Failover到备引擎"""
        logger.info(
            f"Failing over to secondary engine: {self.secondary_name}"
        )

        if not self.secondary.is_available():
            raise EngineError(
                f"Both engines unavailable. Primary: {original_error}"
            )

        try:
            response = self.secondary.chat(
                messages, temperature, max_tokens
            )
            response.from_failover = True
            self._failover_count += 1
            return response
        except Exception as e:
            raise EngineError(
                f"Both engines failed. "
                f"Primary ({self.primary_name}): {original_error}. "
                f"Secondary ({self.secondary_name}): {e}"
            )

    def get_status(self) -> dict:
        """获取所有引擎状态"""
        return {
            "primary": self.primary_name,
            "secondary": self.secondary_name,
            "failover_enabled": self.failover_enabled,
            "failover_count": self._failover_count,
            "engines": {
                name: engine.get_stats()
                for name, engine in self.engines.items()
            },
        }

    def get_engine(self, name: str) -> Optional[BaseEngine]:
        """根据名称获取引擎"""
        return self.engines.get(name)

    def list_engines(self) -> List[str]:
        """列出所有引擎名称"""
        return list(self.engines.keys())
