"""
WeChatGPT-Dual v2.0
双引擎AI聊天Bot - 支持Telegram + 微信webhook

特性:
- 双引擎: OpenAI + Claude，自动failover
- 上下文管理: 多轮对话 + 自动摘要
- 知识库: TF-IDF RAG检索增强
- 管理命令: /status /switch /clear /usage /help
"""

import os
import sys
import time
import logging
import requests
from collections import defaultdict

from config import Config
from engines.openai_engine import OpenAIEngine
from engines.claude_engine import ClaudeEngine
from engines.engine_manager import EngineManager
from context.manager import ContextManager
from context.summarizer import Summarizer
from knowledge.store import KnowledgeStore
from commands.handler import CommandHandler
from wechat.handler import WeChatHandler

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("WeChatGPT-Dual")


class TelegramBot:
    """Telegram Bot 前端"""

    def __init__(self, config: Config):
        self.config = config
        self.api_url = f"https://api.telegram.org/bot{config.bot_token}"

        # 初始化引擎
        openai_engine = OpenAIEngine(
            api_key=config.openai.api_key,
            model=config.openai.model,
            base_url=config.openai.base_url,
            timeout=config.openai.timeout,
        )
        claude_engine = ClaudeEngine(
            api_key=config.claude.api_key,
            model=config.claude.model,
            base_url=config.claude.base_url,
            timeout=config.claude.timeout,
        )

        # 根据配置确定主/备引擎
        if config.primary_engine == "claude":
            primary, secondary = claude_engine, openai_engine
        else:
            primary, secondary = openai_engine, claude_engine

        self.engine_manager = EngineManager(
            primary=primary,
            secondary=secondary,
            failover_enabled=config.failover_enabled,
            max_retries=config.failover_max_retries,
        )

        # 初始化摘要器（使用备引擎生成摘要以节省主引擎配额）
        summarizer = Summarizer(engine=secondary)

        # 上下文管理
        self.context_manager = ContextManager(
            max_history=config.context.max_history,
            summary_threshold=config.context.summary_threshold,
            summary_keep_recent=config.context.summary_keep_recent,
            default_system_prompt=config.system_prompt,
            summarizer=summarizer,
        )

        # 知识库
        self.knowledge_store = KnowledgeStore(
            store_dir=config.knowledge.store_dir,
            chunk_size=config.knowledge.chunk_size,
            chunk_overlap=config.knowledge.chunk_overlap,
            top_k=config.knowledge.top_k,
        )

        # 命令处理器
        self.command_handler = CommandHandler(
            engine_manager=self.engine_manager,
            context_manager=self.context_manager,
            knowledge_store=self.knowledge_store,
        )

        # 微信处理器（备用）
        self.wechat_handler = WeChatHandler(
            engine_manager=self.engine_manager,
            context_manager=self.context_manager,
            knowledge_store=self.knowledge_store,
            command_handler=self.command_handler,
        )

    def tg_request(self, method, params=None):
        """发送Telegram API请求"""
        try:
            r = requests.get(
                f"{self.api_url}/{method}",
                params=params,
                timeout=35,
            )
            return r.json()
        except Exception:
            return None

    def send_message(
        self,
        chat_id,
        text,
        reply_to=None,
        parse_mode="Markdown",
    ):
        """发送消息"""
        params = {"chat_id": chat_id, "text": text}
        if reply_to:
            params["reply_to_message_id"] = reply_to
        if parse_mode:
            params["parse_mode"] = parse_mode

        result = self.tg_request("sendMessage", params)
        if not result or not result.get("ok"):
            params.pop("parse_mode", None)
            result = self.tg_request("sendMessage", params)
        return result

    def get_updates(self, offset=None):
        """获取更新"""
        params = {"timeout": 30}
        if offset:
            params["offset"] = offset
        return self.tg_request("getUpdates", params)

    def handle_message(self, chat_id, msg_id, text, user_id):
        """处理消息"""
        user_key = str(user_id)

        # 命令处理
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            # 特殊处理 /start
            if command == "/start":
                status = self.engine_manager.get_status()
                self.send_message(
                    chat_id,
                    "🤖 *WeChatGPT Dual v2.0*\n\n"
                    "双引擎AI助手，直接发消息开始对话。\n\n"
                    f"主引擎: `{status['primary']}`\n"
                    f"备引擎: `{status['secondary']}`\n"
                    f"自动切换: {'✅' if status['failover_enabled'] else '❌'}\n\n"
                    "输入 /help 查看所有命令",
                    msg_id,
                )
                return

            # 路由到命令处理器
            reply = self.command_handler.handle(
                command, args, user_key
            )
            if reply:
                self.send_message(chat_id, reply, msg_id)
                return

        # 普通对话
        # 知识库检索增强
        if self.knowledge_store:
            kb_context = self.knowledge_store.search_text(text)
            if kb_context:
                self.context_manager.inject_context(
                    user_key, kb_context
                )

        # 添加用户消息
        self.context_manager.add_message(user_key, "user", text)

        # 获取消息列表
        messages = self.context_manager.get_messages(user_key)

        # 调用引擎
        try:
            response = self.engine_manager.chat(
                messages,
                temperature=self.config.openai.temperature,
                max_tokens=self.config.openai.max_tokens,
            )

            # 保存回复到上下文
            self.context_manager.add_message(
                user_key, "assistant", response.content
            )

            reply = response.content
            if response.from_failover:
                reply += (
                    f"\n\n_[auto-failover → {response.engine_name}]_"
                )

            # 长消息分块发送
            if len(reply) > 4000:
                chunks = [
                    reply[i : i + 4000]
                    for i in range(0, len(reply), 4000)
                ]
                for i, chunk in enumerate(chunks):
                    self.send_message(
                        chat_id,
                        chunk,
                        msg_id if i == 0 else None,
                    )
            else:
                self.send_message(chat_id, reply, msg_id)

        except Exception as e:
            logger.error(f"Engine error: {e}")
            self.send_message(
                chat_id, f"⚠️ AI引擎错误: {e}", msg_id
            )

    def run(self):
        """启动Bot"""
        print(f"\n{'='*55}")
        print(f"  WeChatGPT Dual v2.0")
        print(f"  Primary: {self.engine_manager.primary_name} ({self.engine_manager.primary.model})")
        print(f"  Secondary: {self.engine_manager.secondary_name} ({self.engine_manager.secondary.model})")
        print(f"  Failover: {'enabled' if self.config.failover_enabled else 'disabled'}")
        print(f"  Knowledge: {self.knowledge_store.get_stats()['total_chunks']} chunks")
        print(f"{'='*55}")

        me = self.tg_request("getMe")
        if me and me.get("ok"):
            print(f"\n✅ @{me['result']['username']} 已上线!")
        else:
            print("\n❌ 无法连接Telegram!")
            return

        offset = None
        while True:
            try:
                result = self.get_updates(offset)
                if not result or not result.get("ok"):
                    time.sleep(5)
                    continue

                for update in result.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg:
                        continue
                    text = (msg.get("text") or "").strip()
                    if text:
                        self.handle_message(
                            msg["chat"]["id"],
                            msg["message_id"],
                            text,
                            msg["from"]["id"],
                        )
            except KeyboardInterrupt:
                print("\n👋 Bye!")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(5)


def main():
    # 尝试加载 .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    config = Config.from_env()

    if not config.bot_token:
        print("❌ 未设置 BOT_TOKEN!")
        sys.exit(1)

    if not config.openai.api_key and not config.claude.api_key:
        print("❌ 至少需要设置 OPENAI_API_KEY 或 CLAUDE_API_KEY!")
        sys.exit(1)

    bot = TelegramBot(config)
    bot.run()


if __name__ == "__main__":
    main()
