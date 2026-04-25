import hashlib
import sqlite3

import pytest

from app import app
from auth import (
    hash_password,
    password_needs_rehash,
    verify_password,
)
from init_db import init_db


def _legacy_hash(password, salt="0123456789abcdef0123456789abcdef"):
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


@pytest.fixture
def auth_db(tmp_path):
    return str(tmp_path / "test_auth_password_migration.db")


@pytest.fixture
def client(auth_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = auth_db
    app.config["DATABASE_PATH"] = auth_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


def test_hash_password_creates_strong_hash():
    stored = hash_password("fresh-secret")

    assert stored.startswith("scrypt:")
    assert verify_password("fresh-secret", stored) is True
    assert password_needs_rehash(stored) is False


def test_verify_password_accepts_legacy_sha256_hash():
    stored = _legacy_hash("legacy-secret")

    assert verify_password("legacy-secret", stored) is True
    assert verify_password("wrong-secret", stored) is False
    assert password_needs_rehash(stored) is True


def test_login_lazily_migrates_legacy_hash(client, auth_db):
    legacy_hash = _legacy_hash("operatorpass")

    conn = sqlite3.connect(auth_db)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("legacy-user", legacy_hash, "operator"),
        )
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        "/auth/login",
        json={"username": "legacy-user", "password": "operatorpass"},
    )

    assert response.status_code == 200

    conn = sqlite3.connect(auth_db)
    try:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?",
            ("legacy-user",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert stored != legacy_hash
    assert verify_password("operatorpass", stored) is True
    assert password_needs_rehash(stored) is False
