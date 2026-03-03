"""
微信消息处理器 - 接收微信消息并路由到AI引擎
支持通过webhook接收（兼容多种微信机器人框架）
"""

import logging
from typing import Optional, Callable

from wechat.message import WeChatMessage, MessageType
from engines.engine_manager import EngineManager
from context.manager import ContextManager
from knowledge.store import KnowledgeStore
from commands.handler import CommandHandler

logger = logging.getLogger(__name__)


class WeChatHandler:
    """
    微信消息处理器
    - 接收消息 -> 命令检测 -> 知识检索 -> AI引擎 -> 返回回复
    - 支持群聊@触发
    - 支持命令路由
    """

    def __init__(
        self,
        engine_manager: EngineManager,
        context_manager: ContextManager,
        knowledge_store: Optional[KnowledgeStore] = None,
        command_handler: Optional[CommandHandler] = None,
        group_at_only: bool = True,
    ):
        self.engine_manager = engine_manager
        self.context_manager = context_manager
        self.knowledge_store = knowledge_store
        self.command_handler = command_handler
        self.group_at_only = group_at_only
        self._message_count = 0
        self._reply_callback: Optional[Callable] = None

    def set_reply_callback(
        self, callback: Callable[[WeChatMessage, str], None]
    ) -> None:
        """设置回复回调（用于发送微信消息）"""
        self._reply_callback = callback

    def handle_message(self, message: WeChatMessage) -> Optional[str]:
        """
        处理一条微信消息

        Args:
            message: 微信消息

        Returns:
            回复文本，或None（不回复）
        """
        self._message_count += 1

        # 只处理文本消息
        if message.msg_type != MessageType.TEXT:
            return None

        # 群聊只响应@消息
        if message.is_group and self.group_at_only:
            if not message.is_at_me:
                return None

        content = message.content.strip()
        if not content:
            return None

        user_key = message.user_key

        # 检查命令
        if message.is_command and self.command_handler:
            return self.command_handler.handle(
                message.command, message.command_args, user_key
            )

        # 知识库检索增强
        if self.knowledge_store:
            kb_context = self.knowledge_store.search_text(content)
            if kb_context:
                self.context_manager.inject_context(
                    user_key, kb_context
                )

        # 添加用户消息到上下文
        self.context_manager.add_message(user_key, "user", content)

        # 获取完整消息列表
        messages = self.context_manager.get_messages(user_key)

        # 调用AI引擎
        try:
            response = self.engine_manager.chat(messages)

            # 添加助手回复到上下文
            self.context_manager.add_message(
                user_key, "assistant", response.content
            )

            reply = response.content
            if response.from_failover:
                reply += f"\n\n[已自动切换到 {response.engine_name}]"

            # 触发回复回调
            if self._reply_callback:
                self._reply_callback(message, reply)

            return reply

        except Exception as e:
            logger.error(f"AI engine error: {e}")
            return f"⚠️ AI引擎错误: {e}"

    def handle_raw(self, data: dict) -> Optional[str]:
        """处理原始webhook数据"""
        message = WeChatMessage.from_dict(data)
        return self.handle_message(message)

    def get_stats(self) -> dict:
        """获取处理器统计"""
        return {
            "total_messages_processed": self._message_count,
            "active_users": self.context_manager.get_user_count(),
            "engine_status": self.engine_manager.get_status(),
            "knowledge_stats": (
                self.knowledge_store.get_stats()
                if self.knowledge_store
                else None
            ),
        }
