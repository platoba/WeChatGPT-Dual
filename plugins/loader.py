"""
插件加载器 - 热加载/卸载/重载插件
"""

import os
import sys
import time
import logging
import importlib
import importlib.util
from typing import Dict, List, Optional, Type

from plugins.base import PluginBase, PluginContext, PluginResult

logger = logging.getLogger(__name__)


class PluginLoader:
    """
    插件管理器
    - 扫描指定目录加载插件
    - 支持热重载
    - 插件链式调用
    """

    def __init__(self, plugin_dirs: Optional[List[str]] = None):
        self._plugins: Dict[str, PluginBase] = {}
        self._command_map: Dict[str, str] = {}  # command -> plugin_name
        self._plugin_dirs = plugin_dirs or []
        self._load_errors: Dict[str, str] = {}

    @property
    def plugins(self) -> Dict[str, PluginBase]:
        return dict(self._plugins)

    def register(self, plugin: PluginBase) -> bool:
        """注册一个插件实例"""
        if plugin.name in self._plugins:
            logger.warning(f"Plugin {plugin.name} already registered, replacing")
            self.unregister(plugin.name)

        self._plugins[plugin.name] = plugin

        # 注册命令映射
        for cmd in plugin.commands:
            self._command_map[cmd] = plugin.name

        # 调用启动钩子
        try:
            plugin.on_startup()
        except Exception as e:
            logger.error(f"Plugin {plugin.name} startup error: {e}")
            plugin._error_count += 1

        logger.info(f"Registered plugin: {plugin.name} v{plugin.version}")
        return True

    def unregister(self, name: str) -> bool:
        """卸载插件"""
        plugin = self._plugins.pop(name, None)
        if not plugin:
            return False

        # 移除命令映射
        for cmd in plugin.commands:
            self._command_map.pop(cmd, None)

        # 调用关闭钩子
        try:
            plugin.on_shutdown()
        except Exception as e:
            logger.error(f"Plugin {name} shutdown error: {e}")

        logger.info(f"Unregistered plugin: {name}")
        return True

    def load_directory(self, directory: str) -> int:
        """从目录加载所有插件"""
        loaded = 0
        if not os.path.isdir(directory):
            logger.warning(f"Plugin directory not found: {directory}")
            return 0

        for filename in sorted(os.listdir(directory)):
            if filename.startswith("_") or not filename.endswith(".py"):
                continue

            filepath = os.path.join(directory, filename)
            module_name = f"plugin_{filename[:-3]}"

            try:
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                # 查找 PluginBase 子类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, PluginBase)
                        and attr is not PluginBase
                    ):
                        instance = attr()
                        self.register(instance)
                        loaded += 1

            except Exception as e:
                self._load_errors[filename] = str(e)
                logger.error(f"Failed to load plugin {filename}: {e}")

        return loaded

    def load_all(self) -> int:
        """加载所有配置目录的插件"""
        total = 0
        for d in self._plugin_dirs:
            total += self.load_directory(d)
        return total

    def reload_plugin(self, name: str) -> bool:
        """重新加载插件"""
        plugin = self._plugins.get(name)
        if not plugin:
            return False

        # 找到原始模块并重新加载
        module_name = f"plugin_{name}"
        if module_name in sys.modules:
            try:
                module = importlib.reload(sys.modules[module_name])
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, PluginBase)
                        and attr is not PluginBase
                    ):
                        self.unregister(name)
                        instance = attr()
                        self.register(instance)
                        return True
            except Exception as e:
                logger.error(f"Reload failed for {name}: {e}")
                return False
        return False

    def dispatch(self, ctx: PluginContext) -> Optional[PluginResult]:
        """
        分发消息到插件链
        按优先级排序，第一个handled=True且stop_chain=True的终止链
        """
        # 命令直接路由
        if ctx.is_command and ctx.command in self._command_map:
            plugin_name = self._command_map[ctx.command]
            plugin = self._plugins.get(plugin_name)
            if plugin and plugin.enabled:
                try:
                    plugin._call_count += 1
                    result = plugin.on_command(ctx)
                    if result.handled:
                        return result
                except Exception as e:
                    plugin._error_count += 1
                    logger.error(f"Plugin {plugin_name} command error: {e}")

        # 按优先级遍历插件链
        sorted_plugins = sorted(
            self._plugins.values(),
            key=lambda p: p.priority,
        )

        for plugin in sorted_plugins:
            if not plugin.enabled:
                continue
            try:
                plugin._call_count += 1
                result = plugin.on_message(ctx)
                if result.handled:
                    if result.stop_chain:
                        return result
                    # handled but not stop_chain — continue
            except Exception as e:
                plugin._error_count += 1
                logger.error(f"Plugin {plugin.name} error: {e}")

        return None

    def get_command_list(self) -> Dict[str, str]:
        """获取所有插件命令"""
        result = {}
        for plugin in self._plugins.values():
            if not plugin.enabled:
                continue
            for cmd in plugin.commands:
                result[cmd] = f"{plugin.name}: {plugin.description}"
        return result

    def get_status(self) -> Dict:
        """获取所有插件状态"""
        return {
            "total": len(self._plugins),
            "enabled": sum(1 for p in self._plugins.values() if p.enabled),
            "commands": len(self._command_map),
            "load_errors": self._load_errors,
            "plugins": {
                name: p.get_info()
                for name, p in self._plugins.items()
            },
        }
