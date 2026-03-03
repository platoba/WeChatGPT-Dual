"""
流式响应支持 - SSE streaming for engines
"""

import json
import logging
from typing import Generator, Dict, List
from dataclasses import dataclass

from engines.base import ChatResponse

logger = logging.getLogger(__name__)


@dataclass
class StreamChunk:
    """流式响应块"""
    content: str
    done: bool = False
    engine_name: str = ""
    model: str = ""
    tokens_used: int = 0


class StreamingMixin:
    """
    流式响应混入类
    为引擎管理器提供 stream_chat 能力
    """

    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Generator[StreamChunk, None, None]:
        """
        流式聊天（模拟流式 - 将完整响应分块发送）
        真正的SSE streaming需要引擎原生支持

        Yields:
            StreamChunk objects
        """
        try:
            # 获取完整响应
            response = self.chat(messages, temperature, max_tokens)

            # 分块发送（模拟流式效果）
            content = response.content
            chunk_size = max(20, len(content) // 10)

            for i in range(0, len(content), chunk_size):
                chunk = content[i : i + chunk_size]
                is_last = (i + chunk_size) >= len(content)

                yield StreamChunk(
                    content=chunk,
                    done=is_last,
                    engine_name=response.engine_name,
                    model=response.model,
                    tokens_used=response.tokens_used if is_last else 0,
                )

        except Exception as e:
            yield StreamChunk(
                content=f"Error: {e}",
                done=True,
            )

    def stream_to_sse(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> Generator[str, None, None]:
        """
        将流式响应转为SSE格式字符串

        Yields:
            SSE格式字符串 "data: {...}\n\n"
        """
        for chunk in self.stream_chat(messages, temperature, max_tokens):
            data = {
                "content": chunk.content,
                "done": chunk.done,
                "engine": chunk.engine_name,
                "model": chunk.model,
            }
            if chunk.done:
                data["tokens_used"] = chunk.tokens_used
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"


def collect_stream(
    stream: Generator[StreamChunk, None, None],
) -> ChatResponse:
    """
    收集流式响应为完整的 ChatResponse
    """
    parts = []
    engine_name = ""
    model = ""
    tokens = 0

    for chunk in stream:
        parts.append(chunk.content)
        if chunk.engine_name:
            engine_name = chunk.engine_name
        if chunk.model:
            model = chunk.model
        if chunk.done:
            tokens = chunk.tokens_used

    return ChatResponse(
        content="".join(parts),
        engine_name=engine_name,
        model=model,
        tokens_used=tokens,
    )
