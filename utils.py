# utils.py — 共享工具函数
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app, g


DEFAULT_BUSY_TIMEOUT_MS = 30000


def configure_sqlite_connection(
    conn,
    *,
    row_factory=False,
    busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS,
    foreign_keys=True,
):
    """对 SQLite 连接统一施加平台级约束。"""
    if row_factory:
        conn.row_factory = sqlite3.Row if row_factory is True else row_factory
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON;")
    if busy_timeout_ms is not None:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)};")
    return conn


def enable_wal_mode(conn):
    """启用 WAL 模式并沿用统一 busy timeout。"""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};")
    return conn


def create_db_connection(
    db_path,
    *,
    row_factory=False,
    busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS,
    foreign_keys=True,
    enable_wal=False,
    uri=False,
):
    """创建带统一 pragma 的 SQLite 连接。"""
    conn = sqlite3.connect(str(Path(db_path)), uri=uri)
    configure_sqlite_connection(
        conn,
        row_factory=row_factory,
        busy_timeout_ms=busy_timeout_ms,
        foreign_keys=foreign_keys,
    )
    if enable_wal:
        enable_wal_mode(conn)
    return conn


def backup_sqlite_database(db_path, backup_path=None, *, label="backup"):
    """使用 SQLite backup API 创建一致性备份。

    返回生成的备份路径；若源库不存在则返回 ``None``。
    """
    source_path = Path(db_path)
    if not source_path.exists():
        return None

    if backup_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = source_path.with_name(
            f"{source_path.stem}.{label}_{timestamp}{source_path.suffix}"
        )
    else:
        backup_path = Path(backup_path)

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    src_conn = create_db_connection(source_path, uri=False)
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return backup_path


def get_db():
    """获取数据库连接（请求级）"""
    if "db" not in g:
        g.db = create_db_connection(
            current_app.config["DATABASE_PATH"],
            row_factory=True,
            busy_timeout_ms=current_app.config.get(
                "SQLITE_BUSY_TIMEOUT", DEFAULT_BUSY_TIMEOUT_MS
            ),
            enable_wal=current_app.config.get("SQLITE_WAL_MODE", False),
        )
    return g.db


def close_db(exception=None):
    """请求结束后关闭数据库连接（teardown callback）"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_app(app):
    """初始化应用（注册teardown等）

    在Flask应用创建后调用:
        from utils import init_app
        init_app(app)
    """
    app.teardown_appcontext(close_db)
