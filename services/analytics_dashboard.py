"""
高级分析仪表盘 - 用户行为分析 + 互动模式 + 趋势洞察 + 报告导出
"""

import time
import json
import csv
import io
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict, Counter
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class HourlyStats:
    """每小时统计"""
    hour: int
    messages: int = 0
    tokens: int = 0
    avg_latency: float = 0.0
    unique_users: int = 0


@dataclass
class DailyReport:
    """每日报告"""
    date: str
    total_messages: int = 0
    total_tokens: int = 0
    unique_users: int = 0
    new_users: int = 0
    avg_latency: float = 0.0
    error_count: int = 0
    top_users: List[Dict] = field(default_factory=list)
    hourly: List[HourlyStats] = field(default_factory=list)
    channel_breakdown: Dict[str, int] = field(default_factory=dict)
    engine_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass
class UserProfile:
    """用户画像"""
    user_id: str
    total_messages: int = 0
    total_tokens: int = 0
    avg_message_length: float = 0.0
    avg_response_latency: float = 0.0
    active_hours: List[int] = field(default_factory=list)
    preferred_channel: str = ""
    first_seen: float = 0.0
    last_active: float = 0.0
    session_count: int = 0
    avg_session_length: float = 0.0
    engagement_score: float = 0.0


class MetricsCollector:
    """指标收集器 - 实时收集用户交互指标"""

    def __init__(self, max_events: int = 100000):
        self.max_events = max_events
        self._events: List[Dict] = []
        self._user_first_seen: Dict[str, float] = {}
        self._daily_users: Dict[str, set] = defaultdict(set)
        self._hourly_counts: Dict[str, Dict[int, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def record(
        self,
        user_id: str,
        channel: str = "telegram",
        engine: str = "",
        tokens: int = 0,
        latency: float = 0.0,
        message_length: int = 0,
        is_error: bool = False,
    ):
        """记录一次交互事件"""
        now = time.time()
        dt = datetime.fromtimestamp(now)
        date_str = dt.strftime("%Y-%m-%d")
        hour = dt.hour

        event = {
            "user_id": user_id,
            "channel": channel,
            "engine": engine,
            "tokens": tokens,
            "latency": latency,
            "message_length": message_length,
            "is_error": is_error,
            "timestamp": now,
            "date": date_str,
            "hour": hour,
        }

        self._events.append(event)
        if len(self._events) > self.max_events:
            self._events = self._events[-self.max_events:]

        # 追踪首次出现
        if user_id not in self._user_first_seen:
            self._user_first_seen[user_id] = now

        # 每日活跃用户
        self._daily_users[date_str].add(user_id)

        # 每小时计数
        self._hourly_counts[date_str][hour] += 1

    def get_events(
        self,
        since: Optional[float] = None,
        user_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict]:
        """获取事件列表"""
        events = self._events
        if since:
            events = [e for e in events if e["timestamp"] >= since]
        if user_id:
            events = [e for e in events if e["user_id"] == user_id]
        return events[-limit:]


class AnalyticsDashboard:
    """
    分析仪表盘

    Features:
    - 实时指标收集
    - 每日/每小时报告
    - 用户画像分析
    - 互动热力图 (小时维度)
    - 引擎/渠道使用分布
    - 用户留存计算
    - CSV/JSON/Text 报告导出
    """

    def __init__(self, collector: Optional[MetricsCollector] = None):
        self.collector = collector or MetricsCollector()

    def record_interaction(self, **kwargs):
        """记录交互 (代理到collector)"""
        self.collector.record(**kwargs)

    def daily_report(self, date: Optional[str] = None) -> DailyReport:
        """生成每日报告"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        events = [e for e in self.collector._events if e["date"] == date]

        if not events:
            return DailyReport(date=date)

        users = set(e["user_id"] for e in events)
        tokens = sum(e["tokens"] for e in events)
        latencies = [e["latency"] for e in events if e["latency"] > 0]
        errors = sum(1 for e in events if e["is_error"])

        # 新用户
        new_users = 0
        for uid in users:
            first = self.collector._user_first_seen.get(uid, 0)
            if first > 0:
                first_date = datetime.fromtimestamp(first).strftime("%Y-%m-%d")
                if first_date == date:
                    new_users += 1

        # Top用户
        user_counts = Counter(e["user_id"] for e in events)
        top_users = [
            {"user_id": uid, "messages": count}
            for uid, count in user_counts.most_common(10)
        ]

        # 每小时
        hourly = []
        for h in range(24):
            h_events = [e for e in events if e["hour"] == h]
            if h_events:
                h_latencies = [e["latency"] for e in h_events if e["latency"] > 0]
                hourly.append(HourlyStats(
                    hour=h,
                    messages=len(h_events),
                    tokens=sum(e["tokens"] for e in h_events),
                    avg_latency=sum(h_latencies) / len(h_latencies) if h_latencies else 0,
                    unique_users=len(set(e["user_id"] for e in h_events)),
                ))

        # 渠道/引擎分布
        channel_counts = Counter(e["channel"] for e in events)
        engine_counts = Counter(e["engine"] for e in events if e["engine"])

        return DailyReport(
            date=date,
            total_messages=len(events),
            total_tokens=tokens,
            unique_users=len(users),
            new_users=new_users,
            avg_latency=sum(latencies) / len(latencies) if latencies else 0,
            error_count=errors,
            top_users=top_users,
            hourly=hourly,
            channel_breakdown=dict(channel_counts),
            engine_breakdown=dict(engine_counts),
        )

    def user_profile(self, user_id: str) -> UserProfile:
        """生成用户画像"""
        events = [
            e for e in self.collector._events if e["user_id"] == user_id
        ]

        if not events:
            return UserProfile(user_id=user_id)

        tokens = sum(e["tokens"] for e in events)
        latencies = [e["latency"] for e in events if e["latency"] > 0]
        msg_lengths = [e["message_length"] for e in events if e["message_length"] > 0]
        hours = [e["hour"] for e in events]
        channels = Counter(e["channel"] for e in events)

        # 活跃小时 (top 5)
        hour_counts = Counter(hours)
        active_hours = [h for h, _ in hour_counts.most_common(5)]

        # 会话分割 (30分钟间隔)
        timestamps = sorted(e["timestamp"] for e in events)
        sessions = 1
        session_lengths = []
        session_start = timestamps[0]
        for i in range(1, len(timestamps)):
            if timestamps[i] - timestamps[i-1] > 1800:
                sessions += 1
                session_lengths.append(timestamps[i-1] - session_start)
                session_start = timestamps[i]
        session_lengths.append(timestamps[-1] - session_start)

        # 互动评分 (0-100)
        recency = min(1.0, 1.0 / max(1, (time.time() - timestamps[-1]) / 86400))
        frequency = min(1.0, len(events) / 100)
        consistency = min(1.0, sessions / 10)
        engagement = round((recency * 30 + frequency * 40 + consistency * 30), 1)

        return UserProfile(
            user_id=user_id,
            total_messages=len(events),
            total_tokens=tokens,
            avg_message_length=(
                sum(msg_lengths) / len(msg_lengths) if msg_lengths else 0
            ),
            avg_response_latency=(
                sum(latencies) / len(latencies) if latencies else 0
            ),
            active_hours=active_hours,
            preferred_channel=channels.most_common(1)[0][0] if channels else "",
            first_seen=self.collector._user_first_seen.get(user_id, 0),
            last_active=timestamps[-1],
            session_count=sessions,
            avg_session_length=(
                sum(session_lengths) / len(session_lengths) if session_lengths else 0
            ),
            engagement_score=engagement,
        )

    def heatmap(self, days: int = 7) -> Dict[str, List[int]]:
        """
        生成小时×天互动热力图

        Returns:
            {"Mon": [count_h0, count_h1, ..., count_h23], ...}
        """
        cutoff = time.time() - days * 86400
        events = [e for e in self.collector._events if e["timestamp"] >= cutoff]

        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        grid: Dict[str, List[int]] = {d: [0] * 24 for d in weekdays}

        for e in events:
            dt = datetime.fromtimestamp(e["timestamp"])
            day_name = weekdays[dt.weekday()]
            grid[day_name][dt.hour] += 1

        return grid

    def retention(self, window_days: int = 7) -> Dict[str, Any]:
        """
        计算用户留存率

        Returns:
            {"day_1": 0.85, "day_7": 0.42, ...}
        """
        now = time.time()
        base_cutoff = now - window_days * 86400

        # 基准期用户
        base_users = set()
        for uid, first in self.collector._user_first_seen.items():
            if first < base_cutoff:
                base_users.add(uid)

        if not base_users:
            return {"base_users": 0, "retention": {}}

        retention = {}
        for d in [1, 3, 7]:
            if d > window_days:
                continue
            day_cutoff = now - d * 86400
            active = set()
            for e in self.collector._events:
                if e["timestamp"] >= day_cutoff and e["user_id"] in base_users:
                    active.add(e["user_id"])
            retention[f"day_{d}"] = round(len(active) / len(base_users) * 100, 1)

        return {
            "base_users": len(base_users),
            "window_days": window_days,
            "retention": retention,
        }

    def overview(self) -> Dict[str, Any]:
        """仪表盘概览"""
        events = self.collector._events
        if not events:
            return {"status": "no_data"}

        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        today_events = [e for e in events if e["date"] == today]
        last_24h = [e for e in events if e["timestamp"] >= now - 86400]

        return {
            "total_events": len(events),
            "total_users": len(self.collector._user_first_seen),
            "today": {
                "messages": len(today_events),
                "unique_users": len(set(e["user_id"] for e in today_events)),
                "tokens": sum(e["tokens"] for e in today_events),
                "errors": sum(1 for e in today_events if e["is_error"]),
            },
            "last_24h": {
                "messages": len(last_24h),
                "unique_users": len(set(e["user_id"] for e in last_24h)),
            },
            "channels": dict(Counter(e["channel"] for e in events)),
            "engines": dict(Counter(e["engine"] for e in events if e["engine"])),
        }

    # ─── Export ───────────────────────────────────────────

    def export_report(
        self, date: Optional[str] = None, fmt: str = "text"
    ) -> str:
        """
        导出报告

        Args:
            date: 日期 (None=今天)
            fmt: 格式 (text/json/csv)
        """
        report = self.daily_report(date)

        if fmt == "json":
            return self._export_json(report)
        elif fmt == "csv":
            return self._export_csv(report)
        else:
            return self._export_text(report)

    def _export_text(self, r: DailyReport) -> str:
        lines = [
            f"📊 Daily Report — {r.date}",
            "=" * 40,
            f"📨 Messages: {r.total_messages}",
            f"👥 Unique Users: {r.unique_users} (New: {r.new_users})",
            f"🪙 Tokens: {r.total_tokens:,}",
            f"⏱️ Avg Latency: {r.avg_latency:.2f}s",
            f"❌ Errors: {r.error_count}",
            "",
            "📈 Top Users:",
        ]
        for u in r.top_users[:5]:
            lines.append(f"  • {u['user_id']}: {u['messages']} msgs")

        if r.channel_breakdown:
            lines.append("\n🔗 Channels:")
            for ch, count in sorted(
                r.channel_breakdown.items(), key=lambda x: -x[1]
            ):
                lines.append(f"  • {ch}: {count}")

        if r.engine_breakdown:
            lines.append("\n🤖 Engines:")
            for eng, count in sorted(
                r.engine_breakdown.items(), key=lambda x: -x[1]
            ):
                lines.append(f"  • {eng}: {count}")

        if r.hourly:
            lines.append("\n⏰ Peak Hours:")
            sorted_h = sorted(r.hourly, key=lambda h: -h.messages)[:5]
            for h in sorted_h:
                bar = "█" * min(20, h.messages)
                lines.append(f"  {h.hour:02d}:00  {bar} {h.messages}")

        return "\n".join(lines)

    def _export_json(self, r: DailyReport) -> str:
        data = {
            "date": r.date,
            "total_messages": r.total_messages,
            "total_tokens": r.total_tokens,
            "unique_users": r.unique_users,
            "new_users": r.new_users,
            "avg_latency": round(r.avg_latency, 3),
            "error_count": r.error_count,
            "top_users": r.top_users,
            "channels": r.channel_breakdown,
            "engines": r.engine_breakdown,
            "hourly": [
                {"hour": h.hour, "messages": h.messages, "tokens": h.tokens}
                for h in r.hourly
            ],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _export_csv(self, r: DailyReport) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["hour", "messages", "tokens", "avg_latency", "unique_users"])
        for h in r.hourly:
            writer.writerow([
                f"{h.hour:02d}:00", h.messages, h.tokens,
                round(h.avg_latency, 3), h.unique_users,
            ])
        return output.getvalue()

    def get_stats(self) -> Dict:
        return {
            "total_events": len(self.collector._events),
            "total_users": len(self.collector._user_first_seen),
            "days_tracked": len(self.collector._daily_users),
        }
