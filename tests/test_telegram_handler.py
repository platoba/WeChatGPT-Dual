"""Tests for telegram/handler.py"""

import pytest
from telegram.handler import (
    TelegramUser, InlineButton, InlineKeyboard, InlineKeyboardBuilder,
    MessageFormatter, UserRateLimiter, CallbackQuery, TelegramHandler,
)


class TestTelegramUser:
    def test_from_dict(self):
        u = TelegramUser.from_dict({"id": 123, "username": "alice", "first_name": "Alice"})
        assert u.id == 123
        assert u.username == "alice"
        assert u.first_name == "Alice"

    def test_display_name_full(self):
        u = TelegramUser(id=1, first_name="Alice", last_name="Smith")
        assert u.display_name == "Alice Smith"

    def test_display_name_fallback(self):
        u = TelegramUser(id=1, username="bob")
        assert u.display_name == "bob"

    def test_display_name_id_only(self):
        u = TelegramUser(id=42)
        assert u.display_name == "42"


class TestInlineKeyboard:
    def test_button_callback(self):
        btn = InlineButton(text="Click", callback_data="do_thing")
        d = btn.to_dict()
        assert d["text"] == "Click"
        assert d["callback_data"] == "do_thing"
        assert "url" not in d

    def test_button_url(self):
        btn = InlineButton(text="Go", url="https://example.com")
        d = btn.to_dict()
        assert d["url"] == "https://example.com"

    def test_keyboard_to_dict(self):
        kb = InlineKeyboard(rows=[
            [InlineButton("A", callback_data="a"), InlineButton("B", callback_data="b")],
            [InlineButton("C", callback_data="c")],
        ])
        d = kb.to_dict()
        assert len(d["inline_keyboard"]) == 2
        assert len(d["inline_keyboard"][0]) == 2

    def test_builder(self):
        kb = (
            InlineKeyboardBuilder()
            .button("A", callback_data="a")
            .button("B", callback_data="b")
            .row()
            .button("C", callback_data="c")
            .build()
        )
        assert len(kb.rows) == 2
        assert kb.rows[0][0].text == "A"


class TestMessageFormatter:
    def test_escape_markdown(self):
        result = MessageFormatter.escape_markdown("Hello *world* [test]")
        assert "\\*" in result
        assert "\\[" in result

    def test_escape_html(self):
        result = MessageFormatter.escape_html("<b>test</b> & 'x'")
        assert "&lt;" in result
        assert "&amp;" in result

    def test_bold_html(self):
        assert MessageFormatter.bold("hi", "html") == "<b>hi</b>"

    def test_bold_md(self):
        assert MessageFormatter.bold("hi", "md") == "*hi*"

    def test_italic(self):
        assert MessageFormatter.italic("hi", "html") == "<i>hi</i>"

    def test_code(self):
        result = MessageFormatter.code("x = 1", "html")
        assert "<code>" in result

    def test_pre(self):
        result = MessageFormatter.pre("print(1)", "python", "html")
        assert "language-python" in result

    def test_link(self):
        result = MessageFormatter.link("Click", "https://x.com", "html")
        assert 'href="https://x.com"' in result

    def test_truncate(self):
        short = "Hello"
        assert MessageFormatter.truncate(short, 100) == short
        long = "x" * 5000
        result = MessageFormatter.truncate(long, 100)
        assert len(result) == 100
        assert result.endswith("...")

    def test_chunk_message(self):
        text = "word " * 1000
        chunks = MessageFormatter.chunk_message(text, 100)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 100


class TestUserRateLimiter:
    def test_allow(self):
        rl = UserRateLimiter(max_tokens=5)
        for _ in range(5):
            assert rl.check("u1") is True

    def test_block_after_exhaustion(self):
        rl = UserRateLimiter(max_tokens=2, refill_rate=0, refill_interval=9999)
        assert rl.check("u1") is True
        assert rl.check("u1") is True
        assert rl.check("u1") is False

    def test_remaining(self):
        rl = UserRateLimiter(max_tokens=5)
        rl.check("u1")
        assert rl.remaining("u1") >= 3

    def test_reset(self):
        rl = UserRateLimiter(max_tokens=2, refill_rate=0, refill_interval=9999)
        rl.check("u1")
        rl.check("u1")
        assert rl.check("u1") is False
        rl.reset("u1")
        assert rl.check("u1") is True

    def test_reset_all(self):
        rl = UserRateLimiter(max_tokens=1, refill_rate=0, refill_interval=9999)
        rl.check("u1")
        rl.check("u2")
        rl.reset_all()
        assert rl.check("u1") is True
        assert rl.check("u2") is True


