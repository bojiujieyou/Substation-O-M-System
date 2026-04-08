#!/usr/bin/env python3
"""Inspect records affected by a given import_batch_id."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from import_batch_summary import build_import_batch_summary
from import_batch_summary import fetch_batch_metadata as _fetch_batch_metadata
from import_batch_summary import fetch_fault_rows as _fetch_fault_rows
from import_batch_summary import fetch_proposal_rows as _fetch_proposal_rows
from import_batch_summary import fetch_review_rows as _fetch_review_rows
from utils import create_db_connection


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


def inspect_import_batch(
    *,
    database: str | Path,
    batch_id: int,
    export_dir: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict:
    summary = build_import_batch_summary(database=database, batch_id=batch_id)
    report = {
        "batch": summary["batch"],
        "summary": summary["summary"],
        "fault_rows": summary["fault_rows"],
        "review_rows": summary["review_rows"],
        "proposal_rows": summary["proposal_rows"],
        "recommendation": {
            "primary_path": "backup_restore" if summary["fault_rows"] else "manual_queue_cleanup",
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
        _write_csv(export_root / "fault_rows.csv", report["fault_rows"])
        _write_csv(export_root / "review_rows.csv", report["review_rows"])
        _write_csv(export_root / "proposal_rows.csv", report["proposal_rows"])

    if report_path:
        report_file = Path(report_path)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


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
