"""
Telegram Bot Handler - 模块化Telegram消息处理

Features:
- Message routing with command/text/callback dispatch
- Inline keyboard builder
- Media message support (photo, document, voice)
- Group chat support (mention detection, reply-only mode)
- Message formatting utils (Markdown/HTML escape)
- Per-user rate limiting (token bucket)
- Typing action indicator
"""

import re
import time
import logging
from typing import Optional, List, Dict, Any, Callable, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Data Models ──


@dataclass
class TelegramUser:
    """Telegram user info"""
    id: int
    username: str = ""
    first_name: str = ""
    last_name: str = ""
    is_bot: bool = False

    @classmethod
    def from_dict(cls, data: Dict) -> "TelegramUser":
        return cls(
            id=data.get("id", 0),
            username=data.get("username", ""),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            is_bot=data.get("is_bot", False),
        )

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        name = " ".join(p for p in parts if p)
        return name or self.username or str(self.id)


@dataclass
class InlineButton:
    """Inline keyboard button"""
    text: str
    callback_data: str = ""
    url: str = ""

    def to_dict(self) -> Dict:
        btn: Dict[str, Any] = {"text": self.text}
        if self.url:
            btn["url"] = self.url
        elif self.callback_data:
            btn["callback_data"] = self.callback_data
        return btn


@dataclass
class InlineKeyboard:
    """Inline keyboard markup"""
    rows: List[List[InlineButton]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "inline_keyboard": [
                [btn.to_dict() for btn in row]
                for row in self.rows
            ]
        }


class InlineKeyboardBuilder:
    """Fluent builder for inline keyboards"""

    def __init__(self):
        self._rows: List[List[InlineButton]] = []
        self._current_row: List[InlineButton] = []

    def button(self, text: str, callback_data: str = "", url: str = "") -> "InlineKeyboardBuilder":
        """Add a button to the current row"""
        self._current_row.append(InlineButton(text=text, callback_data=callback_data, url=url))
        return self

    def row(self) -> "InlineKeyboardBuilder":
        """Finish current row and start a new one"""
        if self._current_row:
            self._rows.append(self._current_row)
            self._current_row = []
        return self

    def build(self) -> InlineKeyboard:
        """Build the keyboard"""
        if self._current_row:
            self._rows.append(self._current_row)
        return InlineKeyboard(rows=self._rows)


class MessageFormatter:
    """Telegram message formatting utilities"""

    # Markdown V2 special characters that need escaping
    MD_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!"

    @staticmethod
    def escape_markdown(text: str) -> str:
        """Escape text for MarkdownV2 parse mode"""
        result = []
        for ch in text:
            if ch in MessageFormatter.MD_V2_SPECIAL:
                result.append("\\")
            result.append(ch)
        return "".join(result)

    @staticmethod
    def escape_html(text: str) -> str:
        """Escape text for HTML parse mode"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    @staticmethod
    def bold(text: str, mode: str = "html") -> str:
        if mode == "html":
            return f"<b>{text}</b>"
        return f"*{text}*"

    @staticmethod
    def italic(text: str, mode: str = "html") -> str:
        if mode == "html":
            return f"<i>{text}</i>"
        return f"_{text}_"

    @staticmethod
    def code(text: str, mode: str = "html") -> str:
        if mode == "html":
            return f"<code>{MessageFormatter.escape_html(text)}</code>"
        return f"`{text}`"

    @staticmethod
    def pre(text: str, language: str = "", mode: str = "html") -> str:
        if mode == "html":
            safe = MessageFormatter.escape_html(text)
            if language:
                return f'<pre><code class="language-{language}">{safe}</code></pre>'
            return f"<pre>{safe}</pre>"
        if language:
            return f"```{language}\n{text}\n```"
        return f"```\n{text}\n```"

    @staticmethod
    def link(text: str, url: str, mode: str = "html") -> str:
        if mode == "html":
            return f'<a href="{url}">{MessageFormatter.escape_html(text)}</a>'
        return f"[{text}]({url})"

    @staticmethod
    def truncate(text: str, max_length: int = 4096) -> str:
        """Truncate text to Telegram's message limit"""
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."

    @staticmethod
    def chunk_message(text: str, max_length: int = 4096) -> List[str]:
        """Split long message into chunks respecting Telegram limits"""
        if len(text) <= max_length:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            # Try to split at newline
            split_pos = text.rfind("\n", 0, max_length)
            if split_pos < max_length // 2:
                # No good newline, split at space
                split_pos = text.rfind(" ", 0, max_length)
            if split_pos < max_length // 2:
                # No good split point, hard split
                split_pos = max_length

            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip()

        return chunks


