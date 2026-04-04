#!/usr/bin/env python3
"""Phase 1 migration for the multi-project V1 rollout."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import create_db_connection


MIGRATION_VERSION = 1
MIGRATION_NAME = "V1__multi_project.py"
MIGRATION_DESCRIPTION = "Phase 1 executable migration for multi-project V1"

NEW_TABLE_SQL: dict[str, str] = {
    "schema_migrations": """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL UNIQUE,
            script_name TEXT NOT NULL,
            description TEXT,
            checksum TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "projects": """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#1a73e8',
            fault_type_version_id INTEGER,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "project_fault_type_versions": """
        CREATE TABLE IF NOT EXISTS project_fault_type_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            description TEXT,
            is_published INTEGER DEFAULT 0,
            published_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, version),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
    "project_fault_types": """
        CREATE TABLE IF NOT EXISTS project_fault_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            type_code TEXT NOT NULL,
            type_label TEXT NOT NULL,
            semantic_group TEXT,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(version_id, type_code),
            FOREIGN KEY (version_id) REFERENCES project_fault_type_versions(id)
        );
    """,
    "camera_slots": """
        CREATE TABLE IF NOT EXISTS camera_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_code TEXT NOT NULL,
            station_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            location_desc TEXT NOT NULL DEFAULT '',
            area TEXT NOT NULL DEFAULT '',
            channel_number INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(station_id, project_id, slot_code),
            UNIQUE(station_id, project_id, location_desc, area, channel_number),
            FOREIGN KEY (station_id) REFERENCES stations(id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
    "project_notification_policies": """
        CREATE TABLE IF NOT EXISTS project_notification_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            quiet_hours_json TEXT,
            notify_on_create INTEGER DEFAULT 1,
            notify_on_close INTEGER DEFAULT 1,
            escalate_after_minutes INTEGER,
            escalation_target_config_id INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
    "project_notification_configs": """
        CREATE TABLE IF NOT EXISTS project_notification_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            target_value TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            deduplication_window_minutes INTEGER DEFAULT 60,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (policy_id) REFERENCES project_notification_policies(id)
        );
    """,
    "user_project_scopes": """
        CREATE TABLE IF NOT EXISTS user_project_scopes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            can_write INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, project_id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
    "station_external_names": """
        CREATE TABLE IF NOT EXISTS station_external_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            source_system TEXT NOT NULL,
            external_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_system, external_name),
            FOREIGN KEY (station_id) REFERENCES stations(id)
        );
    """,
    "import_batches": """
        CREATE TABLE IF NOT EXISTS import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            file_count INTEGER,
            success_count INTEGER,
            fail_count INTEGER,
            report_path TEXT,
            operator_id INTEGER,
            timezone_default_used TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
    "station_name_mapping_proposals": """
        CREATE TABLE IF NOT EXISTS station_name_mapping_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (import_batch_id) REFERENCES import_batches(id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
    "fault_import_review_queue": """
        CREATE TABLE IF NOT EXISTS fault_import_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (import_batch_id) REFERENCES import_batches(id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
    """,
}

CAMERAS_TABLE_SQL = """
    CREATE TABLE cameras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slot_id INTEGER,
        station_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        project_camera_code TEXT,
        camera_index TEXT,
        area TEXT,
        location_desc TEXT,
        ip_address TEXT,
        channel_port INTEGER,
        channel_number INTEGER,
        status TEXT DEFAULT 'active',
        replaced_by_camera_id INTEGER,
        retired_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (slot_id) REFERENCES camera_slots(id),
        FOREIGN KEY (station_id) REFERENCES stations(id),
        FOREIGN KEY (project_id) REFERENCES projects(id)
    );
"""

CURRENT_CAMERA_VIEW_SQL = """
    CREATE VIEW v_camera_slots_with_current_camera AS
    SELECT
        s.id AS slot_id,
        s.slot_code,
        s.station_id,
        s.project_id,
        s.location_desc,
        s.area,
        s.channel_number,
        c.id AS current_camera_id,
        c.project_camera_code,
        c.ip_address,
        c.camera_index
    FROM camera_slots s
    LEFT JOIN cameras c
        ON c.slot_id = s.id
       AND c.status = 'active';
