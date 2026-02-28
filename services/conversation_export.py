"""
Conversation Export Service - 对话导出

Features:
- JSON / CSV / HTML / Markdown 4种导出格式
- 按用户、时间范围、渠道过滤
- HTML导出带美观样式 (对话气泡)
- Markdown导出兼容Obsidian/Notion
- 批量导出 + 归档
- 导出统计摘要
"""

import csv
import json
import time
import logging
import io
import html as html_lib
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class ExportFormat(Enum):
    JSON = "json"
    CSV = "csv"
    HTML = "html"
    MARKDOWN = "markdown"


@dataclass
class ExportFilter:
    """导出过滤条件"""
    user_id: Optional[str] = None
    channel: Optional[str] = None
    engine: Optional[str] = None
    since: Optional[float] = None  # Unix timestamp
    until: Optional[float] = None
    role: Optional[str] = None  # "user" | "assistant"
    min_tokens: int = 0
    search_text: Optional[str] = None
    limit: int = 0  # 0 = no limit


@dataclass
class ExportResult:
    """导出结果"""
    format: ExportFormat
    content: str
    filename: str
    message_count: int
    user_count: int
    date_range: str
    size_bytes: int


class ConversationExporter:
    """对话导出器"""

    def __init__(self, db=None):
        """
        Args:
            db: Database instance with get_messages() method
        """
        self.db = db

    def export(
        self,
        messages: List[Dict] = None,
        fmt: ExportFormat = ExportFormat.JSON,
        filter_: ExportFilter = None,
        title: str = "WeChatGPT-Dual Conversations",
    ) -> ExportResult:
        """
        导出对话

        Args:
            messages: 消息列表 (如果为None则从db查询)
            fmt: 导出格式
            filter_: 过滤条件
            title: 导出标题
        """
        if messages is None and self.db:
            messages = self._query_messages(filter_)
        elif messages is None:
            messages = []

        # Apply filters to provided messages
        if filter_ and messages:
            messages = self._apply_filter(messages, filter_)

        # Sort by time
        messages = sorted(messages, key=lambda m: m.get("created_at", 0))

        # Generate export
        if fmt == ExportFormat.JSON:
            content = self._export_json(messages, title)
        elif fmt == ExportFormat.CSV:
            content = self._export_csv(messages)
        elif fmt == ExportFormat.HTML:
            content = self._export_html(messages, title)
        elif fmt == ExportFormat.MARKDOWN:
            content = self._export_markdown(messages, title)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        # Build result
        users = set(m.get("user_id", "") for m in messages)
        timestamps = [m.get("created_at", 0) for m in messages if m.get("created_at")]
        if timestamps:
            start = datetime.fromtimestamp(min(timestamps)).strftime("%Y-%m-%d")
            end = datetime.fromtimestamp(max(timestamps)).strftime("%Y-%m-%d")
            date_range = f"{start} ~ {end}"
        else:
            date_range = "N/A"

        ext_map = {
            ExportFormat.JSON: "json",
            ExportFormat.CSV: "csv",
            ExportFormat.HTML: "html",
            ExportFormat.MARKDOWN: "md",
        }

        filename = f"conversations_{int(time.time())}.{ext_map[fmt]}"

        return ExportResult(
            format=fmt,
            content=content,
            filename=filename,
            message_count=len(messages),
            user_count=len(users),
            date_range=date_range,
            size_bytes=len(content.encode("utf-8")),
        )

    def _query_messages(self, filter_: ExportFilter = None) -> List[Dict]:
        """从数据库查询消息"""
        if not self.db:
            return []

        kwargs = {}
        if filter_:
            if filter_.user_id:
                kwargs["user_id"] = filter_.user_id
            if filter_.channel:
                kwargs["channel"] = filter_.channel
            if filter_.since:
                kwargs["since"] = filter_.since
            kwargs["limit"] = filter_.limit or 10000
        else:
            kwargs["limit"] = 10000

        return self.db.get_messages(**kwargs)

    def _apply_filter(self, messages: List[Dict], filter_: ExportFilter) -> List[Dict]:
        """应用过滤条件"""
        result = messages

        if filter_.user_id:
            result = [m for m in result if m.get("user_id") == filter_.user_id]
        if filter_.channel:
            result = [m for m in result if m.get("channel") == filter_.channel]
        if filter_.engine:
            result = [m for m in result if m.get("engine") == filter_.engine]
        if filter_.role:
            result = [m for m in result if m.get("role") == filter_.role]
        if filter_.since:
            result = [m for m in result if m.get("created_at", 0) >= filter_.since]
        if filter_.until:
            result = [m for m in result if m.get("created_at", 0) <= filter_.until]
        if filter_.min_tokens:
            result = [m for m in result if m.get("tokens_used", 0) >= filter_.min_tokens]
        if filter_.search_text:
            text = filter_.search_text.lower()
            result = [m for m in result if text in m.get("content", "").lower()]
        if filter_.limit and filter_.limit > 0:
            result = result[:filter_.limit]

        return result

    # ---- JSON Export ----

    def _export_json(self, messages: List[Dict], title: str) -> str:
        """导出为JSON"""
        export = {
            "title": title,
            "exported_at": datetime.now().isoformat(),
            "total_messages": len(messages),
            "messages": []
        }

        for msg in messages:
            entry = {
                "user_id": msg.get("user_id", ""),
                "role": msg.get("role", ""),
                "content": msg.get("content", ""),
                "channel": msg.get("channel", ""),
                "engine": msg.get("engine", ""),
                "tokens_used": msg.get("tokens_used", 0),
                "latency": msg.get("latency", 0.0),
                "created_at": msg.get("created_at", 0),
                "created_at_iso": "",
            }
            if entry["created_at"]:
                entry["created_at_iso"] = datetime.fromtimestamp(
                    entry["created_at"]
                ).isoformat()
            export["messages"].append(entry)

        return json.dumps(export, ensure_ascii=False, indent=2)

    # ---- CSV Export ----

    def _export_csv(self, messages: List[Dict]) -> str:
        """导出为CSV"""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow([
            "timestamp", "user_id", "role", "content",
            "channel", "engine", "tokens_used", "latency"
        ])

        for msg in messages:
            ts = msg.get("created_at", 0)
            ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
            writer.writerow([
                ts_str,
                msg.get("user_id", ""),
                msg.get("role", ""),
                msg.get("content", ""),
                msg.get("channel", ""),
                msg.get("engine", ""),
                msg.get("tokens_used", 0),
                msg.get("latency", 0.0),
            ])

        return output.getvalue()

    # ---- HTML Export ----

    def _export_html(self, messages: List[Dict], title: str) -> str:
        """导出为带样式的HTML"""
        msg_html = []
        for msg in messages:
            role = msg.get("role", "")
            content = html_lib.escape(msg.get("content", ""))
            content = content.replace("\n", "<br>")
            user_id = html_lib.escape(msg.get("user_id", ""))
            ts = msg.get("created_at", 0)
            ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            engine = msg.get("engine", "")
            tokens = msg.get("tokens_used", 0)

            if role == "user":
                bubble_class = "user-bubble"
                label = f"👤 {user_id}"
            else:
                bubble_class = "assistant-bubble"
                label = f"🤖 {engine}" if engine else "🤖 Assistant"

            meta_parts = [ts_str]
            if tokens:
                meta_parts.append(f"{tokens} tokens")

            meta = " · ".join(meta_parts)

            msg_html.append(f"""
            <div class="message {bubble_class}">
                <div class="label">{label}</div>
                <div class="content">{content}</div>
                <div class="meta">{meta}</div>
            </div>""")

        messages_block = "\n".join(msg_html)

        # Stats
        total = len(messages)
        users = len(set(m.get("user_id", "") for m in messages))
        total_tokens = sum(m.get("tokens_used", 0) for m in messages)

        return f"""<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html_lib.escape(title)}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f0f2f5;
            padding: 20px;
            max-width: 800px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            color: #1a1a2e;
            margin: 20px 0;
            font-size: 1.5rem;
        }}
        .stats {{
            text-align: center;
            color: #666;
            margin-bottom: 20px;
            font-size: 0.9rem;
        }}
        .message {{
            margin: 10px 0;
            padding: 12px 16px;
            border-radius: 12px;
            max-width: 80%;
            word-wrap: break-word;
        }}
        .user-bubble {{
            background: #0084ff;
            color: white;
            margin-left: auto;
            border-bottom-right-radius: 4px;
        }}
        .assistant-bubble {{
            background: white;
            color: #1a1a2e;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        }}
        .label {{
            font-size: 0.75rem;
            opacity: 0.8;
            margin-bottom: 4px;
        }}
        .content {{
            line-height: 1.5;
        }}
        .meta {{
            font-size: 0.7rem;
            opacity: 0.6;
            margin-top: 4px;
            text-align: right;
        }}
        .user-bubble .label {{ color: rgba(255,255,255,0.9); }}
        .user-bubble .meta {{ color: rgba(255,255,255,0.7); }}
    </style>
</head>
<body>
    <h1>{html_lib.escape(title)}</h1>
    <div class="stats">
        📊 {total} messages · {users} users · {total_tokens:,} tokens ·
        Exported {datetime.now().strftime("%Y-%m-%d %H:%M")}
    </div>
    <div class="chat">
        {messages_block}
    </div>
</body>
</html>"""

    # ---- Markdown Export ----

    def _export_markdown(self, messages: List[Dict], title: str) -> str:
        """导出为Markdown"""
        lines = [
            f"# {title}",
            "",
            f"> Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"> Messages: {len(messages)}",
            "",
            "---",
            "",
        ]

        current_date = ""
        for msg in messages:
            ts = msg.get("created_at", 0)
            if ts:
                dt = datetime.fromtimestamp(ts)
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M:%S")

                if date_str != current_date:
                    current_date = date_str
                    lines.append(f"## {date_str}")
                    lines.append("")
            else:
                time_str = ""

            role = msg.get("role", "")
            content = msg.get("content", "")
            user_id = msg.get("user_id", "")
            engine = msg.get("engine", "")

            if role == "user":
                lines.append(f"### 👤 {user_id} `{time_str}`")
            else:
                engine_tag = f" ({engine})" if engine else ""
                lines.append(f"### 🤖 Assistant{engine_tag} `{time_str}`")

            lines.append("")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)

    # ---- Batch Export ----

    def export_by_user(
        self,
        messages: List[Dict],
        fmt: ExportFormat = ExportFormat.JSON,
    ) -> Dict[str, ExportResult]:
        """按用户分别导出"""
        by_user = {}
        for msg in messages:
            uid = msg.get("user_id", "unknown")
            if uid not in by_user:
                by_user[uid] = []
            by_user[uid].append(msg)

        results = {}
        for uid, msgs in by_user.items():
            results[uid] = self.export(
                messages=msgs,
                fmt=fmt,
                title=f"Conversations - {uid}",
            )
        return results

    def export_summary(self, messages: List[Dict]) -> Dict:
        """导出统计摘要"""
        if not messages:
            return {"total": 0}

        users = set()
        engines = set()
        channels = set()
        total_tokens = 0
        total_latency = 0.0
        latency_count = 0
        by_role = {"user": 0, "assistant": 0}
        by_channel = {}
        by_engine = {}
        hourly = {}

        for msg in messages:
            users.add(msg.get("user_id", ""))
            engine = msg.get("engine", "")
            channel = msg.get("channel", "")
            if engine:
                engines.add(engine)
            if channel:
                channels.add(channel)

            total_tokens += msg.get("tokens_used", 0)
            lat = msg.get("latency", 0.0)
            if lat > 0:
                total_latency += lat
                latency_count += 1

            role = msg.get("role", "other")
            by_role[role] = by_role.get(role, 0) + 1

            by_channel[channel] = by_channel.get(channel, 0) + 1
            if engine:
                by_engine[engine] = by_engine.get(engine, 0) + 1

            ts = msg.get("created_at", 0)
            if ts:
                hour = datetime.fromtimestamp(ts).strftime("%H")
                hourly[hour] = hourly.get(hour, 0) + 1

        timestamps = [m.get("created_at", 0) for m in messages if m.get("created_at")]

        return {
            "total_messages": len(messages),
            "unique_users": len(users),
            "unique_engines": list(engines),
            "unique_channels": list(channels),
            "total_tokens": total_tokens,
            "avg_latency": round(total_latency / latency_count, 3) if latency_count else 0,
            "by_role": by_role,
            "by_channel": by_channel,
            "by_engine": by_engine,
            "hourly_distribution": dict(sorted(hourly.items())),
            "date_range": {
                "start": datetime.fromtimestamp(min(timestamps)).isoformat() if timestamps else None,
                "end": datetime.fromtimestamp(max(timestamps)).isoformat() if timestamps else None,
            },
        }
