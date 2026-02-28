"""Tests for CommandHandler"""

import pytest
from commands.handler import CommandHandler


class TestCommandRouting:
    def test_status_command(self, command_handler):
        result = command_handler.handle("/status", "", "user1")
        assert result is not None
        assert "系统状态" in result
        assert "主引擎" in result

    def test_help_command(self, command_handler):
        result = command_handler.handle("/help", "", "user1")
        assert "WeChatGPT" in result
        assert "/status" in result
        assert "/switch" in result

    def test_clear_command(self, command_handler):
        result = command_handler.handle("/clear", "", "user1")
        assert "清除" in result

    def test_usage_command(self, command_handler):
        result = command_handler.handle("/usage", "", "user1")
        assert "统计" in result

    def test_unknown_command(self, command_handler):
        result = command_handler.handle("/unknown", "", "user1")
        assert result is None

    def test_get_commands(self, command_handler):
        cmds = command_handler.get_commands()
        assert "/status" in cmds
        assert "/help" in cmds
        assert "/clear" in cmds
        assert "/switch" in cmds
        assert "/usage" in cmds


class TestSwitchCommand:
    def test_switch_no_args(self, command_handler):
        result = command_handler.handle("/switch", "", "user1")
        assert "当前主引擎" in result
        assert "可用引擎" in result

    def test_switch_valid(self, command_handler):
        result = command_handler.handle("/switch", "secondary", "user1")
        assert "已切换" in result

    def test_switch_invalid(self, command_handler):
        result = command_handler.handle("/switch", "nonexistent", "user1")
        assert "未知引擎" in result


class TestModelCommand:
    def test_model_no_args(self, command_handler):
        result = command_handler.handle("/model", "", "user1")
        assert "当前引擎" in result

    def test_model_with_args(self, command_handler):
        result = command_handler.handle("/model", "gpt-4o", "user1")
        assert "模型切换" in result or "环境变量" in result


class TestRoleCommand:
    def test_role_no_args(self, command_handler):
        result = command_handler.handle("/role", "", "user1")
        assert "当前角色" in result

    def test_role_set(self, command_handler):
        result = command_handler.handle("/role", "你是一个翻译专家", "user1")
        assert "角色已设置" in result
        assert "翻译专家" in result


class TestKBCommand:
    def test_kb_no_args(self, command_handler):
        result = command_handler.handle("/kb", "", "user1")
        assert "知识库" in result

    def test_kb_search_empty(self, command_handler):
        result = command_handler.handle("/kb", "something", "user1")
        assert "未找到" in result

    def test_kb_search_with_data(self, command_handler, knowledge_store):
        knowledge_store.add_document("Python编程语言入门", source="py.md")
        result = command_handler.handle("/kb", "Python", "user1")
        assert "检索结果" in result or "Python" in result
