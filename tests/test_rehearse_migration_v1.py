import sqlite3
from pathlib import Path

from config import Config
from init_db import init_db
from rehearse_migration_v1 import rehearse_migration


def _init_temp_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "rehearsal_source.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db() is True
    return db_path


def test_rehearse_migration_uses_clone_and_leaves_source_untouched(tmp_path, monkeypatch):
    source_db = _init_temp_db(tmp_path, monkeypatch)
    output_dir = tmp_path / "rehearsal_output"

    summary = rehearse_migration(database=source_db, output_dir=output_dir)

    rehearsal_db = Path(summary["rehearsal_database"])
    assert rehearsal_db.exists()
    assert Path(summary["artifacts"]["dry_run_report"]).exists()
    assert Path(summary["artifacts"]["apply_report"]).exists()
    assert Path(summary["artifacts"]["rehearsal_backup"]).exists()
    assert Path(output_dir / "migration_rehearsal_summary.json").exists()

    assert summary["conclusion"]["source_db_untouched"] is True
    assert summary["conclusion"]["rehearsal_apply_completed"] is True
    assert summary["post_validation"]["fault_reports_camera_id_orphans"] == 0
    assert summary["post_validation"]["active_camera_slot_conflicts"] == 0

    source_conn = sqlite3.connect(source_db)
    try:
        source_tables = {
            row[0]
            for row in source_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        source_photo_columns = {
            row[1] for row in source_conn.execute("PRAGMA table_info(photos)").fetchall()
        }
    finally:
        source_conn.close()

    assert "projects" not in source_tables
    assert "schema_migrations" not in source_tables
    assert "project_id" not in source_photo_columns
    assert "project_hint" not in source_photo_columns

    rehearsal_conn = sqlite3.connect(rehearsal_db)
    try:
        rehearsal_tables = {
            row[0]
            for row in rehearsal_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        rehearsal_photo_columns = {
            row[1] for row in rehearsal_conn.execute("PRAGMA table_info(photos)").fetchall()
        }
    finally:
        rehearsal_conn.close()

    assert "projects" in rehearsal_tables
    assert "schema_migrations" in rehearsal_tables
    assert {"project_id", "project_hint"}.issubset(rehearsal_photo_columns)
