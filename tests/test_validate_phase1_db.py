import json
from pathlib import Path

from config import Config
from init_db import init_db
from migrations.V1__multi_project import run_apply
from validate_phase1_db import validate_phase1_database


def _init_temp_db(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "validate_phase1.db"
    monkeypatch.setattr(Config, "DATABASE_PATH", str(db_path))
    assert init_db() is True
    return db_path


def test_validate_phase1_db_reports_legacy_database_as_not_migrated(tmp_path, monkeypatch):
    db_path = _init_temp_db(tmp_path, monkeypatch)
    report_path = tmp_path / "legacy_validation.json"
    summary_path = tmp_path / "legacy_validation.md"

    report = validate_phase1_database(
        database=db_path,
        report_path=report_path,
        summary_path=summary_path,
    )

    assert report["summary"]["status"] == "not_migrated"
    assert "projects" in report["tables"]["missing"]
    assert "slot_id" in report["columns"]["cameras"]["missing"]
    assert "project_id" in report["columns"]["fault_reports"]["missing"]
    assert report["connection"]["foreign_keys"] == 1
    assert report_path.exists()
    assert summary_path.exists()


def test_validate_phase1_db_reports_migrated_database_as_ready(tmp_path, monkeypatch):
    db_path = _init_temp_db(tmp_path, monkeypatch)
    run_apply(
        db_path,
        report_path=tmp_path / "migration_report.json",
        backup_path=tmp_path / "migration_backup.db",
    )

    report = validate_phase1_database(database=db_path)

    assert report["summary"]["status"] == "ready"
    assert report["tables"]["missing"] == []
    assert report["views"]["missing"] == []
    assert report["indexes"]["missing"] == []
    assert report["columns"]["cameras"]["missing"] == []
    assert report["columns"]["fault_reports"]["missing"] == []
    assert report["columns"]["photos"]["missing"] == []
    assert report["critical_checks"]["fault_reports_camera_id_orphans"] == 0
    assert report["post_validation"]["active_camera_slot_conflicts"] == 0
    assert report["project_seed_check"]["missing_project_codes"] == []


def test_validate_phase1_db_writes_machine_readable_report(tmp_path, monkeypatch):
    db_path = _init_temp_db(tmp_path, monkeypatch)
    report_path = tmp_path / "phase1_validation.json"

    validate_phase1_database(database=db_path, report_path=report_path)

    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded["database"].endswith("validate_phase1.db")
    assert loaded["summary"]["status"] == "not_migrated"
