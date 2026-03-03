"""
上下文管理器 - 多轮对话 + 上下文窗口控制 + 自动摘要
"""

import time
from typing import List, Dict
from dataclasses import dataclass, field


@dataclass
class ConversationContext:
    """单个用户的对话上下文"""
    messages: List[Dict[str, str]] = field(default_factory=list)
    summary: str = ""
    system_prompt: str = ""
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    total_messages: int = 0


class ContextManager:
    """
    上下文管理器
    - 每用户独立上下文
    - 自动窗口控制（超出时触发摘要）
    - 支持自定义system prompt
    """

    def __init__(
        self,
        max_history: int = 20,
        summary_threshold: int = 16,
        summary_keep_recent: int = 4,
        default_system_prompt: str = "",
        summarizer=None,
    ):
        self.max_history = max_history
        self.summary_threshold = summary_threshold
        self.summary_keep_recent = summary_keep_recent
        self.default_system_prompt = default_system_prompt
        self.summarizer = summarizer
        self._contexts: Dict[str, ConversationContext] = {}

    def _get_context(self, user_id: str) -> ConversationContext:
        """获取或创建用户上下文"""
        if user_id not in self._contexts:
            self._contexts[user_id] = ConversationContext(
                system_prompt=self.default_system_prompt
            )
        return self._contexts[user_id]

    def add_message(
        self, user_id: str, role: str, content: str
    ) -> None:
        """添加消息到上下文"""
        ctx = self._get_context(user_id)
        ctx.messages.append({"role": role, "content": content})
        ctx.last_active = time.time()
        ctx.total_messages += 1

        # 检查是否需要摘要
        if (
            len(ctx.messages) >= self.summary_threshold
            and self.summarizer
        ):
            self._do_summary(user_id)
        elif len(ctx.messages) > self.max_history:
            # 无摘要器时直接截断
            ctx.messages = ctx.messages[-self.max_history:]

    def _do_summary(self, user_id: str) -> None:
        """执行摘要压缩"""
        ctx = self._get_context(user_id)
        if not self.summarizer:
            ctx.messages = ctx.messages[-self.summary_keep_recent:]
            return

        # 保留最近的消息
        to_summarize = ctx.messages[:-self.summary_keep_recent]
        kept = ctx.messages[-self.summary_keep_recent:]

        # 生成摘要
        new_summary = self.summarizer.summarize(
            to_summarize, ctx.summary
        )
        ctx.summary = new_summary
        ctx.messages = kept

    def get_messages(
        self, user_id: str, include_system: bool = True
    ) -> List[Dict[str, str]]:
        """获取发送给AI的完整消息列表"""
        ctx = self._get_context(user_id)
        messages = []

        # System prompt
        if include_system:
            system_content = ctx.system_prompt or self.default_system_prompt
            if ctx.summary:
                system_content += (
                    f"\n\n[对话摘要]\n{ctx.summary}"
                )
            if system_content:
                messages.append(
                    {"role": "system", "content": system_content}
                )

        # 对话历史
        messages.extend(ctx.messages)
        return messages

    def clear(self, user_id: str) -> None:
        """清除用户上下文"""
        if user_id in self._contexts:
            del self._contexts[user_id]

    def set_system_prompt(
        self, user_id: str, prompt: str
    ) -> None:
        """设置用户的system prompt"""
        ctx = self._get_context(user_id)
        ctx.system_prompt = prompt

    def get_system_prompt(self, user_id: str) -> str:
        """获取用户的system prompt"""
        ctx = self._get_context(user_id)
        return ctx.system_prompt or self.default_system_prompt

    def get_stats(self, user_id: str) -> dict:
        """获取用户上下文统计"""
        ctx = self._get_context(user_id)
        return {
            "message_count": len(ctx.messages),
            "total_messages": ctx.total_messages,
            "has_summary": bool(ctx.summary),
            "summary_length": len(ctx.summary),
            "max_history": self.max_history,
            "last_active": ctx.last_active,
        }

    def inject_context(
        self, user_id: str, extra_context: str
    ) -> None:
        """注入额外上下文（如知识库检索结果）"""
        ctx = self._get_context(user_id)
        ctx.messages.append(
            {
                "role": "system",
                "content": f"[参考资料]\n{extra_context}",
            }
        )

    def get_user_count(self) -> int:
        """获取活跃用户数"""
        return len(self._contexts)

    def get_all_user_ids(self) -> List[str]:
        """获取所有用户ID"""
        return list(self._contexts.keys())
