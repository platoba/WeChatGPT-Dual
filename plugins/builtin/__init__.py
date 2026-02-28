"""
内置插件: 天气查询
"""

import json
import urllib.request
import urllib.error
from plugins.base import PluginBase, PluginContext, PluginResult


class WeatherPlugin(PluginBase):
    """天气查询插件 - 使用 wttr.in API"""

    name = "weather"
    description = "查询天气 /weather <城市>"
    version = "1.0.0"
    commands = ["/weather"]
    priority = 50

    def on_message(self, ctx: PluginContext) -> PluginResult:
        if not ctx.is_command or ctx.command != "/weather":
            return PluginResult.skip()
        return self.on_command(ctx)

    def on_command(self, ctx: PluginContext) -> PluginResult:
        city = ctx.args.strip() or "Beijing"
        try:
            url = f"https://wttr.in/{city}?format=j1"
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            current = data.get("current_condition", [{}])[0]
            temp = current.get("temp_C", "?")
            feels = current.get("FeelsLikeC", "?")
            humidity = current.get("humidity", "?")
            desc = current.get("weatherDesc", [{}])[0].get("value", "Unknown")
            wind = current.get("windspeedKmph", "?")

            reply = (
                f"🌤️ *{city} 天气*\n\n"
                f"天气: {desc}\n"
                f"温度: {temp}°C (体感 {feels}°C)\n"
                f"湿度: {humidity}%\n"
                f"风速: {wind} km/h"
            )
            return PluginResult.respond(reply)

        except urllib.error.URLError:
            return PluginResult.respond(f"❌ 无法获取 {city} 的天气信息")
        except Exception as e:
            return PluginResult.respond(f"❌ 天气查询失败: {e}")
