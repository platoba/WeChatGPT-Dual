"""
文档加载器 - 支持多种文档格式导入
"""

import os
from typing import List, Tuple


class DocumentLoader:
    """
    文档加载器
    支持: .txt, .md, .json, .csv
    """

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".csv"}

    @classmethod
    def load_file(cls, file_path: str) -> Tuple[str, str]:
        """
        加载单个文件

        Args:
            file_path: 文件路径

        Returns:
            (content, source_name)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported format: {ext}. "
                f"Supported: {cls.SUPPORTED_EXTENSIONS}"
            )

        source = os.path.basename(file_path)

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if ext == ".json":
            content = cls._parse_json(content)
        elif ext == ".csv":
            content = cls._parse_csv(content)

        return content, source

    @classmethod
    def load_text(cls, text: str, source: str = "direct_input") -> Tuple[str, str]:
        """直接加载文本"""
        return text, source

    @classmethod
    def load_directory(
        cls, dir_path: str
    ) -> List[Tuple[str, str]]:
        """
        加载目录中所有支持的文件

        Returns:
            [(content, source), ...]
        """
        results = []
        if not os.path.isdir(dir_path):
            raise NotADirectoryError(f"Not a directory: {dir_path}")

        for filename in sorted(os.listdir(dir_path)):
            ext = os.path.splitext(filename)[1].lower()
            if ext in cls.SUPPORTED_EXTENSIONS:
                filepath = os.path.join(dir_path, filename)
                try:
                    content, source = cls.load_file(filepath)
                    results.append((content, source))
                except Exception:
                    continue

        return results

    @classmethod
    def _parse_json(cls, content: str) -> str:
        """将JSON转为可读文本"""
        import json

        try:
            data = json.loads(content)
            if isinstance(data, list):
                parts = []
                for item in data:
                    if isinstance(item, dict):
                        parts.append(
                            "\n".join(
                                f"{k}: {v}"
                                for k, v in item.items()
                            )
                        )
                    else:
                        parts.append(str(item))
                return "\n\n".join(parts)
            elif isinstance(data, dict):
                return "\n".join(
                    f"{k}: {v}" for k, v in data.items()
                )
            return str(data)
        except json.JSONDecodeError:
            return content

    @classmethod
    def _parse_csv(cls, content: str) -> str:
        """将CSV转为可读文本"""
        lines = content.strip().split("\n")
        if not lines:
            return content

        # 假设第一行是header
        header = lines[0].split(",")
        parts = []
        for line in lines[1:]:
            values = line.split(",")
            row = []
            for i, val in enumerate(values):
                key = header[i].strip() if i < len(header) else f"col{i}"
                row.append(f"{key}: {val.strip()}")
            parts.append(", ".join(row))

        return "\n".join(parts)
