"""Tests for DocumentLoader"""

import json
import pytest
from knowledge.loader import DocumentLoader


class TestLoadFile:
    def test_load_txt(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("Hello World", encoding="utf-8")
        content, source = DocumentLoader.load_file(str(f))
        assert content == "Hello World"
        assert source == "test.txt"

    def test_load_md(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Title\nContent", encoding="utf-8")
        content, source = DocumentLoader.load_file(str(f))
        assert "# Title" in content
        assert source == "readme.md"

    def test_load_json(self, tmp_path):
        f = tmp_path / "data.json"
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        f.write_text(json.dumps(data), encoding="utf-8")
        content, source = DocumentLoader.load_file(str(f))
        assert "Alice" in content
        assert "Bob" in content

    def test_load_json_dict(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        content, _ = DocumentLoader.load_file(str(f))
        assert "key: value" in content

    def test_load_csv(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,age\nAlice,30\nBob,25", encoding="utf-8")
        content, source = DocumentLoader.load_file(str(f))
        assert "Alice" in content
        assert source == "data.csv"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            DocumentLoader.load_file("/nonexistent/file.txt")

    def test_unsupported_format(self, tmp_path):
        f = tmp_path / "test.docx"
        f.write_text("test")
        with pytest.raises(ValueError, match="Unsupported"):
            DocumentLoader.load_file(str(f))


class TestLoadText:
    def test_load_text(self):
        content, source = DocumentLoader.load_text("Direct input text")
        assert content == "Direct input text"
        assert source == "direct_input"

    def test_load_text_custom_source(self):
        content, source = DocumentLoader.load_text("Text", source="custom")
        assert source == "custom"


class TestLoadDirectory:
    def test_load_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("File A")
        (tmp_path / "b.md").write_text("File B")
        (tmp_path / "c.pdf").write_text("Skip me")

        results = DocumentLoader.load_directory(str(tmp_path))
        assert len(results) == 2
        contents = [r[0] for r in results]
        assert "File A" in contents
        assert "File B" in contents

    def test_load_empty_directory(self, tmp_path):
        results = DocumentLoader.load_directory(str(tmp_path))
        assert results == []

    def test_not_a_directory(self):
        with pytest.raises(NotADirectoryError):
            DocumentLoader.load_directory("/nonexistent/dir")
