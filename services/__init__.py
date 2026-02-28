"""
对话导出服务 - JSON/Markdown/CSV格式
"""

import csv
import json
import time
import io
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class ExportedChat:
    """导出的对话"""
    user_id: str
    messages: List[Dict[str, str]]
    exported_at: float
    format: str
    content: str


class ConversationExporter:
    """
    对话历史导出器
    支持 JSON / Markdown / CSV 格式
    """

    def __init__(self, context_manager):
        self.context_manager = context_manager

    def export(
        self,
        user_id: str,
        fmt: str = "markdown",
        include_system: bool = False,
    ) -> Optional[ExportedChat]:
        """
        导出用户对话历史

        Args:
            user_id: 用户ID
            fmt: 格式 (json/markdown/csv)
            include_system: 是否包含system消息

        Returns:
            ExportedChat or None
        """
        messages = self.context_manager.get_messages(user_id)
        if not messages:
            return None

        if not include_system:
            messages = [m for m in messages if m.get("role") != "system"]

        if not messages:
            return None

        if fmt == "json":
            content = self._to_json(messages, user_id)
        elif fmt == "csv":
            content = self._to_csv(messages)
        else:
            content = self._to_markdown(messages, user_id)

        return ExportedChat(
            user_id=user_id,
            messages=messages,
            exported_at=time.time(),
            format=fmt,
            content=content,
        )

    def _to_json(self, messages: List[Dict], user_id: str) -> str:
        data = {
            "user_id": user_id,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(messages),
            "messages": messages,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _to_markdown(self, messages: List[Dict], user_id: str) -> str:
        lines = [
            f"# Chat Export — {user_id}",
            f"*Exported: {time.strftime('%Y-%m-%d %H:%M:%S')}*",
            f"*Messages: {len(messages)}*",
            "",
            "---",
            "",
        ]

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            emoji = {"user": "👤", "assistant": "🤖", "system": "⚙️"}.get(role, "❓")
            lines.append(f"### {emoji} {role.title()}")
            lines.append("")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)

    def _to_csv(self, messages: List[Dict]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["role", "content"])
        for msg in messages:
            writer.writerow([msg.get("role", ""), msg.get("content", "")])
        return output.getvalue()

    def get_formats(self) -> List[str]:
        return ["json", "markdown", "csv"]
