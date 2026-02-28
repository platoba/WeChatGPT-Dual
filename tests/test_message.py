"""Tests for WeChatMessage model"""

import time
import pytest
from wechat.message import WeChatMessage, MessageType


class TestMessageType:
    def test_all_types(self):
        assert MessageType.TEXT.value == "text"
        assert MessageType.IMAGE.value == "image"
        assert MessageType.VOICE.value == "voice"
        assert MessageType.SYSTEM.value == "system"
        assert MessageType.UNKNOWN.value == "unknown"


class TestWeChatMessage:
    def test_basic(self, sample_message):
        assert sample_message.msg_id == "msg001"
        assert sample_message.sender_id == "user123"
        assert sample_message.content == "Hello AI"
        assert sample_message.msg_type == MessageType.TEXT
        assert sample_message.is_group is False

    def test_group_message(self, sample_group_message):
        assert sample_group_message.is_group is True
        assert sample_group_message.group_id == "group789"
        assert sample_group_message.is_at_me is True

    def test_is_command(self):
        msg = WeChatMessage(msg_id="1", sender_id="u1", content="/status")
        assert msg.is_command is True
        assert msg.command == "/status"
        assert msg.command_args == ""

    def test_command_with_args(self):
        msg = WeChatMessage(msg_id="1", sender_id="u1", content="/role 你是翻译")
        assert msg.is_command is True
        assert msg.command == "/role"
        assert msg.command_args == "你是翻译"

    def test_not_command(self):
        msg = WeChatMessage(msg_id="1", sender_id="u1", content="Hello")
        assert msg.is_command is False
        assert msg.command is None
        assert msg.command_args == ""

    def test_user_key_private(self):
        msg = WeChatMessage(msg_id="1", sender_id="user123")
        assert msg.user_key == "user_user123"

    def test_user_key_group(self):
        msg = WeChatMessage(
            msg_id="1", sender_id="user123",
            is_group=True, group_id="group456"
        )
        assert msg.user_key == "group_group456_user123"

    def test_from_dict(self):
        data = {
            "msg_id": "m1",
            "sender_id": "s1",
            "sender_name": "Alice",
            "content": "Hi",
            "msg_type": "text",
            "is_group": False,
        }
        msg = WeChatMessage.from_dict(data)
        assert msg.msg_id == "m1"
        assert msg.sender_name == "Alice"
        assert msg.msg_type == MessageType.TEXT

    def test_from_dict_unknown_type(self):
        data = {"msg_id": "m1", "sender_id": "s1", "msg_type": "sticker"}
        msg = WeChatMessage.from_dict(data)
        assert msg.msg_type == MessageType.UNKNOWN

    def test_to_dict(self, sample_message):
        d = sample_message.to_dict()
        assert d["msg_id"] == "msg001"
        assert d["content"] == "Hello AI"
        assert d["msg_type"] == "text"
        assert d["is_group"] is False

    def test_roundtrip(self, sample_message):
        d = sample_message.to_dict()
        msg2 = WeChatMessage.from_dict(d)
        assert msg2.msg_id == sample_message.msg_id
        assert msg2.content == sample_message.content
