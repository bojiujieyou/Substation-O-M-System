"""登录限流测试 — 验证数据库持久化限流行为"""
import time
from unittest.mock import patch, MagicMock
import sqlite3
import pytest


@pytest.fixture
def rate_limit_db(tmp_path):
    """创建临时 SQLite 数据库用于限流测试"""
    db_path = str(tmp_path / "test_rate_limit.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            window_start REAL NOT NULL,
            PRIMARY KEY (ip)
        )
    """)
    conn.commit()
    yield conn
    conn.close()


def _make_get_db(conn):
    """创建返回固定连接的 get_db mock"""
    def get_db():
        return conn
    return get_db


# ============================================================
# 基本行为测试
# ============================================================

def test_first_attempt_allowed(rate_limit_db):
    """首次请求应当放行"""
    with patch("auth.get_db", _make_get_db(rate_limit_db)):
        from auth import rate_limit_check
        assert rate_limit_check("1.2.3.4") is True


def test_under_limit_allowed(rate_limit_db):
    """在阈值内应当放行"""
    with patch("auth.get_db", _make_get_db(rate_limit_db)):
        from auth import rate_limit_check, rate_limit_record
        assert rate_limit_check("1.2.3.4") is True
        for i in range(4):
            rate_limit_record("1.2.3.4")
        assert rate_limit_check("1.2.3.4") is True


def test_at_max_attempts_blocked(rate_limit_db):
    """达到最大尝试次数应当阻断"""
    with patch("auth.get_db", _make_get_db(rate_limit_db)):
        from auth import rate_limit_check, rate_limit_record, RATE_LIMIT_MAX_ATTEMPTS
        assert rate_limit_check("1.2.3.4") is True
        for i in range(RATE_LIMIT_MAX_ATTEMPTS):
            rate_limit_record("1.2.3.4")
        assert rate_limit_check("1.2.3.4") is False


def test_different_ips_independent(rate_limit_db):
    """不同 IP 的限流应当独立"""
    with patch("auth.get_db", _make_get_db(rate_limit_db)):
        from auth import rate_limit_check, rate_limit_record, RATE_LIMIT_MAX_ATTEMPTS
        # 封禁 IP-A
        for i in range(RATE_LIMIT_MAX_ATTEMPTS):
            rate_limit_record("1.1.1.1")
        assert rate_limit_check("1.1.1.1") is False
        # IP-B 应当正常
        assert rate_limit_check("2.2.2.2") is True


# ============================================================
# 窗口过期测试
# ============================================================

def test_window_expiry_resets(rate_limit_db):
    """窗口过期后应当重置"""
    with patch("auth.get_db", _make_get_db(rate_limit_db)):
        with patch("auth.time") as mock_time:
            from auth import rate_limit_check, rate_limit_record, RATE_LIMIT_MAX_ATTEMPTS, RATE_LIMIT_WINDOW_SECONDS

            now = time.time()
            mock_time.time.return_value = now

            # 用完配额
            assert rate_limit_check("1.2.3.4") is True
            for i in range(RATE_LIMIT_MAX_ATTEMPTS):
                rate_limit_record("1.2.3.4")
            assert rate_limit_check("1.2.3.4") is False

            # 快进超过窗口时间
            mock_time.time.return_value = now + RATE_LIMIT_WINDOW_SECONDS + 1
            assert rate_limit_check("1.2.3.4") is True


# ============================================================
# 自修复建表测试
# ============================================================

def test_ensure_table_creates_when_missing(tmp_path):
    """login_attempts 表不存在时应当自动创建"""
    db_path = str(tmp_path / "test_no_table.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 不建表

    with patch("auth.get_db", _make_get_db(conn)):
        from auth import ensure_login_attempts_table
        ensure_login_attempts_table(conn)

        # 验证表已创建
        result = conn.execute("SELECT count(*) as cnt FROM login_attempts").fetchone()
        assert result["cnt"] == 0

    conn.close()


def test_ensure_table_rolls_back_before_recreate():
    """缺表异常后应先回滚事务，再继续建表。"""
    db = MagicMock()
    db.execute.side_effect = [Exception("missing table"), None]

    from auth import ensure_login_attempts_table

    ensure_login_attempts_table(db)

    db.rollback.assert_called_once()
    db.commit.assert_called_once()


# ============================================================
# 集成测试：完整登录限流流程
# ============================================================
