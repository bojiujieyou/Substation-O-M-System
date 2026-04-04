#!/usr/bin/env python3
"""Expire stale pending review items and station mapping proposals."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from utils import create_db_connection


REVIEW_TABLES = [
    {
        "table": "fault_import_review_queue",
        "status_column": "status",
        "created_at_column": "created_at",
        "reviewed_at_column": "reviewed_at",
        "extra_where": "",
    },
    {
        "table": "station_name_mapping_proposals",
        "status_column": "status",
        "created_at_column": "created_at",
        "reviewed_at_column": "reviewed_at",
        "extra_where": "",
    },
]


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def get_pending_expired_counts(conn, *, expire_days: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    modifier = f"-{expire_days} days"
    for spec in REVIEW_TABLES:
        table_name = spec["table"]
        if not table_exists(conn, table_name):
            counts[table_name] = 0
            continue
        count = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {table_name}
            WHERE {spec["status_column"]} = 'pending'
              AND datetime({spec["created_at_column"]}) <= datetime('now', ?)
              {spec["extra_where"]}
            """,
            (modifier,),
        ).fetchone()[0]
        counts[table_name] = int(count or 0)
    return counts


def expire_pending_records(conn, *, expire_days: int) -> dict[str, int]:
    updated: dict[str, int] = {}
    modifier = f"-{expire_days} days"
    for spec in REVIEW_TABLES:
        table_name = spec["table"]
        if not table_exists(conn, table_name):
            updated[table_name] = 0
            continue
        cursor = conn.execute(
            f"""
            UPDATE {table_name}
            SET {spec["status_column"]} = 'expired',
                {spec["reviewed_at_column"]} = CURRENT_TIMESTAMP
            WHERE {spec["status_column"]} = 'pending'
              AND datetime({spec["created_at_column"]}) <= datetime('now', ?)
              {spec["extra_where"]}
            """,
            (modifier,),
        )
        updated[table_name] = cursor.rowcount or 0
    return updated


def build_report(*, database: Path, expire_days: int, mode: str, counts: dict[str, int], applied: dict[str, int] | None = None) -> dict:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": str(database),
        "expire_days": expire_days,
        "mode": mode,
        "tables": counts,
        "applied": applied or {},
    }


def write_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_maintenance(database: Path, *, expire_days: int = 30, apply: bool = False, report_path: Path | None = None) -> dict:
    conn = create_db_connection(database, row_factory=True)
    try:
        pending_counts = get_pending_expired_counts(conn, expire_days=expire_days)
        applied_counts = None
        mode = "dry-run"
        if apply:
            applied_counts = expire_pending_records(conn, expire_days=expire_days)
            conn.commit()
            mode = "apply"
        report = build_report(
            database=database,
            expire_days=expire_days,
            mode=mode,
            counts=pending_counts,
            applied=applied_counts,
        )
        if report_path:
            write_report(report_path, report)
        return report
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expire stale import review records")
    parser.add_argument("--database", default="station_monitor.db", help="Path to SQLite database")
    parser.add_argument("--expire-days", type=int, default=30, help="Pending lifetime in days")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview how many rows would expire (default)")
    mode.add_argument("--apply", action="store_true", help="Expire matched rows")
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
        expire_days=args.expire_days,
        apply=args.apply,
        report_path=Path(args.report).resolve() if args.report else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
