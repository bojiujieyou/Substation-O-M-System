import sqlite3
from pathlib import Path

from openpyxl import Workbook

from config import Config
from import_daily_fault_summary import import_daily_fault_summary_file
from import_review_support import resolve_station_match
from init_db import init_db
from migrations.V1__multi_project import run_apply


def _create_daily_summary_workbook(path: Path, rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _prepare_daily_import_db(tmp_path, monkeypatch):
    db_path = tmp_path / "daily_fault_summary.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db(force=True) is True
    run_apply(db_path, report_path=None, backup_path=tmp_path / "daily_fault_summary.backup.db")
    return db_path


def test_daily_fault_summary_import_writes_faults_and_review_queue(tmp_path, monkeypatch):
    db_path = _prepare_daily_import_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "变电站视频系统监控日报20260407.xlsx"
    _create_daily_summary_workbook(
        workbook_path,
        [
            ["变电站视频系统监控日报", "", ""],
            ["时间：  04 月 07 日", "", "检查人员：正好科技"],
            ["检查发现问题情况", "", ""],
            ["变电站", "问题描述", ""],
            ["省公司平台离线摄像头", "", ""],
            ["110kV测试站", "主变西北侧球机离线", ""],
            ["110kV未知站", "大门口球机离线", ""],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, '110kV测试站', '110kV', '测试县')"
    )
    conn.commit()
    conn.close()

    report = import_daily_fault_summary_file(str(workbook_path), project_code="unified", database_path=db_path)

    assert report["project"] == "unified"
    assert report["source_type"] == "import_daily_fault_summary"
    assert report["source_date"] == "2026-04-07"
    assert report["summary"]["inserted"] == 1
    assert report["summary"]["queue_items_created"] == 1
    assert report["summary"]["station_proposals_created"] == 1
    assert report["summary"]["fail_count"] == 1

    conn = sqlite3.connect(db_path)
    try:
        fault = conn.execute(
            """
            SELECT project_id, source_type, source_batch_id, source_record_key,
                   fault_type_label_snapshot, description, status, camera_location_text
            FROM fault_reports
            WHERE station_id = 1
            """
        ).fetchone()
        review_item = conn.execute(
            """
            SELECT source_type, issue_type, status
            FROM fault_import_review_queue
            ORDER BY id
            """
        ).fetchone()
        proposal = conn.execute(
            """
            SELECT source_system, external_name, status
            FROM station_name_mapping_proposals
            ORDER BY id
            """
        ).fetchone()
        batch = conn.execute(
            """
            SELECT project_id, source_type, mode, success_count, fail_count, report_path
            FROM import_batches
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()

    assert fault[0] == 1
    assert fault[1] == "import_daily_fault_summary"
    assert fault[2]
    assert fault[3].startswith("unified:import_daily_fault_summary:")
    assert fault[4] == "摄像头离线"
    assert "省公司平台离线摄像头" in fault[5]
    assert fault[6] == "open"
    assert fault[7]
    assert review_item == ("import_daily_fault_summary", "station_not_resolved", "pending")
    assert proposal == ("daily_fault_summary", "110kV未知站", "pending")
    assert batch[:5] == (1, "import_daily_fault_summary", "best-effort", 1, 1)
    assert batch[5]


def test_daily_fault_summary_import_skips_duplicates_by_source_key(tmp_path, monkeypatch):
    db_path = _prepare_daily_import_db(tmp_path, monkeypatch)
    workbook_path = tmp_path / "变电站视频系统监控日报20260407.xlsx"
    _create_daily_summary_workbook(
        workbook_path,
        [
            ["变电站视频系统监控日报", "", ""],
            ["时间：  04 月 07 日", "", "检查人员：正好科技"],
            ["检查发现问题情况", "", ""],
            ["变电站", "问题描述", ""],
            ["省公司平台离线摄像头", "", ""],
            ["110kV测试站", "主变西北侧球机离线", ""],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, '110kV测试站', '110kV', '测试县')"
    )
    conn.commit()
    conn.close()

    first = import_daily_fault_summary_file(str(workbook_path), project_code="unified", database_path=db_path)
    second = import_daily_fault_summary_file(str(workbook_path), project_code="unified", database_path=db_path)

    assert first["summary"]["inserted"] == 1
    assert second["summary"]["inserted"] == 0
    assert second["summary"]["duplicates_skipped"] == 1

    conn = sqlite3.connect(db_path)
    try:
        fault_count = conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0]
    finally:
        conn.close()

    assert fault_count == 1


def test_daily_fault_summary_import_skips_semantic_duplicates_for_same_camera_location(tmp_path, monkeypatch):
    db_path = _prepare_daily_import_db(tmp_path, monkeypatch)
    first_workbook_path = tmp_path / "变电站视频系统监控日报20260407_a.xlsx"
    second_workbook_path = tmp_path / "变电站视频系统监控日报20260407_b.xlsx"

    _create_daily_summary_workbook(
        first_workbook_path,
        [
            ["变电站视频系统监控日报", "", ""],
            ["时间： 04 月 07 日", "", "检查人员：正好科技"],
            ["检查发现问题情况", "", ""],
            ["变电站", "问题描述", ""],
            ["省公司平台离线摄像头", "", ""],
            ["220kV睦田变", "2#主变西南角18枪机离线", ""],
        ],
    )
    _create_daily_summary_workbook(
        second_workbook_path,
        [
            ["变电站视频系统监控日报", "", ""],
            ["时间： 04 月 07 日", "", "检查人员：正好科技"],
            ["检查发现问题情况", "", ""],
            ["变电站", "问题描述", ""],
            ["省公司平台离线摄像头", "", ""],
            ["220kV睦田变", "2#主变西南角18枪机掉线", ""],
        ],
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (1, '220kV睦田变', '220kV', '云和')"
    )
    conn.commit()
    conn.close()

    first = import_daily_fault_summary_file(str(first_workbook_path), project_code="unified", database_path=db_path)
    second = import_daily_fault_summary_file(str(second_workbook_path), project_code="unified", database_path=db_path)

    assert first["summary"]["inserted"] == 1
    assert second["summary"]["inserted"] == 0
    assert second["summary"]["duplicates_skipped"] == 1
    assert any("语义指纹" in (row.get("message") or "") for row in second["rows"])

    conn = sqlite3.connect(db_path)
    try:
        fault_rows = conn.execute(
            """
            SELECT station_id, fault_type_label_snapshot, camera_location_text, created_at
            FROM fault_reports
            ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()

    assert len(fault_rows) == 1
    assert fault_rows[0][0] == 1
    assert fault_rows[0][1] == "摄像头离线"
    assert fault_rows[0][2]
    assert fault_rows[0][3] == "2026-04-07"


def test_resolve_station_match_prefers_literal_name_when_normalized_names_collide(tmp_path, monkeypatch):
    db_path = _prepare_daily_import_db(tmp_path, monkeypatch)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (22, '220kV睦田变', '220kV', '云和')"
    )
    conn.execute(
        "INSERT INTO stations (id, name, voltage_level, county) VALUES (117, '220kV睦田变电站', '220kV', '丽宏山运检班')"
    )
    conn.commit()

    match = resolve_station_match(conn, "220kV睦田变", source_system="daily_fault_summary")
    conn.close()

    assert match["matched"] is True
    assert match["station_id"] == 22
    assert match["station_name"] == "220kV睦田变"
    assert match["match_source"] == "stations_literal"
