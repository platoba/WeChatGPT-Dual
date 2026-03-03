"""
定时消息服务 - 延迟投递 + 周期提醒 + 定时广播 + 日程集成

支持:
- 延迟消息 (发送后N分钟/小时投递)
- 定时消息 (指定时间投递)
- 周期提醒 (每天/每周/自定义cron)
- 消息模板 (变量替换)
- 时区支持
- SQLite持久化
- 后台调度器线程
"""

import re
import time
import json
import sqlite3
import hashlib
import logging
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Tuple
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class ScheduleType(Enum):
    """调度类型"""
    ONCE = "once"          # 一次性定时
    DELAY = "delay"        # 延迟N秒
    DAILY = "daily"        # 每日
    WEEKLY = "weekly"      # 每周
    INTERVAL = "interval"  # 固定间隔
    CRON = "cron"          # Cron表达式


class MessageStatus(Enum):
    """消息状态"""
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RepeatPolicy(Enum):
    """重复策略"""
    NONE = "none"          # 不重复
    FIXED_DELAY = "fixed_delay"    # 固定延迟（上次完成后N秒）
    FIXED_RATE = "fixed_rate"      # 固定速率（每N秒）
    CRON = "cron"          # Cron表达式


@dataclass
class ScheduledMessage:
    """定时消息"""
    id: str = ""
    user_id: str = ""
    chat_id: str = ""
    platform: str = ""  # wechat / telegram
    content: str = ""
    content_type: str = "text"  # text / image / voice / template
    template_vars: Dict[str, str] = field(default_factory=dict)

    # 调度
    schedule_type: ScheduleType = ScheduleType.ONCE
    scheduled_at: float = 0.0    # Unix timestamp
    interval_seconds: int = 0
    cron_expr: str = ""
    timezone: str = "Asia/Shanghai"

    # 状态
    status: MessageStatus = MessageStatus.PENDING
    created_at: float = 0.0
    delivered_at: float = 0.0
    delivery_count: int = 0
    max_deliveries: int = 1      # 0 = 无限
    retry_count: int = 0
    max_retries: int = 3
    last_error: str = ""

    # 过期
    expires_at: float = 0.0      # 0 = 不过期
    auto_delete: bool = False

    # 元数据
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_due(self, now: Optional[float] = None) -> bool:
        """是否到期"""
        now = now or time.time()
        if self.status != MessageStatus.PENDING:
            return False
        if self.expires_at > 0 and now > self.expires_at:
            return False
        return now >= self.scheduled_at

    def is_expired(self, now: Optional[float] = None) -> bool:
        """是否过期"""
        now = now or time.time()
        return self.expires_at > 0 and now > self.expires_at

    def should_repeat(self) -> bool:
        """是否应该重复"""
        if self.schedule_type == ScheduleType.ONCE:
            return False
        if self.schedule_type == ScheduleType.DELAY:
            return False
        if self.max_deliveries > 0 and self.delivery_count >= self.max_deliveries:
            return False
        return True