class TestCallbackQuery:
    def test_from_dict(self):
        data = {
            "id": "cb1",
            "from": {"id": 42, "username": "alice"},
            "message": {"chat": {"id": 100}, "message_id": 5},
            "data": "settings:theme",
        }
        cq = CallbackQuery.from_dict(data)
        assert cq.id == "cb1"
        assert cq.from_user.id == 42
        assert cq.chat_id == 100
        assert cq.data == "settings:theme"


class TestTelegramHandler:
    def test_command_routing(self):
        handler = TelegramHandler(bot_username="testbot")
        results = []
        handler.register_command("/help", lambda cmd, args, cid, mid, u: {"text": "Help!"})

        update = {
            "message": {
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42},
                "message_id": 10,
                "text": "/help",
            }
        }
        result = handler.process_update(update)
        assert result is not None
        assert result["text"] == "Help!"

    def test_text_routing(self):
        handler = TelegramHandler(bot_username="testbot")
        handler.register_text_handler(lambda text, cid, mid, u: {"text": f"Echo: {text}"})

        update = {
            "message": {
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42},
                "message_id": 10,
                "text": "Hello",
            }
        }
        result = handler.process_update(update)
        assert result["text"] == "Echo: Hello"

    def test_callback_routing(self):
        handler = TelegramHandler(bot_username="testbot")
        handler.register_callback("settings:", lambda cq: {"text": f"Setting: {cq.data}"})

        update = {
            "callback_query": {
                "id": "1",
                "from": {"id": 42},
                "message": {"chat": {"id": 1}, "message_id": 5},
                "data": "settings:dark",
            }
        }
        result = handler.process_update(update)
        assert "settings:dark" in result["text"]

    def test_group_reply_only(self):
        handler = TelegramHandler(bot_username="testbot", reply_only_in_groups=True)
        handler.register_text_handler(lambda *a: {"text": "Hi"})

        # No mention → should be ignored
        update = {
            "message": {
                "chat": {"id": 1, "type": "group"},
                "from": {"id": 42},
                "message_id": 10,
                "text": "Hello everyone",
            }
        }
        assert handler.process_update(update) is None

    def test_group_mention_detected(self):
        handler = TelegramHandler(bot_username="testbot", reply_only_in_groups=False)
        handler.register_text_handler(lambda text, *a: {"text": text})

        update = {
            "message": {
                "chat": {"id": 1, "type": "group"},
                "from": {"id": 42},
                "message_id": 10,
                "text": "@testbot What's up?",
            }
        }
        result = handler.process_update(update)
        assert result is not None

    def test_rate_limit(self):
        handler = TelegramHandler(bot_username="testbot", rate_limit_messages=1)
        handler.rate_limiter = UserRateLimiter(max_tokens=1, refill_rate=0, refill_interval=9999)
        handler.register_text_handler(lambda *a: {"text": "ok"})

        update = {
            "message": {
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42},
                "message_id": 10,
                "text": "hi",
            }
        }
        handler.process_update(update)  # first call OK
        result = handler.process_update(update)  # rate limited
        assert "频繁" in result["text"]

    def test_media_handler(self):
        handler = TelegramHandler(bot_username="testbot")
        handler.register_media_handler(
            lambda mtype, fid, cap, cid, mid, u: {"text": f"Got {mtype}"}
        )

        update = {
            "message": {
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42},
                "message_id": 10,
                "photo": [{"file_id": "abc", "width": 100, "height": 100}],
            }
        }
        result = handler.process_update(update)
        assert result["text"] == "Got photo"

    def test_typing_action(self):
        action = TelegramHandler.typing_action(123)
        assert action["method"] == "sendChatAction"
        assert action["chat_id"] == 123
        assert action["action"] == "typing"

    def test_no_message(self):
        handler = TelegramHandler(bot_username="testbot")
        assert handler.process_update({}) is None

    def test_command_with_args(self):
        handler = TelegramHandler(bot_username="testbot")
        handler.register_command("/set", lambda cmd, args, *a: {"text": f"Args: {args}"})

        update = {
            "message": {
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42},
                "message_id": 10,
                "text": "/set dark_mode on",
            }
        }
        result = handler.process_update(update)
        assert result["text"] == "Args: dark_mode on"
