"""
多语言国际化 (i18n) 支持
支持: zh/en/ja/ko + 动态语言检测 + 用户偏好
"""

import os
import json
import re
import logging
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Locale:
    """语言区域"""
    code: str        # e.g. "zh", "en"
    name: str        # e.g. "中文", "English"
    native: str      # e.g. "中文", "English"
    direction: str = "ltr"  # ltr or rtl
    fallback: str = "en"


# 内置翻译
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "welcome": "👋 Welcome! I'm your AI assistant.",
        "welcome_back": "Welcome back, {name}!",
        "help": "Available commands:\n{commands}",
        "help_cmd": "📖 Show help",
        "reset_cmd": "🔄 Reset conversation",
        "reset_done": "✅ Conversation reset.",
        "export_cmd": "📤 Export chat history",
        "export_done": "📄 Here's your chat export ({format}).",
        "export_empty": "No messages to export.",
        "lang_cmd": "🌐 Change language",
        "lang_set": "✅ Language set to: {lang}",
        "lang_list": "Available languages:\n{langs}",
        "lang_current": "Current language: {lang}",
        "error_generic": "⚠️ Something went wrong. Please try again.",
        "error_rate_limit": "⏳ Slow down! Try again in {seconds}s.",
        "error_engine": "🔧 AI engine error: {error}",
        "error_muted": "🔇 You are muted for {seconds}s.",
        "error_blocked": "🚫 Message blocked: {reason}",
        "stats_header": "📊 Your Stats",
        "stats_messages": "Messages: {count}",
        "stats_since": "Active since: {date}",
        "thinking": "🤔 Thinking...",
        "generating": "✨ Generating...",
        "model_info": "Model: {model}",
        "tokens_used": "Tokens used: {tokens}",
        "kb_searching": "🔍 Searching knowledge base...",
        "kb_found": "📚 Found {count} relevant entries.",
        "kb_empty": "No relevant knowledge found.",
        "plugin_loaded": "🔌 Plugin loaded: {name}",
        "plugin_error": "❌ Plugin error: {error}",
        "admin_only": "🔒 Admin only.",
        "user_blocked": "🚫 You are blocked.",
        "moderation_warn": "⚠️ Warning: {reason}",
        "queue_full": "📬 Queue full. Try later.",
        "session_expired": "⏰ Session expired. Starting new conversation.",
    },
    "zh": {
        "welcome": "👋 你好！我是你的AI助手。",
        "welcome_back": "欢迎回来，{name}！",
        "help": "可用命令：\n{commands}",
        "help_cmd": "📖 查看帮助",
        "reset_cmd": "🔄 重置对话",
        "reset_done": "✅ 对话已重置。",
        "export_cmd": "📤 导出聊天记录",
        "export_done": "📄 这是你的聊天导出（{format}格式）。",
        "export_empty": "没有消息可导出。",
        "lang_cmd": "🌐 切换语言",
        "lang_set": "✅ 语言已设为：{lang}",
        "lang_list": "可用语言：\n{langs}",
        "lang_current": "当前语言：{lang}",
        "error_generic": "⚠️ 出了点问题，请重试。",
        "error_rate_limit": "⏳ 太快了！请在{seconds}秒后重试。",
        "error_engine": "🔧 AI引擎错误：{error}",
        "error_muted": "🔇 你已被禁言{seconds}秒。",
        "error_blocked": "🚫 消息已拦截：{reason}",
        "stats_header": "📊 你的统计",
        "stats_messages": "消息数：{count}",
        "stats_since": "活跃自：{date}",
        "thinking": "🤔 思考中...",
        "generating": "✨ 生成中...",
        "model_info": "模型：{model}",
        "tokens_used": "已用Token：{tokens}",
        "kb_searching": "🔍 正在搜索知识库...",
        "kb_found": "📚 找到{count}条相关内容。",
        "kb_empty": "未找到相关知识。",
        "plugin_loaded": "🔌 插件已加载：{name}",
        "plugin_error": "❌ 插件错误：{error}",
        "admin_only": "🔒 仅管理员可用。",
        "user_blocked": "🚫 你已被封禁。",
        "moderation_warn": "⚠️ 警告：{reason}",
        "queue_full": "📬 队列已满，请稍后再试。",
        "session_expired": "⏰ 会话已过期，已开始新对话。",
    },
    "ja": {
        "welcome": "👋 こんにちは！AIアシスタントです。",
        "welcome_back": "おかえりなさい、{name}！",
        "help": "利用可能なコマンド：\n{commands}",
        "help_cmd": "📖 ヘルプを表示",
        "reset_cmd": "🔄 会話をリセット",
        "reset_done": "✅ 会話をリセットしました。",
        "export_cmd": "📤 チャット履歴をエクスポート",
        "export_done": "📄 チャットエクスポート（{format}形式）です。",
        "export_empty": "エクスポートするメッセージがありません。",
        "lang_cmd": "🌐 言語を変更",
        "lang_set": "✅ 言語を{lang}に設定しました。",
        "lang_list": "利用可能な言語：\n{langs}",
        "lang_current": "現在の言語：{lang}",
        "error_generic": "⚠️ エラーが発生しました。もう一度お試しください。",
        "error_rate_limit": "⏳ 少々お待ちください。{seconds}秒後にお試しください。",
        "error_engine": "🔧 AIエンジンエラー：{error}",
        "error_muted": "🔇 {seconds}秒間ミュートされています。",
        "error_blocked": "🚫 メッセージがブロックされました：{reason}",
        "stats_header": "📊 あなたの統計",
        "stats_messages": "メッセージ数：{count}",
        "stats_since": "アクティブ開始：{date}",
        "thinking": "🤔 考え中...",
        "generating": "✨ 生成中...",
        "model_info": "モデル：{model}",
        "tokens_used": "使用トークン：{tokens}",
        "kb_searching": "🔍 ナレッジベースを検索中...",
        "kb_found": "📚 {count}件の関連エントリが見つかりました。",
        "kb_empty": "関連するナレッジが見つかりません。",
        "plugin_loaded": "🔌 プラグインが読み込まれました：{name}",
        "plugin_error": "❌ プラグインエラー：{error}",
        "admin_only": "🔒 管理者専用です。",
        "user_blocked": "🚫 ブロックされています。",
        "moderation_warn": "⚠️ 警告：{reason}",
        "queue_full": "📬 キューが満杯です。後でお試しください。",
        "session_expired": "⏰ セッションが期限切れです。新しい会話を開始します。",
    },
    "ko": {
        "welcome": "👋 안녕하세요! AI 어시스턴트입니다.",
        "welcome_back": "다시 오셨군요, {name}!",
        "help": "사용 가능한 명령어:\n{commands}",
        "help_cmd": "📖 도움말 보기",
        "reset_cmd": "🔄 대화 초기화",
        "reset_done": "✅ 대화가 초기화되었습니다.",
        "export_cmd": "📤 채팅 기록 내보내기",
        "export_done": "📄 채팅 내보내기({format} 형식)입니다.",
        "export_empty": "내보낼 메시지가 없습니다.",
        "lang_cmd": "🌐 언어 변경",
        "lang_set": "✅ 언어가 {lang}(으)로 설정되었습니다.",
        "lang_list": "사용 가능한 언어:\n{langs}",
        "lang_current": "현재 언어: {lang}",
        "error_generic": "⚠️ 문제가 발생했습니다. 다시 시도해주세요.",
        "error_rate_limit": "⏳ 너무 빠릅니다! {seconds}초 후에 다시 시도해주세요.",
        "error_engine": "🔧 AI 엔진 오류: {error}",
        "error_muted": "🔇 {seconds}초 동안 음소거되었습니다.",
        "error_blocked": "🚫 메시지가 차단되었습니다: {reason}",
        "stats_header": "📊 통계",
        "stats_messages": "메시지 수: {count}",
        "stats_since": "활동 시작: {date}",
        "thinking": "🤔 생각 중...",
        "generating": "✨ 생성 중...",
        "model_info": "모델: {model}",
        "tokens_used": "사용 토큰: {tokens}",
        "kb_searching": "🔍 지식 베이스 검색 중...",
        "kb_found": "📚 {count}개의 관련 항목을 찾았습니다.",
        "kb_empty": "관련 지식을 찾지 못했습니다.",
        "plugin_loaded": "🔌 플러그인 로드: {name}",
        "plugin_error": "❌ 플러그인 오류: {error}",
        "admin_only": "🔒 관리자 전용입니다.",
        "user_blocked": "🚫 차단되었습니다.",
        "moderation_warn": "⚠️ 경고: {reason}",
        "queue_full": "📬 대기열이 가득 찼습니다. 나중에 다시 시도해주세요.",
        "session_expired": "⏰ 세션이 만료되었습니다. 새 대화를 시작합니다.",
    },
}

