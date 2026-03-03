"""Tests for group_management module."""
import os
import time
import pytest
import tempfile
from services.group_management import GroupManager


@pytest.fixture
def gm():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    g = GroupManager(db_path=db_path)
    yield g
    os.unlink(db_path)


class TestGroupCRUD:
    def test_register_group(self, gm):
        group = gm.register_group("g1", "telegram", name="Test Group")
        assert group.group_id == "g1"
        assert group.platform == "telegram"
        assert group.name == "Test Group"

    def test_register_with_welcome(self, gm):
        group = gm.register_group("g1", "wechat", welcome_message="Welcome {username}!")
        assert "Welcome" in group.welcome_message

    def test_get_group(self, gm):
        gm.register_group("g1", "telegram", name="Test")
        group = gm.get_group("g1")
        assert group is not None
        assert group.name == "Test"
        assert group.platform == "telegram"

    def test_get_nonexistent(self, gm):
        assert gm.get_group("nonexistent") is None

    def test_update_group(self, gm):
        gm.register_group("g1", "telegram", name="Old Name")
        gm.register_group("g1", "telegram", name="New Name")
        group = gm.get_group("g1")
        assert group.name == "New Name"


class TestSettings:
    def test_default_settings(self, gm):
        gm.register_group("g1", "telegram")
        group = gm.get_group("g1")
        assert group.settings['max_messages_per_minute'] == 10
        assert group.settings['welcome_enabled'] is True

    def test_update_settings(self, gm):
        gm.register_group("g1", "telegram")
        gm.update_settings("g1", max_messages_per_minute=20, ai_enabled=False)
        group = gm.get_group("g1")
        assert group.settings['max_messages_per_minute'] == 20
        assert group.settings['ai_enabled'] is False

    def test_update_nonexistent(self, gm):
        assert not gm.update_settings("nonexistent", ai_enabled=False)

    def test_set_welcome(self, gm):
        gm.register_group("g1", "telegram")
        gm.set_welcome("g1", "Hi {username}, welcome!")
        group = gm.get_group("g1")
        assert "Hi {username}" in group.welcome_message

    def test_set_rules(self, gm):
        gm.register_group("g1", "telegram")
        gm.set_rules("g1", "1. Be nice\n2. No spam")
        group = gm.get_group("g1")
        assert "Be nice" in group.rules

    def test_set_ai_persona(self, gm):
        gm.register_group("g1", "telegram")
        gm.set_ai_persona("g1", "You are a helpful coding assistant")
        group = gm.get_group("g1")
        assert "coding assistant" in group.ai_persona


