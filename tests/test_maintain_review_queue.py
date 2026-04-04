import sqlite3

from config import Config
from init_db import init_db
from maintain_review_queue import run_maintenance
from migrations.V1__multi_project import run_apply


def _prepare_db(tmp_path, monkeypatch):
    db_path = tmp_path / "maintain_review_queue.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db(force=True) is True
    run_apply(db_path, report_path=None, backup_path=tmp_path / "backup.db")
    return db_path


def test_maintenance_dry_run_reports_pending_records_without_mutation(tmp_path, monkeypatch):
    db_path = _prepare_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO projects (code, name, short_name) VALUES ('extra', 'Extra', 'EX')")
    conn.execute(
        """
        INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count)
        VALUES (1, 1, 'import_worklog', 'best-effort', 1, 0, 1)
        """
    )
    conn.execute(
        """
        INSERT INTO station_name_mapping_proposals (
            id, import_batch_id, project_id, source_system, external_name, normalized_name, status, created_at
        )
        VALUES (1, 1, 1, 'worklog', 'Old External', 'oldexternal', 'pending', datetime('now', '-31 days'))
        """
    )
    conn.execute(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, raw_payload_json, issue_type, status, created_at
        )
        VALUES (1, 1, 1, 'import_worklog', '{}', 'station_not_resolved', 'pending', datetime('now', '-45 days'))
        """
    )
    conn.commit()
    conn.close()

    report = run_maintenance(db_path, expire_days=30, apply=False)

    assert report["mode"] == "dry-run"
    assert report["tables"]["station_name_mapping_proposals"] == 1
    assert report["tables"]["fault_import_review_queue"] == 1
    assert report["applied"] == {}

    conn = sqlite3.connect(db_path)
    try:
        statuses = conn.execute(
            """
            SELECT
                (SELECT status FROM station_name_mapping_proposals WHERE id = 1),
                (SELECT status FROM fault_import_review_queue WHERE id = 1)
            """
        ).fetchone()
    finally:
        conn.close()

    assert statuses == ("pending", "pending")


def test_maintenance_apply_expires_only_old_pending_records(tmp_path, monkeypatch):
    db_path = _prepare_db(tmp_path, monkeypatch)
    report_path = tmp_path / "maintenance_report.json"

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO import_batches (id, project_id, source_type, mode, file_count, success_count, fail_count)
        VALUES (1, 1, 'import_worklog', 'best-effort', 1, 0, 2)
        """
    )
    conn.executemany(
        """
        INSERT INTO station_name_mapping_proposals (
            id, import_batch_id, project_id, source_system, external_name, normalized_name, status, created_at
        )
        VALUES (?, 1, 1, 'worklog', ?, ?, ?, ?)
        """,
        [
            (1, "Old Proposal", "oldproposal", "pending", "2025-01-01 00:00:00"),
            (2, "Fresh Proposal", "freshproposal", "pending", "2099-01-01 00:00:00"),
            (3, "Done Proposal", "doneproposal", "approved", "2025-01-01 00:00:00"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO fault_import_review_queue (
            id, import_batch_id, project_id, source_type, raw_payload_json, issue_type, status, created_at
        )
        VALUES (?, 1, 1, 'import_worklog', '{}', 'station_not_resolved', ?, ?)
        """,
        [
            (1, "pending", "2025-01-01 00:00:00"),
            (2, "pending", "2099-01-01 00:00:00"),
            (3, "rejected", "2025-01-01 00:00:00"),
        ],
    )
    conn.commit()
    conn.close()

    report = run_maintenance(db_path, expire_days=30, apply=True, report_path=report_path)

    assert report["mode"] == "apply"
    assert report["tables"]["station_name_mapping_proposals"] == 1
    assert report["tables"]["fault_import_review_queue"] == 1
    assert report["applied"]["station_name_mapping_proposals"] == 1
    assert report["applied"]["fault_import_review_queue"] == 1
    assert report_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        proposal_statuses = conn.execute(
            "SELECT id, status, reviewed_at FROM station_name_mapping_proposals ORDER BY id"
        ).fetchall()
        queue_statuses = conn.execute(
            "SELECT id, status, reviewed_at FROM fault_import_review_queue ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert proposal_statuses[0][1] == "expired"
    assert proposal_statuses[0][2] is not None
    assert proposal_statuses[1][1] == "pending"
    assert proposal_statuses[2][1] == "approved"

    assert queue_statuses[0][1] == "expired"
    assert queue_statuses[0][2] is not None
    assert queue_statuses[1][1] == "pending"
    assert queue_statuses[2][1] == "rejected"
