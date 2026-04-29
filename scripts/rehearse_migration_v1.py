#!/usr/bin/env python3
"""Run a safe rehearsal of the Phase 1 migration on a cloned database."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from migrations.V1__multi_project import (
    backup_database,
    build_report,
    connect,
    run_apply,
    validate_post_migration,
)


def rehearse_migration(
    *,
    database: str | Path,
    output_dir: str | Path,
) -> dict:
    source_db = Path(database)
    if not source_db.exists():
        raise FileNotFoundError(f"database not found: {source_db}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    rehearsal_db = output_root / f"{source_db.stem}.rehearsal_{timestamp}{source_db.suffix}"
    dry_run_report_path = output_root / "migration_dry_run_report.json"
    apply_report_path = output_root / "migration_apply_report.json"
    rehearsal_backup_path = output_root / f"{source_db.stem}.rehearsal_pre_apply{source_db.suffix}"
    summary_path = output_root / "migration_rehearsal_summary.json"

    backup_database(source_db, rehearsal_db)

    rehearsal_conn = connect(rehearsal_db)
    try:
        dry_run_report = build_report(rehearsal_conn, rehearsal_db, mode="dry-run")
    finally:
        rehearsal_conn.close()

    dry_run_report_path.write_text(
        json.dumps(dry_run_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    apply_report = run_apply(
        rehearsal_db,
        report_path=apply_report_path,
        backup_path=rehearsal_backup_path,
    )

    rehearsal_conn = connect(rehearsal_db)
    try:
        post_validation = validate_post_migration(rehearsal_conn)
    finally:
        rehearsal_conn.close()

    source_conn = connect(source_db)
    try:
        source_check = build_report(source_conn, source_db, mode="source-check")
    finally:
        source_conn.close()

    summary = {
        "source_database": str(source_db),
        "rehearsal_database": str(rehearsal_db),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "artifacts": {
            "dry_run_report": str(dry_run_report_path),
            "apply_report": str(apply_report_path),
            "rehearsal_backup": str(rehearsal_backup_path),
        },
        "dry_run_action_count": len(dry_run_report.get("actions", [])),
        "apply_mode": apply_report.get("mode"),
        "apply_execution": apply_report.get("execution", {}),
        "post_validation": post_validation,
        "source_database_check": {
            "mode": source_check.get("mode"),
            "current_version": source_check.get("migration", {}).get("current_version"),
            "fault_reports_columns": source_check.get("fault_reports_columns", []),
            "photos_columns": source_check.get("photos_columns", []),
        },
        "conclusion": {
            "source_db_untouched": source_check.get("migration", {}).get("current_version") == 0,
            "rehearsal_apply_completed": bool(apply_report.get("execution", {}).get("applied")),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rehearse the V1 migration on a cloned SQLite database.")
    parser.add_argument("--database", default="station_monitor.db", help="Source SQLite database path")
    parser.add_argument(
        "--output-dir",
        default=str(Path("migrations") / "rehearsal_runs"),
        help="Directory used for cloned DB and rehearsal reports",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    summary = rehearse_migration(database=args.database, output_dir=args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
