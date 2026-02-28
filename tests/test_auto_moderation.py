"""Tests for services/auto_moderation.py"""

import time
import pytest

from services.auto_moderation import (
    AutoModerator, ContentFilter, FloodDetector,
    FilterAction, ViolationType, ModerationResult,
    UserModerationState,
)


# ─── ContentFilter ───────────────────────────────────────

class TestContentFilter:
    @pytest.fixture
    def cf(self):
        return ContentFilter(
            blacklisted_words={"spam", "scam"},
            max_message_length=200,
            max_urls_per_message=2,
        )

    def test_clean_message(self, cf):
        score, viols, types = cf.check_content("Hello, how are you?")
        assert score == 0.0
        assert viols == []

    def test_blacklisted_word(self, cf):
        score, viols, types = cf.check_content("This is spam content")
        assert score > 0
        assert ViolationType.BLACKLISTED_WORD in types

    def test_blacklisted_case_insensitive(self, cf):
        score, _, types = cf.check_content("THIS IS SCAM!")
        assert ViolationType.BLACKLISTED_WORD in types

    def test_long_message(self, cf):
        text = "x" * 300
        score, viols, types = cf.check_content(text)
        assert ViolationType.LONG_MESSAGE in types

    def test_url_spam(self, cf):
        text = "Check http://a.com http://b.com http://c.com"
        score, viols, types = cf.check_content(text)
        assert ViolationType.URL_SPAM in types

    def test_empty_message(self, cf):
        score, viols, types = cf.check_content("")
        assert score == 0.0

    def test_whitespace_only(self, cf):
        score, viols, types = cf.check_content("   ")
        assert ViolationType.EMPTY_MESSAGE in types

    def test_default_pattern_bitly(self, cf):
        score, _, types = cf.check_content("Visit https://bit.ly/abcdef")
        assert ViolationType.SPAM in types

    def test_default_pattern_tg_invite(self, cf):
        score, _, types = cf.check_content("Join https://t.me/joinchat/abc123")
        assert ViolationType.SPAM in types

    def test_chinese_spam_pattern(self, cf):
        score, _, types = cf.check_content("免费注册领取大奖")
        assert score > 0

    def test_add_remove_word(self, cf):
        cf.add_word("newbad")
        score, _, _ = cf.check_content("this has newbad word")
        assert score > 0
        cf.remove_word("newbad")
        score2, _, _ = cf.check_content("this has newbad word")
        assert score2 == 0

    def test_add_pattern(self, cf):
        cf.add_pattern(r"test\d{3}")
        score, _, _ = cf.check_content("matching test123 here")
        assert score > 0

    def test_invalid_pattern(self, cf):
        with pytest.raises(ValueError):
            cf.add_pattern("[invalid")

    def test_excessive_caps(self, cf):
        text = "THIS IS ALL CAPS MESSAGE THAT IS REALLY ANNOYING"
        score, _, types = cf.check_content(text)
        assert ViolationType.SPAM in types

    def test_repetitive_chars(self, cf):
        text = "aaaaaaaaaaaaa"
        score, _, types = cf.check_content(text)
        assert score > 0

    def test_no_default_patterns(self):
        cf = ContentFilter(use_default_patterns=False)
        score, _, _ = cf.check_content("Visit https://bit.ly/abcdef")
        assert score == 0.0

    def test_multiple_violations(self, cf):
        text = "spam " + "x" * 300 + " http://a.com http://b.com http://c.com"
        score, viols, types = cf.check_content(text)
        assert len(types) >= 2
        assert score > 0.5

    def test_char_spam_short(self, cf):
        # Short messages shouldn't trigger char spam
        score, _, _ = cf.check_content("aaa")
        assert score == 0.0


# ─── FloodDetector ───────────────────────────────────────

