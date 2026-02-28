"""
OpenAI引擎 - 支持所有OpenAI兼容API
"""

import time
import requests
from typing import List, Dict

from engines.base import (
    BaseEngine,
    ChatResponse,
    EngineError,
    EngineTimeoutError,
    EngineRateLimitError,
)


class OpenAIEngine(BaseEngine):
    """OpenAI / OpenAI-compatible API 引擎"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 60,
    ):
        super().__init__(name="openai", model=model)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        """检查引擎是否可用（有API key且未被限流）"""
        return bool(self.api_key) and not self.stats.is_rate_limited

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        if not self.api_key:
            raise EngineError("OpenAI API key not configured")

        if self.stats.is_rate_limited:
            raise EngineRateLimitError("OpenAI engine is rate limited")

        start_time = time.time()

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=self.timeout,
            )

            latency = time.time() - start_time

            if response.status_code == 429:
                self.stats.set_rate_limited()
                self.stats.record_failure("Rate limited (429)")
                raise EngineRateLimitError(
                    "OpenAI rate limit exceeded"
                )

            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                self.stats.record_failure(error_msg)
                raise EngineError(error_msg)

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)

            self.stats.record_success(latency, tokens)

            return ChatResponse(
                content=content,
                engine_name=self.name,
                model=self.model,
                tokens_used=tokens,
                latency=latency,
            )

        except requests.Timeout:
            latency = time.time() - start_time
            self.stats.record_failure("Timeout")
            raise EngineTimeoutError(
                f"OpenAI request timed out after {latency:.1f}s"
            )
        except (requests.ConnectionError, requests.RequestException) as e:
            self.stats.record_failure(str(e))
            raise EngineError(f"OpenAI connection error: {e}")
        except (KeyError, IndexError, ValueError) as e:
            self.stats.record_failure(f"Parse error: {e}")
            raise EngineError(f"OpenAI response parse error: {e}")
