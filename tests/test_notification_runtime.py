import sqlite3

import pytest

from app import app
from auth import hash_password
from init_db import init_db
from maintain_notifications import run_maintenance
from notification_runtime import dispatch_notification_event


@pytest.fixture
def notification_runtime_db(tmp_path):
    return str(tmp_path / "test_notification_runtime.db")


@pytest.fixture
def client(notification_runtime_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = notification_runtime_db
    app.config["DATABASE_PATH"] = notification_runtime_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


@pytest.fixture
def seeded_notification_runtime_schema(notification_runtime_db, monkeypatch):
    import config as config_module

    monkeypatch.setattr(config_module.Config, "DATABASE_PATH", notification_runtime_db)
    app.config["DATABASE_PATH"] = notification_runtime_db
    init_db(force=True)

    conn = sqlite3.connect(notification_runtime_db)
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

        ALTER TABLE fault_reports ADD COLUMN project_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN camera_slot_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN handling_started_at TIMESTAMP;
        ALTER TABLE fault_reports ADD COLUMN fault_type_label_snapshot TEXT;
        ALTER TABLE cameras ADD COLUMN slot_id INTEGER;
        ALTER TABLE cameras ADD COLUMN project_id INTEGER;
        ALTER TABLE cameras ADD COLUMN status TEXT DEFAULT 'active';
        """
    )

    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, color, sort_order, is_active)
        VALUES (1, 'inspection', 'Inspection', 'INSP', '#34a853', 1, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO project_notification_policies (
            id, project_id, quiet_hours_json, notify_on_create, notify_on_close,
            escalate_after_minutes, escalation_target_config_id, is_active
        )
        VALUES (1, 1, NULL, 1, 1, 30, 3, 1)
        """
    )
    conn.executemany(
        """
        INSERT INTO project_notification_configs (
            id, policy_id, event_type, channel, target_value, is_active, deduplication_window_minutes
        )
        VALUES (?, 1, ?, ?, ?, 1, 60)
        """,
        [
            (1, "fault_created", "wechat", "ops-group"),
            (2, "fault_closed", "email", "ops@example.com"),
            (3, "fault_escalated", "sms", "13800000000"),
        ],
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'admin1', ?, 'admin')",
        (hash_password("adminpass"),),
    )
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Station A', '110kV', 'County A')"
    )
    conn.execute(
        """
        INSERT INTO cameras (id, station_id, camera_index, location_desc, ip_address, slot_id, project_id, status)
        VALUES (1, 1, '1', 'slot-a', '10.0.0.8', 1, 1, 'active')
        """
    )
    conn.execute(
        """
        INSERT INTO fault_reports (id, station_id, camera_id, fault_type, reporter_name, status, project_id, created_at)
        VALUES (1, 1, 1, 'Blur', 'Alice', 'open', 1, datetime('now', '-2 hours'))
        """
    )
    conn.commit()
    conn.close()
    yield


def login(client):
    response = client.post("/auth/login", json={"username": "admin1", "password": "adminpass"})
    assert response.status_code == 200


def test_create_fault_plans_fault_created_notification(client, seeded_notification_runtime_schema, notification_runtime_db):
    login(client)

    response = client.post(
        "/api/faults",
        json={
            "station_id": 1,
            "camera_id": 1,
            "project": "inspection",
            "fault_type": "Blur",
            "description": "new issue",
            "reporter_name": "Admin One",
        },
    )

    assert response.status_code == 201
    fault_id = response.get_json()["fault_id"]

    conn = sqlite3.connect(notification_runtime_db)
    try:
        row = conn.execute(
            """
            SELECT event_type, channel, target_value
            FROM notification_dispatch_logs
            WHERE fault_id = ?
            """,
            (fault_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row == ("fault_created", "wechat", "ops-group")


def test_close_fault_plans_fault_closed_notification(client, seeded_notification_runtime_schema, notification_runtime_db):
    login(client)

    response = client.put("/api/faults/1/status", json={"status": "closed"})

    assert response.status_code == 200

    conn = sqlite3.connect(notification_runtime_db)
    try:
        row = conn.execute(
            """
            SELECT event_type, channel, target_value
            FROM notification_dispatch_logs
            WHERE fault_id = 1
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert row == ("fault_closed", "email", "ops@example.com")


def test_dispatch_notification_event_respects_quiet_hours(notification_runtime_db, seeded_notification_runtime_schema):
    conn = sqlite3.connect(notification_runtime_db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "UPDATE project_notification_policies SET quiet_hours_json = ? WHERE id = 1",
            ('{"start":"22:00","end":"08:00"}',),
        )
        conn.commit()

        summary = dispatch_notification_event(
            conn,
            1,
            "fault_created",
            now=__import__("datetime").datetime(2026, 4, 3, 15, 0, tzinfo=__import__("datetime").timezone.utc),
        )

        count = conn.execute("SELECT COUNT(*) FROM notification_dispatch_logs").fetchone()[0]
    finally:
        conn.close()

    assert summary["suppressed_reason"] == "quiet_hours"
    assert count == 0


def test_dispatch_notification_event_suppresses_inactive_project(notification_runtime_db, seeded_notification_runtime_schema):
    conn = sqlite3.connect(notification_runtime_db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("UPDATE projects SET is_active = 0 WHERE id = 1")
        conn.commit()

        summary = dispatch_notification_event(conn, 1, "fault_created")
        count = conn.execute("SELECT COUNT(*) FROM notification_dispatch_logs").fetchone()[0]
    finally:
        conn.close()

    assert summary["suppressed_reason"] == "project_inactive"
    assert count == 0


def test_notification_maintenance_escalates_once(notification_runtime_db, seeded_notification_runtime_schema):
    db_path = __import__("pathlib").Path(notification_runtime_db)

    first = run_maintenance(db_path, apply=True)
    second = run_maintenance(db_path, apply=True)

    assert first["escalations"]["candidates"] == 1
    assert first["escalations"]["dispatched"] == 1
    assert second["escalations"]["candidates"] == 0

    conn = sqlite3.connect(notification_runtime_db)
    try:
        rows = conn.execute(
            """
            SELECT event_type, channel, target_value
            FROM notification_dispatch_logs
            WHERE fault_id = 1 AND event_type = 'fault_escalated'
            """
        ).fetchall()
    finally:
        conn.close()

    assert rows == [("fault_escalated", "sms", "13800000000")]


def test_notification_maintenance_skips_inactive_projects(notification_runtime_db, seeded_notification_runtime_schema):
    conn = sqlite3.connect(notification_runtime_db)
    try:
        conn.execute("UPDATE projects SET is_active = 0 WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    db_path = __import__("pathlib").Path(notification_runtime_db)
    report = run_maintenance(db_path, apply=True)

    assert report["escalations"]["candidates"] == 0
    assert report["escalations"]["dispatched"] == 0
