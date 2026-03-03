"""
Admin Dashboard API - 管理后台REST API

Features:
- 系统健康总览 (引擎状态/DB/缓存/队列)
- 用户管理 (列表/封禁/解封/详情)
- 引擎管理 (切换主引擎/查看统计)
- 消息查询 (搜索/过滤/导出)
- 插件管理 (列表/启用/禁用)
- 系统配置 (查看/修改运行时配置)
- API Key认证
"""

import time
import hashlib
import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AdminConfig:
    """管理后台配置"""
    api_key: str = ""
    admin_users: List[str] = field(default_factory=list)  # allowed user IDs
    read_only: bool = False
    max_query_limit: int = 1000
    enable_config_edit: bool = False


@dataclass
class ApiResponse:
    """API响应"""
    success: bool
    data: Any = None
    error: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class AdminDashboard:
    """管理后台"""

    def __init__(
        self,
        config: AdminConfig = None,
        db=None,
        engine_manager=None,
        plugin_loader=None,
        app_config=None,
    ):
        self.config = config or AdminConfig()
        self.db = db
        self.engine_manager = engine_manager
        self.plugin_loader = plugin_loader
        self.app_config = app_config
        self._action_log: List[Dict] = []
        self._max_log = 500

    # ---- Authentication ----

    def authenticate(self, api_key: str = None, user_id: str = None) -> bool:
        """验证管理员身份"""
        if api_key and self.config.api_key:
            return hashlib.sha256(api_key.encode()).hexdigest() == hashlib.sha256(
                self.config.api_key.encode()
            ).hexdigest()
        if user_id and self.config.admin_users:
            return user_id in self.config.admin_users
        # No auth configured = allow
        if not self.config.api_key and not self.config.admin_users:
            return True
        return False

    def _require_write(self) -> Optional[ApiResponse]:
        """检查写权限"""
        if self.config.read_only:
            return ApiResponse(success=False, error="Dashboard is in read-only mode")
        return None

    def _log_action(self, action: str, details: str = "", admin_id: str = ""):
        """记录管理操作"""
        entry = {
            "timestamp": time.time(),
            "action": action,
            "details": details,
            "admin_id": admin_id,
        }
        self._action_log.append(entry)
        if len(self._action_log) > self._max_log:
            self._action_log = self._action_log[-self._max_log:]
        logger.info(f"Admin action: {action} - {details}")

    # ---- System Health ----

    def get_system_health(self) -> ApiResponse:
        """获取系统健康总览"""
        health = {
            "status": "healthy",
            "uptime": time.time(),  # Would be replaced with actual uptime
            "components": {},
        }

        # Database
        if self.db:
            try:
                stats = self.db.get_stats()
                health["components"]["database"] = {
                    "status": "ok",
                    "total_messages": stats.get("total_messages", 0),
                    "total_users": stats.get("total_users", 0),
                }
            except Exception as e:
                health["components"]["database"] = {
                    "status": "error",
                    "error": str(e),
                }
                health["status"] = "degraded"

        # Engine
        if self.engine_manager:
            try:
                health["components"]["engine"] = {
                    "status": "ok",
                    "primary": self.engine_manager.primary_name,
                    "secondary": self.engine_manager.secondary_name,
                    "failover_count": getattr(self.engine_manager, '_failover_count', 0),
                }
            except Exception as e:
                health["components"]["engine"] = {
                    "status": "error",
                    "error": str(e),
                }

        # Plugins
        if self.plugin_loader:
            try:
                plugins = self.plugin_loader.list_plugins() if hasattr(self.plugin_loader, 'list_plugins') else []
                health["components"]["plugins"] = {
                    "status": "ok",
                    "loaded": len(plugins),
                }
            except Exception:
                health["components"]["plugins"] = {"status": "unknown"}

        return ApiResponse(success=True, data=health)

    # ---- User Management ----

    def list_users(self, limit: int = 50, offset: int = 0) -> ApiResponse:
        """列出用户"""
        if not self.db:
            return ApiResponse(success=False, error="Database not available")

        try:
            limit = min(limit, self.config.max_query_limit)
            users = self.db.get_users(limit=limit + offset)
            # Manual offset
            users = users[offset:offset + limit]
            return ApiResponse(success=True, data={
                "users": users,
                "total": len(users),
                "limit": limit,
                "offset": offset,
            })
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    def get_user_detail(self, user_id: str) -> ApiResponse:
        """获取用户详情"""
        if not self.db:
            return ApiResponse(success=False, error="Database not available")

        try:
            user = self.db.get_user(user_id)
            if not user:
                return ApiResponse(success=False, error=f"User {user_id} not found")

            # Get recent messages
            messages = self.db.get_messages(user_id=user_id, limit=20)

            return ApiResponse(success=True, data={
                "user": {
                    "user_id": user.user_id,
                    "username": user.username,
                    "first_seen": user.first_seen,
                    "last_active": user.last_active,
                    "total_messages": user.total_messages,
                    "is_blocked": user.is_blocked,
                    "metadata": user.metadata,
                },
                "recent_messages": messages,
            })
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    def block_user(self, user_id: str, admin_id: str = "") -> ApiResponse:
        """封禁用户"""
        check = self._require_write()
        if check:
            return check

        if not self.db:
            return ApiResponse(success=False, error="Database not available")

        try:
            result = self.db.block_user(user_id)
            self._log_action("block_user", f"user_id={user_id}", admin_id)
            return ApiResponse(success=True, data={"blocked": result, "user_id": user_id})
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    def unblock_user(self, user_id: str, admin_id: str = "") -> ApiResponse:
        """解封用户"""
        check = self._require_write()
        if check:
            return check

        if not self.db:
            return ApiResponse(success=False, error="Database not available")

        try:
            result = self.db.unblock_user(user_id)
            self._log_action("unblock_user", f"user_id={user_id}", admin_id)
            return ApiResponse(success=True, data={"unblocked": result, "user_id": user_id})
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    # ---- Engine Management ----

    def get_engine_status(self) -> ApiResponse:
        """获取引擎状态"""
        if not self.engine_manager:
            return ApiResponse(success=False, error="Engine manager not available")

        try:
            return ApiResponse(success=True, data={
                "primary": self.engine_manager.primary_name,
                "secondary": self.engine_manager.secondary_name,
                "failover_enabled": self.engine_manager.failover_enabled,
                "failover_count": getattr(self.engine_manager, '_failover_count', 0),
                "engines": list(self.engine_manager.engines.keys()),
            })
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    def switch_engine(self, engine_name: str, admin_id: str = "") -> ApiResponse:
        """切换主引擎"""
        check = self._require_write()
        if check:
            return check

        if not self.engine_manager:
            return ApiResponse(success=False, error="Engine manager not available")

        try:
            result = self.engine_manager.switch_primary(engine_name)
            if result:
                self._log_action("switch_engine", f"to={engine_name}", admin_id)
                return ApiResponse(success=True, data={
                    "new_primary": engine_name,
                    "switched": True,
                })
            else:
                return ApiResponse(success=False, error=f"Unknown engine: {engine_name}")
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    # ---- Usage & Analytics ----

    def get_usage_stats(self, days: int = 7) -> ApiResponse:
        """获取使用统计"""
        if not self.db:
            return ApiResponse(success=False, error="Database not available")

        try:
            usage = self.db.get_usage(days=days)
            summary = self.db.get_usage_summary()
            return ApiResponse(success=True, data={
                "daily": usage,
                "summary": summary,
                "days_requested": days,
            })
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    # ---- Message Query ----

    def search_messages(
        self,
        user_id: str = None,
        channel: str = None,
        limit: int = 50,
        since: float = None,
    ) -> ApiResponse:
        """搜索消息"""
        if not self.db:
            return ApiResponse(success=False, error="Database not available")

        try:
            limit = min(limit, self.config.max_query_limit)
            messages = self.db.get_messages(
                user_id=user_id,
                channel=channel,
                limit=limit,
                since=since,
            )
            return ApiResponse(success=True, data={
                "messages": messages,
                "count": len(messages),
            })
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    # ---- Configuration ----

    def get_config(self) -> ApiResponse:
        """获取当前配置 (脱敏)"""
        if not self.app_config:
            return ApiResponse(success=False, error="Config not available")

        try:
            # Sanitize - hide secrets
            cfg = {}
            for key, value in vars(self.app_config).items():
                if hasattr(value, '__dataclass_fields__'):
                    sub = {}
                    for k, v in vars(value).items():
                        if any(s in k.lower() for s in ["key", "token", "secret", "password"]):
                            sub[k] = "***" if v else ""
                        else:
                            sub[k] = v
                    cfg[key] = sub
                elif any(s in key.lower() for s in ["key", "token", "secret", "password"]):
                    cfg[key] = "***" if value else ""
                else:
                    cfg[key] = value

            return ApiResponse(success=True, data=cfg)
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    def update_config(self, updates: Dict, admin_id: str = "") -> ApiResponse:
        """更新运行时配置"""
        check = self._require_write()
        if check:
            return check

        if not self.config.enable_config_edit:
            return ApiResponse(success=False, error="Config editing is disabled")

        if not self.app_config:
            return ApiResponse(success=False, error="Config not available")

        try:
            applied = []
            for key, value in updates.items():
                if hasattr(self.app_config, key):
                    old = getattr(self.app_config, key)
                    # Don't allow changing sensitive fields via API
                    if any(s in key.lower() for s in ["key", "token", "secret"]):
                        continue
                    setattr(self.app_config, key, value)
                    applied.append({"key": key, "old": old, "new": value})

            self._log_action("update_config", f"updated={len(applied)}", admin_id)
            return ApiResponse(success=True, data={
                "applied": applied,
                "count": len(applied),
            })
        except Exception as e:
            return ApiResponse(success=False, error=str(e))

    # ---- Action Log ----

    def get_action_log(self, limit: int = 50) -> ApiResponse:
        """获取管理操作日志"""
        return ApiResponse(success=True, data={
            "actions": self._action_log[-limit:],
            "total": len(self._action_log),
        })

    # ---- Aggregate Dashboard ----

    def get_dashboard(self) -> ApiResponse:
        """获取完整仪表板数据"""
        health = self.get_system_health()
        engine = self.get_engine_status()
        usage = self.get_usage_stats(days=7)

        user_data = None
        if self.db:
            try:
                users = self.db.get_users(limit=5)
                user_data = {
                    "recent_active": users,
                    "total": len(self.db.get_users(limit=10000)),
                }
            except Exception:
                pass

        return ApiResponse(success=True, data={
            "health": health.data if health.success else None,
            "engine": engine.data if engine.success else None,
            "usage": usage.data if usage.success else None,
            "users": user_data,
            "action_log_count": len(self._action_log),
        })
