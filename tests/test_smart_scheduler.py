"""
Tests for Smart Message Scheduler
"""
import pytest
import sqlite3
import json
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from services.smart_scheduler import SmartScheduler


@pytest.fixture
def scheduler(tmp_path):
    """创建临时调度器实例"""
    db_path = tmp_path / "test_scheduler.db"
    return SmartScheduler(str(db_path))


def test_init_db(scheduler):
    """测试数据库初始化"""
    conn = sqlite3.connect(scheduler.db_path)
    cursor = conn.cursor()
    
    # 检查表是否存在
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_messages'")
    assert cursor.fetchone() is not None
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_activity_patterns'")
    assert cursor.fetchone() is not None
    
    conn.close()


def test_record_activity(scheduler):
    """测试记录用户活跃"""
    scheduler.record_activity("user123", "telegram")
    scheduler.record_activity("user123", "telegram")
    scheduler.record_activity("user123", "telegram")
    
    conn = sqlite3.connect(scheduler.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT hourly_activity FROM user_activity_patterns WHERE user_id='user123'")
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    activity = json.loads(row[0])
    current_hour = str(datetime.now().hour)
    assert activity[current_hour] == 3


def test_get_best_send_time_no_data(scheduler):
    """测试无历史数据时的最佳发送时间"""
    send_time = scheduler.get_best_send_time("new_user", "telegram")
    now = datetime.now()
    
    # 应该返回未来时间
    assert send_time > now
    # 应该在24小时内
    assert send_time < now + timedelta(hours=25)


def test_get_best_send_time_with_data(scheduler):
    """测试有历史数据时的最佳发送时间"""
    # 模拟用户在14点最活跃
    conn = sqlite3.connect(scheduler.db_path)
    cursor = conn.cursor()
    activity = {"14": 10, "15": 5, "16": 3}
    cursor.execute(
        "INSERT INTO user_activity_patterns (user_id, platform, hourly_activity) VALUES (?, ?, ?)",
        ("user456", "wechat", json.dumps(activity))
    )
    conn.commit()
    conn.close()
    
    send_time = scheduler.get_best_send_time("user456", "wechat")
    assert send_time.hour == 14


def test_schedule_message(scheduler):
    """测试调度消息"""
    target_time = datetime.now() + timedelta(hours=2)
    result_time = scheduler.schedule_message("user789", "telegram", "Hello!", target_time)
    
    assert result_time == target_time
    
    conn = sqlite3.connect(scheduler.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM scheduled_messages WHERE user_id='user789'")
    count = cursor.fetchone()[0]
    conn.close()
    
    assert count == 1


def test_get_pending_messages(scheduler):
    """测试获取待发送消息"""
    # 添加过去的消息（应该被获取）
    past_time = datetime.now() - timedelta(hours=1)
    scheduler.schedule_message("user1", "telegram", "Past message", past_time)
    
    # 添加未来的消息（不应该被获取）
    future_time = datetime.now() + timedelta(hours=1)
    scheduler.schedule_message("user2", "wechat", "Future message", future_time)
    
    pending = scheduler.get_pending_messages()
    
    assert len(pending) == 1
    assert pending[0]["user_id"] == "user1"
    assert pending[0]["message"] == "Past message"


def test_mark_sent(scheduler):
    """测试标记消息已发送"""
    past_time = datetime.now() - timedelta(hours=1)
    scheduler.schedule_message("user3", "telegram", "Test", past_time)
    
    pending = scheduler.get_pending_messages()
    assert len(pending) == 1
    
    scheduler.mark_sent(pending[0]["id"])
    
    pending_after = scheduler.get_pending_messages()
    assert len(pending_after) == 0


def test_get_user_stats(scheduler):
    """测试获取用户统计"""
    # 记录多次活跃
    for _ in range(5):
        scheduler.record_activity("user_stats", "telegram")
    
    stats = scheduler.get_user_stats("user_stats", "telegram")
    
    assert stats["total_messages"] == 5
    assert "peak_hour" in stats
    assert "hourly_distribution" in stats
    assert "last_updated" in stats


def test_get_user_stats_no_data(scheduler):
    """测试无数据用户的统计"""
    stats = scheduler.get_user_stats("nonexistent", "telegram")
    assert "error" in stats
