import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook

from config import Config
from full_import_worklog import ImportAbortError, STATIONS_TO_ADD, import_full_worklog_file
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
    db_path = tmp_path / "full_worklog_import.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db(force=True) is True
    run_apply(db_path, report_path=None, backup_path=tmp_path / "backup.db")
    return db_path


def _system_type_for(project_code):
    for system_type, mapped_code in PROJECT_CODE_BY_SYSTEM_TYPE.items():
        if mapped_code == project_code:
            return system_type
    raise AssertionError(f"Missing system type mapping for project code: {project_code}")


def test_full_worklog_import_adds_missing_stations_and_routes_review_items(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "full_worklog.xlsx"
    report_path = tmp_path / "full_worklog_report.json"
    seed_short_name, seed_voltage_level, seed_county = STATIONS_TO_ADD[0]
    seed_full_name = f"{seed_voltage_level}{seed_short_name}"
    inspection_type = _system_type_for("inspection")
    unified_type = _system_type_for("unified")
    auxiliary_type = _system_type_for("auxiliary")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Main Room", "\u7f51\u7edc\u65ad\u5f00", inspection_type, "", "Alice"],
            [2, "2025-01-03", seed_full_name, "Outdoor", "Device offline", unified_type, "", "Bob"],
            [3, "2025-01-04", "External Station B", "Aux Building", "Device offline", auxiliary_type, "", "Carol"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.commit()
    conn.close()

    report = import_full_worklog_file(str(workbook_path), database_path=db_path, report_path=report_path)

    assert report["stations_added"] >= 1
    assert report["inserted"] == 2
    assert report["queue_items_created"] == 1
    assert report["station_proposals_created"] == 1
    assert report["duplicates_skipped"] == 0

    conn = sqlite3.connect(db_path)
    try:
        inserted_faults = conn.execute(
            """
            SELECT p.code, s.name, f.source_type, f.source_record_key, f.fault_type_label_snapshot
            FROM fault_reports f
            JOIN stations s ON s.id = f.station_id
            LEFT JOIN projects p ON p.id = f.project_id
            ORDER BY f.id
            """
        ).fetchall()
        proposal = conn.execute(
            """
            SELECT p.code, sp.source_system, sp.external_name, sp.candidate_station_id, sp.status
            FROM station_name_mapping_proposals sp
            LEFT JOIN projects p ON p.id = sp.project_id
            WHERE sp.external_name = 'External Station B'
            """
        ).fetchone()
        review_item = conn.execute(
            """
            SELECT p.code, q.source_type, q.issue_type, q.status
            FROM fault_import_review_queue q
            LEFT JOIN projects p ON p.id = q.project_id
            WHERE q.issue_type = 'station_not_resolved'
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
        new_station = conn.execute(
            "SELECT name, voltage_level, county FROM stations WHERE name = ?",
            (seed_full_name,),
        ).fetchone()
    finally:
        conn.close()

    assert inserted_faults == [
        ("inspection", "Test Station", "import_worklog", inserted_faults[0][3], inserted_faults[0][4]),
        ("unified", seed_full_name, "import_worklog", inserted_faults[1][3], inserted_faults[1][4]),
    ]
    assert inserted_faults[0][3].startswith("inspection:import_worklog:")
    assert inserted_faults[1][3].startswith("unified:import_worklog:")
    assert inserted_faults[0][4]
    assert inserted_faults[1][4]
    assert proposal == ("auxiliary", "worklog", "External Station B", None, "pending")
    assert review_item == ("auxiliary", "import_worklog", "station_not_resolved", "pending")
    assert ("inspection", 1, 0, str(report_path.resolve())) in batches
    assert ("unified", 1, 0, str(report_path.resolve())) in batches
    assert ("auxiliary", 0, 1, str(report_path.resolve())) in batches
    assert new_station == (seed_full_name, seed_voltage_level, seed_county)


def test_full_worklog_import_uses_source_record_key_to_skip_duplicates(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "full_worklog_dupe.xlsx"
    inspection_type = _system_type_for("inspection")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Main Room", "\u7f51\u7edc\u65ad\u5f00", inspection_type, "", "Alice"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.commit()
    conn.close()

    first = import_full_worklog_file(str(workbook_path), database_path=db_path)
    second = import_full_worklog_file(str(workbook_path), database_path=db_path)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 1

    conn = sqlite3.connect(db_path)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 1


def test_full_worklog_import_fail_on_station_not_resolved_aborts_without_writes(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "full_worklog_fail_on.xlsx"
    report_path = tmp_path / "full_worklog_fail_on_report.json"
    seed_short_name, seed_voltage_level, _ = STATIONS_TO_ADD[0]
    seed_full_name = f"{seed_voltage_level}{seed_short_name}"
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Unknown Station", "Aux Building", "Device offline", _system_type_for("auxiliary"), "", "Reviewer"],
        ],
    )

    with pytest.raises(ImportAbortError) as exc_info:
        import_full_worklog_file(
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
        seeded_station = conn.execute("SELECT COUNT(*) FROM stations WHERE name = ?", (seed_full_name,)).fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert proposal_count == 0
    assert queue_count == 0
    assert batch_count == 0
    assert seeded_station == 0


def test_full_worklog_import_dry_run_writes_report_without_database_changes(tmp_path, monkeypatch):
    db_path = _prepare_multi_project_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "full_worklog_dry_run.xlsx"
    report_path = tmp_path / "full_worklog_dry_run_report.json"
    seed_short_name, seed_voltage_level, _ = STATIONS_TO_ADD[0]
    seed_full_name = f"{seed_voltage_level}{seed_short_name}"
    inspection_type = _system_type_for("inspection")
    auxiliary_type = _system_type_for("auxiliary")
    _create_workbook(
        workbook_path,
        [
            [1, "2025-01-02", "Test Station", "Main Room", "\u7f51\u7edc\u65ad\u5f00", inspection_type, "", "Alice"],
            [2, "2025-01-03", seed_full_name, "Outdoor", "Device offline", _system_type_for("unified"), "", "Bob"],
            [3, "2025-01-04", "External Station B", "Aux Building", "Device offline", auxiliary_type, "", "Carol"],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, 'Test Station', '110kV', 'Test County')"
    )
    conn.commit()
    conn.close()

    report = import_full_worklog_file(
        str(workbook_path),
        database_path=db_path,
        dry_run=True,
        report_path=report_path,
    )

    assert report["dry_run"] is True
    assert report["stations_added"] >= 1
    assert report["inserted"] == 2
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
        seeded_station = conn.execute("SELECT COUNT(*) FROM stations WHERE name = ?", (seed_full_name,)).fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 0
    assert proposal_count == 0
    assert queue_count == 0
    assert batch_count == 0
    assert seeded_station == 0
