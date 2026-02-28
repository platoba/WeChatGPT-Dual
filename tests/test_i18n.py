"""Tests for services/i18n.py"""

import json
import pytest
from services.i18n import (
    I18n, LanguageDetector, Locale, TRANSLATIONS, LOCALES,
)


# ─── LanguageDetector ────────────────────────────────────

class TestLanguageDetector:
    @pytest.fixture
    def det(self):
        return LanguageDetector()

    def test_detect_chinese(self, det):
        assert det.detect("你好世界") == "zh"

    def test_detect_english(self, det):
        assert det.detect("Hello world this is English text") == "en"

    def test_detect_japanese(self, det):
        assert det.detect("こんにちは世界") == "ja"

    def test_detect_korean(self, det):
        assert det.detect("안녕하세요 세계") == "ko"

    def test_detect_empty(self, det):
        assert det.detect("") == "en"

    def test_detect_numbers_only(self, det):
        assert det.detect("12345") == "en"

    def test_detect_mixed_cjk(self, det):
        # With hiragana present, should lean Japanese
        result = det.detect("日本語のテスト")
        assert result == "ja"

    def test_detect_pure_cjk(self, det):
        # Pure CJK without kana → Chinese
        assert det.detect("中国大陆") == "zh"

    def test_detect_hangul(self, det):
        assert det.detect("한국어 텍스트입니다") == "ko"


# ─── I18n ─────────────────────────────────────────────────

class TestI18n:
    @pytest.fixture
    def i18n(self):
        return I18n(default_lang="zh")

    def test_basic_translation_zh(self, i18n):
        text = i18n.t("welcome")
        assert "你好" in text

    def test_basic_translation_en(self, i18n):
        text = i18n.t("welcome", lang="en")
        assert "Welcome" in text

    def test_japanese(self, i18n):
        text = i18n.t("welcome", lang="ja")
        assert "こんにちは" in text

    def test_korean(self, i18n):
        text = i18n.t("welcome", lang="ko")
        assert "안녕하세요" in text

    def test_variable_interpolation(self, i18n):
        text = i18n.t("welcome_back", lang="en", name="Alice")
        assert "Alice" in text

    def test_variable_interpolation_zh(self, i18n):
        text = i18n.t("welcome_back", name="用户")
        assert "用户" in text

    def test_missing_key_returns_key(self, i18n):
        text = i18n.t("nonexistent_key")
        assert text == "nonexistent_key"

    def test_fallback_to_english(self, i18n):
        # Add a key only in English
        i18n.load_translations("en", {"special": "Special"})
        text = i18n.t("special", lang="ja")  # Not in Japanese
        assert text == "Special"

    def test_set_user_lang(self, i18n):
        assert i18n.set_user_lang("u1", "en")
        assert i18n.get_user_lang("u1") == "en"

    def test_set_invalid_lang(self, i18n):
        assert not i18n.set_user_lang("u1", "invalid_lang")

    def test_user_lang_in_translation(self, i18n):
        i18n.set_user_lang("u1", "en")
        text = i18n.t("welcome", user_id="u1")
        assert "Welcome" in text

    def test_default_lang(self, i18n):
        assert i18n.get_user_lang("unknown_user") == "zh"

    def test_auto_set_lang(self, i18n):
        lang = i18n.auto_set_lang("u2", "Hello everyone")
        assert lang == "en"

    def test_auto_set_lang_chinese(self, i18n):
        lang = i18n.auto_set_lang("u3", "你好世界")
        assert lang == "zh"

    def test_auto_set_lang_preserves_existing(self, i18n):
        i18n.set_user_lang("u4", "ja")
        lang = i18n.auto_set_lang("u4", "Hello")
        assert lang == "ja"  # Should keep Japanese

    def test_auto_detect_disabled(self):
        i18n = I18n(default_lang="zh", auto_detect=False)
        lang = i18n.auto_set_lang("u5", "Hello everyone")
        assert lang == "zh"  # Should use default

    def test_available_languages(self, i18n):
        langs = i18n.get_available_languages()
        codes = [l["code"] for l in langs]
        assert "en" in codes
        assert "zh" in codes
        assert "ja" in codes
        assert "ko" in codes

    def test_load_translations(self, i18n):
        i18n.load_translations("en", {"custom_key": "Custom Value"})
        assert i18n.t("custom_key", lang="en") == "Custom Value"

    def test_load_translations_new_lang(self, i18n):
        i18n.load_translations("fr", {"welcome": "Bonjour!"})
        assert i18n.t("welcome", lang="fr") == "Bonjour!"

    def test_load_from_file(self, i18n, tmp_path):
        data = {"es": {"welcome": "¡Hola!", "bye": "Adiós"}}
        path = tmp_path / "translations.json"
        path.write_text(json.dumps(data))
        count = i18n.load_from_file(str(path))
        assert count == 2
        assert i18n.t("welcome", lang="es") == "¡Hola!"

    def test_detect_lang(self, i18n):
        assert i18n.detect_lang("你好") == "zh"
        assert i18n.detect_lang("Hello") == "en"

    def test_get_stats(self, i18n):
        stats = i18n.get_stats()
        assert stats["available_languages"] >= 4
        assert stats["default_lang"] == "zh"
        assert stats["total_keys"] > 0

    def test_missing_variable_safe(self, i18n):
        # Should not crash with missing variable
        text = i18n.t("welcome_back", lang="en")  # {name} not provided
        assert "name" in text or "Welcome" in text

    def test_all_keys_consistent(self):
        """Core 4 languages should share the same base keys"""
        # Use a fresh copy to avoid pollution from other tests
        from services.i18n import TRANSLATIONS as _TR
        base_langs = ["en", "zh", "ja", "ko"]
        base_keys = set()
        for lang in base_langs:
            base_keys |= {
                k for k in _TR.get(lang, {})
                if k not in ("special", "custom_key")  # exclude test-injected
            }
        for lang in base_langs:
            lang_keys = set(_TR.get(lang, {}).keys())
            # Only check keys that existed in the original translation
            core_missing = base_keys - lang_keys - {"special", "custom_key"}
            assert not core_missing, f"{lang} missing keys: {core_missing}"

    def test_locales_match_translations(self):
        for code in LOCALES:
            assert code in TRANSLATIONS, f"Locale {code} has no translations"


# ─── TRANSLATIONS ────────────────────────────────────────

class TestTranslations:
    def test_all_languages_have_welcome(self):
        for lang in ["en", "zh", "ja", "ko"]:
            assert "welcome" in TRANSLATIONS[lang]

    def test_all_languages_have_error_generic(self):
        for lang in ["en", "zh", "ja", "ko"]:
            assert "error_generic" in TRANSLATIONS[lang]

    def test_key_count_consistency(self):
        """Core 4 languages should have same number of base keys"""
        base = {"en", "zh", "ja", "ko"}
        counts = {
            lang: len([k for k in msgs if k not in ("special", "custom_key")])
            for lang, msgs in TRANSLATIONS.items()
            if lang in base
        }
        assert len(set(counts.values())) == 1, f"Key count mismatch: {counts}"