"""

PROJECT_SEEDS = [
    ("unified", "统一平台", "统一", "#1a73e8", 1),
    ("inspection", "智慧巡视", "巡视", "#34a853", 2),
    ("auxiliary", "辅控系统", "辅控", "#ea4335", 3),
]

FAULT_REPORT_COLUMN_PLAN = {
    "project_id": "ALTER TABLE fault_reports ADD COLUMN project_id INTEGER;",
    "camera_slot_id": "ALTER TABLE fault_reports ADD COLUMN camera_slot_id INTEGER;",
    "assigned_to": "ALTER TABLE fault_reports ADD COLUMN assigned_to INTEGER;",
    "fault_type_code": "ALTER TABLE fault_reports ADD COLUMN fault_type_code TEXT;",
    "fault_type_label_snapshot": "ALTER TABLE fault_reports ADD COLUMN fault_type_label_snapshot TEXT;",
    "fault_type_version_id": "ALTER TABLE fault_reports ADD COLUMN fault_type_version_id INTEGER;",
    "source_type": "ALTER TABLE fault_reports ADD COLUMN source_type TEXT DEFAULT 'manual';",
    "source_batch_id": "ALTER TABLE fault_reports ADD COLUMN source_batch_id TEXT;",
    "source_record_key": "ALTER TABLE fault_reports ADD COLUMN source_record_key TEXT;",
    "project_device_code": "ALTER TABLE fault_reports ADD COLUMN project_device_code TEXT;",
    "handling_started_at": "ALTER TABLE fault_reports ADD COLUMN handling_started_at TIMESTAMP;",
    "source_time_raw": "ALTER TABLE fault_reports ADD COLUMN source_time_raw TEXT;",
    "source_timezone": "ALTER TABLE fault_reports ADD COLUMN source_timezone TEXT;",
    "tags_json": "ALTER TABLE fault_reports ADD COLUMN tags_json TEXT;",
}

PHOTO_COLUMN_PLAN = {
    "project_id": "ALTER TABLE photos ADD COLUMN project_id INTEGER;",
    "project_hint": "ALTER TABLE photos ADD COLUMN project_hint TEXT;",
}

FAULT_REPORT_EXISTING_COLUMNS = {"status", "closed_at", "system_type", "idempotency_key"}

INDEX_PLAN = {
    "idx_fault_source_record_key": """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fault_source_record_key
        ON fault_reports(source_record_key)
        WHERE source_record_key IS NOT NULL;
    """,
    "idx_cameras_one_active_per_slot": """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_one_active_per_slot
        ON cameras(slot_id)
        WHERE status = 'active';
    """,
}


@dataclass
class PlannedAction:
    category: str
    target: str
    detail: str
    sql: str | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    return create_db_connection(db_path, row_factory=True)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    return row is not None


def view_exists(conn: sqlite3.Connection, view_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?",
        (view_name,),
    ).fetchone()
    return row is not None


def get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def get_counts(conn: sqlite3.Connection, table_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in table_names:
        if table_exists(conn, table_name):
            counts[table_name] = conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]
    return counts


def get_current_version(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "schema_migrations"):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def get_script_checksum() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def backup_database(db_path: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()


def plan_new_tables(conn: sqlite3.Connection) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    for table_name, sql in NEW_TABLE_SQL.items():
        if table_exists(conn, table_name):
            actions.append(
                PlannedAction("skip-existing-table", table_name, "table already exists")
            )
        else:
            actions.append(
                PlannedAction("create-table", table_name, "create missing target table", sql)
            )
    return actions


def plan_fault_report_columns(conn: sqlite3.Connection) -> list[PlannedAction]:
    columns = get_columns(conn, "fault_reports")
    actions: list[PlannedAction] = []

    for column_name, sql in FAULT_REPORT_COLUMN_PLAN.items():
        if column_name in columns:
            actions.append(
                PlannedAction(
                    "skip-existing-column",
                    f"fault_reports.{column_name}",
                    "column already exists",
                )
            )
        else:
            actions.append(
                PlannedAction(
                    "exists-check-add-column",
                    f"fault_reports.{column_name}",
                    "safe only after runtime column existence check",
                    sql,
                )
            )

    for column_name in sorted(FAULT_REPORT_EXISTING_COLUMNS):
        if column_name in columns:
            actions.append(
                PlannedAction(
                    "baseline-confirmed-column",
                    f"fault_reports.{column_name}",
                    "documented as existing; do not add again",
                )
            )

    if "camera_location_text" in columns:
        actions.append(
            PlannedAction(
                "preserve-legacy-column",
                "fault_reports.camera_location_text",
                "live-only legacy field detected; preserve through V1",
            )
        )

    return actions


def plan_photo_columns(conn: sqlite3.Connection) -> list[PlannedAction]:
    columns = get_columns(conn, "photos")
    actions: list[PlannedAction] = []

    for column_name, sql in PHOTO_COLUMN_PLAN.items():
        if column_name in columns:
            actions.append(
                PlannedAction(
                    "skip-existing-column",
                    f"photos.{column_name}",
                    "column already exists",
                )
            )
        else:
            actions.append(
                PlannedAction(
                    "exists-check-add-column",
                    f"photos.{column_name}",
                    "safe only after runtime column existence check",
                    sql,
                )
            )

    return actions


def plan_indexes(conn: sqlite3.Connection) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    for index_name, sql in INDEX_PLAN.items():
        if index_exists(conn, index_name):
            actions.append(
                PlannedAction("skip-existing-index", index_name, "index already exists")
            )
        else:
            actions.append(
                PlannedAction("create-index", index_name, "create missing target index", sql)
            )
    return actions


def plan_cameras_rebuild(conn: sqlite3.Connection) -> list[PlannedAction]:
    columns = get_columns(conn, "cameras")
    required_missing = [
        column_name
        for column_name in [
            "slot_id",
            "project_id",
            "project_camera_code",
            "status",
            "replaced_by_camera_id",
            "retired_at",
        ]
        if column_name not in columns
    ]

    if required_missing:
        detail = (
            "preserve existing ids during rebuild; required missing columns: "
            + ", ".join(required_missing)
        )
        category = "rebuild-table"
    else:
        detail = "table already matches required Phase 1 shape"
        category = "skip-rebuild"

    return [
        PlannedAction(category, "cameras", detail),
        PlannedAction(
            "validation",
            "fault_reports.camera_id",
            "post-rebuild orphan check must return zero",
            "SELECT COUNT(*) FROM fault_reports WHERE camera_id IS NOT NULL AND camera_id NOT IN (SELECT id FROM cameras);",
        ),
    ]


def build_report(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    mode: str,
    notes: list[str] | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live_tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    counts = get_counts(conn, live_tables)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_path": str(db_path),
        "mode": mode,
        "migration": {
            "version": MIGRATION_VERSION,
            "name": MIGRATION_NAME,
            "description": MIGRATION_DESCRIPTION,
            "current_version": get_current_version(conn),
            "checksum": get_script_checksum(),
        },
        "live_tables": live_tables,
        "live_counts": counts,
        "fault_reports_columns": sorted(get_columns(conn, "fault_reports")),
        "cameras_columns": sorted(get_columns(conn, "cameras")),
        "photos_columns": sorted(get_columns(conn, "photos")),
        "actions": [
            asdict(action)
            for action in (
                plan_new_tables(conn)
                + plan_fault_report_columns(conn)
                + plan_photo_columns(conn)
                + plan_indexes(conn)
                + plan_cameras_rebuild(conn)
            )
        ],
    }
    if notes:
        report["notes"] = notes
    if execution is not None:
        report["execution"] = execution
    return report


def write_report(report_path: Path, payload: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def execute_sql(conn: sqlite3.Connection, sql: str) -> None:
    conn.execute(sql)


def ensure_new_tables(conn: sqlite3.Connection) -> list[str]:
    created: list[str] = []
    for table_name, sql in NEW_TABLE_SQL.items():
        existed = table_exists(conn, table_name)
        execute_sql(conn, sql)
        if not existed:
            created.append(table_name)
    return created


def ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    execute_sql(conn, NEW_TABLE_SQL["schema_migrations"])


def seed_projects(conn: sqlite3.Connection) -> int:
    inserted = 0
    for code, name, short_name, color, sort_order in PROJECT_SEEDS:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO projects
                (code, name, short_name, color, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (code, name, short_name, color, sort_order),
        )
        inserted += conn.total_changes - before
    return inserted


def get_project_id_by_code(conn: sqlite3.Connection, code: str) -> int:
    row = conn.execute("SELECT id FROM projects WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise RuntimeError(f"Project seed missing: {code}")
    return int(row["id"])


def ensure_fault_report_columns(conn: sqlite3.Connection) -> list[str]:
    added: list[str] = []
    columns = get_columns(conn, "fault_reports")
    for column_name, sql in FAULT_REPORT_COLUMN_PLAN.items():
        if column_name not in columns:
            execute_sql(conn, sql)
            added.append(column_name)
            columns.add(column_name)
    return added


def ensure_photo_columns(conn: sqlite3.Connection) -> list[str]:
    added: list[str] = []
    columns = get_columns(conn, "photos")
    for column_name, sql in PHOTO_COLUMN_PLAN.items():
        if column_name not in columns:
            execute_sql(conn, sql)
            added.append(column_name)
            columns.add(column_name)
    return added


def create_fault_indexes(conn: sqlite3.Connection) -> list[str]:
    created: list[str] = []
    for index_name, sql in INDEX_PLAN.items():
        if index_name == "idx_cameras_one_active_per_slot":
            continue
        existed = index_exists(conn, index_name)
        execute_sql(conn, sql)
        if not existed:
            created.append(index_name)
    return created


def validate_legacy_slot_conflicts(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT station_id,
               COALESCE(location_desc, '') AS location_desc,
               COALESCE(area, '') AS area,
               channel_number,
               COUNT(*) AS cnt
        FROM cameras
        GROUP BY station_id, COALESCE(location_desc, ''), COALESCE(area, ''), channel_number
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchall()
    if rows:
        row = rows[0]
        raise RuntimeError(
            "Legacy cameras contain duplicate slot tuples; migration aborted. "
            f"station_id={row['station_id']}, location_desc={row['location_desc']!r}, "
            f"area={row['area']!r}, channel_number={row['channel_number']!r}, cnt={row['cnt']}"
        )


def ensure_legacy_camera_slots(conn: sqlite3.Connection, unified_project_id: int) -> int:
    if "slot_id" in get_columns(conn, "cameras"):
        return 0

    validate_legacy_slot_conflicts(conn)
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO camera_slots
            (slot_code, station_id, project_id, location_desc, area, channel_number)
        SELECT
            'MIGRATED_' || id,
            station_id,
            ?,
            COALESCE(location_desc, ''),
            COALESCE(area, ''),
            channel_number
        FROM cameras
        """,
        (unified_project_id,),
    )
    return conn.total_changes - before


