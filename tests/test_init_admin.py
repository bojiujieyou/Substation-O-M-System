import importlib
import sqlite3
from pathlib import Path

from auth import password_needs_rehash, verify_password


def _create_users_table(db_path: Path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'operator'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _reload_init_admin(monkeypatch, db_path: Path, *, init_admin_password: str | None = None):
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if init_admin_password is None:
        monkeypatch.delenv("INIT_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("INIT_ADMIN_PASSWORD", init_admin_password)

    import init_admin as init_admin_module

    module = importlib.reload(init_admin_module)
    monkeypatch.setattr(module.Config, "DATABASE_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(module.Config, "DATABASE_URL", "", raising=False)
    return module


def test_init_admin_returns_false_when_no_admin_exists_and_password_missing(monkeypatch, tmp_path):
    db_path = tmp_path / "no-admin.db"
    _create_users_table(db_path)
    init_admin_module = _reload_init_admin(monkeypatch, db_path)

    assert init_admin_module.init_admin() is False

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
    finally:
        conn.close()

    assert count == 0


def test_init_admin_skips_when_admin_already_exists_without_password(monkeypatch, tmp_path):
    db_path = tmp_path / "existing-admin.db"
    _create_users_table(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("existing-admin", "salt$hash"),
        )
        conn.commit()
    finally:
        conn.close()

    init_admin_module = _reload_init_admin(monkeypatch, db_path)

    assert init_admin_module.init_admin() is True

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT username FROM users WHERE role = 'admin' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [("existing-admin",)]


def test_init_admin_creates_first_admin_from_env_password(monkeypatch, tmp_path):
    db_path = tmp_path / "create-admin.db"
    _create_users_table(db_path)
    init_admin_module = _reload_init_admin(
        monkeypatch,
        db_path,
        init_admin_password="bootstrap-secret",
    )

    assert init_admin_module.init_admin() is True

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT username, password_hash, role FROM users WHERE role = 'admin'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "admin"
    assert row[2] == "admin"
    assert row[1] != "bootstrap-secret"
    assert verify_password("bootstrap-secret", row[1]) is True
    assert password_needs_rehash(row[1]) is False
