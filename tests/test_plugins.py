"""
插件系统测试
"""

import os
import sys
import time
import pytest
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.base import PluginBase, PluginContext, PluginResult
from plugins.loader import PluginLoader
from plugins.builtin import WeatherPlugin
from plugins.builtin.translate import TranslatePlugin
from plugins.builtin.image_gen import ImageGenPlugin


# ---- Test PluginBase / PluginResult ----

class EchoPlugin(PluginBase):
    name = "echo"
    description = "Echo test"
    version = "0.1.0"
    commands = ["/echo"]
    priority = 10

    def on_message(self, ctx: PluginContext) -> PluginResult:
        if ctx.is_command and ctx.command == "/echo":
            return PluginResult.respond(f"ECHO: {ctx.args}")
        if "hello" in ctx.message.lower():
            return PluginResult.respond("Hi there!")
        return PluginResult.skip()


class CounterPlugin(PluginBase):
    name = "counter"
    description = "Count messages"
    version = "0.1.0"
    priority = 1

    def __init__(self):
        super().__init__()
        self.count = 0

    def on_message(self, ctx: PluginContext) -> PluginResult:
        self.count += 1
        return PluginResult(handled=True, stop_chain=False)


class TestPluginResult:
    def test_skip(self):
        r = PluginResult.skip()
        assert not r.handled
        assert r.reply is None

    def test_respond(self):
        r = PluginResult.respond("Hello", stop=True)
        assert r.handled
        assert r.reply == "Hello"
        assert r.stop_chain

    def test_respond_no_stop(self):
        r = PluginResult.respond("Hi", stop=False)
        assert r.handled
        assert not r.stop_chain


class TestPluginBase:
    def test_echo_plugin_command(self):
        plugin = EchoPlugin()
        ctx = PluginContext(user_id="u1", message="/echo test", command="/echo", args="test", is_command=True)
        result = plugin.on_message(ctx)
        assert result.handled
        assert "ECHO: test" in result.reply

    def test_echo_plugin_hello(self):
        plugin = EchoPlugin()
        ctx = PluginContext(user_id="u1", message="hello world")
        result = plugin.on_message(ctx)
        assert result.handled
        assert "Hi" in result.reply

    def test_echo_plugin_skip(self):
        plugin = EchoPlugin()
        ctx = PluginContext(user_id="u1", message="random text")
        result = plugin.on_message(ctx)
        assert not result.handled

    def test_plugin_info(self):
        plugin = EchoPlugin()
        info = plugin.get_info()
        assert info["name"] == "echo"
        assert info["version"] == "0.1.0"
        assert info["enabled"]

    def test_plugin_enable_disable(self):
        plugin = EchoPlugin()
        assert plugin.enabled
        plugin.disable()
        assert not plugin.enabled
        plugin.enable()
        assert plugin.enabled


class TestPluginLoader:
    def test_register_unregister(self):
        loader = PluginLoader()
        plugin = EchoPlugin()
        assert loader.register(plugin)
        assert "echo" in loader.plugins
        assert loader.unregister("echo")
        assert "echo" not in loader.plugins

    def test_command_routing(self):
        loader = PluginLoader()
        loader.register(EchoPlugin())
        ctx = PluginContext(user_id="u1", message="/echo hi", command="/echo", args="hi", is_command=True)
        result = loader.dispatch(ctx)
        assert result is not None
        assert "ECHO: hi" in result.reply

    def test_chain_priority(self):
        loader = PluginLoader()
        counter = CounterPlugin()
        echo = EchoPlugin()
        loader.register(counter)
        loader.register(echo)

        ctx = PluginContext(user_id="u1", message="hello")
        result = loader.dispatch(ctx)
        assert counter.count == 1
        assert result is not None
        assert "Hi" in result.reply

    def test_disabled_plugin_skipped(self):
        loader = PluginLoader()
        echo = EchoPlugin()
        echo.disable()
        loader.register(echo)
        ctx = PluginContext(user_id="u1", message="hello")
        result = loader.dispatch(ctx)
        assert result is None

    def test_get_command_list(self):
        loader = PluginLoader()
        loader.register(EchoPlugin())
        cmds = loader.get_command_list()
        assert "/echo" in cmds

    def test_get_status(self):
        loader = PluginLoader()
        loader.register(EchoPlugin())
        status = loader.get_status()
        assert status["total"] == 1
        assert status["enabled"] == 1

    def test_load_directory(self, tmp_path):
        # Write a simple plugin file
        code = '''
from plugins.base import PluginBase, PluginContext, PluginResult

class TestDirPlugin(PluginBase):
    name = "testdir"
    description = "Test dir load"
    version = "0.1.0"

    def on_message(self, ctx):
        return PluginResult.skip()
'''
        (tmp_path / "test_plugin.py").write_text(code)
        loader = PluginLoader()
        count = loader.load_directory(str(tmp_path))
        assert count == 1
        assert "testdir" in loader.plugins

    def test_load_nonexistent_dir(self):
        loader = PluginLoader()
        count = loader.load_directory("/nonexistent/path")
        assert count == 0


class TestWeatherPlugin:
    def test_skip_non_command(self):
        plugin = WeatherPlugin()
        ctx = PluginContext(user_id="u1", message="what's the weather")
        result = plugin.on_message(ctx)
        assert not result.handled

    def test_command_handler(self):
        plugin = WeatherPlugin()
        ctx = PluginContext(user_id="u1", message="/weather", command="/weather", args="", is_command=True)
        # This makes an actual HTTP call — we just check it doesn't crash
        result = plugin.on_command(ctx)
        assert result.handled


class TestTranslatePlugin:
    def test_no_args(self):
        plugin = TranslatePlugin()
        ctx = PluginContext(user_id="u1", message="/tr", command="/tr", args="", is_command=True)
        result = plugin.on_command(ctx)
        assert result.handled
        assert "支持语言" in result.reply

    def test_translate(self):
        plugin = TranslatePlugin()
        ctx = PluginContext(user_id="u1", message="/tr en 你好", command="/tr", args="en 你好", is_command=True)
        result = plugin.on_command(ctx)
        assert result.handled
        assert "English" in result.reply
        assert result.metadata.get("translate_target") == "en"

    def test_missing_text(self):
        plugin = TranslatePlugin()
        ctx = PluginContext(user_id="u1", message="/tr en", command="/tr", args="en", is_command=True)
        result = plugin.on_command(ctx)
        assert result.handled


class TestImageGenPlugin:
    def test_no_args(self):
        plugin = ImageGenPlugin()
        ctx = PluginContext(user_id="u1", message="/img", command="/img", args="", is_command=True)
        result = plugin.on_command(ctx)
        assert result.handled
        assert "用法" in result.reply

    def test_no_api_key(self):
        plugin = ImageGenPlugin()
        plugin.api_key = ""
        ctx = PluginContext(user_id="u1", message="/img a cat", command="/img", args="a cat", is_command=True)
        result = plugin.on_command(ctx)
        assert result.handled
        assert "未配置" in result.reply