def rebuild_cameras_table(conn: sqlite3.Connection, unified_project_id: int) -> dict[str, Any]:
    current_columns = get_columns(conn, "cameras")
    required_columns = {
        "slot_id",
        "project_id",
        "project_camera_code",
        "status",
        "replaced_by_camera_id",
        "retired_at",
    }
    if required_columns.issubset(current_columns):
        if not index_exists(conn, "idx_cameras_one_active_per_slot"):
            execute_sql(conn, INDEX_PLAN["idx_cameras_one_active_per_slot"])
        conn.execute("DROP VIEW IF EXISTS v_camera_slots_with_current_camera")
        conn.execute(CURRENT_CAMERA_VIEW_SQL)
        return {"rebuilt": False, "reason": "target columns already exist"}

    old_count = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("DROP TABLE IF EXISTS cameras_v2")
        conn.execute(CAMERAS_TABLE_SQL.replace("CREATE TABLE cameras", "CREATE TABLE cameras_v2"))
        conn.execute(
            """
            INSERT INTO cameras_v2 (
                id,
                slot_id,
                station_id,
                project_id,
                project_camera_code,
                camera_index,
                area,
                location_desc,
                ip_address,
                channel_port,
                channel_number,
                status,
                replaced_by_camera_id,
                retired_at,
                created_at
            )
            SELECT
                c.id,
                s.id,
                c.station_id,
                ?,
                NULL,
                c.camera_index,
                c.area,
                c.location_desc,
                c.ip_address,
                c.channel_port,
                c.channel_number,
                'active',
                NULL,
                NULL,
                c.created_at
            FROM cameras c
            JOIN camera_slots s
              ON s.slot_code = 'MIGRATED_' || c.id
             AND s.project_id = ?
            """,
            (unified_project_id, unified_project_id),
        )
        conn.execute("DROP TABLE cameras")
        conn.execute("ALTER TABLE cameras_v2 RENAME TO cameras")
        execute_sql(conn, INDEX_PLAN["idx_cameras_one_active_per_slot"])
        conn.execute("DROP VIEW IF EXISTS v_camera_slots_with_current_camera")
        conn.execute(CURRENT_CAMERA_VIEW_SQL)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")

    new_count = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
    if old_count != new_count:
        raise RuntimeError(
            f"Cameras rebuild row count mismatch: old={old_count}, new={new_count}"
        )

    orphan_fault_camera = conn.execute(
        """
        SELECT COUNT(*)
        FROM fault_reports
        WHERE camera_id IS NOT NULL
          AND camera_id NOT IN (SELECT id FROM cameras)
        """
    ).fetchone()[0]
    if orphan_fault_camera != 0:
        raise RuntimeError(
            f"Cameras rebuild left orphan fault_reports.camera_id rows: {orphan_fault_camera}"
        )

    return {"rebuilt": True, "old_count": old_count, "new_count": new_count}


