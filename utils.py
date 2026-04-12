import os
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
