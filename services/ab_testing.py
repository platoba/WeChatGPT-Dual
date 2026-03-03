"""
A/B 测试服务 - Prompt变体实验 + 用户反馈 + 自动评估 + 统计显著性检验

支持:
- Prompt A/B 测试 (不同提示词 → 对比用户满意度)
- 模型 A/B 测试 (GPT-4 vs Claude → 对比响应质量)
- 多变体测试 (A/B/C/D...)
- 流量分配 (均匀/加权)
- 用户反馈收集 (👍/👎 + 评分)
- 统计显著性检验 (Z-test)
- 实验报告生成
- SQLite持久化
"""

import math
import time
import json
import random
import hashlib
import sqlite3
import logging
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)


class ExperimentStatus(Enum):
    """实验状态"""
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABORTED = "aborted"


class VariantType(Enum):
    """变体类型"""
    PROMPT = "prompt"        # 提示词变体
    MODEL = "model"          # 模型变体
    TEMPERATURE = "temperature"  # 温度变体
    SYSTEM_PROMPT = "system_prompt"  # 系统提示词
    CUSTOM = "custom"        # 自定义参数


class FeedbackType(Enum):
    """反馈类型"""
    THUMBS = "thumbs"    # 👍/👎
    RATING = "rating"    # 1-5 评分
    CHOICE = "choice"    # A/B 选择


@dataclass
class Variant:
    """实验变体"""
    id: str = ""
    name: str = ""
    variant_type: VariantType = VariantType.PROMPT
    value: str = ""
    weight: float = 1.0
    params: Dict[str, Any] = field(default_factory=dict)

    # 运行时统计
    impressions: int = 0
    positive_feedback: int = 0
    negative_feedback: int = 0
    total_rating: float = 0.0
    rating_count: int = 0

    @property
    def feedback_rate(self) -> float:
        """正面反馈率"""
        total = self.positive_feedback + self.negative_feedback
        return self.positive_feedback / total if total > 0 else 0.0

    @property
    def average_rating(self) -> float:
        """平均评分"""
        return self.total_rating / self.rating_count if self.rating_count > 0 else 0.0


@dataclass
class Experiment:
    """A/B 实验"""
    id: str = ""
    name: str = ""
    description: str = ""
    status: ExperimentStatus = ExperimentStatus.DRAFT
    variants: List[Variant] = field(default_factory=list)

    # 配置
    min_samples: int = 30         # 最小样本量
    confidence_level: float = 0.95  # 置信水平
    max_duration_hours: int = 168   # 最长运行时间 (7天)
    auto_stop: bool = True         # 达到显著性自动停止

    # 时间
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0

    # 结果
    winner: str = ""
    significance: float = 0.0

    # 元数据
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class TrafficAllocator:
    """流量分配器"""

    @staticmethod
    def uniform_random(variants: List[Variant]) -> Variant:
        """均匀随机分配"""
        if not variants:
            raise ValueError("No variants")
        return random.choice(variants)

    @staticmethod
    def weighted_random(variants: List[Variant]) -> Variant:
        """加权随机分配"""
        if not variants:
            raise ValueError("No variants")
        weights = [v.weight for v in variants]
        total = sum(weights)
        r = random.uniform(0, total)
        cumulative = 0
        for variant in variants:
            cumulative += variant.weight
            if r <= cumulative:
                return variant
        return variants[-1]

    @staticmethod
    def user_sticky(user_id: str, variants: List[Variant]) -> Variant:
        """用户粘性分配 (同一用户总是看到同一变体)"""
        if not variants:
            raise ValueError("No variants")
        hash_val = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
        index = hash_val % len(variants)
        return variants[index]


