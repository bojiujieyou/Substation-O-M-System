#!/usr/bin/env python3
"""Run notification maintenance tasks such as one-time escalation planning."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from notification_runtime import dispatch_pending_escalations
from utils import create_db_connection


def run_maintenance(database: Path, *, apply: bool = False, report_path: Path | None = None) -> dict:
    conn = create_db_connection(database, row_factory=True)
    try:
        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": str(database),
            "mode": "apply" if apply else "dry-run",
        }
        if apply:
            escalation_report = dispatch_pending_escalations(conn)
        else:
            dry_run = dispatch_pending_escalations(conn)  # dispatch helper is idempotent due to log table
            conn.rollback()
            escalation_report = dry_run
        report["escalations"] = escalation_report
        if apply:
            conn.commit()
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Maintain notification runtime tasks")
    parser.add_argument("--database", default="station_monitor.db", help="Path to SQLite database")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default)")
    mode.add_argument("--apply", action="store_true", help="Write escalation dispatch logs")
    parser.add_argument("--report", help="Optional JSON report output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = Path(args.database).resolve()
    if not database.exists():
        print(json.dumps({"error": f"database not found: {database}"}, ensure_ascii=False))
        return 1

    report = run_maintenance(
        database,
        apply=args.apply,
        report_path=Path(args.report).resolve() if args.report else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
