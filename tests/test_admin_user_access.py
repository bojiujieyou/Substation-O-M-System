import sqlite3

import pytest

from app import app
from auth import hash_password
from init_db import init_db


@pytest.fixture
def user_access_db(tmp_path):
    return str(tmp_path / "test_admin_user_access.db")


@pytest.fixture
def client(user_access_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = user_access_db
    app.config["DATABASE_PATH"] = user_access_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


@pytest.fixture
def seeded_user_access_schema(user_access_db):
    conn = sqlite3.connect(user_access_db)
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#1a73e8',
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE user_project_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            can_write INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, project_id)
        );
        """
    )
    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, sort_order, is_active)
        VALUES
            (1, 'inspection', 'Inspection', 'INSP', 1, 1),
            (2, 'auxiliary', 'Auxiliary', 'AUX', 2, 0)
        """
    )
    conn.executemany(
        "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
        [
            (1, 'admin1', hash_password('adminpass'), 'admin'),
            (2, 'operator1', hash_password('operatorpass'), 'operator'),
        ],
    )
    conn.execute(
        "INSERT INTO user_project_access (user_id, project_id, can_write) VALUES (2, 1, 1)"
    )
    conn.commit()
    conn.close()
    yield


def login(client):
    response = client.post("/auth/login", json={"username": "admin1", "password": "adminpass"})
    assert response.status_code == 200


def test_user_access_center_page_renders(client, seeded_user_access_schema):
    login(client)

    response = client.get("/admin/user-access-center")

    assert response.status_code == 200
    assert "用户项目授权".encode("utf-8") in response.data
    assert b"admin-section-title" in response.data
    assert b"form-actions-start" in response.data
    assert b"admin-section-header" in response.data
    assert b"admin-section-header-title" in response.data
    assert b"admin-section-stack" in response.data
    assert b"user-access-scope-card" in response.data
    assert b"user-access-write-check" in response.data
    assert b"setInlineMessage(" in response.data
    assert b"renderBlockMessage(" in response.data
