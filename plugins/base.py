"""
插件基类 - 定义热插拔插件标准接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import time


@dataclass
class PluginContext:
    """插件执行上下文"""
    user_id: str
    message: str
    command: str = ""
    args: str = ""
    is_command: bool = False
    channel: str = "telegram"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginResult:
    """插件执行结果"""
    handled: bool = False
    reply: Optional[str] = None
    stop_chain: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def skip(cls) -> "PluginResult":
        return cls(handled=False)

    @classmethod
    def respond(cls, text: str, stop: bool = True) -> "PluginResult":
        return cls(handled=True, reply=text, stop_chain=stop)


class PluginBase(ABC):
    """
    插件基类
    所有插件必须继承并实现 on_message 或注册命令
    """

    name: str = "unnamed"
    description: str = ""
    version: str = "1.0.0"
    commands: List[str] = []
    priority: int = 100  # 数字越小优先级越高

    def __init__(self):
        self._enabled = True
        self._load_time = time.time()
        self._call_count = 0
        self._error_count = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def on_startup(self) -> None:
        """插件启动时调用（可选覆盖）"""
        pass

    def on_shutdown(self) -> None:
        """插件关闭时调用（可选覆盖）"""
        pass

    @abstractmethod
    def on_message(self, ctx: PluginContext) -> PluginResult:
        """
        处理消息
        返回 PluginResult.skip() 表示不处理
        返回 PluginResult.respond(text) 表示处理并回复
        """
        pass

    def on_command(self, ctx: PluginContext) -> PluginResult:
        """
        处理命令（默认走on_message）
        子类可覆盖以单独处理命令
        """
        return self.on_message(ctx)

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self._enabled,
            "commands": self.commands,
            "priority": self.priority,
            "call_count": self._call_count,
            "error_count": self._error_count,
            "uptime": round(time.time() - self._load_time, 1),
        }
