"""
Admin管理面板 - FastAPI管理界面
"""

import time
import logging
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

admin_app = FastAPI(title="WeChatGPT-Dual Admin", version="3.0.0")

# Global refs
_engine_manager = None
_context_manager = None
_plugin_loader = None
_rate_limiter = None
_health_checker = None
_start_time = time.time()


def setup_admin(
    engine_manager=None,
    context_manager=None,
    plugin_loader=None,
    rate_limiter=None,
    health_checker=None,
):
    """设置管理面板依赖"""
    global _engine_manager, _context_manager, _plugin_loader
    global _rate_limiter, _health_checker
    _engine_manager = engine_manager
    _context_manager = context_manager
    _plugin_loader = plugin_loader
    _rate_limiter = rate_limiter
    _health_checker = health_checker


class AdminAuth:
    """简易管理员认证 (环境变量 ADMIN_TOKEN)"""

    def __init__(self, token: str = ""):
        import os
        self.token = token or os.environ.get("ADMIN_TOKEN", "admin")

    def verify(self, request: Request) -> bool:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:] == self.token
        token = request.query_params.get("token", "")
        return token == self.token


_auth = AdminAuth()


def require_auth(request: Request):
    if not _auth.verify(request):
        raise HTTPException(401, "Unauthorized")


@admin_app.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """管理仪表盘 HTML 页面"""
    if not _auth.verify(request):
        raise HTTPException(401, "Unauthorized")

    uptime = time.time() - _start_time
    uptime_h = uptime / 3600

    engine_html = ""
    if _engine_manager:
        status = _engine_manager.get_status()
        for name, e in status.get("engines", {}).items():
            marker = "🟢" if e.get("available") else "🔴"
            engine_html += f"""
            <div class='card'>
                <h3>{marker} {name}</h3>
                <p>Model: {e.get('model', '?')}</p>
                <p>Requests: {e.get('total_requests', 0)} (Success: {e.get('success_rate', 0)}%)</p>
                <p>Tokens: {e.get('total_tokens_used', 0)}</p>
                <p>Avg Latency: {e.get('avg_latency', 0)}s</p>
            </div>"""

    plugin_html = ""
    if _plugin_loader:
        ps = _plugin_loader.get_status()
        plugin_html = f"<p>Plugins: {ps['enabled']}/{ps['total']} enabled</p>"
        for name, info in ps.get("plugins", {}).items():
            marker = "✅" if info.get("enabled") else "❌"
            plugin_html += f"<p>{marker} {name} v{info.get('version', '?')} — {info.get('call_count', 0)} calls</p>"

    rate_html = ""
    if _rate_limiter:
        rs = _rate_limiter.get_stats()
        rate_html = f"<p>Rate Limit: {rs['blocked_count']}/{rs['total_checks']} blocked ({rs['block_rate']}%)</p>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>WeChatGPT-Dual Admin</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
h1 {{ color: #333; }} .card {{ background: white; padding: 16px; margin: 10px 0; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
</style></head><body>
<h1>🤖 WeChatGPT-Dual Admin</h1>
<div class='card'><h3>⏱️ Uptime</h3><p>{uptime_h:.1f} hours</p></div>
<h2>🔧 Engines</h2>
<div class='grid'>{engine_html}</div>
<h2>🔌 Plugins</h2>
<div class='card'>{plugin_html}</div>
<h2>🛡️ Rate Limiting</h2>
<div class='card'>{rate_html}</div>
</body></html>"""

    return HTMLResponse(html)


@admin_app.get("/admin/api/status")
async def api_status(request: Request):
    """API状态端点"""
    if not _auth.verify(request):
        raise HTTPException(401, "Unauthorized")

    result = {"uptime": time.time() - _start_time}

    if _engine_manager:
        result["engines"] = _engine_manager.get_status()
    if _plugin_loader:
        result["plugins"] = _plugin_loader.get_status()
    if _rate_limiter:
        result["rate_limit"] = _rate_limiter.get_stats()
    if _health_checker:
        result["health"] = _health_checker.check_all()

    return result


@admin_app.post("/admin/api/plugins/{name}/toggle")
async def toggle_plugin(name: str, request: Request):
    """启用/禁用插件"""
    if not _auth.verify(request):
        raise HTTPException(401, "Unauthorized")
    if not _plugin_loader:
        raise HTTPException(503, "Plugin loader not available")

    plugin = _plugin_loader.plugins.get(name)
    if not plugin:
        raise HTTPException(404, f"Plugin {name} not found")

    if plugin.enabled:
        plugin.disable()
        return {"status": "disabled", "plugin": name}
    else:
        plugin.enable()
        return {"status": "enabled", "plugin": name}


@admin_app.post("/admin/api/plugins/{name}/reload")
async def reload_plugin(name: str, request: Request):
    """重载插件"""
    if not _auth.verify(request):
        raise HTTPException(401, "Unauthorized")
    if not _plugin_loader:
        raise HTTPException(503, "Plugin loader not available")

    if _plugin_loader.reload_plugin(name):
        return {"status": "reloaded", "plugin": name}
    raise HTTPException(500, f"Failed to reload {name}")


@admin_app.get("/admin/api/health")
async def health_check(request: Request):
    """详细健康检查"""
    if _health_checker:
        return _health_checker.check_all()
    return {"status": "ok", "uptime": time.time() - _start_time}