LOCALES: Dict[str, Locale] = {
    "en": Locale(code="en", name="English", native="English"),
    "zh": Locale(code="zh", name="Chinese", native="中文", fallback="en"),
    "ja": Locale(code="ja", name="Japanese", native="日本語", fallback="en"),
    "ko": Locale(code="ko", name="Korean", native="한국어", fallback="en"),
}


class LanguageDetector:
    """简易语言检测"""

    # CJK Unicode ranges
    CJK_RANGES = [
        (0x4E00, 0x9FFF),    # CJK Unified
        (0x3400, 0x4DBF),    # CJK Extension A
        (0x20000, 0x2A6DF),  # CJK Extension B
    ]
    HIRAGANA = (0x3040, 0x309F)
    KATAKANA = (0x30A0, 0x30FF)
    HANGUL = (0xAC00, 0xD7AF)

    @classmethod
    def detect(cls, text: str) -> str:
        """
        检测文本语言

        Returns:
            语言代码 (zh/ja/ko/en)
        """
        if not text:
            return "en"

        counts = {"zh": 0, "ja": 0, "ko": 0, "en": 0}

        for char in text:
            cp = ord(char)

            # Hangul → Korean
            if cls.HANGUL[0] <= cp <= cls.HANGUL[1]:
                counts["ko"] += 1

            # Hiragana/Katakana → Japanese
            elif (cls.HIRAGANA[0] <= cp <= cls.HIRAGANA[1] or
                  cls.KATAKANA[0] <= cp <= cls.KATAKANA[1]):
                counts["ja"] += 1

            # CJK → could be Chinese or Japanese
            elif any(start <= cp <= end for start, end in cls.CJK_RANGES):
                counts["zh"] += 1  # Default CJK to Chinese

            elif char.isalpha():
                counts["en"] += 1

        # If Japanese kana detected, CJK chars are likely Japanese too
        if counts["ja"] > 0:
            counts["ja"] += counts["zh"] * 0.5

        if not any(counts.values()):
            return "en"

        return max(counts, key=counts.get)


