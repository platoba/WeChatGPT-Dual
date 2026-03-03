"""
健康检查服务 - 引擎连通性、内存、运行时间
"""

import os
import time
import platform
from typing import Dict, Any, Optional


class HealthChecker:
    """
    系统健康监控
    - 引擎连通性检查
    - 内存使用
    - 运行时间
    - 依赖状态
    """

    def __init__(self, engine_manager=None, start_time: Optional[float] = None):
        self.engine_manager = engine_manager
        self.start_time = start_time or time.time()
        self._checks_run = 0
        self._last_check_time: Optional[float] = None

    def check_all(self) -> Dict[str, Any]:
        """运行所有健康检查"""
        self._checks_run += 1
        self._last_check_time = time.time()

        result = {
            "status": "healthy",
            "timestamp": time.time(),
            "uptime_seconds": round(time.time() - self.start_time, 1),
            "checks": {},
        }

        # 系统检查
        result["checks"]["system"] = self._check_system()

        # 引擎检查
        if self.engine_manager:
            result["checks"]["engines"] = self._check_engines()

        # 内存检查
        result["checks"]["memory"] = self._check_memory()

        # Python检查
        result["checks"]["runtime"] = self._check_runtime()

        # 汇总状态
        all_healthy = all(
            c.get("status") == "ok"
            for c in result["checks"].values()
        )
        result["status"] = "healthy" if all_healthy else "degraded"

        return result

    def _check_system(self) -> Dict[str, Any]:
        """系统信息"""
        return {
            "status": "ok",
            "platform": platform.system(),
            "arch": platform.machine(),
            "hostname": platform.node(),
            "pid": os.getpid(),
        }

    def _check_engines(self) -> Dict[str, Any]:
        """引擎连通性"""
        status = self.engine_manager.get_status()
        engines_ok = all(
            e.get("available", False)
            for e in status.get("engines", {}).values()
        )
        return {
            "status": "ok" if engines_ok else "degraded",
            "primary": status.get("primary", "unknown"),
            "secondary": status.get("secondary", "unknown"),
            "failover_enabled": status.get("failover_enabled", False),
            "failover_count": status.get("failover_count", 0),
            "engines": {
                name: {
                    "available": e.get("available", False),
                    "model": e.get("model", "unknown"),
                    "success_rate": e.get("success_rate", 0),
                }
                for name, e in status.get("engines", {}).items()
            },
        }

    def _check_memory(self) -> Dict[str, Any]:
        """内存使用检查"""
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            mem_mb = usage.ru_maxrss / 1024  # macOS returns bytes, Linux KB
            if platform.system() == "Linux":
                mem_mb = usage.ru_maxrss / 1024
            else:
                mem_mb = usage.ru_maxrss / (1024 * 1024)
            return {
                "status": "ok" if mem_mb < 512 else "warning",
                "rss_mb": round(mem_mb, 1),
            }
        except ImportError:
            return {"status": "ok", "rss_mb": 0, "note": "resource module not available"}

    def _check_runtime(self) -> Dict[str, Any]:
        """Python运行时信息"""
        return {
            "status": "ok",
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "checks_run": self._checks_run,
        }

    def get_summary(self) -> str:
        """获取简要健康摘要"""
        result = self.check_all()
        status_emoji = "🟢" if result["status"] == "healthy" else "🟡"
        uptime_h = result["uptime_seconds"] / 3600

        lines = [
            f"{status_emoji} *系统健康: {result['status']}*",
            f"运行时间: {uptime_h:.1f}h",
        ]

        for name, check in result["checks"].items():
            marker = "✅" if check.get("status") == "ok" else "⚠️"
            lines.append(f"  {marker} {name}")

        return "\n".join(lines)