class StatisticalTest:
    """统计显著性检验"""

    @staticmethod
    def z_test_proportions(
        successes_a: int, total_a: int,
        successes_b: int, total_b: int,
    ) -> Tuple[float, float, bool]:
        """
        双比例Z检验

        Returns:
            (z_score, p_value, is_significant)
        """
        if total_a == 0 or total_b == 0:
            return 0.0, 1.0, False

        p_a = successes_a / total_a
        p_b = successes_b / total_b

        # 合并比例
        p_pool = (successes_a + successes_b) / (total_a + total_b)

        if p_pool == 0 or p_pool == 1:
            return 0.0, 1.0, False

        # 标准误差
        se = math.sqrt(p_pool * (1 - p_pool) * (1/total_a + 1/total_b))

        if se == 0:
            return 0.0, 1.0, False

        z = (p_a - p_b) / se

        # 近似p值 (双尾)
        p_value = StatisticalTest._normal_cdf(-abs(z)) * 2

        return z, p_value, p_value < 0.05

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """标准正态分布CDF (近似)"""
        # Abramowitz and Stegun approximation
        a1 = 0.254829592
        a2 = -0.284496736
        a3 = 1.421413741
        a4 = -1.453152027
        a5 = 1.061405429
        p = 0.3275911

        sign = 1 if x >= 0 else -1
        x = abs(x) / math.sqrt(2)
        t = 1.0 / (1.0 + p * x)
        y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
        return 0.5 * (1.0 + sign * y)

    @staticmethod
    def confidence_interval(
        successes: int, total: int, confidence: float = 0.95
    ) -> Tuple[float, float]:
        """Wilson score 置信区间"""
        if total == 0:
            return 0.0, 0.0

        z = StatisticalTest._z_score(confidence)
        p_hat = successes / total
        denominator = 1 + z * z / total
        centre = p_hat + z * z / (2 * total)
        spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * total)) / total)

        lower = (centre - spread) / denominator
        upper = (centre + spread) / denominator

        return max(0, lower), min(1, upper)

    @staticmethod
    def _z_score(confidence: float) -> float:
        """置信水平 → Z分数"""
        z_map = {
            0.90: 1.645,
            0.95: 1.960,
            0.99: 2.576,
        }
        return z_map.get(confidence, 1.960)

    @staticmethod
    def sample_size_needed(
        baseline_rate: float, mde: float, confidence: float = 0.95, power: float = 0.80
    ) -> int:
        """
        计算所需样本量

        Args:
            baseline_rate: 基线转化率
            mde: 最小可检测效应 (绝对值)
            confidence: 置信水平
            power: 检验效能

        Returns:
            每组所需样本量
        """
        z_alpha = StatisticalTest._z_score(confidence)
        z_beta = 0.842  # power=0.80 → 0.842
        if power >= 0.90:
            z_beta = 1.282

        p1 = baseline_rate
        p2 = baseline_rate + mde
        p_avg = (p1 + p2) / 2

        numerator = (z_alpha * math.sqrt(2 * p_avg * (1 - p_avg)) +
                     z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
        denominator = (p1 - p2) ** 2

        if denominator == 0:
            return 0

        return math.ceil(numerator / denominator)


class ABTestStore:
    """A/B 测试存储 (SQLite)"""

    def __init__(self, db_path: str = "ab_tests.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'draft',
                    min_samples INTEGER DEFAULT 30,
                    confidence_level REAL DEFAULT 0.95,
                    max_duration_hours INTEGER DEFAULT 168,
                    auto_stop INTEGER DEFAULT 1,
                    created_at REAL NOT NULL,
                    started_at REAL DEFAULT 0,
                    completed_at REAL DEFAULT 0,
                    winner TEXT DEFAULT '',
                    significance REAL DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS variants (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    variant_type TEXT DEFAULT 'prompt',
                    value TEXT DEFAULT '',
                    weight REAL DEFAULT 1.0,
                    params TEXT DEFAULT '{}',
                    impressions INTEGER DEFAULT 0,
                    positive_feedback INTEGER DEFAULT 0,
                    negative_feedback INTEGER DEFAULT 0,
                    total_rating REAL DEFAULT 0,
                    rating_count INTEGER DEFAULT 0,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    assigned_at REAL NOT NULL,
                    UNIQUE(experiment_id, user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    feedback_type TEXT NOT NULL,
                    value TEXT NOT NULL,
                    message_id TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_variants_exp
                ON variants(experiment_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_assignments_exp_user
                ON assignments(experiment_id, user_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_exp_variant
                ON feedback(experiment_id, variant_id)
            """)

    def save_experiment(self, exp: Experiment) -> str:
        """保存实验"""
        if not exp.id:
            exp.id = hashlib.sha256(
                f"{exp.name}:{time.time()}".encode()
            ).hexdigest()[:12]
        if not exp.created_at:
            exp.created_at = time.time()

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO experiments
                    (id, name, description, status, min_samples, confidence_level,
                     max_duration_hours, auto_stop, created_at, started_at, completed_at,
                     winner, significance, tags, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (exp.id, exp.name, exp.description, exp.status.value,
                     exp.min_samples, exp.confidence_level, exp.max_duration_hours,
                     1 if exp.auto_stop else 0, exp.created_at, exp.started_at,
                     exp.completed_at, exp.winner, exp.significance,
                     json.dumps(exp.tags), json.dumps(exp.metadata)),
                )

                # 保存变体
                for v in exp.variants:
                    if not v.id:
                        v.id = hashlib.sha256(
                            f"{exp.id}:{v.name}:{time.time()}".encode()
                        ).hexdigest()[:12]
                    conn.execute(
                        """INSERT OR REPLACE INTO variants
                        (id, experiment_id, name, variant_type, value, weight, params,
                         impressions, positive_feedback, negative_feedback,
                         total_rating, rating_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (v.id, exp.id, v.name, v.variant_type.value, v.value,
                         v.weight, json.dumps(v.params), v.impressions,
                         v.positive_feedback, v.negative_feedback,
                         v.total_rating, v.rating_count),
                    )

        return exp.id

    def get_experiment(self, exp_id: str) -> Optional[Experiment]:
        """获取实验"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                row = conn.execute(
                    "SELECT * FROM experiments WHERE id = ?", (exp_id,)
                ).fetchone()
                if not row:
                    return None

                exp = Experiment(
                    id=row["id"],
                    name=row["name"],
                    description=row["description"],
                    status=ExperimentStatus(row["status"]),
                    min_samples=row["min_samples"],
                    confidence_level=row["confidence_level"],
                    max_duration_hours=row["max_duration_hours"],
                    auto_stop=bool(row["auto_stop"]),
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                    winner=row["winner"],
                    significance=row["significance"],
                    tags=json.loads(row["tags"]),
                    metadata=json.loads(row["metadata"]),
                )

                # 加载变体
                vrows = conn.execute(
                    "SELECT * FROM variants WHERE experiment_id = ?", (exp_id,)
                ).fetchall()
                for vr in vrows:
                    exp.variants.append(Variant(
                        id=vr["id"],
                        name=vr["name"],
                        variant_type=VariantType(vr["variant_type"]),
                        value=vr["value"],
                        weight=vr["weight"],
                        params=json.loads(vr["params"]),
                        impressions=vr["impressions"],
                        positive_feedback=vr["positive_feedback"],
                        negative_feedback=vr["negative_feedback"],
                        total_rating=vr["total_rating"],
                        rating_count=vr["rating_count"],
                    ))

                return exp

    def list_experiments(self, status: Optional[str] = None) -> List[Experiment]:
        """列出实验"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                if status:
                    rows = conn.execute(
                        "SELECT id FROM experiments WHERE status = ? ORDER BY created_at DESC",
                        (status,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id FROM experiments ORDER BY created_at DESC"
                    ).fetchall()

        return [self.get_experiment(r["id"]) for r in rows if self.get_experiment(r["id"])]

    def record_assignment(self, exp_id: str, variant_id: str, user_id: str):
        """记录用户分配"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                try:
                    conn.execute(
                        """INSERT INTO assignments (experiment_id, variant_id, user_id, assigned_at)
                        VALUES (?, ?, ?, ?)""",
                        (exp_id, variant_id, user_id, time.time()),
                    )
                except sqlite3.IntegrityError:
                    pass  # 已分配

                # 更新impression
                conn.execute(
                    "UPDATE variants SET impressions = impressions + 1 WHERE id = ?",
                    (variant_id,),
                )

    def get_user_assignment(self, exp_id: str, user_id: str) -> Optional[str]:
        """获取用户已分配的变体"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT variant_id FROM assignments WHERE experiment_id = ? AND user_id = ?",
                    (exp_id, user_id),
                ).fetchone()
                return row[0] if row else None

    def record_feedback(
        self, exp_id: str, variant_id: str, user_id: str,
        feedback_type: FeedbackType, value: str, message_id: str = ""
    ):
        """记录反馈"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO feedback
                    (experiment_id, variant_id, user_id, feedback_type, value, message_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (exp_id, variant_id, user_id, feedback_type.value, value, message_id, time.time()),
                )

                # 更新变体统计
                if feedback_type == FeedbackType.THUMBS:
                    if value == "up":
                        conn.execute(
                            "UPDATE variants SET positive_feedback = positive_feedback + 1 WHERE id = ?",
                            (variant_id,),
                        )
                    else:
                        conn.execute(
                            "UPDATE variants SET negative_feedback = negative_feedback + 1 WHERE id = ?",
                            (variant_id,),
                        )
                elif feedback_type == FeedbackType.RATING:
                    rating = float(value)
                    conn.execute(
                        """UPDATE variants SET total_rating = total_rating + ?,
                        rating_count = rating_count + 1 WHERE id = ?""",
                        (rating, variant_id),
                    )
                    if rating >= 4:
                        conn.execute(
                            "UPDATE variants SET positive_feedback = positive_feedback + 1 WHERE id = ?",
                            (variant_id,),
                        )
                    elif rating <= 2:
                        conn.execute(
                            "UPDATE variants SET negative_feedback = negative_feedback + 1 WHERE id = ?",
                            (variant_id,),
                        )

    def update_experiment_status(self, exp_id: str, status: ExperimentStatus,
                                  winner: str = "", significance: float = 0.0):
        """更新实验状态"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                now = time.time()
                conn.execute(
                    """UPDATE experiments SET status = ?, winner = ?, significance = ?,
                    completed_at = CASE WHEN ? IN ('completed', 'aborted') THEN ? ELSE completed_at END,
                    started_at = CASE WHEN ? = 'running' AND started_at = 0 THEN ? ELSE started_at END
                    WHERE id = ?""",
                    (status.value, winner, significance,
                     status.value, now,
                     status.value, now, exp_id),
                )


class ABTestService:
    """
    A/B 测试服务

    用于:
    - 创建和管理实验
    - 分配用户到变体
    - 收集反馈
    - 分析结果
    - 生成报告
    """

    def __init__(self, store: Optional[ABTestStore] = None, db_path: str = "ab_tests.db"):
        self.store = store or ABTestStore(db_path)
        self.stats_engine = StatisticalTest()
        self._lock = threading.Lock()

    def create_experiment(
        self, name: str, variants: List[Dict[str, Any]],
        description: str = "", min_samples: int = 30,
        confidence_level: float = 0.95, auto_start: bool = False,
        **kwargs
    ) -> Experiment:
        """
        创建实验

        Args:
            name: 实验名称
            variants: 变体列表 [{"name": "A", "type": "prompt", "value": "..."}]
            description: 描述
            min_samples: 最小样本量
            auto_start: 是否自动开始

        Returns:
            Experiment
        """
        if len(variants) < 2:
            raise ValueError("Need at least 2 variants")

        variant_objects = []
        for v in variants:
            variant_objects.append(Variant(
                name=v.get("name", f"Variant {len(variant_objects)}"),
                variant_type=VariantType(v.get("type", "prompt")),
                value=v.get("value", ""),
                weight=v.get("weight", 1.0),
                params=v.get("params", {}),
            ))

        exp = Experiment(
            name=name,
            description=description,
            variants=variant_objects,
            min_samples=min_samples,
            confidence_level=confidence_level,
            status=ExperimentStatus.RUNNING if auto_start else ExperimentStatus.DRAFT,
            **kwargs,
        )

        if auto_start:
            exp.started_at = time.time()

        self.store.save_experiment(exp)
        logger.info(f"Created experiment: {exp.id} ({name})")
        return exp

    def start_experiment(self, exp_id: str) -> bool:
        """开始实验"""
        exp = self.store.get_experiment(exp_id)
        if not exp:
            return False
        if exp.status != ExperimentStatus.DRAFT:
            return False

        self.store.update_experiment_status(exp_id, ExperimentStatus.RUNNING)
        return True

    def pause_experiment(self, exp_id: str) -> bool:
        """暂停实验"""
        exp = self.store.get_experiment(exp_id)
        if not exp or exp.status != ExperimentStatus.RUNNING:
            return False
        self.store.update_experiment_status(exp_id, ExperimentStatus.PAUSED)
        return True

    def resume_experiment(self, exp_id: str) -> bool:
        """恢复实验"""
        exp = self.store.get_experiment(exp_id)
        if not exp or exp.status != ExperimentStatus.PAUSED:
            return False
        self.store.update_experiment_status(exp_id, ExperimentStatus.RUNNING)
        return True

    def abort_experiment(self, exp_id: str) -> bool:
        """终止实验"""
        exp = self.store.get_experiment(exp_id)
        if not exp:
            return False
        self.store.update_experiment_status(exp_id, ExperimentStatus.ABORTED)
        return True

    def assign_variant(
        self, exp_id: str, user_id: str,
        sticky: bool = True
    ) -> Optional[Variant]:
        """
        为用户分配变体

        Args:
            exp_id: 实验ID
            user_id: 用户ID
            sticky: 是否粘性分配

        Returns:
            分配的变体
        """
        exp = self.store.get_experiment(exp_id)
        if not exp or exp.status != ExperimentStatus.RUNNING:
            return None

        if not exp.variants:
            return None

        # 检查已有分配
        if sticky:
            existing = self.store.get_user_assignment(exp_id, user_id)
            if existing:
                for v in exp.variants:
                    if v.id == existing:
                        return v

        # 分配新变体
        variant = TrafficAllocator.user_sticky(user_id, exp.variants) if sticky \
            else TrafficAllocator.weighted_random(exp.variants)

        self.store.record_assignment(exp_id, variant.id, user_id)
        return variant

    def record_feedback(
        self, exp_id: str, user_id: str,
        feedback_type: FeedbackType, value: str,
        variant_id: Optional[str] = None, message_id: str = ""
    ) -> bool:
        """记录用户反馈"""
        if not variant_id:
            variant_id = self.store.get_user_assignment(exp_id, user_id)
        if not variant_id:
            return False

        self.store.record_feedback(
            exp_id, variant_id, user_id, feedback_type, value, message_id
        )

        # 检查是否应该自动停止
        exp = self.store.get_experiment(exp_id)
        if exp and exp.auto_stop and exp.status == ExperimentStatus.RUNNING:
            self._check_auto_stop(exp)

        return True

    def _check_auto_stop(self, exp: Experiment):
        """检查是否满足自动停止条件"""
        if len(exp.variants) < 2:
            return

        # 检查最小样本量
        min_impressions = min(v.impressions for v in exp.variants)
        if min_impressions < exp.min_samples:
            return

        # 检查最大时长
        if exp.max_duration_hours > 0:
            elapsed = (time.time() - exp.started_at) / 3600
            if elapsed > exp.max_duration_hours:
                result = self.analyze(exp.id)
                winner = result.get("winner", "")
                sig = result.get("significance", 0)
                self.store.update_experiment_status(
                    exp.id, ExperimentStatus.COMPLETED, winner, sig
                )
                return

        # 两两比较检查显著性
        for i, va in enumerate(exp.variants):
            for vb in exp.variants[i+1:]:
                total_a = va.positive_feedback + va.negative_feedback
                total_b = vb.positive_feedback + vb.negative_feedback
                if total_a >= exp.min_samples and total_b >= exp.min_samples:
                    _, p_val, significant = StatisticalTest.z_test_proportions(
                        va.positive_feedback, total_a,
                        vb.positive_feedback, total_b,
                    )
                    if significant:
                        winner = va.name if va.feedback_rate > vb.feedback_rate else vb.name
                        self.store.update_experiment_status(
                            exp.id, ExperimentStatus.COMPLETED, winner, 1 - p_val
                        )
                        logger.info(f"Experiment {exp.id} auto-stopped: winner={winner}")
                        return

    def analyze(self, exp_id: str) -> Dict[str, Any]:
        """
        分析实验结果

        Returns:
            {
                "experiment": {...},
                "variants": [...],
                "comparisons": [...],
                "winner": "...",
                "significance": 0.0,
                "recommendation": "..."
            }
        """
        exp = self.store.get_experiment(exp_id)
        if not exp:
            return {"error": "Experiment not found"}

        result = {
            "experiment": {
                "id": exp.id,
                "name": exp.name,
                "status": exp.status.value,
                "created_at": exp.created_at,
                "started_at": exp.started_at,
            },
            "variants": [],
            "comparisons": [],
            "winner": "",
            "significance": 0.0,
            "recommendation": "",
        }

        # 变体统计
        for v in exp.variants:
            total_fb = v.positive_feedback + v.negative_feedback
            ci_low, ci_high = StatisticalTest.confidence_interval(
                v.positive_feedback, total_fb, exp.confidence_level
            ) if total_fb > 0 else (0.0, 0.0)

            result["variants"].append({
                "id": v.id,
                "name": v.name,
                "type": v.variant_type.value,
                "impressions": v.impressions,
                "positive": v.positive_feedback,
                "negative": v.negative_feedback,
                "feedback_rate": round(v.feedback_rate * 100, 2),
                "average_rating": round(v.average_rating, 2),
                "ci_lower": round(ci_low * 100, 2),
                "ci_upper": round(ci_high * 100, 2),
            })

        # 两两比较
        best_winner = ""
        best_sig = 0.0

        for i, va in enumerate(exp.variants):
            for j, vb in enumerate(exp.variants[i+1:], i+1):
                total_a = va.positive_feedback + va.negative_feedback
                total_b = vb.positive_feedback + vb.negative_feedback

                if total_a > 0 and total_b > 0:
                    z, p_val, significant = StatisticalTest.z_test_proportions(
                        va.positive_feedback, total_a,
                        vb.positive_feedback, total_b,
                    )
                    # 相对提升
                    if vb.feedback_rate > 0:
                        lift = (va.feedback_rate - vb.feedback_rate) / vb.feedback_rate
                    else:
                        lift = 0.0

                    comparison = {
                        "variant_a": va.name,
                        "variant_b": vb.name,
                        "z_score": round(z, 4),
                        "p_value": round(p_val, 6),
                        "significant": significant,
                        "lift": round(lift * 100, 2),
                        "winner": va.name if va.feedback_rate > vb.feedback_rate else vb.name,
                    }
                    result["comparisons"].append(comparison)

                    if significant:
                        winner = va.name if va.feedback_rate > vb.feedback_rate else vb.name
                        sig = 1 - p_val
                        if sig > best_sig:
                            best_sig = sig
                            best_winner = winner

        result["winner"] = best_winner
        result["significance"] = round(best_sig, 4)

        # 推荐
        if best_winner:
            result["recommendation"] = f"变体 '{best_winner}' 显著优于其他变体 (置信度 {best_sig*100:.1f}%)"
        elif all(v.impressions >= exp.min_samples for v in exp.variants):
            result["recommendation"] = "样本量充足但未达到统计显著性，各变体表现相似"
        else:
            result["recommendation"] = "样本量不足，需要继续收集数据"

        return result

    def generate_report(self, exp_id: str, format: str = "text") -> str:
        """生成实验报告"""
        analysis = self.analyze(exp_id)
        if "error" in analysis:
            return f"Error: {analysis['error']}"

        exp_info = analysis["experiment"]
        lines = [
            "═══ A/B Test Report ═══",
            f"实验: {exp_info['name']} ({exp_info['id']})",
            f"状态: {exp_info['status']}",
            "",
            "─── 变体统计 ───",
        ]

        for v in analysis["variants"]:
            lines.extend([
                f"  [{v['name']}] ({v['type']})",
                f"    曝光: {v['impressions']}",
                f"    正面: {v['positive']} | 负面: {v['negative']}",
                f"    正面率: {v['feedback_rate']}% [{v['ci_lower']}%, {v['ci_upper']}%]",
                f"    平均评分: {v['average_rating']}",
            ])

        if analysis["comparisons"]:
            lines.extend(["", "─── 显著性检验 ───"])
            for c in analysis["comparisons"]:
                sig_mark = "✅" if c["significant"] else "❌"
                lines.append(
                    f"  {c['variant_a']} vs {c['variant_b']}: "
                    f"z={c['z_score']}, p={c['p_value']}, lift={c['lift']}% {sig_mark}"
                )

        lines.extend([
            "",
            "─── 结论 ───",
            f"  胜出: {analysis['winner'] or '暂无'}",
            f"  置信度: {analysis['significance'] * 100:.1f}%",
            f"  建议: {analysis['recommendation']}",
        ])

        return "\n".join(lines)

    def get_active_experiments(self) -> List[Experiment]:
        """获取所有运行中的实验"""
        return self.store.list_experiments("running")

    def get_stats(self) -> Dict[str, Any]:
        """统计概览"""
        all_exps = self.store.list_experiments()
        status_counts = defaultdict(int)
        total_impressions = 0
        total_feedback = 0

        for exp in all_exps:
            status_counts[exp.status.value] += 1
            for v in exp.variants:
                total_impressions += v.impressions
                total_feedback += v.positive_feedback + v.negative_feedback

        return {
            "total_experiments": len(all_exps),
            "by_status": dict(status_counts),
            "total_impressions": total_impressions,
            "total_feedback": total_feedback,
        }
