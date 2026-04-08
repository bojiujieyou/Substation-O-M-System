import sqlite3

import pytest

from app import app
from auth import hash_password
from init_db import init_db


@pytest.fixture
def notification_db(tmp_path):
    return str(tmp_path / "test_admin_notifications.db")


@pytest.fixture
def client(notification_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = notification_db
    app.config["DATABASE_PATH"] = notification_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


@pytest.fixture
def seeded_notification_schema(notification_db):
    conn = sqlite3.connect(notification_db)
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

        CREATE TABLE project_notification_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            quiet_hours_json TEXT,
            notify_on_create INTEGER DEFAULT 1,
            notify_on_close INTEGER DEFAULT 1,
            escalate_after_minutes INTEGER,
            escalation_target_config_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE project_notification_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            target_value TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            deduplication_window_minutes INTEGER DEFAULT 60,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            voltage_level TEXT,
            county TEXT
        );

        CREATE TABLE IF NOT EXISTS fault_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            station_id INTEGER,
            status TEXT NOT NULL,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            handling_started_at TIMESTAMP,
            closed_at TIMESTAMP,
            fault_type TEXT,
            fault_type_label_snapshot TEXT,
            description TEXT
        );
        """
    )
    fault_report_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(fault_reports)").fetchall()
    }
    for column_name, column_type in [
        ("project_id", "INTEGER"),
        ("handling_started_at", "TIMESTAMP"),
        ("fault_type_label_snapshot", "TEXT"),
    ]:
        if column_name not in fault_report_columns:
            conn.execute(f"ALTER TABLE fault_reports ADD COLUMN {column_name} {column_type}")
    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, sort_order, is_active)
        VALUES
            (1, 'inspection', 'Inspection', 'INSP', 1, 1),
            (2, 'auxiliary', 'Auxiliary', 'AUX', 2, 0)
        """
    )
    conn.execute(
        """
        INSERT INTO project_notification_policies (
            id, project_id, quiet_hours_json, notify_on_create, notify_on_close,
            escalate_after_minutes, escalation_target_config_id, is_active
        )
        VALUES (1, 1, '{"start":"22:00","end":"08:00"}', 1, 0, 45, NULL, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO project_notification_configs (
            id, policy_id, event_type, channel, target_value, is_active, deduplication_window_minutes
        )
        VALUES
            (1, 1, 'fault_created', 'wechat', 'ops-group', 1, 60),
            (2, 1, 'fault_escalated', 'sms', '13800000000', 1, 15)
        """
    )
    conn.execute(
        "UPDATE project_notification_policies SET escalation_target_config_id = 2 WHERE id = 1"
    )
    conn.execute(
        """
        INSERT INTO stations (id, name, voltage_level, county)
        VALUES (1, 'Station Alpha', '220kV', 'Test County')
        """
    )
    conn.execute(
        """
        INSERT INTO fault_reports (
            id, project_id, station_id, status, created_at, updated_at,
            handling_started_at, fault_type, fault_type_label_snapshot, description
        )
        VALUES (
            1, 1, 1, 'open', '2026-04-01 00:00:00', '2026-04-01 00:00:00',
            NULL, 'network', 'Network Fault', 'Link down'
        )
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


def disable_quiet_hours(notification_db):
    conn = sqlite3.connect(notification_db)
    try:
        conn.execute("UPDATE project_notification_policies SET quiet_hours_json = NULL WHERE id = 1")
        conn.commit()
    finally:
        conn.close()


def test_notification_center_page_renders(client, seeded_notification_schema):
    login(client)

    response = client.get("/admin/notification-center")

    assert response.status_code == 200
    assert "项目通知配置".encode("utf-8") in response.data
    assert b"form-actions-start" in response.data
    assert b"notification-actions-col" in response.data
    assert b"admin-section-title" in response.data
    assert b"admin-section-header" in response.data
    assert b"admin-section-header-title" in response.data
    assert b"admin-section-header-actions" in response.data
    assert b"setInlineMessage(" in response.data
    assert b"renderTableMessage(" in response.data


def test_list_project_notifications_returns_policies_and_configs(client, seeded_notification_schema):
    login(client)

    response = client.get("/admin/project-notifications")

    assert response.status_code == 200
    payload = response.get_json()
    assert [item["code"] for item in payload["projects"]] == ["inspection", "auxiliary"]
    inspection_policy = next(item for item in payload["policies"] if item["project_code"] == "inspection")
    assert inspection_policy["quiet_hours"] == {"start": "22:00", "end": "08:00"}
    assert inspection_policy["notify_on_close"] is False
    assert any(item["target_value"] == "ops-group" for item in payload["configs"])


def test_update_project_notification_policy(client, seeded_notification_schema, notification_db):
    login(client)

    response = client.put(
        "/admin/project-notification-policies/inspection",
        json={
            "quiet_hours": {"start": "23:00", "end": "07:00"},
            "notify_on_create": False,
            "notify_on_close": True,
            "escalate_after_minutes": 30,
            "escalation_target_config_id": 2,
            "is_active": True,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["policy"]["quiet_hours"] == {"start": "23:00", "end": "07:00"}
    assert payload["policy"]["notify_on_create"] is False
    assert payload["policy"]["notify_on_close"] is True

    conn = sqlite3.connect(notification_db)
    try:
        row = conn.execute(
            """
            SELECT quiet_hours_json, notify_on_create, notify_on_close, escalate_after_minutes, escalation_target_config_id
            FROM project_notification_policies
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert row == ('{"start": "23:00", "end": "07:00"}', 0, 1, 30, 2)


def test_create_and_update_project_notification_config(client, seeded_notification_schema, notification_db):
    login(client)

    create_response = client.post(
        "/admin/project-notifications",
        json={
            "project_code": "inspection",
            "event_type": "fault_closed",
            "channel": "email",
            "target_value": "ops@example.com",
            "deduplication_window_minutes": 120,
            "is_active": True,
        },
    )
    assert create_response.status_code == 201
    config_id = create_response.get_json()["config"]["id"]

    update_response = client.put(
        f"/admin/project-notifications/{config_id}",
        json={
            "event_type": "fault_closed",
            "channel": "email",
            "target_value": "ops-lead@example.com",
            "deduplication_window_minutes": 90,
            "is_active": False,
        },
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["config"]["target_value"] == "ops-lead@example.com"
    assert update_response.get_json()["config"]["is_active"] is False

    conn = sqlite3.connect(notification_db)
    try:
        row = conn.execute(
            """
            SELECT target_value, deduplication_window_minutes, is_active
            FROM project_notification_configs
            WHERE id = ?
            """,
            (config_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row == ("ops-lead@example.com", 90, 0)


def test_delete_notification_config_is_blocked_when_referenced(client, seeded_notification_schema):
    login(client)

    response = client.delete("/admin/project-notifications/2")

    assert response.status_code == 409
    assert "referenced" in response.get_json()["error"]


def test_delete_notification_config_soft_deactivates_when_unreferenced(client, seeded_notification_schema, notification_db):
    login(client)

    response = client.delete("/admin/project-notifications/1")

    assert response.status_code == 200
    assert response.get_json()["config_id"] == 1

    conn = sqlite3.connect(notification_db)
    try:
        row = conn.execute(
            "SELECT is_active FROM project_notification_configs WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert row == (0,)


def test_list_notification_dispatch_logs_returns_empty_before_runtime_runs(client, seeded_notification_schema):
    login(client)

    response = client.get("/admin/project-notification-dispatch-logs?project=inspection")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total"] == 0
    assert payload["logs"] == []


def test_notification_maintenance_dry_run_does_not_persist_dispatch_logs(client, seeded_notification_schema, notification_db):
    disable_quiet_hours(notification_db)
    login(client)

    response = client.post("/admin/project-notifications/maintenance", json={"apply": False})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "dry-run"
    assert payload["report"]["candidates"] == 1
    assert payload["report"]["dispatched"] == 1

    logs_response = client.get("/admin/project-notification-dispatch-logs?project=inspection")
    assert logs_response.status_code == 200
    assert logs_response.get_json()["total"] == 0


def test_notification_maintenance_apply_persists_dispatch_logs_and_skips_duplicates(client, seeded_notification_schema, notification_db):
    disable_quiet_hours(notification_db)
    login(client)

    first_response = client.post("/admin/project-notifications/maintenance", json={"apply": True})

    assert first_response.status_code == 200
    first_payload = first_response.get_json()
    assert first_payload["mode"] == "apply"
    assert first_payload["report"]["candidates"] == 1
    assert first_payload["report"]["dispatched"] == 1

    logs_response = client.get("/admin/project-notification-dispatch-logs?project=inspection")
    assert logs_response.status_code == 200
    logs_payload = logs_response.get_json()
    assert logs_payload["total"] == 1
    assert logs_payload["logs"][0]["event_type"] == "fault_escalated"
    assert logs_payload["logs"][0]["target_value"] == "13800000000"
    assert logs_payload["logs"][0]["payload"]["station_name"] == "Station Alpha"

    second_response = client.post("/admin/project-notifications/maintenance", json={"apply": True})
    assert second_response.status_code == 200
    second_payload = second_response.get_json()
    assert second_payload["report"]["candidates"] == 0
    assert second_payload["report"]["dispatched"] == 0
