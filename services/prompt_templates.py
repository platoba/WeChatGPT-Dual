"""
Prompt模板服务 - 预定义AI角色/人设模板

Features:
- Built-in prompt templates (translator, coder, writer, analyst, etc.)
- Custom template CRUD with SQLite persistence
- Template categories and tags
- Template variables with {{placeholder}} support
- Import/export (JSON)
- Usage tracking per template
"""

import json
import re
import time
import sqlite3
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

TEMPLATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_templates (
    template_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'custom',
    system_prompt TEXT NOT NULL,
    variables TEXT DEFAULT '[]',
    tags TEXT DEFAULT '[]',
    is_builtin INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'system',
    use_count INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS user_active_template (
    user_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    variable_values TEXT DEFAULT '{}',
    activated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_template_category ON prompt_templates(category);
CREATE INDEX IF NOT EXISTS idx_template_builtin ON prompt_templates(is_builtin);
"""


# Built-in templates
BUILTIN_TEMPLATES = [
    {
        "template_id": "translator",
        "name": "翻译专家",
        "description": "Professional translator supporting multiple languages",
        "category": "language",
        "system_prompt": (
            "You are a professional translator. "
            "Translate the user's input from {{source_lang}} to {{target_lang}}. "
            "Maintain the original tone and style. Only output the translation, no explanations."
        ),
        "variables": ["source_lang", "target_lang"],
        "tags": ["translate", "language", "multilingual"],
    },
    {
        "template_id": "coder",
        "name": "代码助手",
        "description": "Senior software engineer for coding assistance",
        "category": "tech",
        "system_prompt": (
            "You are a senior software engineer specializing in {{language}}. "
            "Write clean, well-documented, production-ready code. "
            "Follow best practices: proper error handling, type hints, tests. "
            "Explain complex logic with concise comments."
        ),
        "variables": ["language"],
        "tags": ["code", "programming", "developer"],
    },
    {
        "template_id": "creative_writer",
        "name": "创意写手",
        "description": "Creative writer for stories, poems, and content",
        "category": "writing",
        "system_prompt": (
            "You are a talented creative writer. "
            "Write in a {{style}} style with vivid imagery and compelling narrative. "
            "Adapt your tone to the genre: {{genre}}. "
            "Be original and engaging."
        ),
        "variables": ["style", "genre"],
        "tags": ["writing", "creative", "story", "poetry"],
    },
    {
        "template_id": "data_analyst",
        "name": "数据分析师",
        "description": "Data analyst for insights and visualization advice",
        "category": "tech",
        "system_prompt": (
            "You are a senior data analyst. "
            "Analyze data thoroughly, identify patterns and anomalies. "
            "Provide actionable insights with clear reasoning. "
            "Suggest appropriate visualizations. "
            "Use statistical methods when relevant."
        ),
        "variables": [],
        "tags": ["data", "analytics", "statistics", "visualization"],
    },
    {
        "template_id": "tutor",
        "name": "AI导师",
        "description": "Patient teacher who explains concepts step by step",
        "category": "education",
        "system_prompt": (
            "You are a patient and knowledgeable tutor specializing in {{subject}}. "
            "Explain concepts step by step, from basics to advanced. "
            "Use analogies and examples. Check understanding before moving on. "
            "Adjust difficulty to the student's level: {{level}}."
        ),
        "variables": ["subject", "level"],
        "tags": ["education", "teaching", "learning", "tutorial"],
    },
    {
        "template_id": "copywriter",
        "name": "营销文案",
        "description": "Marketing copywriter for ads and product descriptions",
        "category": "marketing",
        "system_prompt": (
            "You are an expert marketing copywriter. "
            "Write persuasive, converting copy for {{platform}}. "
            "Use proven frameworks: AIDA, PAS, or storytelling. "
            "Keep it concise, impactful, and action-oriented. "
            "Target audience: {{audience}}."
        ),
        "variables": ["platform", "audience"],
        "tags": ["marketing", "copywriting", "ads", "conversion"],
    },
    {
        "template_id": "summarizer",
        "name": "摘要专家",
        "description": "Expert at summarizing long texts into key points",
        "category": "productivity",
        "system_prompt": (
            "You are an expert at summarizing information. "
            "Extract the key points from the user's input. "
            "Format: {{format}}. "
            "Be concise but don't miss critical details. "
            "Preserve the original meaning accurately."
        ),
        "variables": ["format"],
        "tags": ["summary", "tldr", "key-points", "digest"],
    },
    {
        "template_id": "debate_partner",
        "name": "辩论伙伴",
        "description": "Respectful debate partner who challenges your thinking",
        "category": "thinking",
        "system_prompt": (
            "You are a thoughtful debate partner. "
            "Challenge the user's arguments respectfully and constructively. "
            "Present counterarguments backed by evidence and logic. "
            "Acknowledge valid points. Help sharpen their thinking. "
            "Play {{role}}: advocate or devil's advocate as needed."
        ),
        "variables": ["role"],
        "tags": ["debate", "critical-thinking", "argumentation"],
    },
]


@dataclass
class PromptTemplate:
    """A prompt template"""

    template_id: str
    name: str
    description: str = ""
    category: str = "custom"
    system_prompt: str = ""
    variables: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    is_builtin: bool = False
    created_by: str = "system"
    use_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    def render(self, values: Optional[Dict[str, str]] = None) -> str:
        """
        Render the system prompt with variable substitution.

        Args:
            values: Dict of variable values to substitute

        Returns:
            Rendered prompt string
        """
        prompt = self.system_prompt
        if values:
            for key, val in values.items():
                prompt = prompt.replace(f"{{{{{key}}}}}", str(val))

        # Remove any unreplaced variables, replace with generic
        prompt = re.sub(r"\{\{(\w+)\}\}", r"[\1]", prompt)
        return prompt

    def get_required_variables(self) -> List[str]:
        """Extract variables still in the template"""
        return re.findall(r"\{\{(\w+)\}\}", self.system_prompt)

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


class PromptTemplateService:
    """
    Prompt template management service.

    Provides CRUD operations for prompt templates,
    template activation per user, and usage tracking.

    Usage:
        svc = PromptTemplateService("bot.db")
        svc.seed_builtins()
        tpl = svc.get("translator")
        rendered = tpl.render({"source_lang": "English", "target_lang": "Chinese"})
    """

    def __init__(self, db_path: str = "wechatgpt.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(TEMPLATE_SCHEMA)

    def seed_builtins(self):
        """Insert built-in templates (skip existing)"""
        now = time.time()
        with self._conn() as conn:
            for tpl in BUILTIN_TEMPLATES:
                existing = conn.execute(
                    "SELECT template_id FROM prompt_templates WHERE template_id = ?",
                    (tpl["template_id"],),
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO prompt_templates
                           (template_id, name, description, category, system_prompt,
                            variables, tags, is_builtin, created_by, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'system', ?, ?)""",
                        (
                            tpl["template_id"],
                            tpl["name"],
                            tpl["description"],
                            tpl["category"],
                            tpl["system_prompt"],
                            json.dumps(tpl["variables"]),
                            json.dumps(tpl["tags"]),
                            now,
                            now,
                        ),
                    )
        logger.info(f"Seeded {len(BUILTIN_TEMPLATES)} built-in templates")

    def get(self, template_id: str) -> Optional[PromptTemplate]:
        """Get a template by ID"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM prompt_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
        return self._row_to_template(row) if row else None

    def list_templates(
        self,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        builtin_only: bool = False,
        custom_only: bool = False,
    ) -> List[PromptTemplate]:
        """List templates with optional filters"""
        query = "SELECT * FROM prompt_templates WHERE 1=1"
        params = []

        if category:
            query += " AND category = ?"
            params.append(category)
        if builtin_only:
            query += " AND is_builtin = 1"
        if custom_only:
            query += " AND is_builtin = 0"

        query += " ORDER BY use_count DESC, name"

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()

        templates = [self._row_to_template(r) for r in rows]

        # Filter by tag if specified
        if tag:
            templates = [t for t in templates if tag in t.tags]

        return templates

    def create(
        self,
        template_id: str,
        name: str,
        system_prompt: str,
        description: str = "",
        category: str = "custom",
        tags: Optional[List[str]] = None,
        created_by: str = "user",
    ) -> PromptTemplate:
        """Create a custom template"""
        now = time.time()
        variables = re.findall(r"\{\{(\w+)\}\}", system_prompt)

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO prompt_templates
                   (template_id, name, description, category, system_prompt,
                    variables, tags, is_builtin, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (
                    template_id,
                    name,
                    description,
                    category,
                    system_prompt,
                    json.dumps(variables),
                    json.dumps(tags or []),
                    created_by,
                    now,
                    now,
                ),
            )

        logger.info(f"Created template: {template_id}")
        return self.get(template_id)

    def update(self, template_id: str, **kwargs) -> Optional[PromptTemplate]:
        """Update a template (cannot update builtins)"""
        tpl = self.get(template_id)
        if not tpl:
            return None
        if tpl.is_builtin:
            raise ValueError("Cannot modify built-in templates")

        now = time.time()
        updates = []
        params = []

        for key in ("name", "description", "category", "system_prompt"):
            if key in kwargs:
                updates.append(f"{key} = ?")
                params.append(kwargs[key])

        if "tags" in kwargs:
            updates.append("tags = ?")
            params.append(json.dumps(kwargs["tags"]))

        if "system_prompt" in kwargs:
            variables = re.findall(r"\{\{(\w+)\}\}", kwargs["system_prompt"])
            updates.append("variables = ?")
            params.append(json.dumps(variables))

        if not updates:
            return tpl

        updates.append("updated_at = ?")
        params.append(now)
        params.append(template_id)

        with self._conn() as conn:
            conn.execute(
                f"UPDATE prompt_templates SET {', '.join(updates)} WHERE template_id = ?",
                params,
            )

        return self.get(template_id)

    def delete(self, template_id: str) -> bool:
        """Delete a custom template (cannot delete builtins)"""
        tpl = self.get(template_id)
        if not tpl:
            return False
        if tpl.is_builtin:
            raise ValueError("Cannot delete built-in templates")

        with self._conn() as conn:
            conn.execute(
                "DELETE FROM prompt_templates WHERE template_id = ?",
                (template_id,),
            )
            # Also deactivate for users using it
            conn.execute(
                "DELETE FROM user_active_template WHERE template_id = ?",
                (template_id,),
            )
        return True

    def activate(
        self,
        user_id: str,
        template_id: str,
        variable_values: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """
        Activate a template for a user.

        Returns the rendered system prompt, or None if template not found.
        """
        tpl = self.get(template_id)
        if not tpl:
            return None

        now = time.time()

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_active_template (user_id, template_id, variable_values, activated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                    template_id=excluded.template_id,
                    variable_values=excluded.variable_values,
                    activated_at=excluded.activated_at""",
                (user_id, template_id, json.dumps(variable_values or {}), now),
            )

            # Increment use count
            conn.execute(
                "UPDATE prompt_templates SET use_count = use_count + 1 WHERE template_id = ?",
                (template_id,),
            )

        return tpl.render(variable_values)

    def deactivate(self, user_id: str) -> bool:
        """Deactivate template for a user (revert to default)"""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_active_template WHERE user_id = ?",
                (user_id,),
            )
            return cursor.rowcount > 0

    def get_active(self, user_id: str) -> Optional[Dict]:
        """Get user's active template and rendered prompt"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_active_template WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        if not row:
            return None

        tpl = self.get(row["template_id"])
        if not tpl:
            return None

        values = json.loads(row["variable_values"])
        return {
            "template": tpl.to_dict(),
            "variable_values": values,
            "rendered_prompt": tpl.render(values),
            "activated_at": row["activated_at"],
        }

    def get_categories(self) -> List[str]:
        """Get all unique categories"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM prompt_templates ORDER BY category"
            ).fetchall()
        return [row["category"] for row in rows]

    def search(self, query: str) -> List[PromptTemplate]:
        """Search templates by name, description, or tags"""
        query_lower = query.lower()
        all_templates = self.list_templates()
        results = []

        for tpl in all_templates:
            if (
                query_lower in tpl.name.lower()
                or query_lower in tpl.description.lower()
                or any(query_lower in tag.lower() for tag in tpl.tags)
            ):
                results.append(tpl)

        return results

    def export_templates(self, custom_only: bool = True) -> str:
        """Export templates as JSON"""
        templates = self.list_templates(custom_only=custom_only)
        data = [t.to_dict() for t in templates]
        return json.dumps(data, ensure_ascii=False, indent=2)

    def import_templates(self, json_str: str, created_by: str = "import") -> int:
        """Import templates from JSON string. Returns count of imported."""
        data = json.loads(json_str)
        count = 0

        for item in data:
            tid = item.get("template_id")
            if not tid:
                continue
            existing = self.get(tid)
            if existing:
                continue

            self.create(
                template_id=tid,
                name=item.get("name", tid),
                system_prompt=item.get("system_prompt", ""),
                description=item.get("description", ""),
                category=item.get("category", "imported"),
                tags=item.get("tags", []),
                created_by=created_by,
            )
            count += 1

        return count

    def get_popular(self, limit: int = 5) -> List[PromptTemplate]:
        """Get most used templates"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM prompt_templates ORDER BY use_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_template(r) for r in rows]

    @staticmethod
    def _row_to_template(row) -> PromptTemplate:
        return PromptTemplate(
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"],
            category=row["category"],
            system_prompt=row["system_prompt"],
            variables=json.loads(row["variables"]),
            tags=json.loads(row["tags"]),
            is_builtin=bool(row["is_builtin"]),
            created_by=row["created_by"],
            use_count=row["use_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
