from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migrations.V1__multi_project import (
    FAULT_REPORT_COLUMN_PLAN,
    INDEX_PLAN,
    MIGRATION_VERSION,
    NEW_TABLE_SQL,
    PHOTO_COLUMN_PLAN,
    get_columns,
    get_current_version,
    index_exists,
    table_exists,
    validate_post_migration,
    view_exists,
)
from utils import create_db_connection


REQUIRED_CAMERA_COLUMNS = {
    "id",
    "slot_id",
    "station_id",
    "project_id",
    "project_camera_code",
    "camera_index",
    "area",
    "location_desc",
    "ip_address",
    "channel_port",
    "channel_number",
    "status",
    "replaced_by_camera_id",
    "retired_at",
    "created_at",
}

REQUIRED_VIEWS = {
    "v_camera_slots_with_current_camera",
}


def _list_missing(required: set[str], actual: set[str]) -> list[str]:
    return sorted(required - actual)


def _compute_critical_checks(conn) -> dict[str, int]:
    checks = {
        "fault_reports_camera_id_orphans": conn.execute(
            """
            SELECT COUNT(*)
            FROM fault_reports
            WHERE camera_id IS NOT NULL
              AND camera_id NOT IN (SELECT id FROM cameras)
            """
        ).fetchone()[0],
        "fault_reports_station_id_orphans": conn.execute(
            """
            SELECT COUNT(*)
            FROM fault_reports
            WHERE station_id IS NOT NULL
              AND station_id NOT IN (SELECT id FROM stations)
            """
        ).fetchone()[0],
        "cameras_station_id_orphans": conn.execute(
            """
            SELECT COUNT(*)
            FROM cameras
            WHERE station_id IS NOT NULL
              AND station_id NOT IN (SELECT id FROM stations)
            """
        ).fetchone()[0],
    }
    if table_exists(conn, "project_notification_configs") and table_exists(conn, "project_notification_policies"):
        checks["notification_config_policy_orphans"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM project_notification_configs c
            LEFT JOIN project_notification_policies p ON p.id = c.policy_id
            WHERE c.policy_id IS NOT NULL
              AND p.id IS NULL
            """
        ).fetchone()[0]
    else:
        checks["notification_config_policy_orphans"] = -1
    return checks


def _build_summary(report: dict[str, Any]) -> dict[str, Any]:
    current_version = report["migration"]["current_version"]
    structural_missing = (
        len(report["tables"]["missing"])
        + len(report["views"]["missing"])
        + len(report["indexes"]["missing"])
        + len(report["columns"]["cameras"]["missing"])
        + len(report["columns"]["fault_reports"]["missing"])
        + len(report["columns"]["photos"]["missing"])
    )

    critical_values = [
        value
        for value in report["critical_checks"].values()
        if isinstance(value, int) and value >= 0
    ]
    critical_failures = sum(1 for value in critical_values if value != 0)

    if current_version >= MIGRATION_VERSION and structural_missing == 0 and critical_failures == 0:
        status = "ready"
    elif current_version == 0 and report["tables"]["present"] < len(NEW_TABLE_SQL):
        status = "not_migrated"
    elif critical_failures > 0:
        status = "failed"
    else:
        status = "partial"

    next_actions: list[str] = []
    if status == "not_migrated":
        next_actions.append("先执行 Phase 1 迁移演练或正式迁移，再重新校验。")
    if report["tables"]["missing"]:
        next_actions.append(f"补齐缺失表：{', '.join(report['tables']['missing'])}")
    if report["columns"]["cameras"]["missing"]:
        next_actions.append("`cameras` 尚未达到冻结版结构，需完成重建步骤。")
    if report["columns"]["fault_reports"]["missing"]:
        next_actions.append("`fault_reports` 尚未完成新增字段补齐。")
    if report["columns"]["photos"]["missing"]:
        next_actions.append("`photos.project_id/project_hint` 尚未补齐。")
    for name, value in report["critical_checks"].items():
        if isinstance(value, int) and value > 0:
            next_actions.append(f"修复关键完整性问题：{name}={value}")
    if not next_actions:
        next_actions.append("当前库已满足 Phase 1 结构校验，可进入下一阶段验收。")

    return {
        "status": status,
        "structural_missing_count": structural_missing,
        "critical_failure_count": critical_failures,
        "next_actions": next_actions,
    }


def validate_phase1_database(
    *,
    database: str | Path,
    report_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> dict[str, Any]:
    db_path = Path(database)
    conn = create_db_connection(db_path, row_factory=True)
    try:
        required_tables = set(NEW_TABLE_SQL.keys())
        actual_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        present_tables = sorted(required_tables & actual_tables)
        missing_tables = sorted(required_tables - actual_tables)

        actual_indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            if row["name"]
        }
        required_indexes = set(INDEX_PLAN.keys())
        present_indexes = sorted(required_indexes & actual_indexes)
        missing_indexes = sorted(required_indexes - actual_indexes)

        actual_views = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        present_views = sorted(REQUIRED_VIEWS & actual_views)
        missing_views = sorted(REQUIRED_VIEWS - actual_views)

        cameras_columns = get_columns(conn, "cameras") if table_exists(conn, "cameras") else set()
        fault_report_columns = get_columns(conn, "fault_reports") if table_exists(conn, "fault_reports") else set()
        photos_columns = get_columns(conn, "photos") if table_exists(conn, "photos") else set()

        report = {
            "database": str(db_path.resolve()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "connection": {
                "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
            },
            "migration": {
                "target_version": MIGRATION_VERSION,
                "current_version": get_current_version(conn) if table_exists(conn, "schema_migrations") else 0,
            },
            "tables": {
                "required": sorted(required_tables),
                "present": len(present_tables),
                "missing": missing_tables,
            },
            "views": {
                "required": sorted(REQUIRED_VIEWS),
                "missing": missing_views,
            },
            "indexes": {
                "required": sorted(required_indexes),
                "missing": missing_indexes,
            },
            "columns": {
                "cameras": {
                    "required_count": len(REQUIRED_CAMERA_COLUMNS),
                    "missing": _list_missing(REQUIRED_CAMERA_COLUMNS, cameras_columns),
                },
                "fault_reports": {
                    "required_count": len(FAULT_REPORT_COLUMN_PLAN),
                    "missing": _list_missing(set(FAULT_REPORT_COLUMN_PLAN.keys()), fault_report_columns),
                },
                "photos": {
                    "required_count": len(PHOTO_COLUMN_PLAN),
                    "missing": _list_missing(set(PHOTO_COLUMN_PLAN.keys()), photos_columns),
                },
            },
            "critical_checks": _compute_critical_checks(conn),
            "post_validation": validate_post_migration(conn)
            if table_exists(conn, "projects") and table_exists(conn, "camera_slots")
            else None,
            "project_seed_check": {
                "missing_project_codes": [],
            },
        }

        if table_exists(conn, "projects"):
            project_codes = {
                row["code"] for row in conn.execute("SELECT code FROM projects").fetchall()
            }
            report["project_seed_check"]["missing_project_codes"] = sorted(
                {"unified", "inspection", "auxiliary"} - project_codes
            )

        report["summary"] = _build_summary(report)

        if report_path:
            output_path = Path(report_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if summary_path:
            output_path = Path(summary_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "# Phase 1 DB Validation Summary",
                "",
                f"- Database: `{db_path.resolve()}`",
                f"- Current version: `{report['migration']['current_version']}`",
                f"- Target version: `{report['migration']['target_version']}`",
                f"- Foreign keys: `{report['connection']['foreign_keys']}`",
                f"- Overall status: `{report['summary']['status']}`",
                f"- Structural missing count: `{report['summary']['structural_missing_count']}`",
                f"- Critical failure count: `{report['summary']['critical_failure_count']}`",
                "",
                "## Missing Structure",
                "",
                f"- Tables: `{', '.join(report['tables']['missing']) or 'none'}`",
                f"- Views: `{', '.join(report['views']['missing']) or 'none'}`",
                f"- Indexes: `{', '.join(report['indexes']['missing']) or 'none'}`",
                f"- Cameras columns: `{', '.join(report['columns']['cameras']['missing']) or 'none'}`",
                f"- Fault report columns: `{', '.join(report['columns']['fault_reports']['missing']) or 'none'}`",
                f"- Photo columns: `{', '.join(report['columns']['photos']['missing']) or 'none'}`",
                "",
                "## Next Actions",
                "",
            ]
            for action in report["summary"]["next_actions"]:
                lines.append(f"- {action}")
            if report["post_validation"]:
                lines.extend(
                    [
                        "",
                        "## Post Validation",
                        "",
                    ]
                )
                for key, value in sorted(report["post_validation"].items()):
                    lines.append(f"- {key}: `{value}`")
            output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        return report
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验数据库是否满足 Phase 1 冻结版结构")
    parser.add_argument("--database", default="station_monitor.db", help="SQLite 数据库路径")
    parser.add_argument("--report", help="输出 JSON 报告")
    parser.add_argument("--summary", help="输出 Markdown 摘要")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate_phase1_database(
        database=args.database,
        report_path=args.report,
        summary_path=args.summary,
    )
    print(f"status: {report['summary']['status']}")
    print(f"structural_missing_count: {report['summary']['structural_missing_count']}")
    print(f"critical_failure_count: {report['summary']['critical_failure_count']}")
    if args.report:
        print(f"json: {args.report}")
    if args.summary:
        print(f"summary: {args.summary}")
    return 0 if report["summary"]["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
