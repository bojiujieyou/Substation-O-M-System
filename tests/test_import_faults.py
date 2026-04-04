import sqlite3
from pathlib import Path

import openpyxl
import pytest

from import_faults import ImportAbortError, run_batch_import
from init_db import init_db


@pytest.fixture
def import_faults_db(tmp_path):
    return str(tmp_path / "test_import_faults.db")


@pytest.fixture
def seeded_import_faults_schema(import_faults_db):
    import config as config_module

    original_path = config_module.Config.DATABASE_PATH
    config_module.Config.DATABASE_PATH = import_faults_db
    init_db(force=True)
    config_module.Config.DATABASE_PATH = original_path

    conn = sqlite3.connect(import_faults_db)
    conn.executescript(
        """
        ALTER TABLE fault_reports ADD COLUMN project_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN camera_slot_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN fault_type_code TEXT;
        ALTER TABLE fault_reports ADD COLUMN fault_type_label_snapshot TEXT;
        ALTER TABLE fault_reports ADD COLUMN fault_type_version_id INTEGER;
        ALTER TABLE fault_reports ADD COLUMN source_type TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_batch_id TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_record_key TEXT;
        ALTER TABLE fault_reports ADD COLUMN project_device_code TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_time_raw TEXT;
        ALTER TABLE fault_reports ADD COLUMN source_timezone TEXT;

        ALTER TABLE cameras ADD COLUMN slot_id INTEGER;
        ALTER TABLE cameras ADD COLUMN project_id INTEGER;
        ALTER TABLE cameras ADD COLUMN project_camera_code TEXT;
        ALTER TABLE cameras ADD COLUMN status TEXT DEFAULT 'active';
        ALTER TABLE cameras ADD COLUMN replaced_by_camera_id INTEGER;
        ALTER TABLE cameras ADD COLUMN retired_at TIMESTAMP;

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

        CREATE TABLE camera_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_code TEXT NOT NULL,
            station_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            location_desc TEXT NOT NULL DEFAULT '',
            area TEXT NOT NULL DEFAULT '',
            channel_number INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(station_id, project_id, slot_code)
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

        CREATE UNIQUE INDEX idx_fault_source_record_key
        ON fault_reports(source_record_key)
        WHERE source_record_key IS NOT NULL;
        """
    )

    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, color, fault_type_version_id, sort_order, is_active)
        VALUES (1, 'inspection', 'Inspection', 'INSP', '#34a853', 10, 1, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO project_fault_type_versions (id, project_id, version, description, is_published, published_at)
        VALUES (10, 1, 3, 'current', 1, '2026-04-02T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO project_fault_types (version_id, type_code, type_label, semantic_group, sort_order, is_active)
        VALUES (10, 'BLUR', 'Blur', 'BLUR', 1, 1)
        """
    )
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Station A', '110kV', 'County A')"
    )
    conn.execute(
        """
        INSERT INTO camera_slots (id, slot_code, station_id, project_id, location_desc, area, channel_number)
        VALUES (1, 'SLOT-1', 1, 1, 'yard-east', 'yard', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO cameras
            (id, station_id, camera_index, area, location_desc, ip_address, channel_number, slot_id, project_id, project_camera_code, status)
        VALUES
            (1, 1, '1', 'yard', 'yard-east', '10.0.0.1', 1, 1, 1, 'INS-0001', 'active')
        """
    )
    conn.commit()
    conn.close()
    yield Path(import_faults_db)


def build_workbook(path: Path, rows: list[list]):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


def test_import_faults_batch_inserts_project_scoped_fault(seeded_import_faults_schema, tmp_path):
    source = tmp_path / "history.xlsx"
    build_workbook(
        source,
        [
            [
                "external_id",
                "station_name",
                "slot_code",
                "project_device_code",
                "fault_type_label",
                "description",
                "occurred_at",
                "status",
            ],
            [
                "ROW-001",
                "Station A",
                "SLOT-1",
                "INS-0001",
                "Blur",
                "Lens blur detected",
                "2026-04-01 08:00:00",
                "open",
            ],
        ],
    )

    report = run_batch_import(
        database=seeded_import_faults_schema,
        source=source,
        project_code="inspection",
        dry_run=False,
    )

    assert report["inserted"] == 1
    assert report["queue_items_created"] == 0

    conn = sqlite3.connect(seeded_import_faults_schema)
    try:
        row = conn.execute(
            """
            SELECT project_id, camera_slot_id, fault_type_code, fault_type_label_snapshot,
                   source_type, source_record_key, source_time_raw, source_timezone,
                   created_at, status, project_device_code
            FROM fault_reports
            """
        ).fetchone()
        batch = conn.execute(
            "SELECT project_id, source_type, mode, success_count, fail_count FROM import_batches"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == 1
    assert row[1] == 1
    assert row[2] == "BLUR"
    assert row[3] == "Blur"
    assert row[4] == "import_excel"
    assert row[5].startswith("inspection:import_excel:")
    assert row[6] == "2026-04-01 08:00:00"
    assert row[7] == "Asia/Shanghai"
    assert row[8] == "2026-04-01T00:00:00Z"
    assert row[9] == "open"
    assert row[10] == "INS-0001"
    assert batch == (1, "import_excel", "best-effort", 1, 0)


def test_import_faults_batch_queues_rows_without_external_id(seeded_import_faults_schema, tmp_path):
    source = tmp_path / "history_no_external_id.xlsx"
    build_workbook(
        source,
        [
            [
                "station_name",
                "slot_code",
                "fault_type_label",
                "description",
                "occurred_at",
            ],
            [
                "Station A",
                "SLOT-1",
                "Blur",
                "Lens blur detected",
                "2026-04-01 08:00:00",
            ],
        ],
    )

    report = run_batch_import(
        database=seeded_import_faults_schema,
        source=source,
        project_code="inspection",
        dry_run=False,
    )

    assert report["inserted"] == 0
    assert report["fail_count"] == 1
    assert report["queue_items_created"] == 1

    conn = sqlite3.connect(seeded_import_faults_schema)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
        queue_item = conn.execute(
            """
            SELECT project_id, source_type, issue_type, status, source_record_key_candidate
            FROM fault_import_review_queue
            """
        ).fetchone()
    finally:
        conn.close()

    assert fault_count == 0
    assert queue_item[0] == 1
    assert queue_item[1] == "import_excel"
    assert queue_item[2] == "source_record_key_unavailable"
    assert queue_item[3] == "pending"
    assert queue_item[4].startswith("inspection:import_excel:")


def test_import_faults_batch_dry_run_does_not_write(seeded_import_faults_schema, tmp_path):
    source = tmp_path / "history_dry_run.xlsx"
    build_workbook(
        source,
        [
            [
                "external_id",
                "station_name",
                "slot_code",
                "fault_type_label",
                "description",
                "occurred_at",
            ],
            [
                "ROW-DRY-1",
                "Station A",
                "SLOT-1",
                "Blur",
                "Lens blur detected",
                "2026-04-01 08:00:00",
            ],
        ],
    )

    report = run_batch_import(
        database=seeded_import_faults_schema,
        source=source,
        project_code="inspection",
        dry_run=True,
    )

    assert report["inserted"] == 1
    assert report["dry_run"] is True

    conn = sqlite3.connect(seeded_import_faults_schema)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
        batch_count = conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert batch_count == 0


def test_import_faults_fail_on_specific_rule_aborts_and_writes_report(seeded_import_faults_schema, tmp_path):
    source = tmp_path / "history_fail_on_station.xlsx"
    report_path = tmp_path / "fail_on_station_report.json"
    build_workbook(
        source,
        [
            [
                "external_id",
                "station_name",
                "slot_code",
                "fault_type_label",
                "description",
                "occurred_at",
            ],
            [
                "ROW-FAIL-1",
                "Unknown Station",
                "SLOT-404",
                "Blur",
                "Unmatched station",
                "2026-04-01 08:00:00",
            ],
        ],
    )

    with pytest.raises(ImportAbortError) as exc_info:
        run_batch_import(
            database=seeded_import_faults_schema,
            source=source,
            project_code="inspection",
            dry_run=False,
            report_path=report_path,
            fail_on="station_not_resolved",
        )

    report = exc_info.value.report
    assert report is not None
    assert report["aborted"] is True
    assert report["fail_on"] == ["station_not_resolved"]
    assert report["rows"][0]["reason"] == "station_not_resolved"
    assert report_path.exists()

    conn = sqlite3.connect(seeded_import_faults_schema)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
        batch_count = conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
        queue_count = conn.execute("SELECT COUNT(*) FROM fault_import_review_queue").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert batch_count == 0
    assert queue_count == 0


def test_import_faults_fail_on_any_failure_aborts_on_review_queue_case(seeded_import_faults_schema, tmp_path):
    source = tmp_path / "history_fail_on_any.xlsx"
    build_workbook(
        source,
        [
            [
                "station_name",
                "slot_code",
                "fault_type_label",
                "description",
                "occurred_at",
            ],
            [
                "Station A",
                "SLOT-1",
                "Blur",
                "Missing external id",
                "2026-04-01 08:00:00",
            ],
        ],
    )

    with pytest.raises(ImportAbortError) as exc_info:
        run_batch_import(
            database=seeded_import_faults_schema,
            source=source,
            project_code="inspection",
            dry_run=False,
            fail_on="any_failure",
        )

    report = exc_info.value.report
    assert report is not None
    assert report["aborted"] is True
    assert report["rows"][0]["reason"] == "source_record_key_unavailable"

    conn = sqlite3.connect(seeded_import_faults_schema)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
        queue_count = conn.execute("SELECT COUNT(*) FROM fault_import_review_queue").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert queue_count == 0
