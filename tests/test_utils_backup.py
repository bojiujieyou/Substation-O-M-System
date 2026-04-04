import sqlite3

from utils import backup_sqlite_database


def test_backup_sqlite_database_creates_consistent_copy(tmp_path):
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE demo (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO demo (name) VALUES ('alpha')")
        conn.commit()
    finally:
        conn.close()

    backup_path = backup_sqlite_database(db_path, label="pytest")

    assert backup_path is not None
    assert backup_path.exists()
    assert backup_path.name.startswith("source.pytest_")

    backup_conn = sqlite3.connect(backup_path)
    try:
        rows = backup_conn.execute("SELECT id, name FROM demo").fetchall()
    finally:
        backup_conn.close()

    assert rows == [(1, "alpha")]


def test_backup_sqlite_database_returns_none_for_missing_db(tmp_path):
    missing_path = tmp_path / "missing.db"

    assert backup_sqlite_database(missing_path) is None
