"""Tests for services/backup.py"""

import os
import time
import pytest
import sqlite3
import tempfile
from services.backup import BackupService


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def backup_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db_with_messages(db_path):
    """Create database with sample messages"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            channel TEXT DEFAULT 'telegram',
            engine TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            latency REAL DEFAULT 0.0,
            created_at REAL NOT NULL
        );
    """)

    now = time.time()
    messages = [
        ("user1", "user", "Hello, how are you?", "telegram", now - 3600),
        ("user1", "assistant", "I'm doing great!", "telegram", now - 3590),
        ("user2", "user", "What is AI?", "wechat", now - 2000),
        ("user2", "assistant", "AI is artificial intelligence.", "wechat", now - 1990),
        ("user1", "user", "Tell me a joke", "telegram", now - 1000),
        ("user1", "assistant", "Why did the chicken cross the road?", "telegram", now - 990),
    ]

    for user_id, role, content, channel, created_at in messages:
        conn.execute(
            "INSERT INTO messages (user_id, role, content, channel, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, role, content, channel, created_at),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def backup(db_with_messages):
    return BackupService(db_with_messages)


class TestBackupService:
    def test_export_json(self, backup, backup_dir):
        manifest = backup.export_json(output_dir=backup_dir)
        assert manifest.format == "json"
        assert manifest.total_messages == 6
        assert manifest.total_users == 2
        assert os.path.isfile(manifest.file_path)
        assert manifest.size_bytes > 0

    def test_export_json_filter_user(self, backup, backup_dir):
        manifest = backup.export_json(output_dir=backup_dir, user_id="user1")
        assert manifest.total_messages == 4
        assert manifest.total_users == 1

    def test_export_json_filter_channel(self, backup, backup_dir):
        manifest = backup.export_json(output_dir=backup_dir, channel="wechat")
        assert manifest.total_messages == 2

    def test_export_markdown(self, backup, backup_dir):
        manifest = backup.export_markdown(output_dir=backup_dir)
        assert manifest.format == "markdown"
        assert manifest.total_messages == 6
        assert os.path.isfile(manifest.file_path)

        with open(manifest.file_path, "r") as f:
            content = f.read()
        assert "Conversation Backup" in content
        assert "Hello" in content

    def test_export_html(self, backup, backup_dir):
        manifest = backup.export_html(output_dir=backup_dir)
        assert manifest.format == "html"
        assert manifest.total_messages == 6
        assert os.path.isfile(manifest.file_path)

        with open(manifest.file_path, "r") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "Hello" in content

    def test_restore_json(self, backup, backup_dir, db_path):
        # Export first
        manifest = backup.export_json(output_dir=backup_dir)

        # Create new empty DB
        fd, new_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(new_db)
            conn.executescript("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    channel TEXT DEFAULT 'telegram',
                    engine TEXT DEFAULT '',
                    tokens_used INTEGER DEFAULT 0,
                    latency REAL DEFAULT 0.0,
                    created_at REAL NOT NULL
                );
            """)
            conn.close()

            new_backup = BackupService(new_db)
            result = new_backup.restore_json(manifest.file_path)
            assert result["restored"] == 6
            assert result["skipped"] == 0
        finally:
            os.unlink(new_db)

    def test_restore_overwrite(self, backup, backup_dir):
        manifest = backup.export_json(output_dir=backup_dir)
        result = backup.restore_json(manifest.file_path, overwrite=True)
        assert result["restored"] == 6

    def test_list_backups(self, backup, backup_dir):
        backup.export_json(output_dir=backup_dir)
        backup.export_markdown(output_dir=backup_dir)

        backups = backup.list_backups(backup_dir)
        assert len(backups) >= 2
        formats = [b["format"] for b in backups]
        assert "json" in formats
        assert "md" in formats

    def test_list_backups_empty(self, backup):
        backups = backup.list_backups("/nonexistent_dir_xyz")
        assert backups == []

    def test_manifest_to_dict(self, backup, backup_dir):
        manifest = backup.export_json(output_dir=backup_dir)
        d = manifest.to_dict()
        assert d["format"] == "json"
        assert d["total_messages"] == 6
        assert "checksum" in d
        assert "backup_id" in d

    def test_export_date_range(self, backup, backup_dir):
        now = time.time()
        manifest = backup.export_json(
            output_dir=backup_dir,
            since=now - 1500,
            until=now,
        )
        # Should only get messages within last 1500 seconds
        assert manifest.total_messages < 6

    def test_html_escaping(self, backup_dir):
        """Test that HTML special chars are escaped"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    channel TEXT DEFAULT 'telegram',
                    engine TEXT DEFAULT '',
                    tokens_used INTEGER DEFAULT 0,
                    latency REAL DEFAULT 0.0,
                    created_at REAL NOT NULL
                );
            """)
            conn.execute(
                "INSERT INTO messages (user_id, role, content, channel, created_at) VALUES (?, ?, ?, ?, ?)",
                ("u1", "user", "<script>alert('xss')</script>", "telegram", time.time()),
            )
            conn.commit()
            conn.close()

            svc = BackupService(path)
            manifest = svc.export_html(output_dir=backup_dir)

            with open(manifest.file_path, "r") as f:
                content = f.read()
            assert "<script>" not in content
            assert "&lt;script&gt;" in content
        finally:
            os.unlink(path)

    def test_empty_db_export(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT, role TEXT, content TEXT,
                    channel TEXT, engine TEXT, tokens_used INTEGER,
                    latency REAL, created_at REAL
                );
            """)
            conn.close()

            d = tempfile.mkdtemp()
            svc = BackupService(path)
            manifest = svc.export_json(output_dir=d)
            assert manifest.total_messages == 0

            import shutil
            shutil.rmtree(d, ignore_errors=True)
        finally:
            os.unlink(path)
