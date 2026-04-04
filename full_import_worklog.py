#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Full worklog importer with station seed backfill and review-queue support."""

import argparse
import json
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).parent))
from init_db import get_db_path
from import_faults_worklog import (
    IMPORT_MODE,
    SOURCE_FILE,
    SOURCE_SYSTEM,
    SOURCE_TYPE,
    TARGET_TYPES,
    TIMEZONE_DEFAULT,
    ImportAbortError,
    abort_import_if_needed,
    build_description,
    build_report,
    get_or_create_project_batch,
    infer_fault_type,
    insert_fault_report,
    make_legacy_idempotency_key,
    parse_fail_on_rules,
    parse_time,
    write_report_file,
)
from import_review_support import (
    build_source_record_key,
    enqueue_fault_review_item,
    ensure_station_name_proposal,
    fault_source_key_exists,
    get_project_row,
    multi_project_import_enabled,
    project_code_from_system_type,
    resolve_station_match,
    split_station_tokens,
    update_import_batch_stats,
)
from utils import backup_sqlite_database, create_db_connection


STATIONS_TO_ADD = [
    ("\u4e1c\u65b9\u53d8", "110kV", "\u9752\u7530"),
    ("\u4ed9\u5bab\u53d8", "110kV", "\u4e91\u548c"),
    ("\u5317\u754c\u53d8", "35kV", "\u4e3d\u6c34"),
    ("\u53f6\u6751\u53d8", "35kV", "\u677e\u9633"),
    ("\u57ce\u5173\u53d8", "35kV", "\u677e\u9633"),
    ("\u5927\u4e1c\u575d\u53d8", "35kV", "\u4e91\u548c"),
    ("\u5927\u6e90\u53d8", "35kV", "\u7f19\u4e91"),
    ("\u5999\u9ad8\u53d8", "35kV", "\u4e3d\u6c34"),
    ("\u5bff\u5143\u53d8", "110kV", "\u4e3d\u6c34"),
    ("\u5c0f\u987a\u53d8", "35kV", "\u4e91\u548c"),
    ("\u65b0\u5174\u53d8", "110kV", "\u677e\u9633"),
    ("\u6924\u6797\u53d8", "110kV", "\u9042\u660c"),
    ("\u6c64\u516c\u53d8", "110kV", "\u9042\u660c"),
    ("\u7389\u5ca9\u53d8", "110kV", "\u677e\u9633"),
    ("\u738b\u6751\u53e3\u53d8", "35kV", "\u9042\u660c"),
    ("\u7d27\u6c34\u6ee9\u53d8", "110kV", "\u4e91\u548c"),
    ("\u82e5\u5bee\u53d8", "35kV", "\u677e\u9633"),
    ("\u8c61\u6eaa\u53d8", "110kV", "\u677e\u9633"),
    ("\u8d64\u5bff\u53d8", "35kV", "\u677e\u9633"),
    ("\u9756\u5c45\u53d8", "35kV", "\u677e\u9633"),
    ("\u9ec4\u5c97\u53d8", "35kV", "\u9f99\u6cc9"),
    ("\u9ec4\u6c99\u8170\u53d8", "35kV", "\u9042\u660c"),
    ("\u5cf0\u6e90\u53d8", "35kV", "\u83b2\u90fd"),
    ("\u96c5\u6eaa\u53d8", "35kV", "\u83b2\u90fd"),
    ("\u8239\u5bee\u53d8", "35kV", "\u9752\u7530"),
]

SKIP_STATIONS = {
    "\u7f19\u4e91",
    "\u9042\u660c\u516c\u53f8",
    "\u4e3d\u6c34",
    "\u6e56\u5dde",
    "\u94a6\u77ff\u53d8",
    "\u94bc\u77ff\u53d8",
}