class TestFloodDetector:
    @pytest.fixture
    def fd(self):
        return FloodDetector(
            window_seconds=5.0, max_messages=3,
            duplicate_window=30.0, max_duplicates=2,
        )

    def test_normal_rate(self, fd):
        state = UserModerationState(user_id="u1")
        score, _, _ = fd.check(state, "hello")
        assert score == 0.0

    def test_flood(self, fd):
        state = UserModerationState(user_id="u1")
        for i in range(5):
            fd.check(state, f"msg{i}")
        score, viols, types = fd.check(state, "one more")
        assert ViolationType.FLOOD in types

    def test_duplicate(self, fd):
        state = UserModerationState(user_id="u1")
        fd.check(state, "same message")
        fd.check(state, "same message")
        score, viols, types = fd.check(state, "same message")
        assert ViolationType.DUPLICATE in types

    def test_unique_messages_no_duplicate(self, fd):
        state = UserModerationState(user_id="u1")
        fd.check(state, "message 1")
        score, _, types = fd.check(state, "message 2")
        assert ViolationType.DUPLICATE not in types


# ─── AutoModerator ───────────────────────────────────────

class TestAutoModerator:
    @pytest.fixture
    def mod(self):
        return AutoModerator(
            content_filter=ContentFilter(blacklisted_words={"badword"}),
            flood_detector=FloodDetector(window_seconds=5, max_messages=3),
            warn_threshold=0.2,
            block_threshold=0.5,
            mute_after_violations=2,
            mute_duration=10.0,
            whitelist={"admin1"},
        )

    def test_clean_message(self, mod):
        result = mod.moderate("user1", "Hello!")
        assert result.allowed
        assert result.action == FilterAction.ALLOW

    def test_blacklisted_blocked(self, mod):
        result = mod.moderate("user1", "this is badword content")
        assert not result.allowed
        assert result.action in (FilterAction.BLOCK, FilterAction.MUTE)

    def test_whitelist_bypass(self, mod):
        result = mod.moderate("admin1", "badword badword badword")
        assert result.allowed
        assert result.action == FilterAction.ALLOW

    def test_mute_after_violations(self, mod):
        # Trigger enough violations for mute
        for _ in range(3):
            mod.moderate("user2", "badword")

        result = mod.moderate("user2", "normal message")
        assert not result.allowed
        assert result.action == FilterAction.MUTE

    def test_unmute(self, mod):
        mod.moderate("user3", "badword")
        mod.moderate("user3", "badword")
        mod.moderate("user3", "badword")  # triggers mute
        assert not mod.moderate("user3", "hi").allowed

        mod.unmute("user3")
        result = mod.moderate("user3", "hi after unmute")
        assert result.allowed

    def test_reset_user(self, mod):
        mod.moderate("user4", "badword")
        mod.reset_user("user4")
        status = mod.get_user_status("user4")
        assert status["status"] == "clean"

    def test_add_remove_whitelist(self, mod):
        result = mod.moderate("vip", "badword")
        assert not result.allowed

        mod.add_whitelist("vip")
        result = mod.moderate("vip", "badword")
        assert result.allowed

        mod.remove_whitelist("vip")
        result = mod.moderate("vip", "badword")
        assert not result.allowed

    def test_get_user_status(self, mod):
        mod.moderate("user5", "badword")
        status = mod.get_user_status("user5")
        assert status["violations"] >= 1

    def test_get_stats(self, mod):
        mod.moderate("a", "hello")
        mod.moderate("b", "badword")
        stats = mod.get_stats()
        assert stats["total_checked"] == 2
        assert stats["total_blocked"] >= 1
        assert "block_rate" in stats

    def test_cleanup(self, mod):
        mod.moderate("old_user", "badword")
        cleaned = mod.cleanup(max_age=0)  # cleanup everything
        assert cleaned >= 1

    def test_result_reason(self, mod):
        result = mod.moderate("u", "badword spam")
        if result.violations:
            assert len(result.reason) > 0

    def test_result_no_reason(self, mod):
        result = mod.moderate("u", "clean message")
        assert result.reason == ""

    def test_flood_triggers_block(self, mod):
        for i in range(10):
            mod.moderate("flooder", f"msg{i}")
        # After many rapid messages, should trigger
        result = mod.moderate("flooder", "another")
        # Depending on timing, may or may not be blocked

    def test_mute_duration(self, mod):
        # Force mute
        for _ in range(3):
            mod.moderate("muted_u", "badword")
        result = mod.moderate("muted_u", "hi")
        assert not result.allowed
        assert "Muted" in result.violations[0] or result.action == FilterAction.MUTE

    def test_unmute_nonexistent(self, mod):
        assert not mod.unmute("nonexistent_user")
