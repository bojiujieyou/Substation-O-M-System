# test_import.py — 导入流程测试
import os
import sys
import pytest
import tempfile
import shutil

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from init_db import init_db, get_db_path
from config import Config

@pytest.fixture
def temp_db():
    """临时测试数据库"""
    # 保存原始路径
    original_path = Config.DATABASE_PATH

    # 创建临时数据库
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        temp_path = f.name

    Config.DATABASE_PATH = temp_path
    init_db()

    yield temp_path

    # 清理
    Config.DATABASE_PATH = original_path
    if os.path.exists(temp_path):
        os.remove(temp_path)
    for ext in ('-wal', '-shm', '.backup'):
        backup_path = temp_path + ext
        if os.path.exists(backup_path):
            os.remove(backup_path)


class TestImportScript:
    """导入脚本基础测试"""

    def test_db_initialization(self, temp_db):
        """数据库初始化"""
        conn = __import__('sqlite3').connect(temp_db)
        cursor = conn.cursor()

        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        assert 'stations' in tables
        assert 'cameras' in tables
        assert 'fault_reports' in tables

        # 检查索引
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cursor.fetchall()]

        assert any('station' in idx for idx in indexes)
        assert any('fault' in idx for idx in indexes)

        conn.close()

    def test_wal_mode_enabled(self, temp_db):
        """WAL模式已启用"""
        conn = __import__('sqlite3').connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        conn.close()

        assert mode.upper() == 'WAL'
