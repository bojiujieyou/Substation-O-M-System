import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook

from config import Config
from import_faults_worklog import ImportAbortError, TARGET_TYPES, import_worklog_file
from import_review_support import PROJECT_CODE_BY_SYSTEM_TYPE
from init_db import init_db
from migrations.V1__multi_project import run_apply


def _create_workbook(path: Path, data_rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["header"] * 8)
    sheet.append(["subheader"] * 8)
    for row in data_rows:
        sheet.append(row)
    workbook.save(path)


def _prepare_multi_project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "worklog_import.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db(force=True) is True
    run_apply(db_path, report_path=None, backup_path=tmp_path / "backup.db")
    return db_path


def _system_type_for(project_code):
    for system_type, mapped_code in PROJECT_CODE_BY_SYSTEM_TYPE.items():
        if mapped_code == project_code:
            return system_type
    raise AssertionError(f"Missing system type mapping for project code: {project_code}")


def test_worklog_import_writes_queue_and_station_proposals(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "worklog.xlsx"
    report_path = tmp_path / "worklog_report.json"
    inspection_type = _system_type_for("inspection")
    auxiliary_type = _system_type_for("auxiliary")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Main Room", "Network disconnected", inspection_type, "", "Alice"],
            [2, "2025-01-03", "External Station A", "Aux Building", "Device offline", auxiliary_type, "", "Bob"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.commit()
    conn.close()

    stats = import_worklog_file(str(workbook_path), database_path=db_path, report_path=report_path)

    assert stats["inserted"] == 1
    assert stats["queue_items_created"] == 1
    assert stats["station_proposals_created"] == 1
    assert stats["duplicates_skipped"] == 0

    conn = sqlite3.connect(db_path)
    try:
        fault = conn.execute(
            """
            SELECT p.code, fr.source_type, fr.source_batch_id, fr.source_record_key, fr.fault_type_label_snapshot
            FROM fault_reports fr
            JOIN projects p ON p.id = fr.project_id
            WHERE fr.station_id = 1
            """
        ).fetchone()
        proposal = conn.execute(
            """
            SELECT p.code, snmp.source_system, snmp.external_name, snmp.candidate_station_id, snmp.status
            FROM station_name_mapping_proposals snmp
            JOIN projects p ON p.id = snmp.project_id
            WHERE snmp.external_name = 'External Station A'
            """
        ).fetchone()
        review_item = conn.execute(
            """
            SELECT p.code, firq.source_type, firq.issue_type, firq.status
            FROM fault_import_review_queue firq
            JOIN projects p ON p.id = firq.project_id
            WHERE firq.issue_type = 'station_not_resolved'
            """
        ).fetchone()
        batches = conn.execute(
            """
            SELECT p.code, b.success_count, b.fail_count, b.report_path
            FROM import_batches b
            JOIN projects p ON p.id = b.project_id
            ORDER BY b.id
            """
        ).fetchall()
    finally:
        conn.close()

    assert fault[0] == "inspection"
    assert fault[1] == "import_worklog"
    assert fault[2] is not None
    assert fault[3].startswith("inspection:import_worklog:")
    assert fault[4]

    assert proposal == ("auxiliary", "worklog", "External Station A", None, "pending")
    assert review_item == ("auxiliary", "import_worklog", "station_not_resolved", "pending")
    assert batches == [
        ("inspection", 1, 0, str(report_path.resolve())),
        ("auxiliary", 0, 1, str(report_path.resolve())),
    ]


def test_worklog_import_uses_source_record_key_to_skip_duplicates(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "worklog_dupe.xlsx"
    inspection_type = _system_type_for("inspection")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Main Room", "Network disconnected", inspection_type, "", "Alice"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.commit()
    conn.close()

    first = import_worklog_file(str(workbook_path))
    second = import_worklog_file(str(workbook_path))

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 1

    conn = sqlite3.connect(db_path)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 1


def test_worklog_import_fail_on_station_not_resolved_aborts_without_writes(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "worklog_fail_on.xlsx"
    report_path = tmp_path / "worklog_fail_on_report.json"
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Unknown Station", "Aux Building", "Device offline", next(iter(TARGET_TYPES)), "", "Reviewer"],
        ],
    )

    with pytest.raises(ImportAbortError) as exc_info:
        import_worklog_file(
            str(workbook_path),
            database_path=db_path,
            report_path=report_path,
            fail_on="station_not_resolved",
        )

    report = exc_info.value.report
    assert report is not None
    assert report["aborted"] is True
    assert report["fail_on"] == ["station_not_resolved"]
    assert report["rows"][0]["reason"] == "station_not_resolved"
    assert report_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
        proposal_count = conn.execute("SELECT COUNT(*) FROM station_name_mapping_proposals").fetchone()[0]
        queue_count = conn.execute("SELECT COUNT(*) FROM fault_import_review_queue").fetchone()[0]
        batch_count = conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert proposal_count == 0
    assert queue_count == 0
    assert batch_count == 0


def test_worklog_import_dry_run_writes_report_without_database_changes(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "worklog_dry_run.xlsx"
    report_path = tmp_path / "worklog_dry_run_report.json"
    inspection_type = _system_type_for("inspection")
    auxiliary_type = _system_type_for("auxiliary")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Main Room", "Network disconnected", inspection_type, "", "Alice"],
            [2, "2025-01-03", "External Station A", "Aux Building", "Device offline", auxiliary_type, "", "Bob"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.commit()
    conn.close()

    report = import_worklog_file(
        str(workbook_path),
        database_path=db_path,
        dry_run=True,
        report_path=report_path,
    )

    assert report["dry_run"] is True
    assert report["inserted"] == 1
    assert report["fail_count"] == 1
    assert report["queue_items_created"] == 0
    assert report["station_proposals_created"] == 0
    assert report_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
        proposal_count = conn.execute("SELECT COUNT(*) FROM station_name_mapping_proposals").fetchone()[0]
        queue_count = conn.execute("SELECT COUNT(*) FROM fault_import_review_queue").fetchone()[0]
        batch_count = conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert proposal_count == 0
    assert queue_count == 0
    assert batch_count == 0


def test_worklog_import_binds_camera_and_fills_camera_location_text(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "worklog_camera_binding.xlsx"
    inspection_type = _system_type_for("inspection")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Test County", "East Yard camera power fault recovered", inspection_type, "", "Alice"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.execute(
        """
        INSERT INTO camera_slots (id, slot_code, station_id, project_id, location_desc, area, channel_number)
        VALUES (1, 'SLOT-1', 1, 2, 'East Yard', '', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO cameras
            (id, slot_id, station_id, project_id, project_camera_code, camera_index, area, location_desc, ip_address, channel_port, channel_number, status)
        VALUES
            (1, 1, 1, 2, 'INS-001', '1', '', 'East Yard', '10.0.0.1', NULL, 1, 'active')
        """
    )
    conn.commit()
    conn.close()

    report = import_worklog_file(str(workbook_path), database_path=db_path)

    assert report["inserted"] == 1

    conn = sqlite3.connect(db_path)
    try:
        fault = conn.execute(
            """
            SELECT camera_id, camera_slot_id, camera_location_text, project_device_code
            FROM fault_reports
            WHERE station_id = 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert fault == (1, 1, "East Yard", "INS-001")
