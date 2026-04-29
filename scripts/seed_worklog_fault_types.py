#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path

from import_review_support import get_project_row, table_exists
from init_db import get_db_path
from utils import backup_sqlite_database, create_db_connection
from worklog_fault_types import classify_worklog_entry, get_catalog_for_project


PROJECT_CODES = ("unified", "inspection", "auxiliary")


def ensure_fault_type_tables(conn):
    required_tables = {"projects", "project_fault_type_versions", "project_fault_types", "fault_reports"}
    missing = [name for name in required_tables if not table_exists(conn, name)]
    if missing:
        raise RuntimeError(f"missing required tables: {', '.join(missing)}")


def create_and_publish_version(conn, project_code: str, *, description: str, force_new_version: bool = False):
    project = get_project_row(conn, project_code)
    if not project:
        raise RuntimeError(f"project not found: {project_code}")

    current_version_id = project.get("fault_type_version_id")
    if current_version_id and not force_new_version:
        return {"project_code": project_code, "project_id": project["id"], "version_id": current_version_id, "created": False}

    next_version = (
        conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM project_fault_type_versions WHERE project_id = ?",
            (project["id"],),
        ).fetchone()[0]
        or 1
    )
    cursor = conn.execute(
        """
        INSERT INTO project_fault_type_versions (project_id, version, description, is_published, published_at)
        VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
        """,
        (project["id"], next_version, description),
    )
    version_id = cursor.lastrowid
    conn.execute(
        "UPDATE project_fault_type_versions SET is_published = 0 WHERE project_id = ? AND id != ?",
        (project["id"], version_id),
    )
    for item in get_catalog_for_project(project_code):
        conn.execute(
            """
            INSERT INTO project_fault_types (
                version_id, type_code, type_label, semantic_group, sort_order, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                item["type_code"],
                item["type_label"],
                item["semantic_group"],
                item["sort_order"],
                item["is_active"],
            ),
        )
    conn.execute(
        "UPDATE projects SET fault_type_version_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (version_id, project["id"]),
    )
    return {"project_code": project_code, "project_id": project["id"], "version_id": version_id, "created": True}


def backfill_worklog_fault_reports(conn):
    rows = conn.execute(
        """
        SELECT id, project_id, system_type, description, fault_type, fault_type_label_snapshot
        FROM fault_reports
        WHERE source_type = 'import_worklog'
          AND deleted_at IS NULL
        ORDER BY id
        """
    ).fetchall()
    updated = []
    soft_deleted = []
    for row in rows:
        project_id = row["project_id"]
        if not project_id:
            continue
        version_row = conn.execute(
            "SELECT fault_type_version_id FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        version_id = version_row["fault_type_version_id"] if version_row else None
        if not version_id:
            continue

        fault_type = classify_worklog_entry(row["description"] or row["fault_type_label_snapshot"] or row["fault_type"])
        if not fault_type["is_fault"]:
            conn.execute(
                """
                UPDATE fault_reports
                SET deleted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row["id"],),
            )
            soft_deleted.append(
                {
                    "id": row["id"],
                    "project_id": project_id,
                    "reason": fault_type["reason"],
                }
            )
            continue
        conn.execute(
            """
            UPDATE fault_reports
            SET fault_type = ?,
                fault_type_label_snapshot = ?,
                fault_type_code = ?,
                fault_type_version_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                fault_type["type_label"],
                fault_type["type_label"],
                fault_type["type_code"],
                version_id,
                row["id"],
            ),
        )
        updated.append(
            {
                "id": row["id"],
                "project_id": project_id,
                "fault_type": fault_type["type_label"],
                "fault_type_code": fault_type["type_code"],
                "fault_type_version_id": version_id,
            }
        )
    return updated, soft_deleted


def main():
    parser = argparse.ArgumentParser(description="Seed and publish richer worklog fault types, then backfill worklog fault reports.")
    parser.add_argument("--database", default=get_db_path(), help="SQLite database path")
    parser.add_argument("--report", help="Optional JSON report path")
    parser.add_argument("--skip-backup", action="store_true", help="Skip automatic database backup")
    parser.add_argument("--force-new-version", action="store_true", help="Create and publish a new fault-type version even if one already exists")
    args = parser.parse_args()

    database_path = Path(args.database).resolve()
    report_path = Path(args.report).resolve() if args.report else None

    if not args.skip_backup:
        backup_sqlite_database(database_path, label="worklog_fault_types")

    conn = create_db_connection(database_path, row_factory=True, enable_wal=True)
    ensure_fault_type_tables(conn)

    try:
        conn.execute("BEGIN")
        version_results = []
        for project_code in PROJECT_CODES:
            version_results.append(
                create_and_publish_version(
                    conn,
                    project_code,
                    description="Seeded from 工作记录.xlsx field terminology fault categories",
                    force_new_version=args.force_new_version,
                )
            )
        updated_rows, soft_deleted_rows = backfill_worklog_fault_reports(conn)
        conn.commit()
    finally:
        conn.close()

    report = {
        "database": str(database_path),
        "versions": version_results,
        "backfilled_count": len(updated_rows),
        "backfilled_rows": updated_rows,
        "soft_deleted_count": len(soft_deleted_rows),
        "soft_deleted_rows": soft_deleted_rows,
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