class I18n:
    """
    国际化服务

    Features:
    - 4语言内置翻译 (zh/en/ja/ko)
    - 变量插值 {variable}
    - 自动语言检测
    - 用户语言偏好持久化
    - 自定义翻译加载
    - 缺失key自动fallback
    """

    def __init__(
        self,
        default_lang: str = "zh",
        translations: Optional[Dict[str, Dict[str, str]]] = None,
        auto_detect: bool = True,
    ):
        self.default_lang = default_lang
        self.auto_detect = auto_detect
        self._translations = dict(TRANSLATIONS)
        self._user_langs: Dict[str, str] = {}
        self._detector = LanguageDetector()

        # Merge custom translations
        if translations:
            for lang, msgs in translations.items():
                if lang in self._translations:
                    self._translations[lang].update(msgs)
                else:
                    self._translations[lang] = dict(msgs)

    def t(
        self,
        key: str,
        lang: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        翻译key为指定语言

        Args:
            key: 翻译key
            lang: 强制语言 (None=使用用户偏好/默认)
            user_id: 用户ID (用于查询偏好语言)
            **kwargs: 变量插值

        Returns:
            翻译后的字符串
        """
        # 确定语言
        target_lang = lang
        if not target_lang and user_id:
            target_lang = self._user_langs.get(user_id)
        if not target_lang:
            target_lang = self.default_lang

        # 查找翻译
        text = self._get_translation(key, target_lang)

        # 变量插值
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass

        return text

    def _get_translation(self, key: str, lang: str) -> str:
        """获取翻译 (含fallback链)"""
        # 1. 尝试目标语言
        msgs = self._translations.get(lang, {})
        if key in msgs:
            return msgs[key]

        # 2. 尝试fallback语言
        locale = LOCALES.get(lang)
        if locale and locale.fallback != lang:
            fallback_msgs = self._translations.get(locale.fallback, {})
            if key in fallback_msgs:
                return fallback_msgs[key]

        # 3. 尝试英文 (最终fallback)
        en_msgs = self._translations.get("en", {})
        if key in en_msgs:
            return en_msgs[key]

        # 4. 返回key本身
        return key

    def set_user_lang(self, user_id: str, lang: str) -> bool:
        """设置用户语言偏好"""
        if lang not in self._translations:
            return False
        self._user_langs[user_id] = lang
        return True

    def get_user_lang(self, user_id: str) -> str:
        """获取用户语言偏好"""
        return self._user_langs.get(user_id, self.default_lang)

    def detect_lang(self, text: str) -> str:
        """检测文本语言"""
        return self._detector.detect(text)

    def auto_set_lang(self, user_id: str, text: str) -> str:
        """自动检测并设置用户语言"""
        if not self.auto_detect:
            return self.get_user_lang(user_id)
        if user_id in self._user_langs:
            return self._user_langs[user_id]

        detected = self.detect_lang(text)
        if detected in self._translations:
            self._user_langs[user_id] = detected
        return self.get_user_lang(user_id)

    def get_available_languages(self) -> List[Dict[str, str]]:
        """获取所有可用语言"""
        result = []
        for code in sorted(self._translations.keys()):
            locale = LOCALES.get(code)
            result.append({
                "code": code,
                "name": locale.name if locale else code,
                "native": locale.native if locale else code,
            })
        return result

    def load_translations(self, lang: str, data: Dict[str, str]):
        """加载自定义翻译"""
        if lang in self._translations:
            self._translations[lang].update(data)
        else:
            self._translations[lang] = dict(data)

    def load_from_file(self, path: str) -> int:
        """从JSON文件加载翻译 (格式: {"lang": {"key": "value"}})"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for lang, msgs in data.items():
            self.load_translations(lang, msgs)
            count += len(msgs)
        return count

    def get_stats(self) -> Dict:
        return {
            "default_lang": self.default_lang,
            "available_languages": len(self._translations),
            "languages": list(self._translations.keys()),
            "total_keys": sum(len(m) for m in self._translations.values()),
            "user_preferences": len(self._user_langs),
            "auto_detect": self.auto_detect,
        }
