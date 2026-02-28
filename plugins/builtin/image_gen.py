"""
内置插件: AI图片生成
"""

import os
import json
import urllib.request
import urllib.error
from plugins.base import PluginBase, PluginContext, PluginResult


class ImageGenPlugin(PluginBase):
    """AI图片生成插件 - DALL-E API"""

    name = "imagegen"
    description = "AI图片生成 /img <描述>"
    version = "1.0.0"
    commands = ["/img", "/image"]
    priority = 50

    def __init__(self):
        super().__init__()
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def on_message(self, ctx: PluginContext) -> PluginResult:
        if not ctx.is_command or ctx.command not in ("/img", "/image"):
            return PluginResult.skip()
        return self.on_command(ctx)

    def on_command(self, ctx: PluginContext) -> PluginResult:
        prompt = ctx.args.strip()
        if not prompt:
            return PluginResult.respond(
                "🎨 *AI图片生成*\n\n用法: /img <描述>\n示例: /img a cat sitting on a rainbow"
            )

        if not self.api_key:
            return PluginResult.respond("❌ 未配置 OPENAI_API_KEY")

        try:
            url = f"{self.base_url}/images/generations"
            payload = json.dumps({
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "quality": "standard",
            }).encode()

            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())

            image_url = data["data"][0].get("url", "")
            revised = data["data"][0].get("revised_prompt", prompt)

            reply = f"🎨 *生成完成*\n\n_{revised[:200]}_\n\n{image_url}"
            result = PluginResult.respond(reply)
            result.metadata["image_url"] = image_url
            return result

        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            return PluginResult.respond(f"❌ 图片生成失败 ({e.code}): {body[:200]}")
        except Exception as e:
            return PluginResult.respond(f"❌ 图片生成错误: {e}")
