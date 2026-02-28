"""
微信消息模型
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import time


class MessageType(Enum):
    """消息类型"""
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    LOCATION = "location"
    LINK = "link"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class WeChatMessage:
    """微信消息数据模型"""
    msg_id: str
    sender_id: str
    sender_name: str = ""
    content: str = ""
    msg_type: MessageType = MessageType.TEXT
    is_group: bool = False
    group_id: str = ""
    group_name: str = ""
    is_at_me: bool = False
    timestamp: float = field(default_factory=time.time)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WeChatMessage":
        """从字典创建消息"""
        msg_type_str = data.get("msg_type", "text")
        try:
            msg_type = MessageType(msg_type_str)
        except ValueError:
            msg_type = MessageType.UNKNOWN

        return cls(
            msg_id=str(data.get("msg_id", "")),
            sender_id=str(data.get("sender_id", "")),
            sender_name=data.get("sender_name", ""),
            content=data.get("content", ""),
            msg_type=msg_type,
            is_group=data.get("is_group", False),
            group_id=str(data.get("group_id", "")),
            group_name=data.get("group_name", ""),
            is_at_me=data.get("is_at_me", False),
            timestamp=data.get("timestamp", time.time()),
            raw_data=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        return {
            "msg_id": self.msg_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "msg_type": self.msg_type.value,
            "is_group": self.is_group,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "is_at_me": self.is_at_me,
            "timestamp": self.timestamp,
        }

    @property
    def is_command(self) -> bool:
        """是否是命令消息"""
        return self.content.startswith("/")

    @property
    def command(self) -> Optional[str]:
        """提取命令名"""
        if not self.is_command:
            return None
        parts = self.content.split(maxsplit=1)
        return parts[0].lower()

    @property
    def command_args(self) -> str:
        """提取命令参数"""
        if not self.is_command:
            return ""
        parts = self.content.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    @property
    def user_key(self) -> str:
        """用户唯一标识（群聊用群ID+用户ID）"""
        if self.is_group:
            return f"group_{self.group_id}_{self.sender_id}"
        return f"user_{self.sender_id}"