def backfill_fault_reports(conn: sqlite3.Connection, unified_project_id: int) -> dict[str, int]:
    metrics: dict[str, int] = {}

    before = conn.total_changes
    conn.execute(
        """
        UPDATE fault_reports
        SET project_id = COALESCE(project_id, ?)
        WHERE project_id IS NULL
        """,
        (unified_project_id,),
    )
    metrics["project_id_backfilled"] = conn.total_changes - before

    before = conn.total_changes
    conn.execute(
        """
        UPDATE fault_reports
        SET fault_type_label_snapshot = COALESCE(fault_type_label_snapshot, fault_type)
        WHERE fault_type_label_snapshot IS NULL
        """
    )
    metrics["fault_type_label_snapshot_backfilled"] = conn.total_changes - before

    before = conn.total_changes
    conn.execute(
        """
        UPDATE fault_reports
        SET source_type = 'migration'
        WHERE source_type IS NULL
           OR source_type = 'manual'
        """
    )
    metrics["source_type_backfilled"] = conn.total_changes - before

    before = conn.total_changes
    conn.execute(
        """
        UPDATE fault_reports
        SET assigned_to = (
            SELECT u.id
            FROM users u
            WHERE u.username = fault_reports.handler_name
        )
        WHERE assigned_to IS NULL
          AND handler_name IS NOT NULL
          AND (
                SELECT COUNT(*)
                FROM users u
                WHERE u.username = fault_reports.handler_name
              ) = 1
        """
    )
    metrics["assigned_to_backfilled"] = conn.total_changes - before

    before = conn.total_changes
    conn.execute(
        """
        UPDATE fault_reports
        SET camera_slot_id = (
                SELECT c.slot_id
                FROM cameras c
                WHERE c.id = fault_reports.camera_id
            ),
            project_id = COALESCE(
                project_id,
                (
                    SELECT c.project_id
                    FROM cameras c
                    WHERE c.id = fault_reports.camera_id
                )
            ),
            project_device_code = COALESCE(
                project_device_code,
                (
                    SELECT COALESCE(c.project_camera_code, c.camera_index)
                    FROM cameras c
                    WHERE c.id = fault_reports.camera_id
                )
            )
        WHERE camera_id IS NOT NULL
        """
    )
    metrics["camera_link_backfilled"] = conn.total_changes - before

    metrics["fault_reports_with_camera_slot_id"] = conn.execute(
        "SELECT COUNT(*) FROM fault_reports WHERE camera_slot_id IS NOT NULL"
    ).fetchone()[0]
    metrics["fault_reports_without_camera_slot_id"] = conn.execute(
        "SELECT COUNT(*) FROM fault_reports WHERE camera_slot_id IS NULL"
    ).fetchone()[0]
    metrics["fault_reports_with_project_id"] = conn.execute(
        "SELECT COUNT(*) FROM fault_reports WHERE project_id IS NOT NULL"
    ).fetchone()[0]
    return metrics


