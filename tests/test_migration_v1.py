import sqlite3
from pathlib import Path

from config import Config
from init_db import init_db
from migrations.V1__multi_project import build_report, connect, run_apply


def _init_temp_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "migration_v1.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db() is True
    return db_path


def test_dry_run_plans_photo_project_columns(tmp_path, monkeypatch):
    db_path = _init_temp_db(tmp_path, monkeypatch)

    conn = connect(db_path)
    try:
        report = build_report(conn, db_path, mode="dry-run")
    finally:
        conn.close()

    photo_targets = {
        action["target"]
        for action in report["actions"]
        if action["target"].startswith("photos.")
    }
    assert "photos.project_id" in photo_targets
    assert "photos.project_hint" in photo_targets
    assert "project_id" not in report["photos_columns"]
    assert "project_hint" not in report["photos_columns"]


def test_run_apply_adds_photo_project_columns(tmp_path, monkeypatch):
    db_path = _init_temp_db(tmp_path, monkeypatch)
    report_path = tmp_path / "migration_report.json"
    backup_path = tmp_path / "migration_backup.db"

    report = run_apply(db_path, report_path=report_path, backup_path=backup_path)

    assert report["execution"]["applied"] is True
    assert set(report["execution"]["added_photo_columns"]) == {"project_id", "project_hint"}
    assert report_path.exists()
    assert backup_path.exists()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(photos)").fetchall()}
    finally:
        conn.close()

    assert {"project_id", "project_hint"}.issubset(columns)
