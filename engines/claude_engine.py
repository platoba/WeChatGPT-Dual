"""
Claude引擎 - Anthropic Claude API
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


class ClaudeEngine(BaseEngine):
    """Anthropic Claude API 引擎"""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-haiku-20240307",
        base_url: str = "https://api.anthropic.com",
        timeout: int = 60,
    ):
        super().__init__(name="claude", model=model)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key) and not self.stats.is_rate_limited

    def _convert_messages(
        self, messages: List[Dict[str, str]]
    ) -> tuple:
        """
        将OpenAI格式消息转换为Claude格式
        提取system prompt，分离user/assistant消息
        """
        system = ""
        claude_messages = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system = content
            elif role in ("user", "assistant"):
                claude_messages.append(
                    {"role": role, "content": content}
                )

        # Claude要求消息列表非空且第一条是user
        if not claude_messages:
            claude_messages = [
                {"role": "user", "content": "Hello"}
            ]

        # 确保交替出现
        cleaned = []
        last_role = None
        for msg in claude_messages:
            if msg["role"] == last_role:
                # 合并相同角色的连续消息
                cleaned[-1]["content"] += "\n" + msg["content"]
            else:
                cleaned.append(msg)
                last_role = msg["role"]

        # 确保第一条是user
        if cleaned and cleaned[0]["role"] != "user":
            cleaned.insert(
                0, {"role": "user", "content": "(continued)"}
            )

        return system, cleaned

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ChatResponse:
        if not self.api_key:
            raise EngineError("Claude API key not configured")

        if self.stats.is_rate_limited:
            raise EngineRateLimitError("Claude engine is rate limited")

        system, claude_messages = self._convert_messages(messages)
        start_time = time.time()

        try:
            payload = {
                "model": self.model,
                "messages": claude_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                payload["system"] = system

            response = requests.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )

            latency = time.time() - start_time

            if response.status_code == 429:
                self.stats.set_rate_limited()
                self.stats.record_failure("Rate limited (429)")
                raise EngineRateLimitError(
                    "Claude rate limit exceeded"
                )

            if response.status_code != 200:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                self.stats.record_failure(error_msg)
                raise EngineError(error_msg)

            data = response.json()
            content_blocks = data.get("content", [])
            content = ""
            for block in content_blocks:
                if block.get("type") == "text":
                    content += block.get("text", "")

            usage = data.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get(
                "output_tokens", 0
            )

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
                f"Claude request timed out after {latency:.1f}s"
            )
        except (requests.ConnectionError, requests.RequestException) as e:
            self.stats.record_failure(str(e))
            raise EngineError(f"Claude connection error: {e}")
        except (KeyError, IndexError, ValueError) as e:
            self.stats.record_failure(f"Parse error: {e}")
            raise EngineError(f"Claude response parse error: {e}")
