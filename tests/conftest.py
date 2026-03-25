# conftest.py — Pytest配置
import os
import sys
import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 重要：测试使用独立的数据库文件，避免污染生产数据
TEST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'test_station_monitor.db')

@pytest.fixture(autouse=True)
def use_test_db(monkeypatch):
    """强制所有测试使用测试数据库"""
    import config
    monkeypatch.setattr(config.Config, 'DATABASE_PATH', TEST_DB_PATH)

@pytest.fixture
def test_db():
    """测试前清理测试数据库"""
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
    yield TEST_DB_PATH
    # 测试后清理
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