def record_migration_version(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO schema_migrations (version, script_name, description, checksum)
        VALUES (?, ?, ?, ?)
        """,
        (
            MIGRATION_VERSION,
            MIGRATION_NAME,
            MIGRATION_DESCRIPTION,
            get_script_checksum(),
        ),
    )


def validate_post_migration(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "projects_count": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "camera_slots_count": conn.execute("SELECT COUNT(*) FROM camera_slots").fetchone()[0],
        "cameras_count": conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0],
        "fault_reports_count": conn.execute("SELECT COUNT(*) FROM fault_reports").fetchone()[0],
        "fault_reports_camera_id_orphans": conn.execute(
            """
            SELECT COUNT(*)
            FROM fault_reports
            WHERE camera_id IS NOT NULL
              AND camera_id NOT IN (SELECT id FROM cameras)
            """
        ).fetchone()[0],
        "active_camera_slot_conflicts": conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT slot_id
                FROM cameras
                WHERE status = 'active'
                GROUP BY slot_id
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0],
        "fault_reports_with_camera_slot_id": conn.execute(
            "SELECT COUNT(*) FROM fault_reports WHERE camera_slot_id IS NOT NULL"
        ).fetchone()[0],
        "fault_reports_without_camera_slot_id": conn.execute(
            "SELECT COUNT(*) FROM fault_reports WHERE camera_slot_id IS NULL"
        ).fetchone()[0],
    }


def run_apply(
    db_path: Path,
    *,
    report_path: Path | None,
    backup_path: Path | None,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    resolved_backup_path = backup_path or db_path.with_name(
        f"{db_path.stem}.pre_v1_{timestamp}{db_path.suffix}"
    )
    backup_database(db_path, resolved_backup_path)

    conn = connect(db_path)
    try:
        ensure_schema_migrations_table(conn)
        if get_current_version(conn) >= MIGRATION_VERSION:
            execution = {
                "backup_path": str(resolved_backup_path),
                "applied": False,
                "reason": f"schema version {MIGRATION_VERSION} already recorded",
                "post_validation": validate_post_migration(conn),
            }
            report = build_report(
                conn,
                db_path,
                mode="apply-skip",
                notes=["Migration version already present; no changes applied."],
                execution=execution,
            )
            if report_path:
                write_report(report_path, report)
            return report

        created_tables = ensure_new_tables(conn)
        seeded_projects = seed_projects(conn)
        unified_project_id = get_project_id_by_code(conn, "unified")
        added_fault_columns = ensure_fault_report_columns(conn)
        added_photo_columns = ensure_photo_columns(conn)
        created_fault_indexes = create_fault_indexes(conn)
        created_slots = ensure_legacy_camera_slots(conn, unified_project_id)
        rebuild_result = rebuild_cameras_table(conn, unified_project_id)
        fault_backfill = backfill_fault_reports(conn, unified_project_id)
        record_migration_version(conn)
        conn.commit()

        execution = {
            "backup_path": str(resolved_backup_path),
            "applied": True,
            "created_tables": created_tables,
            "seeded_projects_inserted": seeded_projects,
            "added_fault_columns": added_fault_columns,
            "added_photo_columns": added_photo_columns,
            "created_fault_indexes": created_fault_indexes,
            "created_legacy_camera_slots": created_slots,
            "cameras_rebuild": rebuild_result,
            "fault_backfill": fault_backfill,
            "post_validation": validate_post_migration(conn),
        }
        report = build_report(
            conn,
            db_path,
            mode="apply",
            notes=[
                "Legacy cameras were migrated into project=unified and one legacy slot per legacy camera.",
                "fault_reports.camera_slot_id was backfilled only for rows with a non-null camera_id.",
                "fault_type_code/source_record_key remain null for legacy rows when source semantics are unsafe.",
            ],
            execution=execution,
        )
        if report_path:
            write_report(report_path, report)
        return report
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the multi-project V1 migration")
    parser.add_argument(
        "--database",
        default="station_monitor.db",
        help="Path to the SQLite database file",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect the current schema and write a structured plan (default mode)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Execute the Phase 1 migration against the target database",
    )
    parser.add_argument(
        "--report",
        help="Optional JSON report output path",
    )
    parser.add_argument(
        "--backup",
        help="Optional backup file path used when --apply is selected",
    )
    parser.add_argument(
        "--fail-on",
        default="",
        help="Reserved for future rule-based fail-fast checks",
    )
    parser.add_argument(
        "--timezone-default",
        default="Asia/Shanghai",
        help="Reserved for future time normalization logic",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.database).resolve()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    report_path = Path(args.report).resolve() if args.report else None
    if args.apply:
        report = run_apply(
            db_path,
            report_path=report_path,
            backup_path=Path(args.backup).resolve() if args.backup else None,
        )
        print(
            json.dumps(
                {
                    "migration": report["migration"],
                    "mode": report["mode"],
                    "backup_path": report["execution"]["backup_path"],
                    "applied": report["execution"]["applied"],
                    "post_validation": report["execution"]["post_validation"],
                    "report": str(report_path) if report_path else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    conn = connect(db_path)
    try:
        report = build_report(conn, db_path, mode="dry-run")
    finally:
        conn.close()

    if report_path:
        write_report(report_path, report)

    print(
        json.dumps(
            {
                "migration": report["migration"],
                "mode": report["mode"],
                "live_counts": report["live_counts"],
                "planned_action_count": len(report["actions"]),
                "report": str(report_path) if report_path else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
