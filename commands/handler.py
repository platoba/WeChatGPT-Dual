"""
管理命令处理器
支持: /status /switch /clear /usage /help + 更多
"""

from typing import Optional, Callable, Dict
from engines.engine_manager import EngineManager
from context.manager import ContextManager
from knowledge.store import KnowledgeStore


class CommandHandler:
    """
    命令路由和处理
    支持的命令:
    - /status - 系统状态
    - /switch <engine> - 切换引擎
    - /clear - 清除对话
    - /usage - 使用统计
    - /help - 帮助
    - /model <name> - 查看/切换模型
    - /role <desc> - 设置角色
    - /kb <query> - 知识库查询
    """

    def __init__(
        self,
        engine_manager: EngineManager,
        context_manager: ContextManager,
        knowledge_store: Optional[KnowledgeStore] = None,
    ):
        self.engine_manager = engine_manager
        self.context_manager = context_manager
        self.knowledge_store = knowledge_store

        self._commands: Dict[str, Callable] = {
            "/status": self._cmd_status,
            "/switch": self._cmd_switch,
            "/clear": self._cmd_clear,
            "/usage": self._cmd_usage,
            "/help": self._cmd_help,
            "/model": self._cmd_model,
            "/role": self._cmd_role,
            "/kb": self._cmd_kb,
        }

    def handle(
        self, command: str, args: str, user_key: str
    ) -> Optional[str]:
        """处理命令"""
        handler = self._commands.get(command)
        if handler:
            return handler(args, user_key)
        return None  # 未知命令不处理

    def _cmd_status(self, args: str, user_key: str) -> str:
        """系统状态"""
        status = self.engine_manager.get_status()
        ctx_stats = self.context_manager.get_stats(user_key)

        lines = [
            "📊 *系统状态*\n",
            f"主引擎: `{status['primary']}`",
            f"备引擎: `{status['secondary']}`",
            f"自动切换: {'✅' if status['failover_enabled'] else '❌'}",
            f"切换次数: {status['failover_count']}",
            "",
        ]

        for name, engine_stats in status["engines"].items():
            marker = "🟢" if engine_stats["available"] else "🔴"
            lines.append(f"{marker} *{name}*")
            lines.append(f"  模型: `{engine_stats['model']}`")
            lines.append(
                f"  请求: {engine_stats['total_requests']} "
                f"(成功率 {engine_stats['success_rate']}%)"
            )
            lines.append(
                f"  平均延迟: {engine_stats['avg_latency']}s"
            )
            lines.append(
                f"  Token: {engine_stats['total_tokens_used']}"
            )

        lines.append(f"\n💬 当前对话: {ctx_stats['message_count']}条")

        if self.knowledge_store:
            kb = self.knowledge_store.get_stats()
            lines.append(
                f"📚 知识库: {kb['total_chunks']}块"
            )

        return "\n".join(lines)

    def _cmd_switch(self, args: str, user_key: str) -> str:
        """切换引擎"""
        engine_name = args.strip().lower()
        if not engine_name:
            engines = self.engine_manager.list_engines()
            current = self.engine_manager.primary_name
            return (
                f"当前主引擎: `{current}`\n"
                f"可用引擎: {', '.join(f'`{e}`' for e in engines)}\n"
                f"用法: /switch openai 或 /switch claude"
            )

        if self.engine_manager.switch_primary(engine_name):
            return f"✅ 已切换主引擎: `{engine_name}`"
        else:
            engines = self.engine_manager.list_engines()
            return (
                f"❌ 未知引擎: {engine_name}\n"
                f"可用: {', '.join(f'`{e}`' for e in engines)}"
            )

    def _cmd_clear(self, args: str, user_key: str) -> str:
        """清除对话"""
        self.context_manager.clear(user_key)
        return "🗑️ 对话历史已清除"

    def _cmd_usage(self, args: str, user_key: str) -> str:
        """使用统计"""
        status = self.engine_manager.get_status()
        ctx = self.context_manager.get_stats(user_key)

        total_tokens = sum(
            e["total_tokens_used"]
            for e in status["engines"].values()
        )
        total_requests = sum(
            e["total_requests"]
            for e in status["engines"].values()
        )

        lines = [
            "📈 *使用统计*\n",
            f"总请求数: {total_requests}",
            f"总Token: {total_tokens}",
            f"当前对话消息: {ctx['message_count']}",
            f"历史总消息: {ctx['total_messages']}",
            f"有摘要: {'是' if ctx['has_summary'] else '否'}",
            f"Failover次数: {status['failover_count']}",
        ]

        for name, engine_stats in status["engines"].items():
            lines.append(
                f"\n*{name}*: "
                f"{engine_stats['total_requests']}请求 / "
                f"{engine_stats['total_tokens_used']}token"
            )

        return "\n".join(lines)

    def _cmd_help(self, args: str, user_key: str) -> str:
        """帮助信息"""
        return (
            "🤖 *WeChatGPT Dual v2.0*\n\n"
            "直接发消息开始AI对话\n\n"
            "📌 *管理命令:*\n"
            "  /status — 系统状态\n"
            "  /switch <engine> — 切换引擎\n"
            "  /clear — 清除对话历史\n"
            "  /usage — 使用统计\n"
            "  /model <name> — 切换模型\n"
            "  /role <desc> — 设置AI角色\n"
            "  /kb <query> — 查询知识库\n"
            "  /help — 显示帮助\n\n"
            "🔧 *特性:*\n"
            "  • 双引擎 (OpenAI + Claude)\n"
            "  • 自动failover\n"
            "  • 多轮对话 + 自动摘要\n"
            "  • 知识库RAG检索增强"
        )

    def _cmd_model(self, args: str, user_key: str) -> str:
        """查看/切换模型"""
        # 显示当前模型
        status = self.engine_manager.get_status()
        primary = status["primary"]
        model = status["engines"][primary]["model"]

        if not args.strip():
            lines = [f"当前引擎: `{primary}`", f"当前模型: `{model}`"]
            for name, es in status["engines"].items():
                lines.append(f"  {name}: `{es['model']}`")
            return "\n".join(lines)

        # 注意：模型切换需要重新初始化引擎，这里只显示信息
        return (
            f"💡 模型切换需要修改环境变量后重启\n"
            f"当前: `{primary}` / `{model}`"
        )

    def _cmd_role(self, args: str, user_key: str) -> str:
        """设置角色"""
        if not args.strip():
            current = self.context_manager.get_system_prompt(
                user_key
            )
            return f"当前角色:\n{current[:200]}\n\n用法: /role 你是一个翻译专家"

        self.context_manager.set_system_prompt(user_key, args.strip())
        self.context_manager.clear(user_key)
        return f"✅ 角色已设置，对话已重置\n\n角色: {args.strip()[:100]}"

    def _cmd_kb(self, args: str, user_key: str) -> str:
        """知识库查询"""
        if not self.knowledge_store:
            return "❌ 知识库未启用"

        query = args.strip()
        if not query:
            stats = self.knowledge_store.get_stats()
            return (
                f"📚 *知识库*\n"
                f"文档块: {stats['total_chunks']}\n"
                f"总字符: {stats['total_chars']}\n"
                f"来源: {', '.join(stats['sources']) or '无'}\n\n"
                f"用法: /kb <查询内容>"
            )

        results = self.knowledge_store.search(query)
        if not results:
            return "🔍 未找到相关内容"

        lines = ["🔍 *检索结果:*\n"]
        for i, (doc, score) in enumerate(results, 1):
            source = f" ({doc.source})" if doc.source else ""
            lines.append(
                f"*{i}.* [相似度 {score:.2f}]{source}\n"
                f"{doc.content[:200]}..."
            )
        return "\n\n".join(lines)

    def get_commands(self) -> list:
        """获取所有命令列表"""
        return list(self._commands.keys())