class UserRateLimiter:
    """Per-user rate limiter using token bucket algorithm"""

    def __init__(
        self,
        max_tokens: int = 10,
        refill_rate: float = 1.0,
        refill_interval: float = 60.0,
    ):
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.refill_interval = refill_interval
        self._buckets: Dict[str, Tuple[float, float]] = {}  # user_id -> (tokens, last_refill)

    def check(self, user_id: str) -> bool:
        """Check if user can send a message (consumes 1 token)"""
        now = time.time()
        tokens, last_refill = self._buckets.get(user_id, (self.max_tokens, now))

        # Refill tokens
        elapsed = now - last_refill
        refill = (elapsed / self.refill_interval) * self.refill_rate
        tokens = min(self.max_tokens, tokens + refill)

        if tokens < 1:
            self._buckets[user_id] = (tokens, now)
            return False

        self._buckets[user_id] = (tokens - 1, now)
        return True

    def remaining(self, user_id: str) -> float:
        """Get remaining tokens for user"""
        tokens, last_refill = self._buckets.get(user_id, (self.max_tokens, time.time()))
        elapsed = time.time() - last_refill
        refill = (elapsed / self.refill_interval) * self.refill_rate
        return min(self.max_tokens, tokens + refill)

    def reset(self, user_id: str):
        """Reset rate limit for user"""
        self._buckets[user_id] = (self.max_tokens, time.time())

    def reset_all(self):
        """Reset all rate limits"""
        self._buckets.clear()


@dataclass
class CallbackQuery:
    """Parsed callback query"""
    id: str
    from_user: TelegramUser
    chat_id: int
    message_id: int
    data: str

    @classmethod
    def from_dict(cls, data: Dict) -> "CallbackQuery":
        msg = data.get("message", {})
        return cls(
            id=data.get("id", ""),
            from_user=TelegramUser.from_dict(data.get("from", {})),
            chat_id=msg.get("chat", {}).get("id", 0),
            message_id=msg.get("message_id", 0),
            data=data.get("data", ""),
        )


