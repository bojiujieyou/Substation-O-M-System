import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app, g

from db import (
    POSTGRES_BACKEND,
    create_connection,
    get_database_backend,
    get_integrity_error_class,
    get_operational_error_class,
)


DEFAULT_BUSY_TIMEOUT_MS = 30000

# ============================================================
# SQL 标识符安全校验
# ============================================================

_SQL_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def validate_sql_identifier(name: str, *, kind: str = "identifier") -> str:
    """校验 SQL 标识符（表名、列名）是否只含安全字符。

    防止通过 f-string 拼接时注入恶意 SQL。
    仅允许 [A-Za-z_][A-Za-z0-9_]* 格式。

    Args:
        name: 待校验的标识符字符串。
        kind: 标识符类型描述（用于错误信息），如 'table' 或 'column'。

    Returns:
        校验通过的标识符（原值）。

    Raises:
        ValueError: 标识符不合法时。
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"SQL {kind} name must be a non-empty string, got: {name!r}")
    if not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid SQL {kind} name: {name!r} "
            f"(must match [A-Za-z_][A-Za-z0-9_]*)"
        )
    return name


def validate_sql_type(type_str: str) -> str:
    """校验 SQL 列类型字符串是否安全（仅允许常用 SQL 类型关键字和符号）。

    Args:
        type_str: SQL 列类型定义，如 'TEXT', 'INTEGER DEFAULT 0'。

    Returns:
        校验通过的字符串（原值）。

    Raises:
        ValueError: 类型字符串不合法时。
    """
    if not isinstance(type_str, str) or not type_str.strip():
        raise ValueError(f"SQL type must be a non-empty string, got: {type_str!r}")
    # 允许：字母、数字、空格、括号、逗号、等号、单引号（用于 DEFAULT 'xxx'）、下划线
    if not re.match(r"^[A-Za-z0-9\s()'_,=]+$", type_str.strip()):
        raise ValueError(f"Invalid SQL type definition: {type_str!r}")
    return type_str


def configure_sqlite_connection(
    conn,
    *,
    row_factory=False,
    busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS,
    foreign_keys=True,
):
    if row_factory:
        conn.row_factory = sqlite3.Row if row_factory is True else row_factory
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON;")
    if busy_timeout_ms is not None:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)};")
    return conn


def enable_wal_mode(conn):
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};")
    return conn


def create_db_connection(
    db_path,
    *,
    database_url=None,
    row_factory=False,
    busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS,
    foreign_keys=True,
    enable_wal=False,
    uri=False,
):
    database_url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "").strip()
    backend = get_database_backend(database_url=database_url)
    conn = create_connection(
        database_url=database_url,
        database_path=db_path,
        row_factory=row_factory,
        uri=uri,
    )
    if backend != POSTGRES_BACKEND:
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
    src_conn = create_db_connection(source_path, database_url="", uri=False)
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return backup_path


def get_db():
    if "db" not in g:
        g.db = create_db_connection(
            current_app.config["DATABASE_PATH"],
            database_url=current_app.config.get("DATABASE_URL"),
            row_factory=True,
            busy_timeout_ms=current_app.config.get(
                "SQLITE_BUSY_TIMEOUT", DEFAULT_BUSY_TIMEOUT_MS
            ),
            enable_wal=current_app.config.get("SQLITE_WAL_MODE", False),
        )
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def get_data_dir():
    path = Path(current_app.config["APP_DATA_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_backend_from_app():
    return current_app.config.get("DATABASE_BACKEND") or get_database_backend(
        database_url=current_app.config.get("DATABASE_URL")
    )


def get_db_integrity_error():
    return get_integrity_error_class(get_db_backend_from_app())


def get_db_operational_error():
    return get_operational_error_class(get_db_backend_from_app())


def is_postgres_app():
    return get_db_backend_from_app() == POSTGRES_BACKEND


def init_app(app):
    app.teardown_appcontext(close_db)
    import atexit
    from db import close_pool
    atexit.register(close_pool)


def get_table_columns(db, table_name: str) -> set[str]:
    """返回表的字段名集合（统一版本，带安全校验）。

    自动适配 SQLite（PRAGMA table_info）和 PostgreSQL（information_schema）。

    Args:
        db: 数据库连接。
        table_name: 表名（仅允许 [A-Za-z_][A-Za-z0-9_]* 格式）。

    Returns:
        字段名集合。
    """
    validate_sql_identifier(table_name, kind="table")
    from project_access import table_exists
    if not table_exists(db, table_name):
        return set()
    backend = get_db_backend_from_app() if current_app else "sqlite"
    if backend == POSTGRES_BACKEND:
        rows = db.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        ).fetchall()
    else:
        rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}
