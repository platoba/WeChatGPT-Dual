"""Tests for services/user_preferences.py"""

import os
import pytest
import tempfile
from services.user_preferences import PreferenceService, UserPrefs


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def svc(db_path):
    return PreferenceService(db_path)


class TestUserPrefs:
    def test_defaults(self):
        p = UserPrefs(user_id="u1")
        assert p.language == "auto"
        assert p.engine == ""
        assert p.temperature == -1.0
        assert p.auto_translate is False
        assert p.response_style == "balanced"

    def test_effective_temperature_default(self):
        p = UserPrefs(user_id="u1")
        assert p.effective_temperature() == 0.7

    def test_effective_temperature_custom(self):
        p = UserPrefs(user_id="u1", temperature=1.2)
        assert p.effective_temperature() == 1.2

    def test_effective_max_tokens_default(self):
        p = UserPrefs(user_id="u1")
        assert p.effective_max_tokens() == 2000

    def test_effective_max_tokens_custom(self):
        p = UserPrefs(user_id="u1", max_tokens=500)
        assert p.effective_max_tokens() == 500

    def test_has_budget_false(self):
        p = UserPrefs(user_id="u1")
        assert p.has_budget() is False

    def test_has_budget_daily(self):
        p = UserPrefs(user_id="u1", daily_token_budget=10000)
        assert p.has_budget() is True

    def test_has_budget_monthly(self):
        p = UserPrefs(user_id="u1", monthly_token_budget=100000)
        assert p.has_budget() is True

    def test_to_dict(self):
        p = UserPrefs(user_id="u1", language="zh", auto_translate=True)
        d = p.to_dict()
        assert d["user_id"] == "u1"
        assert d["language"] == "zh"
        assert d["auto_translate"] is True


class TestPreferenceService:
    def test_get_default(self, svc):
        prefs = svc.get("unknown_user")
        assert prefs.user_id == "unknown_user"
        assert prefs.language == "auto"
        assert prefs.created_at == 0

    def test_set_single_field(self, svc):
        result = svc.set("u1", language="zh")
        assert result.language == "zh"
        assert result.engine == ""

    def test_set_multiple_fields(self, svc):
        result = svc.set("u1", language="ja", engine="claude", temperature=0.5)
        assert result.language == "ja"
        assert result.engine == "claude"
        assert result.temperature == 0.5

    def test_set_preserves_existing(self, svc):
        svc.set("u1", language="zh", engine="openai")
        result = svc.set("u1", temperature=1.0)
        assert result.language == "zh"
        assert result.engine == "openai"
        assert result.temperature == 1.0

    def test_set_auto_translate(self, svc):
        result = svc.set("u1", auto_translate=True)
        assert result.auto_translate is True

    def test_set_persona(self, svc):
        result = svc.set("u1", persona="translator")
        assert result.persona == "translator"

    def test_set_system_prompt(self, svc):
        result = svc.set("u1", system_prompt="You are a helpful assistant")
        assert result.system_prompt == "You are a helpful assistant"

    def test_set_response_style(self, svc):
        result = svc.set("u1", response_style="concise")
        assert result.response_style == "concise"

    def test_set_timezone(self, svc):
        result = svc.set("u1", timezone="Asia/Shanghai")
        assert result.timezone == "Asia/Shanghai"

    def test_set_budget(self, svc):
        result = svc.set("u1", daily_token_budget=5000, monthly_token_budget=100000)
        assert result.daily_token_budget == 5000
        assert result.monthly_token_budget == 100000

    def test_set_extra(self, svc):
        result = svc.set("u1", extra={"theme": "dark"})
        assert result.extra["theme"] == "dark"

    def test_set_extra_merge(self, svc):
        svc.set("u1", extra={"theme": "dark"})
        result = svc.set("u1", extra={"lang_detect": True})
        assert result.extra["theme"] == "dark"
        assert result.extra["lang_detect"] is True

    def test_set_updates_timestamp(self, svc):
        r1 = svc.set("u1", language="en")
        import time
        time.sleep(0.01)
        r2 = svc.set("u1", language="zh")
        assert r2.updated_at >= r1.updated_at

    def test_reset(self, svc):
        svc.set("u1", language="zh", engine="claude")
        assert svc.reset("u1") is True
        prefs = svc.get("u1")
        assert prefs.language == "auto"
        assert prefs.created_at == 0

    def test_reset_nonexistent(self, svc):
        assert svc.reset("unknown") is False

    def test_list_users(self, svc):
        svc.set("u1", language="zh")
        svc.set("u2", language="en")
        svc.set("u3", language="ja")
        users = svc.list_users()
        assert len(users) == 3

    def test_list_users_limit(self, svc):
        for i in range(5):
            svc.set(f"u{i}", language="en")
        users = svc.list_users(limit=3)
        assert len(users) == 3

    def test_get_users_by_engine(self, svc):
        svc.set("u1", engine="openai")
        svc.set("u2", engine="claude")
        svc.set("u3", engine="openai")
        result = svc.get_users_by_engine("openai")
        assert len(result) == 2
        assert "u1" in result
        assert "u3" in result

    def test_get_users_by_language(self, svc):
        svc.set("u1", language="zh")
        svc.set("u2", language="en")
        svc.set("u3", language="zh")
        result = svc.get_users_by_language("zh")
        assert len(result) == 2

    def test_bulk_set(self, svc):
        updates = {
            "u1": {"language": "zh"},
            "u2": {"language": "en", "engine": "claude"},
        }
        count = svc.bulk_set(updates)
        assert count == 2
        assert svc.get("u1").language == "zh"
        assert svc.get("u2").engine == "claude"

    def test_export_all(self, svc):
        svc.set("u1", language="zh")
        svc.set("u2", language="en")
        data = svc.export_all()
        assert len(data) == 2
        assert all(isinstance(d, dict) for d in data)


class TestPreferenceValidation:
    def test_invalid_language(self, svc):
        with pytest.raises(ValueError, match="Invalid language"):
            svc.set("u1", language="xx")

    def test_invalid_style(self, svc):
        with pytest.raises(ValueError, match="Invalid style"):
            svc.set("u1", response_style="nonexistent")

    def test_valid_languages(self, svc):
        for lang in ["auto", "en", "zh", "ja", "ko", "es", "fr"]:
            result = svc.set("u1", language=lang)
            assert result.language == lang

    def test_valid_styles(self, svc):
        for style in ["concise", "balanced", "detailed", "creative"]:
            result = svc.set("u1", response_style=style)
            assert result.response_style == style

    def test_temperature_too_high(self, svc):
        with pytest.raises(ValueError):
            svc.set("u1", temperature=2.5)

    def test_budget_type_validation(self, svc):
        with pytest.raises(ValueError):
            svc.set("u1", daily_token_budget="not_a_number")