def add_missing_stations(conn):
    added = []
    skipped = []

    for short_name, voltage_level, county in STATIONS_TO_ADD:
        full_name = f"{voltage_level}{short_name}"
        existing = conn.execute(
            "SELECT id FROM stations WHERE name = ?",
            (full_name,),
        ).fetchone()
        if existing:
            skipped.append(full_name)
            continue

        try:
            conn.execute(
                """
                INSERT INTO stations (name, voltage_level, county)
                VALUES (?, ?, ?)
                """,
                (full_name, voltage_level, county),
            )
            added.append(full_name)
        except Exception as exc:
            skipped.append(f"{full_name} (error: {exc})")

    return added, skipped


def import_full_worklog_file(
    source_file=SOURCE_FILE,
    *,
    database_path=None,
    dry_run=False,
    report_path=None,
    fail_on="",
    timezone_default=TIMEZONE_DEFAULT,
):
    workbook = openpyxl.load_workbook(source_file, data_only=True)
    try:
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()

    database_path = Path(database_path or get_db_path()).resolve()
    source_path = Path(source_file).resolve()
    conn = create_db_connection(database_path, row_factory=True, enable_wal=True)
    multi_project_enabled = multi_project_import_enabled(conn)
    batch_cache = {}
    fail_on_rules = parse_fail_on_rules(fail_on)
    report_rows = []
    stats = {
        "stations_added": 0,
        "stations_already_present": 0,
        "inserted": 0,
        "duplicates_skipped": 0,
        "queue_items_created": 0,
        "station_proposals_created": 0,
        "rows_skipped": 0,
        "fail_count": 0,
        "errors": [],
    }

    try:
        added, skipped = add_missing_stations(conn)
        stats["stations_added"] = len(added)
        stats["stations_already_present"] = len(skipped)

        for row_index, row in enumerate(rows[2:], start=3):
            if not row or len(row) <= 5 or row[5] is None:
                continue

            system_type = str(row[5]).strip()
            if system_type not in TARGET_TYPES:
                continue

            sequence = row[0]
            raw_time = row[1]
            created_at = parse_time(raw_time)
            station_text = str(row[2]).strip() if row[2] else ""
            location = str(row[3]).strip() if row[3] else ""
            content = str(row[4]).strip() if row[4] else ""
            handler_name = str(row[7]).strip() if len(row) > 7 and row[7] else None
            fault_type = infer_fault_type(content)
            description = build_description(location, content)

            if not station_text:
                row_result = {"row_index": row_index, "sequence": sequence, "action": "skip", "reason": "missing_station_name"}
                stats["rows_skipped"] += 1
                report_rows.append(row_result)
                abort_import_if_needed(
                    fail_on_rules=fail_on_rules,
                    row_result=row_result,
                    reason="missing_station_name",
                    action="skip",
                )
                continue

            project_code = project_code_from_system_type(system_type)
            batch_state = (
                get_or_create_project_batch(
                    conn,
                    batch_cache,
                    project_code,
                    dry_run=dry_run,
                    timezone_default=timezone_default,
                    report_path=Path(report_path).resolve() if report_path else None,
                )
                if multi_project_enabled and project_code
                else None
            )
            batch_id = batch_state["batch_id"] if batch_state else None
            project_row = batch_state["project"] if batch_state else (
                get_project_row(conn, project_code) if multi_project_enabled else None
            )

            for station_token in split_station_tokens(station_text):
                row_result = {"row_index": row_index, "sequence": sequence, "station_token": station_token}

                if station_token in SKIP_STATIONS:
                    stats["rows_skipped"] += 1
                    row_result["action"] = "skip"
                    row_result["reason"] = "station_skipped"
                    report_rows.append(row_result)
                    abort_import_if_needed(
                        fail_on_rules=fail_on_rules,
                        row_result=row_result,
                        reason="station_skipped",
                        action="skip",
                    )
                    continue

                source_record_key = (
                    build_source_record_key(
                        project_code or "legacy",
                        SOURCE_TYPE,
                        raw_external_id=f"{SOURCE_SYSTEM}:{sequence}:{station_token}",
                    )
                    if multi_project_enabled
                    else None
                )
                match = resolve_station_match(
                    conn,
                    station_token,
                    source_system=SOURCE_SYSTEM,
                )
                raw_context = {
                    "sequence": sequence,
                    "system_type": system_type,
                    "station_text": station_text,
                    "station_token": station_token,
                    "location": location,
                    "content": content,
                    "description": description,
                    "fault_type": fault_type,
                    "handler_name": handler_name,
                    "parsed_time": created_at,
                    "raw_time": str(raw_time) if raw_time is not None else None,
                    "source_timezone": timezone_default,
                }

                if match["should_create_proposal"] and multi_project_enabled and batch_state and not dry_run:
                    proposal_id = ensure_station_name_proposal(
                        conn,
                        import_batch_id=batch_id,
                        project_id=batch_state["project"]["id"],
                        source_system=SOURCE_SYSTEM,
                        external_name=station_token,
                        candidate_station_id=match["proposal_candidate_station_id"],
                        confidence_score=match["confidence_score"],
                        raw_context=raw_context,
                    )
                    if proposal_id:
                        stats["station_proposals_created"] += 1

                if not match["matched"]:
                    stats["fail_count"] += 1
                    row_result["action"] = "queue"
                    row_result["reason"] = "station_not_resolved"
                    report_rows.append(row_result)
                    if multi_project_enabled and batch_state and not dry_run:
                        queue_id = enqueue_fault_review_item(
                            conn,
                            import_batch_id=batch_id,
                            project_id=batch_state["project"]["id"],
                            source_type=SOURCE_TYPE,
                            source_record_key_candidate=source_record_key,
                            raw_payload=raw_context,
                            issue_type="station_not_resolved",
                            issue_detail=f"Unable to resolve station token: {station_token}",
                        )
                        if queue_id:
                            stats["queue_items_created"] += 1
                        batch_state["fail_count"] += 1
                    else:
                        stats["errors"].append(
                            {
                                "sequence": sequence,
                                "station_token": station_token,
                                "error": "station_not_resolved",
                            }
                        )
                    abort_import_if_needed(
                        fail_on_rules=fail_on_rules,
                        row_result=row_result,
                        reason="station_not_resolved",
                        action="queue",
                    )
                    continue

                if multi_project_enabled and source_record_key and fault_source_key_exists(conn, source_record_key):
                    stats["duplicates_skipped"] += 1
                    row_result["action"] = "duplicate-skip"
                    row_result["reason"] = "source_record_key_exists"
                    report_rows.append(row_result)
                    abort_import_if_needed(
                        fail_on_rules=fail_on_rules,
                        row_result=row_result,
                        reason="source_record_key_exists",
                        action="duplicate_skip",
                    )
                    continue

                legacy_idempotency_key = make_legacy_idempotency_key(
                    match["station_id"],
                    created_at,
                    description,
                )
                existing = conn.execute(
                    "SELECT id FROM fault_reports WHERE idempotency_key = ?",
                    (legacy_idempotency_key,),
                ).fetchone()
                if existing:
                    stats["duplicates_skipped"] += 1
                    row_result["action"] = "duplicate-skip"
                    row_result["reason"] = "legacy_idempotency_key_exists"
                    report_rows.append(row_result)
                    abort_import_if_needed(
                        fail_on_rules=fail_on_rules,
                        row_result=row_result,
                        reason="legacy_idempotency_key_exists",
                        action="duplicate_skip",
                    )
                    continue

                try:
                    insert_fault_report(
                        conn,
                        station_id=match["station_id"],
                        system_type=system_type,
                        fault_type=fault_type,
                        description=description,
                        handler_name=handler_name,
                        created_at=created_at,
                        legacy_idempotency_key=legacy_idempotency_key,
                        project_row=project_row,
                        batch_id=batch_id,
                        source_record_key=source_record_key,
                        raw_time_value=raw_time,
                        timezone_default=timezone_default,
                    )
                    stats["inserted"] += 1
                    row_result["action"] = "insert"
                    row_result["station_id"] = match["station_id"]
                    report_rows.append(row_result)
                    if batch_state:
                        batch_state["success_count"] += 1
                except Exception as exc:
                    stats["fail_count"] += 1
                    row_result["action"] = "queue"
                    row_result["reason"] = "insert_failed"
                    row_result["error"] = str(exc)
                    report_rows.append(row_result)
                    stats["errors"].append(
                        {
                            "sequence": sequence,
                            "station_token": station_token,
                            "error": str(exc),
                        }
                    )
                    if multi_project_enabled and batch_state and not dry_run:
                        queue_id = enqueue_fault_review_item(
                            conn,
                            import_batch_id=batch_id,
                            project_id=batch_state["project"]["id"],
                            source_type=SOURCE_TYPE,
                            source_record_key_candidate=source_record_key,
                            raw_payload=raw_context,
                            issue_type="insert_failed",
                            issue_detail=str(exc),
                        )
                        if queue_id:
                            stats["queue_items_created"] += 1
                        batch_state["fail_count"] += 1
                    abort_import_if_needed(
                        fail_on_rules=fail_on_rules,
                        row_result=row_result,
                        reason="insert_failed",
                        action="queue",
                    )

        if dry_run:
            conn.rollback()
        else:
            for state in batch_cache.values():
                if state["batch_id"] is None:
                    continue
                update_import_batch_stats(
                    conn,
                    state["batch_id"],
                    success_count=state["success_count"],
                    fail_count=state["fail_count"],
                )
            conn.commit()
    except Exception as exc:
        conn.rollback()
        report = build_report(
            database_path=database_path,
            source_file=source_path,
            dry_run=dry_run,
            fail_on_rules=fail_on_rules,
            timezone_default=timezone_default,
            stats=stats,
            report_rows=report_rows,
            aborted=True,
            error=str(exc),
        )
        write_report_file(Path(report_path).resolve() if report_path else None, report)
        if isinstance(exc, ImportAbortError):
            exc.report = report
        raise
    finally:
        conn.close()

    report = build_report(
        database_path=database_path,
        source_file=source_path,
        dry_run=dry_run,
        fail_on_rules=fail_on_rules,
        timezone_default=timezone_default,
        stats=stats,
        report_rows=report_rows,
    )
    write_report_file(Path(report_path).resolve() if report_path else None, report)
    return report