class TestMembers:
    def test_add_member(self, gm):
        gm.register_group("g1", "telegram")
        member = gm.add_member("g1", "u1", username="Alice")
        assert member.user_id == "u1"
        assert member.username == "Alice"
        assert member.role == "member"

    def test_add_admin(self, gm):
        gm.register_group("g1", "telegram")
        member = gm.add_member("g1", "u1", role="admin")
        assert member.role == "admin"

    def test_remove_member(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1")
        assert gm.remove_member("g1", "u1")
        group = gm.get_group("g1")
        assert group.member_count == 0

    def test_remove_nonexistent(self, gm):
        assert not gm.remove_member("g1", "nobody")

    def test_member_count(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1")
        gm.add_member("g1", "u2")
        gm.add_member("g1", "u3")
        group = gm.get_group("g1")
        assert group.member_count == 3

    def test_record_activity(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1")
        gm.record_activity("g1", "u1")
        gm.record_activity("g1", "u1")
        leaderboard = gm.get_leaderboard("g1")
        assert leaderboard[0].message_count == 2

    def test_leaderboard(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1", username="Alice")
        gm.add_member("g1", "u2", username="Bob")
        for _ in range(5):
            gm.record_activity("g1", "u1")
        for _ in range(10):
            gm.record_activity("g1", "u2")
        board = gm.get_leaderboard("g1", limit=2)
        assert board[0].username == "Bob"
        assert board[0].message_count == 10

    def test_inactive_members(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1")
        # Make u1 inactive by setting old last_active
        with gm._get_conn() as conn:
            old = time.time() - 60 * 86400  # 60 days ago
            conn.execute(
                "UPDATE group_members SET last_active = ? WHERE user_id = ?",
                (old, "u1")
            )
        inactive = gm.get_inactive_members("g1", inactive_days=30)
        assert len(inactive) == 1
        assert inactive[0].user_id == "u1"


class TestWelcome:
    def test_welcome_message(self, gm):
        gm.register_group("g1", "telegram", name="Cool Group",
                          welcome_message="Welcome {username} to {group}!")
        msg = gm.get_welcome_message("g1", username="Alice")
        assert "Welcome Alice" in msg
        assert "Cool Group" in msg

    def test_welcome_with_rules(self, gm):
        gm.register_group("g1", "telegram", name="G",
                          welcome_message="Hi!", rules="No spam")
        msg = gm.get_welcome_message("g1")
        assert "No spam" in msg

    def test_welcome_disabled(self, gm):
        gm.register_group("g1", "telegram", welcome_message="Hi!")
        gm.update_settings("g1", welcome_enabled=False)
        msg = gm.get_welcome_message("g1")
        assert msg is None

    def test_welcome_no_message(self, gm):
        gm.register_group("g1", "telegram")
        msg = gm.get_welcome_message("g1")
        assert msg is None

    def test_welcome_nonexistent_group(self, gm):
        msg = gm.get_welcome_message("nonexistent")
        assert msg is None


class TestFlood:
    def test_normal_messages_pass(self, gm):
        gm.register_group("g1", "telegram")
        result = gm.check_flood("g1", "u1")
        assert not result.is_flood
        assert result.action == 'pass'

    def test_flood_detected(self, gm):
        gm.register_group("g1", "telegram")
        gm.update_settings("g1", max_messages_per_minute=3)
        for i in range(5):
            result = gm.check_flood("g1", "u1")
        assert result.is_flood
        assert result.action in ('warn', 'mute')

    def test_flood_mute_threshold(self, gm):
        gm.register_group("g1", "telegram")
        gm.update_settings("g1", max_messages_per_minute=2)
        for i in range(10):
            result = gm.check_flood("g1", "u1")
        assert result.action == 'mute'

    def test_flood_disabled(self, gm):
        gm.register_group("g1", "telegram")
        gm.update_settings("g1", flood_protection=False)
        for i in range(20):
            result = gm.check_flood("g1", "u1")
        assert not result.is_flood

    def test_flood_per_user(self, gm):
        gm.register_group("g1", "telegram")
        gm.update_settings("g1", max_messages_per_minute=3)
        for i in range(5):
            gm.check_flood("g1", "u1")
        result_u2 = gm.check_flood("g1", "u2")
        assert not result_u2.is_flood


class TestTopics:
    def test_create_topic(self, gm):
        gm.register_group("g1", "telegram")
        topic = gm.create_topic("g1", "Discussion about Python", started_by="u1")
        assert topic.title == "Discussion about Python"
        assert topic.started_by == "u1"

    def test_list_topics(self, gm):
        gm.register_group("g1", "telegram")
        gm.create_topic("g1", "Topic A")
        gm.create_topic("g1", "Topic B")
        topics = gm.get_topics("g1")
        assert len(topics) == 2

    def test_pin_topic(self, gm):
        gm.register_group("g1", "telegram")
        topic = gm.create_topic("g1", "Important")
        gm.pin_topic(topic.topic_id, True)
        topics = gm.get_topics("g1")
        assert topics[0].is_pinned

    def test_unpin_topic(self, gm):
        gm.register_group("g1", "telegram")
        topic = gm.create_topic("g1", "Temp pin")
        gm.pin_topic(topic.topic_id, True)
        gm.pin_topic(topic.topic_id, False)
        topics = gm.get_topics("g1")
        assert not topics[0].is_pinned

    def test_pinned_first(self, gm):
        gm.register_group("g1", "telegram")
        t1 = gm.create_topic("g1", "Regular")
        t2 = gm.create_topic("g1", "Pinned")
        gm.pin_topic(t2.topic_id, True)
        topics = gm.get_topics("g1")
        assert topics[0].title == "Pinned"


class TestFAQ:
    def test_add_faq(self, gm):
        gm.register_group("g1", "telegram")
        faq_id = gm.add_faq("g1", ["pricing", "price", "cost"], "Our pricing starts at $9.99")
        assert faq_id

    def test_match_faq(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_faq("g1", ["pricing", "price"], "Starts at $9.99")
        result = gm.match_faq("g1", "What is the pricing?")
        assert result is not None
        assert "$9.99" in result

    def test_match_faq_no_match(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_faq("g1", ["pricing"], "Starts at $9.99")
        result = gm.match_faq("g1", "What is the weather?")
        assert result is None

    def test_faq_disabled(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_faq("g1", ["help"], "Contact support")
        gm.update_settings("g1", faq_enabled=False)
        result = gm.match_faq("g1", "I need help")
        assert result is None

    def test_list_faq(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_faq("g1", ["a"], "Answer A")
        gm.add_faq("g1", ["b"], "Answer B")
        faqs = gm.list_faq("g1")
        assert len(faqs) == 2

    def test_remove_faq(self, gm):
        gm.register_group("g1", "telegram")
        faq_id = gm.add_faq("g1", ["test"], "Test answer")
        assert gm.remove_faq(faq_id)
        faqs = gm.list_faq("g1")
        assert len(faqs) == 0

    def test_faq_use_count(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_faq("g1", ["help"], "Help text")
        gm.match_faq("g1", "I need help")
        gm.match_faq("g1", "help me please")
        faqs = gm.list_faq("g1")
        assert faqs[0]['use_count'] == 2


class TestAnnouncements:
    def test_schedule(self, gm):
        gm.register_group("g1", "telegram")
        aid = gm.schedule_announcement("g1", "Hello everyone!")
        assert aid > 0

    def test_pending(self, gm):
        gm.register_group("g1", "telegram")
        gm.schedule_announcement("g1", "Now!", scheduled_at=time.time() - 10)
        pending = gm.get_pending_announcements()
        assert len(pending) >= 1

    def test_future_not_pending(self, gm):
        gm.register_group("g1", "telegram")
        gm.schedule_announcement("g1", "Future", scheduled_at=time.time() + 3600)
        pending = gm.get_pending_announcements()
        assert len(pending) == 0

    def test_mark_sent(self, gm):
        gm.register_group("g1", "telegram")
        gm.schedule_announcement("g1", "One-time", scheduled_at=time.time() - 10)
        pending = gm.get_pending_announcements()
        assert len(pending) >= 1
        gm.mark_sent(pending[0]['id'])
        pending2 = gm.get_pending_announcements()
        assert len(pending2) == 0

    def test_repeat_announcement(self, gm):
        gm.register_group("g1", "telegram")
        gm.schedule_announcement("g1", "Repeat", scheduled_at=time.time() - 10,
                                  repeat_interval=3600)
        pending = gm.get_pending_announcements()
        gm.mark_sent(pending[0]['id'])
        # Should be rescheduled, not in pending yet (future)
        pending2 = gm.get_pending_announcements()
        assert len(pending2) == 0  # rescheduled to future


class TestStats:
    def test_group_stats(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1")
        gm.add_member("g1", "u2")
        gm.record_activity("g1", "u1")
        gm.create_topic("g1", "Test topic")
        gm.add_faq("g1", ["help"], "Help")
        stats = gm.get_group_stats("g1")
        assert stats['member_count'] == 2
        assert stats['total_messages'] >= 1
        assert stats['topic_count'] == 1
        assert stats['faq_count'] == 1

    def test_stats_active_today(self, gm):
        gm.register_group("g1", "telegram")
        gm.add_member("g1", "u1")
        gm.record_activity("g1", "u1")
        stats = gm.get_group_stats("g1")
        assert stats['active_today'] >= 1
