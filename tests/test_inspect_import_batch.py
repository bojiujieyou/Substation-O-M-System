import json
import sqlite3
from pathlib import Path

from inspect_import_batch import inspect_import_batch


def seed_batch_audit_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL
            );

            CREATE TABLE import_batches (
                id INTEGER PRIMARY KEY,
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

            CREATE TABLE fault_reports (
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                station_id INTEGER,
                camera_id INTEGER,
                camera_slot_id INTEGER,
                source_type TEXT,
                source_batch_id TEXT,
                source_record_key TEXT,
                fault_type TEXT,
                fault_type_code TEXT,
                fault_type_label_snapshot TEXT,
                project_device_code TEXT,
                description TEXT,
                status TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            );

            CREATE TABLE fault_import_review_queue (
                id INTEGER PRIMARY KEY,
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

            CREATE TABLE station_name_mapping_proposals (
                id INTEGER PRIMARY KEY,
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
            """
        )

        conn.execute("INSERT INTO projects (id, code, name) VALUES (1, 'unified', '统一平台')")
        conn.execute(
            """
            INSERT INTO import_batches (
                id, project_id, source_type, mode, file_count,
                success_count, fail_count, report_path, operator_id,
                timezone_default_used, created_at
            )
            VALUES (12, 1, 'import_excel', 'best-effort', 2, 5, 1, 'report.json', 99, 'Asia/Shanghai', '2026-04-03T10:00:00Z')
            """
        )
        conn.executemany(
            """
            INSERT INTO fault_reports (
                id, project_id, station_id, camera_id, camera_slot_id, source_type,
                source_batch_id, source_record_key, fault_type, fault_type_code,
                fault_type_label_snapshot, project_device_code, description, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    1,
                    101,
                    201,
                    301,
                    "import_excel",
                    "12",
                    "k1",
                    "无图像",
                    "NO_IMAGE",
                    "无图像",
                    "CAM-01",
                    "fault 1",
                    "open",
                    "2026-04-03T10:01:00Z",
                    "2026-04-03T10:01:00Z",
                ),
                (
                    2,
                    1,
                    102,
                    202,
                    302,
                    "import_excel",
                    "12",
                    "k2",
                    "模糊",
                    "BLUR",
                    "模糊",
                    "CAM-02",
                    "fault 2",
                    "closed",
                    "2026-04-03T10:02:00Z",
                    "2026-04-03T11:02:00Z",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO fault_import_review_queue (
                id, import_batch_id, project_id, source_type, source_record_key_candidate,
                raw_payload_json, issue_type, issue_detail, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 12, 1, "import_excel", "rk-1", "{}", "ambiguous_station", "station unclear", "pending"),
                (2, 12, 1, "import_excel", "rk-2", "{}", "unmapped_fault_type", "type unclear", "rejected"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO station_name_mapping_proposals (
                id, import_batch_id, project_id, source_system, external_name,
                normalized_name, candidate_station_id, confidence_score, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 12, 1, "import_excel", "外部站名A", "外部站名a", 101, 0.9, "pending"),
                (2, 12, 1, "import_excel", "外部站名B", "外部站名b", None, None, "approved"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_inspect_import_batch_reports_and_exports(tmp_path):
    db_path = tmp_path / "batch_audit.db"
    seed_batch_audit_db(db_path)

    export_dir = tmp_path / "exports"
    report_path = tmp_path / "batch_report.json"
    report = inspect_import_batch(
        database=db_path,
        batch_id=12,
        export_dir=export_dir,
        report_path=report_path,
    )

    assert report["batch"]["project_code"] == "unified"
    assert report["summary"]["fault_rows"] == 2
    assert report["summary"]["review_queue_rows"] == 2
    assert report["summary"]["station_name_proposals"] == 2
    assert report["summary"]["fault_status_breakdown"] == {"closed": 1, "open": 1}
    assert report["summary"]["review_issue_type_breakdown"] == {
        "ambiguous_station": 1,
        "unmapped_fault_type": 1,
    }
    assert report["summary"]["proposal_status_breakdown"] == {"approved": 1, "pending": 1}
    assert report["recommendation"]["primary_path"] == "backup_restore"

    assert report_path.exists()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["summary"]["fault_rows"] == 2

    assert (export_dir / "fault_rows.csv").exists()
    assert (export_dir / "review_rows.csv").exists()
    assert (export_dir / "proposal_rows.csv").exists()


def test_inspect_import_batch_uses_manual_queue_cleanup_when_no_fault_rows(tmp_path):
    db_path = tmp_path / "batch_audit.db"
    seed_batch_audit_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM fault_reports WHERE source_batch_id = '12'")
        conn.commit()
    finally:
        conn.close()

    report = inspect_import_batch(database=db_path, batch_id=12)
    assert report["summary"]["fault_rows"] == 0
    assert report["recommendation"]["primary_path"] == "manual_queue_cleanup"
