"""
摘要器 - 自动摘要长对话以控制上下文长度
"""

from typing import List, Dict, Optional


class Summarizer:
    """
    对话摘要器
    - 将长对话压缩为摘要
    - 支持增量摘要（在已有摘要上追加）
    """

    def __init__(self, engine=None):
        """
        Args:
            engine: AI引擎实例，用于生成摘要。
                    如果为None则使用简单截取策略
        """
        self.engine = engine

    def summarize(
        self,
        messages: List[Dict[str, str]],
        existing_summary: str = "",
    ) -> str:
        """
        生成摘要

        Args:
            messages: 需要摘要的消息列表
            existing_summary: 已有的摘要（增量追加）

        Returns:
            摘要文本
        """
        if not messages:
            return existing_summary

        if self.engine is None:
            return self._simple_summarize(
                messages, existing_summary
            )

        return self._ai_summarize(messages, existing_summary)

    def _simple_summarize(
        self,
        messages: List[Dict[str, str]],
        existing_summary: str,
    ) -> str:
        """简单摘要策略：提取关键消息"""
        parts = []
        if existing_summary:
            parts.append(existing_summary)

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                # 截取用户消息的前100字符
                short = content[:100]
                if len(content) > 100:
                    short += "..."
                parts.append(f"用户问: {short}")
            elif role == "assistant":
                short = content[:100]
                if len(content) > 100:
                    short += "..."
                parts.append(f"助手答: {short}")

        return "\n".join(parts[-10:])  # 只保留最近10条摘要

    def _ai_summarize(
        self,
        messages: List[Dict[str, str]],
        existing_summary: str,
    ) -> str:
        """使用AI引擎生成摘要"""
        conversation_text = ""
        for msg in messages:
            role = "用户" if msg.get("role") == "user" else "助手"
            conversation_text += (
                f"{role}: {msg.get('content', '')}\n"
            )

        prompt = (
            "请将以下对话摘要压缩为简洁的要点，保留关键信息。\n\n"
        )
        if existing_summary:
            prompt += f"已有摘要:\n{existing_summary}\n\n"
        prompt += f"新对话:\n{conversation_text}\n\n"
        prompt += "请输出更新后的摘要（不超过300字）:"

        try:
            response = self.engine.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            return response.content
        except Exception:
            # AI摘要失败，降级到简单摘要
            return self._simple_summarize(
                messages, existing_summary
            )
