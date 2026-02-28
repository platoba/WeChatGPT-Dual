"""
Backup Service - 对话备份与恢复

Features:
- Export conversations to JSON, Markdown, HTML
- Per-user or full backup
- Date range filtering
- Import from backup (restore)
- Backup manifest with checksums
"""

import os
import json
import time
import sqlite3
import hashlib
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BackupManifest:
    """Backup metadata"""
    backup_id: str
    created_at: str
    format: str
    total_messages: int
    total_users: int
    date_range: Dict
    checksum: str
    file_path: str
    size_bytes: int

    def to_dict(self) -> Dict:
        return asdict(self)


class BackupService:
    """
    Conversation backup and restore service.

    Usage:
        backup = BackupService("wechatgpt.db")
        manifest = backup.export_json("backups/", user_id="user1")
        backup.restore_json("backups/backup_xxx.json")
    """

    def __init__(self, db_path: str = "wechatgpt.db"):
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _query_messages(
        self,
        conn,
        user_id: Optional[str] = None,
        channel: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> List[Dict]:
        """Query messages with filters"""
        query = "SELECT * FROM messages WHERE 1=1"
        params: list = []

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if channel:
            query += " AND channel = ?"
            params.append(channel)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        if until:
            query += " AND created_at <= ?"
            params.append(until)

        query += " ORDER BY created_at ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def _make_backup_id(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _checksum(self, data: str) -> str:
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]

    def export_json(
        self,
        output_dir: str = "backups",
        user_id: Optional[str] = None,
        channel: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> BackupManifest:
        """
        Export conversations to JSON.

        Args:
            output_dir: Directory to save backup
            user_id: Filter by user
            channel: Filter by channel
            since: Start timestamp
            until: End timestamp

        Returns:
            BackupManifest with backup metadata
        """
        os.makedirs(output_dir, exist_ok=True)
        backup_id = self._make_backup_id()

        with self._conn() as conn:
            messages = self._query_messages(conn, user_id, channel, since, until)

        # Compute stats
        users = set(m["user_id"] for m in messages)
        timestamps = [m["created_at"] for m in messages] if messages else [0]

        data = {
            "backup_id": backup_id,
            "created_at": datetime.now().isoformat(),
            "total_messages": len(messages),
            "filters": {
                "user_id": user_id,
                "channel": channel,
                "since": since,
                "until": until,
            },
            "messages": messages,
        }

        content = json.dumps(data, ensure_ascii=False, indent=2)
        file_path = os.path.join(output_dir, f"backup_{backup_id}.json")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return BackupManifest(
            backup_id=backup_id,
            created_at=data["created_at"],
            format="json",
            total_messages=len(messages),
            total_users=len(users),
            date_range={
                "earliest": min(timestamps),
                "latest": max(timestamps),
            },
            checksum=self._checksum(content),
            file_path=file_path,
            size_bytes=len(content.encode("utf-8")),
        )

    def export_markdown(
        self,
        output_dir: str = "backups",
        user_id: Optional[str] = None,
        channel: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> BackupManifest:
        """Export conversations to Markdown"""
        os.makedirs(output_dir, exist_ok=True)
        backup_id = self._make_backup_id()

        with self._conn() as conn:
            messages = self._query_messages(conn, user_id, channel, since, until)

        lines = [
            f"# Conversation Backup — {backup_id}",
            f"",
            f"Generated: {datetime.now().isoformat()}",
            f"Total messages: {len(messages)}",
            f"",
            f"---",
            f"",
        ]

        current_date = ""
        for msg in messages:
            ts = datetime.fromtimestamp(msg["created_at"])
            msg_date = ts.strftime("%Y-%m-%d")
            if msg_date != current_date:
                lines.append(f"## {msg_date}")
                lines.append("")
                current_date = msg_date

            role_emoji = "👤" if msg["role"] == "user" else "🤖"
            time_str = ts.strftime("%H:%M:%S")
            lines.append(f"**{role_emoji} {msg['user_id']}** [{time_str}] [{msg.get('channel', '')}]")
            lines.append("")
            lines.append(msg["content"])
            lines.append("")

        content = "\n".join(lines)
        file_path = os.path.join(output_dir, f"backup_{backup_id}.md")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        users = set(m["user_id"] for m in messages)
        timestamps = [m["created_at"] for m in messages] if messages else [0]

        return BackupManifest(
            backup_id=backup_id,
            created_at=datetime.now().isoformat(),
            format="markdown",
            total_messages=len(messages),
            total_users=len(users),
            date_range={"earliest": min(timestamps), "latest": max(timestamps)},
            checksum=self._checksum(content),
            file_path=file_path,
            size_bytes=len(content.encode("utf-8")),
        )

    def export_html(
        self,
        output_dir: str = "backups",
        user_id: Optional[str] = None,
        channel: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> BackupManifest:
        """Export conversations to HTML"""
        os.makedirs(output_dir, exist_ok=True)
        backup_id = self._make_backup_id()

        with self._conn() as conn:
            messages = self._query_messages(conn, user_id, channel, since, until)

        html_parts = [
            "<!DOCTYPE html>",
            "<html><head><meta charset='utf-8'>",
            f"<title>Backup {backup_id}</title>",
            "<style>",
            "body{font-family:system-ui;max-width:800px;margin:auto;padding:20px;background:#f5f5f5}",
            ".msg{margin:10px 0;padding:12px;border-radius:8px;background:white;box-shadow:0 1px 3px rgba(0,0,0,0.1)}",
            ".msg.user{border-left:4px solid #007bff}",
            ".msg.assistant{border-left:4px solid #28a745}",
            ".meta{font-size:12px;color:#666;margin-bottom:6px}",
            ".content{white-space:pre-wrap}",
            "h1{color:#333}h2{color:#555;border-bottom:1px solid #ddd;padding-bottom:5px}",
            "</style></head><body>",
            f"<h1>💬 Conversation Backup</h1>",
            f"<p>ID: {backup_id} | Messages: {len(messages)} | Generated: {datetime.now().isoformat()}</p>",
        ]

        current_date = ""
        for msg in messages:
            ts = datetime.fromtimestamp(msg["created_at"])
            msg_date = ts.strftime("%Y-%m-%d")
            if msg_date != current_date:
                html_parts.append(f"<h2>{msg_date}</h2>")
                current_date = msg_date

            role = msg["role"]
            time_str = ts.strftime("%H:%M:%S")
            escaped_content = (
                msg["content"]
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

            html_parts.append(f'<div class="msg {role}">')
            html_parts.append(
                f'<div class="meta">{msg["user_id"]} · {time_str} · {msg.get("channel", "")}</div>'
            )
            html_parts.append(f'<div class="content">{escaped_content}</div>')
            html_parts.append("</div>")

        html_parts.append("</body></html>")
        content = "\n".join(html_parts)
        file_path = os.path.join(output_dir, f"backup_{backup_id}.html")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        users = set(m["user_id"] for m in messages)
        timestamps = [m["created_at"] for m in messages] if messages else [0]

        return BackupManifest(
            backup_id=backup_id,
            created_at=datetime.now().isoformat(),
            format="html",
            total_messages=len(messages),
            total_users=len(users),
            date_range={"earliest": min(timestamps), "latest": max(timestamps)},
            checksum=self._checksum(content),
            file_path=file_path,
            size_bytes=len(content.encode("utf-8")),
        )

    def restore_json(self, file_path: str, overwrite: bool = False) -> Dict:
        """
        Restore conversations from JSON backup.

        Args:
            file_path: Path to backup JSON file
            overwrite: If True, clear existing messages first

        Returns:
            Dict with restore stats
        """
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        messages = data.get("messages", [])
        if not messages:
            return {"restored": 0, "skipped": 0}

        restored = 0
        skipped = 0

        with self._conn() as conn:
            if overwrite:
                conn.execute("DELETE FROM messages")

            for msg in messages:
                try:
                    conn.execute(
                        """INSERT INTO messages
                           (user_id, role, content, channel, engine, tokens_used, latency, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            msg["user_id"],
                            msg["role"],
                            msg["content"],
                            msg.get("channel", "telegram"),
                            msg.get("engine", ""),
                            msg.get("tokens_used", 0),
                            msg.get("latency", 0.0),
                            msg["created_at"],
                        ),
                    )
                    restored += 1
                except sqlite3.IntegrityError:
                    skipped += 1

        return {
            "restored": restored,
            "skipped": skipped,
            "backup_id": data.get("backup_id", ""),
        }

    def list_backups(self, backup_dir: str = "backups") -> List[Dict]:
        """List available backups in directory"""
        if not os.path.isdir(backup_dir):
            return []

        backups = []
        for f in sorted(os.listdir(backup_dir)):
            if f.startswith("backup_") and f.endswith((".json", ".md", ".html")):
                fpath = os.path.join(backup_dir, f)
                stat = os.stat(fpath)
                fmt = f.rsplit(".", 1)[-1]
                backups.append({
                    "filename": f,
                    "path": fpath,
                    "format": fmt,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
        return backups