class CronParser:
    """简易Cron表达式解析器 (分 时 日 月 周)"""

    @staticmethod
    def parse(expr: str) -> Dict[str, List[int]]:
        """解析cron表达式"""
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Invalid cron expression: {expr} (need 5 fields)")

        fields = ["minute", "hour", "day", "month", "weekday"]
        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        result = {}

        for i, (part, (low, high)) in enumerate(zip(parts, ranges)):
            result[fields[i]] = CronParser._parse_field(part, low, high)

        return result

    @staticmethod
    def _parse_field(field_str: str, low: int, high: int) -> List[int]:
        """解析单个字段"""
        if field_str == "*":
            return list(range(low, high + 1))

        values = set()
        for part in field_str.split(","):
            # 范围 (e.g. 1-5)
            if "-" in part and "/" not in part:
                start, end = part.split("-", 1)
                values.update(range(int(start), int(end) + 1))
            # 步进 (e.g. */5, 1-10/2)
            elif "/" in part:
                range_part, step = part.split("/", 1)
                step = int(step)
                if range_part == "*":
                    values.update(range(low, high + 1, step))
                elif "-" in range_part:
                    start, end = range_part.split("-", 1)
                    values.update(range(int(start), int(end) + 1, step))
                else:
                    values.update(range(int(range_part), high + 1, step))
            else:
                values.add(int(part))

        return sorted(v for v in values if low <= v <= high)

    @staticmethod
    def next_run(expr: str, after: Optional[float] = None, tz_offset: int = 8) -> float:
        """计算下次运行时间"""
        parsed = CronParser.parse(expr)
        tz = timezone(timedelta(hours=tz_offset))

        if after is None:
            now = datetime.now(tz)
        else:
            now = datetime.fromtimestamp(after, tz)

        # 从下一分钟开始搜索
        candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)

        for _ in range(525960):  # 最多搜索1年
            if (candidate.minute in parsed["minute"] and
                candidate.hour in parsed["hour"] and
                candidate.day in parsed["day"] and
                candidate.month in parsed["month"] and
                candidate.weekday() in parsed["weekday"]):
                return candidate.timestamp()
            candidate += timedelta(minutes=1)

        raise ValueError(f"No next run found for cron: {expr}")

    @staticmethod
    def matches(expr: str, dt: datetime) -> bool:
        """检查时间是否匹配cron表达式"""
        parsed = CronParser.parse(expr)
        return (
            dt.minute in parsed["minute"] and
            dt.hour in parsed["hour"] and
            dt.day in parsed["day"] and
            dt.month in parsed["month"] and
            dt.weekday() in parsed["weekday"]
        )


