"""Tests for WeChatHandler"""

from wechat.message import WeChatMessage, MessageType
from wechat.handler import WeChatHandler


class TestWeChatHandlerBasic:
    def test_handle_text_message(self, wechat_handler, sample_message):
        reply = wechat_handler.handle_message(sample_message)
        assert reply is not None
        assert "Mock response" in reply

    def test_handle_non_text(self, wechat_handler):
        msg = WeChatMessage(
            msg_id="1", sender_id="u1",
            content="", msg_type=MessageType.IMAGE,
        )
        reply = wechat_handler.handle_message(msg)
        assert reply is None

    def test_handle_empty_content(self, wechat_handler):
        msg = WeChatMessage(
            msg_id="1", sender_id="u1", content="  "
        )
        reply = wechat_handler.handle_message(msg)
        assert reply is None

    def test_handle_command(self, wechat_handler):
        msg = WeChatMessage(
            msg_id="1", sender_id="u1", content="/help"
        )
        reply = wechat_handler.handle_message(msg)
        assert reply is not None
        assert "WeChatGPT" in reply


class TestWeChatHandlerGroup:
    def test_group_at_me(self, wechat_handler, sample_group_message):
        reply = wechat_handler.handle_message(sample_group_message)
        assert reply is not None

    def test_group_not_at_me(self, wechat_handler):
        msg = WeChatMessage(
            msg_id="1", sender_id="u1",
            content="Random chat",
            is_group=True, group_id="g1",
            is_at_me=False,
        )
        reply = wechat_handler.handle_message(msg)
        assert reply is None

    def test_group_at_disabled(self, engine_manager, context_manager, knowledge_store, command_handler):
        handler = WeChatHandler(
            engine_manager=engine_manager,
            context_manager=context_manager,
            knowledge_store=knowledge_store,
            command_handler=command_handler,
            group_at_only=False,
        )
        msg = WeChatMessage(
            msg_id="1", sender_id="u1",
            content="Hello",
            is_group=True, group_id="g1",
            is_at_me=False,
        )
        reply = handler.handle_message(msg)
        assert reply is not None


class TestWeChatHandlerRaw:
    def test_handle_raw(self, wechat_handler):
        data = {
            "msg_id": "raw1",
            "sender_id": "u1",
            "content": "Raw message",
            "msg_type": "text",
        }
        reply = wechat_handler.handle_raw(data)
        assert reply is not None

    def test_handle_raw_image(self, wechat_handler):
        data = {
            "msg_id": "raw2",
            "sender_id": "u1",
            "msg_type": "image",
        }
        reply = wechat_handler.handle_raw(data)
        assert reply is None


class TestWeChatHandlerFailover:
    def test_failover_indicator(self, wechat_handler, mock_engine):
        mock_engine.should_fail = True
        msg = WeChatMessage(
            msg_id="1", sender_id="u1", content="Test failover"
        )
        reply = wechat_handler.handle_message(msg)
        assert "auto-failover" in reply or "自动切换" in reply

    def test_both_fail(self, wechat_handler, mock_engine, mock_secondary):
        mock_engine.should_fail = True
        mock_secondary.should_fail = True
        msg = WeChatMessage(
            msg_id="1", sender_id="u1", content="Test both fail"
        )
        reply = wechat_handler.handle_message(msg)
        assert "错误" in reply or "error" in reply.lower()


class TestWeChatHandlerCallback:
    def test_reply_callback(self, wechat_handler, sample_message):
        callback_calls = []

        def on_reply(msg, text):
            callback_calls.append((msg, text))

        wechat_handler.set_reply_callback(on_reply)
        wechat_handler.handle_message(sample_message)
        assert len(callback_calls) == 1
        assert callback_calls[0][0] is sample_message


class TestWeChatHandlerStats:
    def test_stats(self, wechat_handler, sample_message):
        wechat_handler.handle_message(sample_message)
        stats = wechat_handler.get_stats()
        assert stats["total_messages_processed"] == 1
        assert stats["active_users"] >= 1
        assert "engine_status" in stats
