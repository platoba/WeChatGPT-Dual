"""
内置插件: 翻译
"""

from plugins.base import PluginBase, PluginContext, PluginResult


class TranslatePlugin(PluginBase):
    """简易翻译插件 - 将文本委托给AI引擎翻译"""

    name = "translate"
    description = "翻译文本 /tr <语言> <文本>"
    version = "1.0.0"
    commands = ["/tr", "/translate"]
    priority = 50

    LANG_MAP = {
        "en": "English",
        "zh": "中文",
        "ja": "日本語",
        "ko": "한국어",
        "es": "Español",
        "fr": "Français",
        "de": "Deutsch",
        "ru": "Русский",
        "pt": "Português",
        "ar": "العربية",
    }

    def on_message(self, ctx: PluginContext) -> PluginResult:
        if not ctx.is_command or ctx.command not in ("/tr", "/translate"):
            return PluginResult.skip()
        return self.on_command(ctx)

    def on_command(self, ctx: PluginContext) -> PluginResult:
        args = ctx.args.strip()
        if not args:
            langs = ", ".join(f"`{k}` ({v})" for k, v in self.LANG_MAP.items())
            return PluginResult.respond(
                f"📝 *翻译*\n\n用法: /tr <语言代码> <文本>\n\n支持语言: {langs}"
            )

        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return PluginResult.respond("用法: /tr en 你好世界")

        target_lang = parts[0].lower()
        text = parts[1]

        lang_name = self.LANG_MAP.get(target_lang, target_lang)

        # 返回翻译提示 (实际翻译会由AI引擎处理)
        result = PluginResult.respond(
            f"🔄 翻译到 {lang_name}:\n\n"
            f"*原文:* {text}\n\n"
            f"_请通过AI引擎处理翻译（插件模式）_"
        )
        result.metadata["translate_target"] = target_lang
        result.metadata["translate_text"] = text
        result.metadata["translate_prompt"] = (
            f"Translate the following text to {lang_name}. "
            f"Only output the translation, nothing else:\n\n{text}"
        )
        return result
