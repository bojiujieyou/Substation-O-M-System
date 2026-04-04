import sqlite3

import pytest

from app import app
from auth import hash_password
from init_db import init_db


@pytest.fixture
def review_db(tmp_path):
    return str(tmp_path / "test_review.db")


@pytest.fixture
def client(review_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    app_original_db_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = review_db
    app.config["DATABASE_PATH"] = review_db
    app.config["TESTING"] = True

    init_db(force=True)
    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = app_original_db_path


@pytest.fixture
def seeded_review_schema(review_db):
    conn = sqlite3.connect(review_db)
    conn.executescript(
        """
        ALTER TABLE fault_reports ADD COLUMN project_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN source_type TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_batch_id TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_record_key TEXT;
        ALTER TABLE fault_reports ADD COLUMN fault_type_label_snapshot TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_time_raw TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_timezone TEXT;

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

        CREATE TABLE import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            file_count INTEGER,
            success_count INTEGER,
            fail_count INTEGER,
            report_path TEXT,
            operator_id INTEGER,
            timezone_default_used TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE station_external_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            source_system TEXT NOT NULL,
            external_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_system, external_name)
        );

        CREATE TABLE station_name_mapping_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_batch_id INTEGER,
            project_id INTEGER,
            source_system TEXT NOT NULL,
            external_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            candidate_station_id INTEGER,
            confidence_score REAL,
            raw_context_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_id INTEGER,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE fault_import_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_batch_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_record_key_candidate TEXT,
            raw_payload_json TEXT NOT NULL,
            issue_type TEXT NOT NULL,
            issue_detail TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_fault_id INTEGER,
            reviewer_id INTEGER,
            reviewed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (2, 'Station B', '220kV', 'County B')"
    )
    conn.execute(
        """
        INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count)
        VALUES (1, 1, 'import_excel', 'best-effort', 1, 0, 1)
        """
    )
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Station A', '110kV', 'County A')"
    )

    password_hash = hash_password("adminpass")
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'admin1', ?, 'admin')",
        (password_hash,),
    )
    conn.commit()
    conn.close()
    yield


def login(client):
    response = client.post("/auth/login", json={"username": "admin1", "password": "adminpass"})
    assert response.status_code == 200


def test_review_center_page_renders_for_admin(client, seeded_review_schema):
    login(client)

    response = client.get("/admin/review-center")

    assert response.status_code == 200
    assert "导入审查中心".encode("utf-8") in response.data


def test_station_name_proposal_can_be_listed_and_approved(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.execute(
        """
        INSERT INTO station_name_mapping_proposals (
            id, import_batch_id, project_id, source_system, external_name,
            normalized_name, candidate_station_id, confidence_score, raw_context_json, status
        )
        VALUES (
            1, 1, 1, 'auxiliary', 'Station-A-External',
            'stationaexternal', 1, 0.9, '{"sample": "row-1"}', 'pending'
        )
        """
    )
    conn.commit()
    conn.close()

    login(client)

    response = client.get("/admin/station-name-proposals?status=pending")
    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] == 1
    assert data["proposals"][0]["project_code"] == "inspection"
    assert data["proposals"][0]["candidate_station_name"] == "Station A"
    assert data["proposals"][0]["raw_context"]["sample"] == "row-1"

    approve = client.post(
        "/admin/station-name-proposals/1/approve",
        json={"station_id": 1, "is_primary": True},
    )
    assert approve.status_code == 200
    payload = approve.get_json()
    assert payload["station_id"] == 1

    conn = sqlite3.connect(review_db)
    try:
        mapping = conn.execute(
            """
            SELECT station_id, source_system, external_name, normalized_name, is_primary
            FROM station_external_names
            WHERE source_system = 'auxiliary' AND external_name = 'Station-A-External'
            """
        ).fetchone()
        proposal = conn.execute(
            """
            SELECT status, reviewer_id, candidate_station_id
            FROM station_name_mapping_proposals
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert mapping == (1, "auxiliary", "Station-A-External", "stationaexternal", 1)
    assert proposal == ("approved", 1, 1)


def test_station_name_proposals_support_batch_reject(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.executemany(
        """
        INSERT INTO station_name_mapping_proposals (
            id, import_batch_id, project_id, source_system, external_name, normalized_name, status
        )
        VALUES (?, 1, 1, 'inspection', ?, ?, 'pending')
        """,
        [
            (1, "External A", "externala"),
            (2, "External B", "externalb"),
        ],
    )
    conn.commit()
    conn.close()

    login(client)

    response = client.post(
        "/admin/station-name-proposals/batch-reject",
        json={"proposal_ids": [1, 2]},
    )
    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 2

    conn = sqlite3.connect(review_db)
    try:
        statuses = conn.execute(
            "SELECT id, status, reviewer_id FROM station_name_mapping_proposals ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert statuses == [(1, "rejected", 1), (2, "rejected", 1)]


def test_station_name_proposal_detail_and_batch_approve(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.executemany(
        """
        INSERT INTO station_name_mapping_proposals (
            id, import_batch_id, project_id, source_system, external_name,
            normalized_name, candidate_station_id, confidence_score, raw_context_json, status
        )
        VALUES (?, 1, 1, 'auxiliary', ?, ?, 2, 0.7, '{"station": "candidate"}', 'pending')
        """,
        [
            (1, "External C", "externalc"),
            (2, "External D", "externald"),
        ],
    )
    conn.commit()
    conn.close()

    login(client)

    detail = client.get("/admin/station-name-proposals/1")
    assert detail.status_code == 200
    assert detail.get_json()["proposal"]["raw_context"]["station"] == "candidate"

    response = client.post(
        "/admin/station-name-proposals/batch-approve",
        json={"proposal_ids": [1, 2], "station_id": 2},
    )
    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 2

    conn = sqlite3.connect(review_db)
    try:
        statuses = conn.execute(
            "SELECT id, status, candidate_station_id FROM station_name_mapping_proposals ORDER BY id"
        ).fetchall()
        mappings = conn.execute(
            "SELECT external_name, station_id FROM station_external_names ORDER BY external_name"
        ).fetchall()
    finally:
        conn.close()

    assert statuses == [(1, "approved", 2), (2, "approved", 2)]
    assert mappings == [("External C", 2), ("External D", 2)]


def test_import_review_queue_can_be_listed_and_rejected(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.execute(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, source_record_key_candidate,
            raw_payload_json, issue_type, issue_detail, status
        )
        VALUES (
            1, 1, 1, 'import_excel', 'inspection:import_excel:abc',
            '{"station_name": "External Station"}', 'ambiguous_station', 'needs review', 'pending'
        )
        """
    )
    conn.commit()
    conn.close()

    login(client)

    response = client.get("/admin/import-review-queue?status=pending")
    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] == 1
    assert data["items"][0]["project_code"] == "inspection"
    assert data["items"][0]["raw_payload"]["station_name"] == "External Station"

    reject = client.post(
        "/admin/import-review-queue/1/reject",
        json={"reason": "manual duplicate"},
    )
    assert reject.status_code == 200

    conn = sqlite3.connect(review_db)
    try:
        item = conn.execute(
            """
            SELECT status, reviewer_id, issue_detail
            FROM fault_import_review_queue
            WHERE id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert item[0] == "rejected"
    assert item[1] == 1
    assert "manual duplicate" in item[2]


def test_import_review_queue_supports_batch_reject(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.executemany(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, raw_payload_json, issue_type, status
        )
        VALUES (?, 1, 1, 'import_excel', '{}', 'duplicate_candidate', 'pending')
        """,
        [(1,), (2,)],
    )
    conn.commit()
    conn.close()

    login(client)

    response = client.post(
        "/admin/import-review-queue/batch-reject",
        json={"item_ids": [1, 2]},
    )
    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 2

    conn = sqlite3.connect(review_db)
    try:
        statuses = conn.execute(
            "SELECT id, status, reviewer_id FROM fault_import_review_queue ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert statuses == [(1, "rejected", 1), (2, "rejected", 1)]


def test_import_review_item_detail_and_approve_import(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.execute(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, source_record_key_candidate,
            raw_payload_json, issue_type, issue_detail, status
        )
        VALUES (
            1, 1, 1, 'import_worklog', 'inspection:import_worklog:key1',
            '{"system_type":"智能巡视","content":"网络中断","location":"主控室","handler_name":"张三","parsed_time":"2025-01-02","raw_time":"2025年01月02日","station_token":"External Station"}',
            'station_not_resolved', 'needs station', 'pending'
        )
        """
    )
    conn.commit()
    conn.close()

    login(client)

    detail = client.get("/admin/import-review-queue/1")
    assert detail.status_code == 200
    assert detail.get_json()["item"]["raw_payload"]["location"] == "主控室"

    approve = client.post(
        "/admin/import-review-queue/1/approve-import",
        json={"station_id": 1, "note": "confirmed manually"},
    )
    assert approve.status_code == 200
    payload = approve.get_json()
    assert payload["fault_id"] > 0

    conn = sqlite3.connect(review_db)
    try:
        item = conn.execute(
            "SELECT status, resolved_fault_id, reviewer_id, issue_detail FROM fault_import_review_queue WHERE id = 1"
        ).fetchone()
        fault = conn.execute(
            """
            SELECT station_id, project_id, source_type, source_batch_id, source_record_key, fault_type, fault_type_label_snapshot
            FROM fault_reports
            WHERE id = ?
            """,
            (item[1],),
        ).fetchone()
    finally:
        conn.close()

    assert item[0] == "approved"
    assert item[2] == 1
    assert "confirmed manually" in item[3]
    assert fault == (1, 1, "import_worklog", "1", "inspection:import_worklog:key1", "网络故障", "网络故障")


def test_import_review_approve_preserves_richer_import_metadata(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.executescript(
        """
        ALTER TABLE fault_reports ADD COLUMN camera_slot_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN fault_type_code TEXT;
        ALTER TABLE fault_reports ADD COLUMN fault_type_version_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN project_device_code TEXT;
        ALTER TABLE fault_reports ADD COLUMN handling_started_at TIMESTAMP;

        CREATE TABLE camera_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_code TEXT NOT NULL,
            station_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO camera_slots (id, slot_code, station_id, project_id) VALUES (8, 'SLOT-8', 1, 1)"
    )
    conn.execute(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, source_record_key_candidate,
            raw_payload_json, issue_type, issue_detail, status
        )
        VALUES (
            2, 1, 1, 'import_excel', 'inspection:import_excel:key-rich',
            '{"reporter_name":"Alice","status":"handling","parsed_time":"2025-01-05T08:00:00Z","handling_started_at":"2025-01-05T08:10:00Z","raw_time":"2025-01-05 16:00:00","source_timezone":"Asia/Shanghai","fault_type":"Blur","fault_type_label_snapshot":"Blur Snapshot","fault_type_code":"BLUR","fault_type_version_id":10,"project_device_code":"DEV-9","slot_id":8,"station_id":1}',
            'slot_not_resolved', 'needs station', 'pending'
        )
        """
    )
    conn.commit()
    conn.close()

    login(client)

    approve = client.post(
        "/admin/import-review-queue/2/approve-import",
        json={"station_id": 1},
    )
    assert approve.status_code == 200
    fault_id = approve.get_json()["fault_id"]

    conn = sqlite3.connect(review_db)
    try:
        fault = conn.execute(
            """
            SELECT reporter_name, status, handling_started_at, camera_slot_id,
                   fault_type, fault_type_label_snapshot, fault_type_code,
                   fault_type_version_id, project_device_code, source_time_raw, source_timezone
            FROM fault_reports
            WHERE id = ?
            """,
            (fault_id,),
        ).fetchone()
    finally:
        conn.close()

    assert fault == (
        "Alice",
        "handling",
        "2025-01-05T08:10:00Z",
        8,
        "Blur Snapshot",
        "Blur Snapshot",
        "BLUR",
        10,
        "DEV-9",
        "2025-01-05 16:00:00",
        "Asia/Shanghai",
    )


def test_import_review_item_can_merge_existing_and_apply_mapping(client, seeded_review_schema, review_db):
    conn = sqlite3.connect(review_db)
    conn.execute(
        """
        INSERT INTO fault_reports (
            id, station_id, fault_type, description, reporter_name, status
        ) VALUES (9, 1, '设备故障', 'existing', 'admin', 'open')
        """
    )
    conn.executemany(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, source_record_key_candidate,
            raw_payload_json, issue_type, issue_detail, status
        )
        VALUES (?, 1, 1, 'import_worklog', ?, ?, 'station_not_resolved', 'needs station', 'pending')
        """,
        [
            (
                1,
                "inspection:import_worklog:key2",
                '{"station_token":"Map A","content":"设备离线","parsed_time":"2025-01-03"}',
            ),
            (
                2,
                "inspection:import_worklog:key3",
                '{"station_token":"Map B","content":"设备离线","parsed_time":"2025-01-03"}',
            ),
        ],
    )
    conn.commit()
    conn.close()

    login(client)

    assign = client.post(
        "/admin/import-review-queue/batch-assign-station",
        json={"item_ids": [1, 2], "station_id": 2},
    )
    assert assign.status_code == 200
    assert assign.get_json()["updated_count"] == 2

    mapping = client.post(
        "/admin/import-review-queue/batch-apply-mapping",
        json={"item_ids": [1, 2], "station_id": 2, "source_system": "worklog"},
    )
    assert mapping.status_code == 200
    assert mapping.get_json()["mapped_count"] == 2

    merge = client.post(
        "/admin/import-review-queue/1/merge-existing",
        json={"fault_id": 9, "note": "duplicate of existing"},
    )
    assert merge.status_code == 200

    conn = sqlite3.connect(review_db)
    try:
        queue_item = conn.execute(
            "SELECT status, resolved_fault_id, reviewer_id, issue_detail, raw_payload_json FROM fault_import_review_queue WHERE id = 1"
        ).fetchone()
        mappings = conn.execute(
            "SELECT external_name, station_id, source_system FROM station_external_names ORDER BY external_name"
        ).fetchall()
    finally:
        conn.close()

    assert queue_item[0] == "approved"
    assert queue_item[1] == 9
    assert queue_item[2] == 1
    assert "duplicate of existing" in queue_item[3]
    assert '"assigned_station_id": 2' in queue_item[4]
    assert mappings == [("Map A", 2, "worklog"), ("Map B", 2, "worklog")]