class TelegramHandler:
    """
    Modular Telegram message handler with routing, rate limiting,
    and group chat support.

    Usage:
        handler = TelegramHandler(bot_username="mybot")
        handler.register_command("/help", help_handler)
        handler.register_callback("settings:", settings_callback)
        response = handler.process_update(update_dict)
    """

    def __init__(
        self,
        bot_username: str = "",
        reply_only_in_groups: bool = True,
        rate_limit_messages: int = 10,
        rate_limit_window: float = 60.0,
    ):
        self.bot_username = bot_username.lower().lstrip("@")
        self.reply_only_in_groups = reply_only_in_groups
        self.rate_limiter = UserRateLimiter(
            max_tokens=rate_limit_messages,
            refill_rate=rate_limit_messages,
            refill_interval=rate_limit_window,
        )

        self._command_handlers: Dict[str, Callable] = {}
        self._callback_handlers: Dict[str, Callable] = {}
        self._text_handler: Optional[Callable] = None
        self._media_handler: Optional[Callable] = None

    def register_command(self, command: str, handler: Callable):
        """Register a command handler (e.g. '/help')"""
        self._command_handlers[command.lower()] = handler

    def register_callback(self, prefix: str, handler: Callable):
        """Register a callback query handler by data prefix"""
        self._callback_handlers[prefix] = handler

    def register_text_handler(self, handler: Callable):
        """Register the default text message handler"""
        self._text_handler = handler

    def register_media_handler(self, handler: Callable):
        """Register the media message handler"""
        self._media_handler = handler

    def process_update(self, update: Dict) -> Optional[Dict]:
        """
        Process a Telegram update and return response dict.

        Returns:
            Dict with 'method', 'chat_id', 'text', etc. or None if no response
        """
        # Handle callback queries
        if "callback_query" in update:
            return self._handle_callback(update["callback_query"])

        msg = update.get("message")
        if not msg:
            return None

        chat_id = msg.get("chat", {}).get("id")
        chat_type = msg.get("chat", {}).get("type", "private")
        user = TelegramUser.from_dict(msg.get("from", {}))
        msg_id = msg.get("message_id")

        # Group chat: check if bot is mentioned or replied to
        is_group = chat_type in ("group", "supergroup")
        if is_group and self.reply_only_in_groups:
            if not self._is_bot_mentioned(msg):
                return None

        # Rate limiting
        user_key = str(user.id)
        if not self.rate_limiter.check(user_key):
            return {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": "⏳ 请求太频繁，请稍后再试。",
                "reply_to_message_id": msg_id,
            }

        text = msg.get("text", "").strip()

        # Command handling
        if text.startswith("/"):
            return self._handle_command(text, chat_id, msg_id, user, is_group)

        # Media handling
        if any(k in msg for k in ("photo", "document", "voice", "video", "audio")):
            return self._handle_media(msg, chat_id, msg_id, user)

        # Text handling
        if text and self._text_handler:
            # Strip bot mention in group chats
            if is_group:
                text = self._strip_mention(text)
            return self._text_handler(text, chat_id, msg_id, user)

        return None

    def _is_bot_mentioned(self, msg: Dict) -> bool:
        """Check if bot is mentioned in group message"""
        text = msg.get("text", "")

        # Direct @mention
        if f"@{self.bot_username}" in text.lower():
            return True

        # Reply to bot's message
        reply = msg.get("reply_to_message", {})
        reply_from = reply.get("from", {})
        if reply_from.get("username", "").lower() == self.bot_username:
            return True

        # Check entities for mention
        for entity in msg.get("entities", []):
            if entity.get("type") == "mention":
                mention = text[entity["offset"]:entity["offset"] + entity["length"]]
                if mention.lower() == f"@{self.bot_username}":
                    return True

        return False

    def _strip_mention(self, text: str) -> str:
        """Remove bot mention from text"""
        pattern = re.compile(rf"@{re.escape(self.bot_username)}\s*", re.IGNORECASE)
        return pattern.sub("", text).strip()

    def _handle_command(
        self, text: str, chat_id: int, msg_id: int, user: TelegramUser, is_group: bool
    ) -> Optional[Dict]:
        """Route command to registered handler"""
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Strip @botname from command in groups
        if "@" in command:
            command = command.split("@")[0]

        handler = self._command_handlers.get(command)
        if handler:
            return handler(command, args, chat_id, msg_id, user)

        return None

    def _handle_callback(self, callback: Dict) -> Optional[Dict]:
        """Route callback query to registered handler"""
        query = CallbackQuery.from_dict(callback)

        for prefix, handler in self._callback_handlers.items():
            if query.data.startswith(prefix):
                return handler(query)

        return None

    def _handle_media(
        self, msg: Dict, chat_id: int, msg_id: int, user: TelegramUser
    ) -> Optional[Dict]:
        """Handle media messages"""
        if self._media_handler:
            media_type = "unknown"
            file_id = ""
            caption = msg.get("caption", "")

            if "photo" in msg:
                media_type = "photo"
                # Get highest resolution
                file_id = msg["photo"][-1]["file_id"] if msg["photo"] else ""
            elif "document" in msg:
                media_type = "document"
                file_id = msg["document"].get("file_id", "")
            elif "voice" in msg:
                media_type = "voice"
                file_id = msg["voice"].get("file_id", "")
            elif "video" in msg:
                media_type = "video"
                file_id = msg["video"].get("file_id", "")
            elif "audio" in msg:
                media_type = "audio"
                file_id = msg["audio"].get("file_id", "")

            return self._media_handler(
                media_type, file_id, caption, chat_id, msg_id, user
            )

        return None

    @staticmethod
    def typing_action(chat_id: int) -> Dict:
        """Generate typing action request"""
        return {
            "method": "sendChatAction",
            "chat_id": chat_id,
            "action": "typing",
        }
