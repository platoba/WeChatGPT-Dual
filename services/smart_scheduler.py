"""
Smart Message Scheduler
根据用户历史活跃时间，智能调度消息发送时机
"""
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json


class SmartScheduler:
    """智能消息调度器"""
    
    def __init__(self, db_path: str = "bot.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                message TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_activity_patterns (
                user_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                hourly_activity TEXT NOT NULL,
                last_updated TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    
    def record_activity(self, user_id: str, platform: str):
        """记录用户活跃时间"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取当前小时
        current_hour = datetime.now().hour
        
        # 获取现有活跃模式
        cursor.execute(
            "SELECT hourly_activity FROM user_activity_patterns WHERE user_id=? AND platform=?",
            (user_id, platform)
        )
        row = cursor.fetchone()
        
        if row:
            activity = json.loads(row[0])
            activity[str(current_hour)] = activity.get(str(current_hour), 0) + 1
            cursor.execute(
                "UPDATE user_activity_patterns SET hourly_activity=?, last_updated=? WHERE user_id=? AND platform=?",
                (json.dumps(activity), datetime.now().isoformat(), user_id, platform)
            )
        else:
            activity = {str(current_hour): 1}
            cursor.execute(
                "INSERT INTO user_activity_patterns (user_id, platform, hourly_activity) VALUES (?, ?, ?)",
                (user_id, platform, json.dumps(activity))
            )
        
        conn.commit()
        conn.close()
    
    def get_best_send_time(self, user_id: str, platform: str) -> datetime:
        """获取最佳发送时间"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT hourly_activity FROM user_activity_patterns WHERE user_id=? AND platform=?",
            (user_id, platform)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            # 无历史数据，默认1小时后
            return datetime.now() + timedelta(hours=1)
        
        activity = json.loads(row[0])
        
        # 找到活跃度最高的时段
        best_hour = max(activity.items(), key=lambda x: x[1])[0]
        best_hour = int(best_hour)
        
        now = datetime.now()
        target_time = now.replace(hour=best_hour, minute=0, second=0, microsecond=0)
        
        # 如果目标时间已过，推到明天
        if target_time <= now:
            target_time += timedelta(days=1)
        
        return target_time
    
    def schedule_message(self, user_id: str, platform: str, message: str, send_time: Optional[datetime] = None):
        """调度消息"""
        if send_time is None:
            send_time = self.get_best_send_time(user_id, platform)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scheduled_messages (user_id, platform, message, scheduled_time) VALUES (?, ?, ?, ?)",
            (user_id, platform, message, send_time.isoformat())
        )
        conn.commit()
        conn.close()
        
        return send_time
    
    def get_pending_messages(self) -> List[Dict]:
        """获取待发送消息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.now().isoformat()
        cursor.execute(
            "SELECT id, user_id, platform, message FROM scheduled_messages WHERE sent=0 AND scheduled_time <= ?",
            (now,)
        )
        
        messages = []
        for row in cursor.fetchall():
            messages.append({
                "id": row[0],
                "user_id": row[1],
                "platform": row[2],
                "message": row[3]
            })
        
        conn.close()
        return messages
    
    def mark_sent(self, message_id: int):
        """标记消息已发送"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE scheduled_messages SET sent=1 WHERE id=?", (message_id,))
        conn.commit()
        conn.close()
    
    def get_user_stats(self, user_id: str, platform: str) -> Dict:
        """获取用户活跃统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT hourly_activity, last_updated FROM user_activity_patterns WHERE user_id=? AND platform=?",
            (user_id, platform)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {"error": "No activity data"}
        
        activity = json.loads(row[0])
        total_messages = sum(activity.values())
        peak_hour = max(activity.items(), key=lambda x: x[1])[0]
        
        return {
            "total_messages": total_messages,
            "peak_hour": int(peak_hour),
            "hourly_distribution": activity,
            "last_updated": row[1]
        }