class ScheduleStore:
    """定时消息存储 (SQLite)"""

    def __init__(self, db_path: str = "scheduled_messages.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    platform TEXT DEFAULT '',
                    content TEXT NOT NULL,
                    content_type TEXT DEFAULT 'text',
                    template_vars TEXT DEFAULT '{}',
                    schedule_type TEXT NOT NULL,
                    scheduled_at REAL NOT NULL,
                    interval_seconds INTEGER DEFAULT 0,
                    cron_expr TEXT DEFAULT '',
                    timezone TEXT DEFAULT 'Asia/Shanghai',
                    status TEXT DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    delivered_at REAL DEFAULT 0,
                    delivery_count INTEGER DEFAULT 0,
                    max_deliveries INTEGER DEFAULT 1,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    last_error TEXT DEFAULT '',
                    expires_at REAL DEFAULT 0,
                    auto_delete INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_schedule_status
                ON scheduled_messages(status, scheduled_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_schedule_user
                ON scheduled_messages(user_id, status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_schedule_chat
                ON scheduled_messages(chat_id, status)
            """)

    def _msg_to_row(self, msg: ScheduledMessage) -> tuple:
        """消息对象转数据库行"""
        return (
            msg.id, msg.user_id, msg.chat_id, msg.platform,
            msg.content, msg.content_type, json.dumps(msg.template_vars),
            msg.schedule_type.value, msg.scheduled_at, msg.interval_seconds,
            msg.cron_expr, msg.timezone, msg.status.value,
            msg.created_at, msg.delivered_at, msg.delivery_count,
            msg.max_deliveries, msg.retry_count, msg.max_retries,
            msg.last_error, msg.expires_at, 1 if msg.auto_delete else 0,
            json.dumps(msg.tags), json.dumps(msg.metadata),
        )

    def _row_to_msg(self, row: sqlite3.Row) -> ScheduledMessage:
        """数据库行转消息对象"""
        return ScheduledMessage(
            id=row["id"],
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            platform=row["platform"],
            content=row["content"],
            content_type=row["content_type"],
            template_vars=json.loads(row["template_vars"]),
            schedule_type=ScheduleType(row["schedule_type"]),
            scheduled_at=row["scheduled_at"],
            interval_seconds=row["interval_seconds"],
            cron_expr=row["cron_expr"],
            timezone=row["timezone"],
            status=MessageStatus(row["status"]),
            created_at=row["created_at"],
            delivered_at=row["delivered_at"],
            delivery_count=row["delivery_count"],
            max_deliveries=row["max_deliveries"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            last_error=row["last_error"],
            expires_at=row["expires_at"],
            auto_delete=bool(row["auto_delete"]),
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
        )

    def save(self, msg: ScheduledMessage) -> str:
        """保存/更新消息"""
        if not msg.id:
            msg.id = hashlib.sha256(
                f"{msg.user_id}:{msg.chat_id}:{msg.content}:{time.time()}".encode()
            ).hexdigest()[:16]

        if not msg.created_at:
            msg.created_at = time.time()

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO scheduled_messages
                    (id, user_id, chat_id, platform, content, content_type,
                     template_vars, schedule_type, scheduled_at, interval_seconds,
                     cron_expr, timezone, status, created_at, delivered_at,
                     delivery_count, max_deliveries, retry_count, max_retries,
                     last_error, expires_at, auto_delete, tags, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._msg_to_row(msg),
                )
        return msg.id

    def get(self, msg_id: str) -> Optional[ScheduledMessage]:
        """获取消息"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM scheduled_messages WHERE id = ?", (msg_id,)
                ).fetchone()
                return self._row_to_msg(row) if row else None

    def get_due(self, now: Optional[float] = None, limit: int = 100) -> List[ScheduledMessage]:
        """获取到期消息"""
        now = now or time.time()
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT * FROM scheduled_messages
                    WHERE status = 'pending' AND scheduled_at <= ?
                    ORDER BY scheduled_at ASC LIMIT ?""",
                    (now, limit),
                ).fetchall()
                return [self._row_to_msg(r) for r in rows]

    def get_by_user(self, user_id: str, status: Optional[str] = None) -> List[ScheduledMessage]:
        """获取用户的消息"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                if status:
                    rows = conn.execute(
                        "SELECT * FROM scheduled_messages WHERE user_id = ? AND status = ? ORDER BY scheduled_at",
                        (user_id, status),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM scheduled_messages WHERE user_id = ? ORDER BY scheduled_at",
                        (user_id,),
                    ).fetchall()
                return [self._row_to_msg(r) for r in rows]

    def update_status(self, msg_id: str, status: MessageStatus,
                      error: str = "", delivered_at: float = 0.0):
        """更新消息状态"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                if status == MessageStatus.DELIVERED:
                    conn.execute(
                        """UPDATE scheduled_messages
                        SET status = ?, delivered_at = ?, delivery_count = delivery_count + 1, last_error = ''
                        WHERE id = ?""",
                        (status.value, delivered_at or time.time(), msg_id),
                    )
                elif status == MessageStatus.FAILED:
                    conn.execute(
                        """UPDATE scheduled_messages
                        SET status = ?, retry_count = retry_count + 1, last_error = ?
                        WHERE id = ?""",
                        (status.value, error, msg_id),
                    )
                else:
                    conn.execute(
                        "UPDATE scheduled_messages SET status = ? WHERE id = ?",
                        (status.value, msg_id),
                    )

    def reschedule(self, msg_id: str, next_at: float):
        """重新调度 (用于重复消息)"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE scheduled_messages SET scheduled_at = ?, status = 'pending' WHERE id = ?",
                    (next_at, msg_id),
                )

    def cancel(self, msg_id: str) -> bool:
        """取消消息"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "UPDATE scheduled_messages SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
                    (msg_id,),
                )
                return cursor.rowcount > 0

    def delete(self, msg_id: str) -> bool:
        """删除消息"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM scheduled_messages WHERE id = ?", (msg_id,)
                )
                return cursor.rowcount > 0

    def cleanup_expired(self, now: Optional[float] = None) -> int:
        """清理过期消息"""
        now = now or time.time()
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                # 标记过期
                conn.execute(
                    """UPDATE scheduled_messages SET status = 'expired'
                    WHERE status = 'pending' AND expires_at > 0 AND expires_at < ?""",
                    (now,),
                )
                # 删除 auto_delete 的
                cursor = conn.execute(
                    """DELETE FROM scheduled_messages
                    WHERE auto_delete = 1 AND status IN ('delivered', 'expired', 'cancelled')""",
                )
                return cursor.rowcount

    def stats(self) -> Dict[str, Any]:
        """统计"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM scheduled_messages").fetchone()[0]
                pending = conn.execute(
                    "SELECT COUNT(*) FROM scheduled_messages WHERE status = 'pending'"
                ).fetchone()[0]
                delivered = conn.execute(
                    "SELECT COUNT(*) FROM scheduled_messages WHERE status = 'delivered'"
                ).fetchone()[0]
                failed = conn.execute(
                    "SELECT COUNT(*) FROM scheduled_messages WHERE status = 'failed'"
                ).fetchone()[0]
                cancelled = conn.execute(
                    "SELECT COUNT(*) FROM scheduled_messages WHERE status = 'cancelled'"
                ).fetchone()[0]

        return {
            "total": total,
            "pending": pending,
            "delivered": delivered,
            "failed": failed,
            "cancelled": cancelled,
        }


class ScheduledMessageService:
    """
    定时消息服务

    功能:
    - 创建定时/延迟/周期消息
    - 后台调度器检查到期消息
    - 投递回调 (通过callback发送)
    - 重试机制
    - 模板变量替换
    """

    def __init__(
        self,
        store: Optional[ScheduleStore] = None,
        db_path: str = "scheduled_messages.db",
        check_interval: int = 10,
        delivery_callback: Optional[Callable] = None,
    ):
        self.store = store or ScheduleStore(db_path)
        self.check_interval = check_interval
        self.delivery_callback = delivery_callback

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 统计
        self._stats = {
            "created": 0,
            "delivered": 0,
            "failed": 0,
            "cancelled": 0,
            "retried": 0,
        }
        self._lock = threading.Lock()

    def schedule_once(
        self, user_id: str, chat_id: str, content: str,
        at: float, platform: str = "", **kwargs
    ) -> str:
        """创建一次性定时消息"""
        msg = ScheduledMessage(
            user_id=user_id,
            chat_id=chat_id,
            platform=platform,
            content=content,
            schedule_type=ScheduleType.ONCE,
            scheduled_at=at,
            **kwargs,
        )
        msg_id = self.store.save(msg)
        with self._lock:
            self._stats["created"] += 1
        logger.info(f"Scheduled once: {msg_id} at {at}")
        return msg_id

    def schedule_delay(
        self, user_id: str, chat_id: str, content: str,
        delay_seconds: int, platform: str = "", **kwargs
    ) -> str:
        """创建延迟消息"""
        at = time.time() + delay_seconds
        msg = ScheduledMessage(
            user_id=user_id,
            chat_id=chat_id,
            platform=platform,
            content=content,
            schedule_type=ScheduleType.DELAY,
            scheduled_at=at,
            **kwargs,
        )
        msg_id = self.store.save(msg)
        with self._lock:
            self._stats["created"] += 1
        logger.info(f"Scheduled delay: {msg_id} in {delay_seconds}s")
        return msg_id

    def schedule_daily(
        self, user_id: str, chat_id: str, content: str,
        hour: int, minute: int = 0, platform: str = "",
        max_deliveries: int = 0, **kwargs
    ) -> str:
        """创建每日定时消息"""
        # 计算下次运行时间
        now = datetime.now()
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)

        msg = ScheduledMessage(
            user_id=user_id,
            chat_id=chat_id,
            platform=platform,
            content=content,
            schedule_type=ScheduleType.DAILY,
            scheduled_at=next_run.timestamp(),
            interval_seconds=86400,
            max_deliveries=max_deliveries,
            **kwargs,
        )
        msg_id = self.store.save(msg)
        with self._lock:
            self._stats["created"] += 1
        logger.info(f"Scheduled daily: {msg_id} at {hour:02d}:{minute:02d}")
        return msg_id

    def schedule_interval(
        self, user_id: str, chat_id: str, content: str,
        interval_seconds: int, platform: str = "",
        max_deliveries: int = 0, **kwargs
    ) -> str:
        """创建固定间隔消息"""
        msg = ScheduledMessage(
            user_id=user_id,
            chat_id=chat_id,
            platform=platform,
            content=content,
            schedule_type=ScheduleType.INTERVAL,
            scheduled_at=time.time() + interval_seconds,
            interval_seconds=interval_seconds,
            max_deliveries=max_deliveries,
            **kwargs,
        )
        msg_id = self.store.save(msg)
        with self._lock:
            self._stats["created"] += 1
        logger.info(f"Scheduled interval: {msg_id} every {interval_seconds}s")
        return msg_id

    def schedule_cron(
        self, user_id: str, chat_id: str, content: str,
        cron_expr: str, platform: str = "",
        max_deliveries: int = 0, tz_offset: int = 8, **kwargs
    ) -> str:
        """创建Cron调度消息"""
        next_at = CronParser.next_run(cron_expr, tz_offset=tz_offset)
        msg = ScheduledMessage(
            user_id=user_id,
            chat_id=chat_id,
            platform=platform,
            content=content,
            schedule_type=ScheduleType.CRON,
            scheduled_at=next_at,
            cron_expr=cron_expr,
            max_deliveries=max_deliveries,
            **kwargs,
        )
        msg_id = self.store.save(msg)
        with self._lock:
            self._stats["created"] += 1
        logger.info(f"Scheduled cron: {msg_id} expr={cron_expr}")
        return msg_id

    def cancel(self, msg_id: str) -> bool:
        """取消消息"""
        result = self.store.cancel(msg_id)
        if result:
            with self._lock:
                self._stats["cancelled"] += 1
        return result

    def get_user_messages(self, user_id: str, status: Optional[str] = None) -> List[ScheduledMessage]:
        """获取用户消息列表"""
        return self.store.get_by_user(user_id, status)

    def render_template(self, content: str, variables: Dict[str, str]) -> str:
        """渲染消息模板"""
        result = content
        for key, value in variables.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        # 清理未替换的变量
        result = re.sub(r"\{\{[^}]+\}\}", "", result)
        return result

    def process_due_messages(self) -> List[Tuple[str, bool]]:
        """处理到期消息"""
        results = []
        due_messages = self.store.get_due()

        for msg in due_messages:
            # 检查过期
            if msg.is_expired():
                self.store.update_status(msg.id, MessageStatus.EXPIRED)
                continue

            # 渲染模板
            content = msg.content
            if msg.template_vars:
                content = self.render_template(content, msg.template_vars)

            # 投递
            success = False
            error = ""
            try:
                if self.delivery_callback:
                    self.delivery_callback(msg, content)
                success = True
            except Exception as e:
                error = str(e)
                logger.error(f"Delivery failed for {msg.id}: {e}")

            if success:
                self.store.update_status(msg.id, MessageStatus.DELIVERED)
                with self._lock:
                    self._stats["delivered"] += 1

                # 重复调度
                if msg.should_repeat():
                    next_at = self._calc_next_run(msg)
                    if next_at:
                        self.store.reschedule(msg.id, next_at)
            else:
                # 重试
                if msg.retry_count < msg.max_retries:
                    # 指数退避重试
                    retry_delay = 60 * (2 ** msg.retry_count)
                    self.store.update_status(msg.id, MessageStatus.FAILED, error)
                    self.store.reschedule(msg.id, time.time() + retry_delay)
                    with self._lock:
                        self._stats["retried"] += 1
                else:
                    self.store.update_status(msg.id, MessageStatus.FAILED, error)
                    with self._lock:
                        self._stats["failed"] += 1

            results.append((msg.id, success))

        return results

    def _calc_next_run(self, msg: ScheduledMessage) -> Optional[float]:
        """计算下次运行时间"""
        if msg.schedule_type == ScheduleType.DAILY:
            return msg.scheduled_at + 86400
        elif msg.schedule_type == ScheduleType.WEEKLY:
            return msg.scheduled_at + 604800
        elif msg.schedule_type == ScheduleType.INTERVAL:
            return time.time() + msg.interval_seconds
        elif msg.schedule_type == ScheduleType.CRON and msg.cron_expr:
            try:
                return CronParser.next_run(msg.cron_expr, after=time.time())
            except Exception:
                return None
        return None

    def start(self):
        """启动后台调度器"""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        """停止调度器"""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Scheduler stopped")

    def _scheduler_loop(self):
        """调度器循环"""
        while self._running:
            try:
                self.process_due_messages()
                self.store.cleanup_expired()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")

            self._stop_event.wait(self.check_interval)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计"""
        with self._lock:
            stats = dict(self._stats)
        stats["store"] = self.store.stats()
        stats["scheduler_running"] = self._running
        return stats
