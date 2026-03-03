"""
引擎基类 - 定义AI引擎的标准接口
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional


class EngineError(Exception):
    """引擎通用错误"""
    pass


class EngineTimeoutError(EngineError):
    """引擎超时错误"""
    pass


class EngineRateLimitError(EngineError):
    """引擎限流错误"""
    pass


@dataclass
class EngineStats:
    """引擎使用统计"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens_used: int = 0
    total_latency: float = 0.0
    last_error: Optional[str] = None
    last_error_time: Optional[float] = None
    rate_limited_until: Optional[float] = None

    @property
    def avg_latency(self) -> float:
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency / self.successful_requests

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests

    @property
    def is_rate_limited(self) -> bool:
        if self.rate_limited_until is None:
            return False
        return time.time() < self.rate_limited_until

    def record_success(self, latency: float, tokens: int = 0):
        self.total_requests += 1
        self.successful_requests += 1
        self.total_latency += latency
        self.total_tokens_used += tokens

    def record_failure(self, error: str):
        self.total_requests += 1
        self.failed_requests += 1
        self.last_error = error
        self.last_error_time = time.time()

    def set_rate_limited(self, cooldown: int = 60):
        self.rate_limited_until = time.time() + cooldown

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "total_tokens_used": self.total_tokens_used,
            "avg_latency": round(self.avg_latency, 2),
            "success_rate": round(self.success_rate * 100, 1),
            "is_rate_limited": self.is_rate_limited,
            "last_error": self.last_error,
        }


@dataclass
class ChatResponse:
    """统一的聊天响应"""
    content: str
    engine_name: str
    model: str
    tokens_used: int = 0
    latency: float = 0.0
    from_failover: bool = False


class BaseEngine(ABC):
    """AI引擎基类"""

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.stats = EngineStats()

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        """
        发送聊天请求

        Args:
            messages: OpenAI格式的消息列表
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            ChatResponse

        Raises:
            EngineTimeoutError: 超时
            EngineRateLimitError: 限流
            EngineError: 其他错误
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查引擎是否可用"""
        pass

    def get_stats(self) -> dict:
        """获取引擎统计"""
        return {
            "name": self.name,
            "model": self.model,
            "available": self.is_available(),
            **self.stats.to_dict(),
        }
