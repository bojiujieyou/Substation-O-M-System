import sqlite3

import pytest

from app import app
from auth import hash_password
from init_db import init_db


@pytest.fixture
def fault_type_db(tmp_path):
    return str(tmp_path / "test_admin_fault_types.db")


@pytest.fixture
def client(fault_type_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = fault_type_db
    app.config["DATABASE_PATH"] = fault_type_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


@pytest.fixture
def seeded_fault_type_schema(fault_type_db):
    conn = sqlite3.connect(fault_type_db)
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

        CREATE TABLE project_fault_type_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            description TEXT,
            is_published INTEGER DEFAULT 0,
            published_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, version)
        );

        CREATE TABLE project_fault_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            type_code TEXT NOT NULL,
            type_label TEXT NOT NULL,
            semantic_group TEXT,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(version_id, type_code)
        );
        """
    )

    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, sort_order, is_active)
        VALUES (1, 'inspection', 'Inspection', 'INSP', 1, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO project_fault_type_versions (id, project_id, version, description, is_published, published_at)
        VALUES (10, 1, 1, 'baseline', 1, '2026-04-02T00:00:00')
        """
    )
    conn.execute("UPDATE projects SET fault_type_version_id = 10 WHERE id = 1")
    conn.executemany(
        """
        INSERT INTO project_fault_types (version_id, type_code, type_label, semantic_group, sort_order, is_active)
        VALUES (10, ?, ?, ?, ?, 1)
        """,
        [
            ("NO_IMAGE", "No Image", "NO_IMAGE", 1),
            ("BLUR", "Blur", "BLUR", 2),
        ],
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


def test_fault_type_center_page_renders(client, seeded_fault_type_schema):
    login(client)

    response = client.get("/admin/fault-type-center")

    assert response.status_code == 200
    assert "故障类型版本管理".encode("utf-8") in response.data


def test_list_fault_type_versions_returns_types_and_diff_summary(client, seeded_fault_type_schema):
    login(client)

    response = client.get("/admin/projects/inspection/fault-type-versions")

    assert response.status_code == 200
    data = response.get_json()
    assert data["project"]["code"] == "inspection"
    assert len(data["versions"]) == 1
    version = data["versions"][0]
    assert version["version"] == 1
    assert version["is_published"] is True
    assert [item["type_code"] for item in version["types"]] == ["NO_IMAGE", "BLUR"]
    assert version["diff_summary"] == {"added": 2, "removed": 0, "changed": 0}


def test_create_fault_type_version_from_payload(client, seeded_fault_type_schema, fault_type_db):
    login(client)

    response = client.post(
        "/admin/projects/inspection/fault-type-versions",
        json={
            "description": "v2 draft",
            "fault_types": [
                {"type_code": "NO_IMAGE", "type_label": "No Image", "semantic_group": "NO_IMAGE", "sort_order": 1},
                {"type_code": "BLUR", "type_label": "Image Blur", "semantic_group": "BLUR_V2", "sort_order": 2},
                {"type_code": "OFFLINE", "type_label": "Offline", "semantic_group": "OFFLINE", "sort_order": 3},
            ],
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["version"]["version"] == 2
    assert payload["version"]["editable"] is True
    assert len(payload["version"]["types"]) == 3

    conn = sqlite3.connect(fault_type_db)
    try:
        versions = conn.execute(
            "SELECT version, description, is_published FROM project_fault_type_versions ORDER BY version"
        ).fetchall()
    finally:
        conn.close()

    assert versions == [(1, "baseline", 1), (2, "v2 draft", 0)]


def test_publish_requires_diff_confirmation(client, seeded_fault_type_schema):
    login(client)

    create = client.post(
        "/admin/projects/inspection/fault-type-versions",
        json={
            "description": "v2 draft",
            "fault_types": [
                {"type_code": "NO_IMAGE", "type_label": "No Image", "semantic_group": "NO_IMAGE", "sort_order": 1},
                {"type_code": "BLUR", "type_label": "Image Blur", "semantic_group": "BLUR", "sort_order": 2},
            ],
        },
    )
    version_id = create.get_json()["version"]["id"]

    publish = client.post(
        f"/admin/projects/inspection/fault-type-versions/{version_id}/publish",
        json={"confirmations": {}},
    )

    assert publish.status_code == 400
    payload = publish.get_json()
    assert "missing or invalid confirmation" in payload["error"]
    assert any(item["type_code"] == "BLUR" for item in payload["diff_items"])


def test_publish_requires_new_semantic_group_for_semantic_changed(client, seeded_fault_type_schema):
    login(client)

    create = client.post(
        "/admin/projects/inspection/fault-type-versions",
        json={
            "description": "v2 draft",
            "fault_types": [
                {"type_code": "NO_IMAGE", "type_label": "No Image", "semantic_group": "NO_IMAGE", "sort_order": 1},
                {"type_code": "BLUR", "type_label": "Image Blur", "semantic_group": "BLUR", "sort_order": 2},
            ],
        },
    )
    version_id = create.get_json()["version"]["id"]

    publish = client.post(
        f"/admin/projects/inspection/fault-type-versions/{version_id}/publish",
        json={"confirmations": {"type:BLUR": "semantic_changed"}},
    )

    assert publish.status_code == 400
    assert "must use a new semantic_group" in publish.get_json()["error"]


def test_publish_fault_type_version_updates_current_version(client, seeded_fault_type_schema, fault_type_db):
    login(client)

    create = client.post(
        "/admin/projects/inspection/fault-type-versions",
        json={
            "description": "v2 draft",
            "fault_types": [
                {"type_code": "NO_IMAGE", "type_label": "No Image", "semantic_group": "NO_IMAGE", "sort_order": 1},
                {"type_code": "BLUR", "type_label": "Image Blur", "semantic_group": "BLUR_V2", "sort_order": 2},
                {"type_code": "OFFLINE", "type_label": "Offline", "semantic_group": "OFFLINE", "sort_order": 3},
            ],
        },
    )
    version_id = create.get_json()["version"]["id"]

    publish = client.post(
        f"/admin/projects/inspection/fault-type-versions/{version_id}/publish",
        json={
            "confirmations": {
                "type:BLUR": "semantic_changed",
                "type:OFFLINE": "new_type",
            }
        },
    )

    assert publish.status_code == 200
    payload = publish.get_json()
    assert payload["version"]["id"] == version_id
    assert payload["version"]["is_published"] is True

    conn = sqlite3.connect(fault_type_db)
    try:
        project_row = conn.execute(
            "SELECT fault_type_version_id FROM projects WHERE code = 'inspection'"
        ).fetchone()
        version_rows = conn.execute(
            "SELECT version, is_published FROM project_fault_type_versions ORDER BY version"
        ).fetchall()
    finally:
        conn.close()

    assert project_row == (version_id,)
    assert version_rows == [(1, 0), (2, 1)]