def parse_args():
    parser = argparse.ArgumentParser(description="Full worklog import with station seed backfill")
    parser.add_argument("--source", default=SOURCE_FILE, help="Path to worklog xlsx file")
    parser.add_argument("--database", default=get_db_path(), help="Path to SQLite database")
    parser.add_argument("--report", help="Optional JSON report output path")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not persist database changes")
    parser.add_argument("--fail-on", default="", help="Comma-separated fail-fast rules")
    parser.add_argument("--timezone-default", default=TIMEZONE_DEFAULT, help="Timezone label stored on imported rows")
    parser.add_argument("--skip-backup", action="store_true", help="Skip automatic SQLite backup before write import")
    return parser.parse_args()


def main():
    args = parse_args()
    database_path = Path(args.database).resolve()
    report_path = Path(args.report).resolve() if args.report else None

    if not args.dry_run and not args.skip_backup:
        backup_path = backup_sqlite_database(database_path, label="full_import_worklog")
        if backup_path:
            print(f"[backup] created sqlite backup: {backup_path}")

    try:
        report = import_full_worklog_file(
            args.source,
            database_path=database_path,
            dry_run=args.dry_run,
            report_path=report_path,
            fail_on=args.fail_on,
            timezone_default=args.timezone_default,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    except ImportAbortError as exc:
        if exc.report:
            print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
