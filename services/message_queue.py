"""
异步消息队列 - 优先级队列 + 重试 + 死信队列 + 并发控制
"""

import time
import json
import heapq
import threading
import logging
import sqlite3
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, List, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """消息优先级"""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BULK = 4


class MessageStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


@dataclass(order=True)
class QueueMessage:
    """队列消息 (优先级 + 时间戳排序)"""
    priority: int
    timestamp: float = field(compare=True)
    message_id: str = field(compare=False)
    user_id: str = field(compare=False)
    channel: str = field(compare=False)
    content: str = field(compare=False)
    metadata: Dict[str, Any] = field(compare=False, default_factory=dict)
    retry_count: int = field(compare=False, default=0)
    max_retries: int = field(compare=False, default=3)
    status: str = field(compare=False, default=MessageStatus.PENDING)
    error: str = field(compare=False, default="")
    created_at: float = field(compare=False, default_factory=time.time)
    completed_at: float = field(compare=False, default=0.0)


class DeadLetterQueue:
    """死信队列 - 处理失败的消息存储"""

    def __init__(self, db_path: str = "dlq.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dead_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    content TEXT NOT NULL,
                    error TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    dead_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dlq_user ON dead_letters(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dlq_dead ON dead_letters(dead_at)"
            )

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add(self, msg: QueueMessage):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO dead_letters
                   (message_id, user_id, channel, content, error,
                    retry_count, metadata, created_at, dead_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.message_id, msg.user_id, msg.channel,
                    msg.content, msg.error, msg.retry_count,
                    json.dumps(msg.metadata), msg.created_at, time.time(),
                ),
            )
        logger.warning(
            "Message %s moved to DLQ after %d retries: %s",
            msg.message_id, msg.retry_count, msg.error,
        )

    def list_messages(
        self, limit: int = 50, user_id: Optional[str] = None
    ) -> List[Dict]:
        with self._conn() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT * FROM dead_letters WHERE user_id = ? "
                    "ORDER BY dead_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dead_letters ORDER BY dead_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def replay(self, message_id: str) -> Optional[Dict]:
        """从DLQ中取出消息准备重放"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM dead_letters WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM dead_letters WHERE message_id = ?",
                    (message_id,),
                )
                return dict(row)
        return None

    def purge(self, older_than_hours: float = 72) -> int:
        cutoff = time.time() - older_than_hours * 3600
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM dead_letters WHERE dead_at < ?", (cutoff,)
            )
            return cursor.rowcount

    def count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()
            return row[0] if row else 0


class MessageQueue:
    """
    异步消息队列

    Features:
    - 优先级排序 (CRITICAL > HIGH > NORMAL > LOW > BULK)
    - 自动重试 + 指数退避
    - 死信队列 (DLQ) 存储失败消息
    - 并发控制 (可配置worker数)
    - 背压保护 (队列容量限制)
    - 消息统计
    """

    def __init__(
        self,
        max_size: int = 10000,
        max_workers: int = 4,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        dlq_path: str = "dlq.db",
    ):
        self.max_size = max_size
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        self._queue: List[QueueMessage] = []
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._running = False
        self._workers: List[threading.Thread] = []
        self._handler: Optional[Callable] = None
        self._dlq = DeadLetterQueue(dlq_path)
        self._counter = 0

        # Stats
        self._processed = 0
        self._failed = 0
        self._retried = 0
        self._rejected = 0

    def set_handler(self, handler: Callable[[QueueMessage], Any]):
        """设置消息处理函数"""
        self._handler = handler

    def enqueue(
        self,
        user_id: str,
        content: str,
        channel: str = "telegram",
        priority: Priority = Priority.NORMAL,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """入队消息，返回message_id"""
        with self._lock:
            if len(self._queue) >= self.max_size:
                self._rejected += 1
                logger.warning("Queue full (%d), rejecting message", self.max_size)
                return None

            self._counter += 1
            msg_id = f"msg-{int(time.time()*1000)}-{self._counter}"

            msg = QueueMessage(
                priority=int(priority),
                timestamp=time.time(),
                message_id=msg_id,
                user_id=user_id,
                channel=channel,
                content=content,
                metadata=metadata or {},
            )
            heapq.heappush(self._queue, msg)
            self._not_empty.notify()

        return msg_id

    def _dequeue(self, timeout: float = 1.0) -> Optional[QueueMessage]:
        """出队消息"""
        with self._not_empty:
            while not self._queue and self._running:
                self._not_empty.wait(timeout=timeout)
                if not self._running:
                    return None
            if not self._queue:
                return None
            return heapq.heappop(self._queue)

    def _worker_loop(self, worker_id: int):
        """Worker线程主循环"""
        logger.info("Worker-%d started", worker_id)
        while self._running:
            msg = self._dequeue(timeout=1.0)
            if msg is None:
                continue

            msg.status = MessageStatus.PROCESSING
            try:
                if self._handler:
                    self._handler(msg)
                msg.status = MessageStatus.COMPLETED
                msg.completed_at = time.time()
                self._processed += 1
            except Exception as e:
                msg.error = str(e)
                msg.retry_count += 1
                logger.error(
                    "Worker-%d failed on %s (attempt %d): %s",
                    worker_id, msg.message_id, msg.retry_count, e,
                )

                if msg.retry_count < self.max_retries:
                    # 指数退避重试
                    delay = self.retry_base_delay * (2 ** (msg.retry_count - 1))
                    msg.timestamp = time.time() + delay
                    msg.status = MessageStatus.PENDING
                    with self._lock:
                        heapq.heappush(self._queue, msg)
                    self._retried += 1
                else:
                    msg.status = MessageStatus.DEAD
                    self._dlq.add(msg)
                    self._failed += 1

        logger.info("Worker-%d stopped", worker_id)

    def start(self):
        """启动队列处理"""
        if self._running:
            return
        self._running = True
        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker_loop, args=(i,), daemon=True,
                name=f"mq-worker-{i}",
            )
            t.start()
            self._workers.append(t)
        logger.info("MessageQueue started with %d workers", self.max_workers)

    def stop(self, timeout: float = 5.0):
        """停止队列处理"""
        self._running = False
        with self._not_empty:
            self._not_empty.notify_all()
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers.clear()
        logger.info("MessageQueue stopped")

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def is_running(self) -> bool:
        return self._running

    def get_stats(self) -> Dict[str, Any]:
        """获取队列统计"""
        return {
            "queue_size": self.size,
            "max_size": self.max_size,
            "workers": self.max_workers,
            "running": self._running,
            "processed": self._processed,
            "failed": self._failed,
            "retried": self._retried,
            "rejected": self._rejected,
            "dlq_size": self._dlq.count(),
            "utilization": (
                round(self.size / self.max_size * 100, 1) if self.max_size > 0 else 0
            ),
        }

    def get_dlq(self) -> DeadLetterQueue:
        return self._dlq
