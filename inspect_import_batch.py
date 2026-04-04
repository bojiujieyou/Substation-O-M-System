#!/usr/bin/env python3
"""Inspect records affected by a given import_batch_id."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from import_review_support import get_columns, table_exists
from utils import create_db_connection


def _row_to_dict(row):
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _fetch_batch_metadata(conn, batch_id: int) -> dict | None:
    if not table_exists(conn, "import_batches"):
        return None
    row = conn.execute(
        """
        SELECT
            b.id,
            b.project_id,
            p.code AS project_code,
            p.name AS project_name,
            b.source_type,
            b.mode,
            b.file_count,
            b.success_count,
            b.fail_count,
            b.report_path,
            b.operator_id,
            b.timezone_default_used,
            b.created_at
        FROM import_batches b
        LEFT JOIN projects p ON p.id = b.project_id
        WHERE b.id = ?
        """,
        (batch_id,),
    ).fetchone()
    return _row_to_dict(row)


def _fetch_fault_rows(conn, batch_id: int) -> list[dict]:
    if not table_exists(conn, "fault_reports"):
        return []
    columns = get_columns(conn, "fault_reports")
    if "source_batch_id" not in columns:
        return []

    select_parts = ["id", "station_id", "camera_id", "status", "created_at", "updated_at"]
    for optional in [
        "project_id",
        "camera_slot_id",
        "source_type",
        "source_record_key",
        "fault_type",
        "fault_type_code",
        "fault_type_label_snapshot",
        "description",
        "project_device_code",
    ]:
        if optional in columns:
            select_parts.append(optional)

    rows = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
        FROM fault_reports
        WHERE source_batch_id = ?
        ORDER BY id
        """,
        (str(batch_id),),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _fetch_review_rows(conn, batch_id: int) -> list[dict]:
    if not table_exists(conn, "fault_import_review_queue"):
        return []
    rows = conn.execute(
        """
        SELECT
            id,
            project_id,
            source_type,
            source_record_key_candidate,
            issue_type,
            issue_detail,
            status,
            resolved_fault_id,
            reviewer_id,
            reviewed_at,
            created_at
        FROM fault_import_review_queue
        WHERE import_batch_id = ?
        ORDER BY id
        """,
        (batch_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _fetch_proposal_rows(conn, batch_id: int) -> list[dict]:
    if not table_exists(conn, "station_name_mapping_proposals"):
        return []
    rows = conn.execute(
        """
        SELECT
            id,
            project_id,
            source_system,
            external_name,
            normalized_name,
            candidate_station_id,
            confidence_score,
            status,
            reviewer_id,
            reviewed_at,
            created_at
        FROM station_name_mapping_proposals
        WHERE import_batch_id = ?
        ORDER BY id
        """,
        (batch_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _build_breakdown(rows: list[dict], key: str) -> dict:
    counter = Counter()
    for row in rows:
        counter[str(row.get(key) or "NULL")] += 1
    return dict(sorted(counter.items()))


def inspect_import_batch(
    *,
    database: str | Path,
    batch_id: int,
    export_dir: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict:
    conn = create_db_connection(database, row_factory=True)
    try:
        metadata = _fetch_batch_metadata(conn, batch_id)
        if metadata is None:
            raise RuntimeError("import_batches table not found")
        if not metadata:
            raise RuntimeError(f"import batch not found: {batch_id}")

        fault_rows = _fetch_fault_rows(conn, batch_id)
        review_rows = _fetch_review_rows(conn, batch_id)
        proposal_rows = _fetch_proposal_rows(conn, batch_id)

        report = {
            "batch": metadata,
            "summary": {
                "fault_rows": len(fault_rows),
                "review_queue_rows": len(review_rows),
                "station_name_proposals": len(proposal_rows),
                "fault_status_breakdown": _build_breakdown(fault_rows, "status"),
                "review_status_breakdown": _build_breakdown(review_rows, "status"),
                "review_issue_type_breakdown": _build_breakdown(review_rows, "issue_type"),
                "proposal_status_breakdown": _build_breakdown(proposal_rows, "status"),
            },
            "fault_rows": fault_rows,
            "review_rows": review_rows,
            "proposal_rows": proposal_rows,
            "recommendation": {
                "primary_path": "backup_restore" if fault_rows else "manual_queue_cleanup",
                "notes": [
                    "Use this report to confirm the blast radius before any recovery action.",
                    "If fault_rows > 0 and the batch is systemically wrong, prefer backup restore or a dedicated rollback script reviewed by a human.",
                    "If only review/proposal rows exist, manual cleanup is usually safer than database restore.",
                ],
            },
        }

        if export_dir:
            export_root = Path(export_dir)
            export_root.mkdir(parents=True, exist_ok=True)
            _write_csv(export_root / "fault_rows.csv", fault_rows)
            _write_csv(export_root / "review_rows.csv", review_rows)
            _write_csv(export_root / "proposal_rows.csv", proposal_rows)

        if report_path:
            report_file = Path(report_path)
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        return report
    finally:
        conn.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect records affected by an import batch.")
    parser.add_argument("--database", required=True, help="SQLite database path")
    parser.add_argument("--batch-id", required=True, type=int, help="import_batches.id to inspect")
    parser.add_argument("--export-dir", help="Optional directory used to export fault/review/proposal CSV files")
    parser.add_argument("--report", help="Optional JSON report output path")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    report = inspect_import_batch(
        database=args.database,
        batch_id=args.batch_id,
        export_dir=args.export_dir,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
