import sqlite3

import pytest

from app import app
from auth import hash_password
from init_db import init_db


@pytest.fixture
def project_admin_db(tmp_path):
    return str(tmp_path / "test_admin_projects.db")


@pytest.fixture
def client(project_admin_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = project_admin_db
    app.config["DATABASE_PATH"] = project_admin_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


@pytest.fixture
def seeded_project_admin_schema(project_admin_db):
    conn = sqlite3.connect(project_admin_db)
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#1a73e8',
            fault_type_version_id INTEGER,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, color, sort_order, is_active)
        VALUES
            (1, 'unified', '统一平台', '统一', '#1a73e8', 1, 1),
            (2, 'inspection', '智能巡视', '巡视', '#34a853', 2, 0)
        """
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'admin1', ?, 'admin')",
        (hash_password("adminpass"),),
    )
    conn.commit()
    conn.close()
    yield


def login(client):
    response = client.post("/auth/login", json={"username": "admin1", "password": "adminpass"})
    assert response.status_code == 200


def test_project_center_page_renders(client, seeded_project_admin_schema):
    login(client)

    response = client.get("/admin/project-center")

    assert response.status_code == 200
    assert "项目管理中心".encode("utf-8") in response.data


def test_list_projects_returns_inactive_projects_for_admin(client, seeded_project_admin_schema):
    login(client)

    response = client.get("/admin/projects")

    assert response.status_code == 200
    data = response.get_json()
    assert [item["code"] for item in data["projects"]] == ["unified", "inspection"]
    inspection = next(item for item in data["projects"] if item["code"] == "inspection")
    assert inspection["is_active"] is False


def test_create_project_admin(client, seeded_project_admin_schema, project_admin_db):
    login(client)

    response = client.post(
        "/admin/projects",
        json={
            "code": "auxiliary_v2",
            "name": "辅控系统二期",
            "short_name": "辅控二期",
            "color": "#112233",
            "sort_order": 5,
            "is_active": True,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["project"]["code"] == "auxiliary_v2"
    assert payload["project"]["is_active"] is True

    conn = sqlite3.connect(project_admin_db)
    try:
        row = conn.execute(
            "SELECT code, name, short_name, color, sort_order, is_active FROM projects WHERE code = 'auxiliary_v2'"
        ).fetchone()
    finally:
        conn.close()

    assert row == ("auxiliary_v2", "辅控系统二期", "辅控二期", "#112233", 5, 1)


def test_update_project_can_toggle_active_and_preserve_immutable_fields(client, seeded_project_admin_schema, project_admin_db):
    login(client)

    response = client.put(
        "/admin/projects/inspection",
        json={
            "is_active": True,
            "color": "#445566",
            "sort_order": 9,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["project"]["is_active"] is True

    conn = sqlite3.connect(project_admin_db)
    try:
        row = conn.execute(
            "SELECT code, name, short_name, color, sort_order, is_active FROM projects WHERE code = 'inspection'"
        ).fetchone()
    finally:
        conn.close()

    assert row == ("inspection", "智能巡视", "巡视", "#445566", 9, 1)


def test_update_project_rejects_immutable_field_changes(client, seeded_project_admin_schema):
    login(client)

    response = client.put(
        "/admin/projects/inspection",
        json={"name": "改名后的项目"},
    )

    assert response.status_code == 400
    assert "immutable fields" in response.get_json()["error"]
