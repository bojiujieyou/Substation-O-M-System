#!/usr/bin/env python3
"""Data Discovery Sprint runner for multi-project onboarding."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from import_faults import (
    DEFAULT_TIMEZONE,
    build_canonical_row,
    get_current_fault_type_catalog,
    load_rows,
    load_type_mapping,
    parse_source_timestamp,
)
from import_review_support import (
    build_source_record_key,
    get_project_row,
    normalize_station_name,
    resolve_station_match,
    table_exists,
)
from parse_excel import ExcelParseError, parse_station_excel
from utils import create_db_connection


DEVICE_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm"}
FAULT_SUFFIXES = DEVICE_SUFFIXES | {".csv"}
PERF_TRIGGER_P95_MS = 200.0


def _safe_text(value) -> str:
    return str(value or "").strip()


def _slugify(value: str, *, fallback: str = "NA") -> str:
    normalized = normalize_station_name(value)
    if not normalized:
        return fallback
    slug = []
    for ch in normalized.upper():
        if ch.isalnum():
            slug.append(ch)
        else:
            slug.append("_")
    result = "".join(slug).strip("_")
    return result[:48] or fallback


def _build_legacy_slot_suffix(area_key: str, location_key: str) -> str:
    seed = f"{area_key}|{location_key}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    compact = "".join(ch for ch in seed if ch.isalnum() or ch in {"#", "-"})
    compact = compact[:24] or "NA"
    return f"{compact}_{digest}"


def _collect_files(paths: Iterable[str | Path], allowed_suffixes: set[str]) -> list[Path]:
    files: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"input path not found: {path}")
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in allowed_suffixes:
                    files.append(child)
        elif path.is_file():
            if path.suffix.lower() not in allowed_suffixes:
                raise ValueError(f"unsupported input file: {path}")
            files.append(path)
    return files


def _quantile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _threshold_result(metric_name: str, rate: float | None) -> dict:
    if rate is None:
        return {
            "metric": metric_name,
            "status": "skipped",
            "decision": "insufficient_data",
            "rate": None,
        }

    if metric_name == "slot_code_conflict_rate":
        if rate <= 0.05:
            status = "pass"
            decision = "allow_phase_4"
        elif rate <= 0.15:
            status = "dual_track"
            decision = "allow_with_rule_tuning"
        else:
            status = "block"
            decision = "block_full_import"
    elif metric_name == "fault_type_mapping_rate":
        if rate >= 0.9:
            status = "pass"
            decision = "allow_auto_import"
        elif rate >= 0.75:
            status = "dual_track"
            decision = "allow_with_review_queue"
        else:
            status = "block"
            decision = "block_full_import"
    elif metric_name == "timestamp_parse_rate":
        if rate >= 0.95:
            status = "pass"
            decision = "allow_stats_and_idempotency"
        else:
            status = "block"
            decision = "block_stats_and_idempotency"
    else:
        status = "unknown"
        decision = "unknown"

    return {
        "metric": metric_name,
        "status": status,
        "decision": decision,
        "rate": round(rate, 4),
    }


def build_generated_slot_code(project_code: str, station_name: str, camera: dict, row_index: int) -> str:
    raw_slot_code = _safe_text(camera.get("slot_code"))
    if raw_slot_code:
        return raw_slot_code

    channel_number = camera.get("channel_number")
    if channel_number in (None, ""):
        channel_number = _safe_text(camera.get("camera_index")) or f"ROW{row_index + 1}"
    location_desc = _safe_text(camera.get("location_desc") or camera.get("location"))
    area_key = _safe_text(camera.get("area"))
    station_key = _slugify(station_name, fallback="UNKNOWN_STATION")
    suffix = _build_legacy_slot_suffix(area_key, location_desc)
    return f"LEGACY_{project_code}_{station_key}_{channel_number}_{suffix}"


def analyze_device_sources(*, paths: Iterable[str | Path], project_code: str) -> dict:
    files = _collect_files(paths, DEVICE_SUFFIXES)
    field_counter = Counter()
    slot_registry: dict[tuple[str, str], dict] = defaultdict(lambda: {"signatures": set(), "rows": []})
    parse_errors = []
    generated_slot_count = 0
    explicit_slot_count = 0
    total_cameras = 0
    unique_station_names = set()

    for file_path in files:
        try:
            parsed = parse_station_excel(str(file_path))
        except ExcelParseError as exc:
            parse_errors.append({"file": str(file_path), "error": str(exc)})
            continue

        station = parsed.get("station") or {}
        station_name = _safe_text(station.get("name"))
        unique_station_names.add(station_name)
        cameras = parsed.get("cameras") or []
        for row_index, camera in enumerate(cameras):
            total_cameras += 1
            for field_name, field_value in camera.items():
                if field_value not in (None, ""):
                    field_counter[field_name] += 1

            raw_slot_code = _safe_text(camera.get("slot_code"))
            if raw_slot_code:
                explicit_slot_count += 1
            else:
                generated_slot_count += 1

            slot_code = build_generated_slot_code(project_code, station_name, camera, row_index)
            signature = (
                _safe_text(camera.get("location_desc") or camera.get("location")),
                _safe_text(camera.get("area")),
                camera.get("channel_number"),
            )
            registry_key = (normalize_station_name(station_name), slot_code)
            slot_registry[registry_key]["signatures"].add(signature)
            slot_registry[registry_key]["rows"].append(
                {
                    "file": str(file_path),
                    "station_name": station_name,
                    "slot_code": slot_code,
                    "signature": signature,
                    "raw_slot_code": raw_slot_code or None,
                    "project_camera_code": _safe_text(camera.get("project_camera_code")) or None,
                }
            )

    conflict_examples = []
    conflicting_rows = 0
    conflicting_slots = 0
    for payload in slot_registry.values():
        if len(payload["signatures"]) > 1:
            conflicting_slots += 1
            conflicting_rows += len(payload["rows"])
            if len(conflict_examples) < 10:
                conflict_examples.append(
                    {
                        "slot_code": payload["rows"][0]["slot_code"],
                        "station_name": payload["rows"][0]["station_name"],
                        "rows": payload["rows"],
                    }
                )

    conflict_rate = (conflicting_rows / total_cameras) if total_cameras else None

    return {
        "files_scanned": len(files),
        "parse_errors": parse_errors,
        "station_count": len(unique_station_names),
        "camera_count": total_cameras,
        "explicit_slot_code_count": explicit_slot_count,
        "generated_slot_code_count": generated_slot_count,
        "conflicting_slot_count": conflicting_slots,
        "conflicting_row_count": conflicting_rows,
        "slot_code_conflict_rate": round(conflict_rate, 4) if conflict_rate is not None else None,
        "slot_code_threshold": _threshold_result("slot_code_conflict_rate", conflict_rate),
        "field_coverage": {
            field: {
                "non_empty_rows": count,
                "coverage_rate": round(count / total_cameras, 4) if total_cameras else None,
            }
            for field, count in sorted(field_counter.items())
        },
        "conflict_examples": conflict_examples,
    }


def _resolve_fault_type_mapping(row: dict, *, by_code: dict, by_label: dict, type_mapping: dict) -> tuple[str | None, str]:
    raw_code = _safe_text(row.get("fault_type_code"))
    raw_label = _safe_text(row.get("fault_type_label"))

    if raw_code and raw_code in by_code:
        return raw_code, "catalog_code"
    if raw_label and raw_label in by_label:
        return by_label[raw_label]["type_code"], "catalog_label"

    mapped_code = None
    if raw_code and raw_code in type_mapping:
        mapped_code = type_mapping[raw_code]
    elif raw_label and raw_label in type_mapping:
        mapped_code = type_mapping[raw_label]

    if mapped_code and (not by_code or mapped_code in by_code):
        return mapped_code, "mapping_file"
    return None, "unmapped"


def _parse_row_timestamp(value, timezone_label: str) -> tuple[str | None, str | None, str | None]:
    try:
        parsed, raw_value = parse_source_timestamp(value, timezone_label)
        return parsed, raw_value, None
    except Exception as exc:  # pragma: no cover - defensive for invalid ZoneInfo labels
        return None, _safe_text(value) or None, str(exc)


def analyze_fault_sources(
    *,
    paths: Iterable[str | Path],
    project_code: str,
    source_type: str,
    timezone_default: str,
    type_mapping_path: str | Path | None,
    database: str | Path | None = None,
) -> dict:
    files = _collect_files(paths, FAULT_SUFFIXES)
    type_mapping = load_type_mapping(Path(type_mapping_path)) if type_mapping_path else {}

    conn = None
    project_row = None
    by_code: dict = {}
    by_label: dict = {}
    if database:
        conn = create_db_connection(database, row_factory=True)
        project_row = get_project_row(conn, project_code)
        if project_row and table_exists(conn, "project_fault_types"):
            by_code, by_label = get_current_fault_type_catalog(conn, project_row["id"])

    field_counter = Counter()
    mapping_counter = Counter()
    match_source_counter = Counter()
    timezone_counter = Counter()
    unresolved_station_counter = Counter()
    invalid_time_examples = []
    source_key_counter = Counter()

    total_rows = 0
    rows_with_station_name = 0
    rows_with_occurred_at = 0
    parsed_occurred_at_rows = 0
    rows_with_fault_type = 0
    mapped_fault_type_rows = 0

    for file_path in files:
        rows = load_rows(Path(file_path))
        for row in rows:
            total_rows += 1
            for field_name, field_value in row.items():
                if field_name.startswith("_"):
                    continue
                if field_value not in (None, ""):
                    field_counter[field_name] += 1

            station_name = _safe_text(row.get("station_name"))
            if station_name:
                rows_with_station_name += 1
                if conn:
                    match = resolve_station_match(conn, station_name, source_system=source_type)
                    match_source_counter[match["match_source"]] += 1
                    if not match["matched"]:
                        unresolved_station_counter[station_name] += 1
                else:
                    match_source_counter["not_checked_without_db"] += 1

            fault_type_present = _safe_text(row.get("fault_type_code")) or _safe_text(row.get("fault_type_label"))
            if fault_type_present:
                rows_with_fault_type += 1
                mapped_code, mapping_source = _resolve_fault_type_mapping(
                    row,
                    by_code=by_code,
                    by_label=by_label,
                    type_mapping=type_mapping,
                )
                mapping_counter[mapping_source] += 1
                if mapped_code:
                    mapped_fault_type_rows += 1

            raw_timezone = _safe_text(row.get("source_timezone")) or timezone_default
            timezone_counter[raw_timezone] += 1
            occurred_at = row.get("occurred_at")
            if occurred_at not in (None, ""):
                rows_with_occurred_at += 1
                parsed_value, raw_value, error = _parse_row_timestamp(occurred_at, raw_timezone)
                if parsed_value:
                    parsed_occurred_at_rows += 1
                elif len(invalid_time_examples) < 10:
                    invalid_time_examples.append(
                        {
                            "row_index": row.get("_row_index"),
                            "file": str(file_path),
                            "occurred_at": raw_value,
                            "source_timezone": raw_timezone,
                            "error": error or "unparseable_timestamp",
                        }
                    )

            source_record_key = build_source_record_key(
                project_code,
                source_type,
                raw_external_id=_safe_text(row.get("external_id")) or None,
                canonical_row=build_canonical_row(row),
            )
            if _safe_text(row.get("external_id")):
                source_key_counter["external_id"] += 1
            elif source_record_key:
                source_key_counter["canonical_fallback"] += 1
            else:
                source_key_counter["unavailable"] += 1

    if conn:
        conn.close()

    mapping_rate = (mapped_fault_type_rows / rows_with_fault_type) if rows_with_fault_type else None
    timestamp_rate = (parsed_occurred_at_rows / rows_with_occurred_at) if rows_with_occurred_at else None

    return {
        "files_scanned": len(files),
        "row_count": total_rows,
        "rows_with_station_name": rows_with_station_name,
        "rows_with_fault_type": rows_with_fault_type,
        "rows_with_occurred_at": rows_with_occurred_at,
        "mapped_fault_type_count": mapped_fault_type_rows,
        "fault_type_mapping_rate": round(mapping_rate, 4) if mapping_rate is not None else None,
        "fault_type_threshold": _threshold_result("fault_type_mapping_rate", mapping_rate),
        "parsed_occurred_at_count": parsed_occurred_at_rows,
        "timestamp_parse_rate": round(timestamp_rate, 4) if timestamp_rate is not None else None,
        "timestamp_threshold": _threshold_result("timestamp_parse_rate", timestamp_rate),
        "field_coverage": {
            field: {
                "non_empty_rows": count,
                "coverage_rate": round(count / total_rows, 4) if total_rows else None,
            }
            for field, count in sorted(field_counter.items())
        },
        "station_match_breakdown": dict(sorted(match_source_counter.items())),
        "top_unresolved_station_names": [
            {"station_name": name, "count": count}
            for name, count in unresolved_station_counter.most_common(10)
        ],
        "fault_type_mapping_breakdown": dict(sorted(mapping_counter.items())),
        "timezone_breakdown": dict(sorted(timezone_counter.items())),
        "invalid_time_examples": invalid_time_examples,
        "source_record_key_breakdown": dict(sorted(source_key_counter.items())),
        "catalog_available": bool(by_code or by_label),
        "type_mapping_file_entries": len(type_mapping),
        "project_catalog_code_count": len(by_code),
    }


def estimate_slot_query_performance(
    *,
    database: str | Path | None,
    project_code: str,
    iterations: int = 25,
) -> dict:
    if not database:
        return {"status": "skipped", "reason": "database_not_provided"}

    conn = create_db_connection(database, row_factory=True)
    try:
        if not table_exists(conn, "camera_slots") or not table_exists(conn, "cameras"):
            return {"status": "skipped", "reason": "slot_tables_missing"}

        project_row = get_project_row(conn, project_code)
        project_id = project_row["id"] if project_row else None
        sql = """
            SELECT s.id, s.slot_code, c.id AS current_camera_id
            FROM camera_slots s
            LEFT JOIN cameras c
              ON c.slot_id = s.id
             AND c.status = 'active'
            WHERE (? IS NULL OR s.project_id = ?)
            ORDER BY s.id
            LIMIT 500
        """

        latencies = []
        row_count = 0
        for _ in range(max(iterations, 1)):
            started = time.perf_counter()
            rows = conn.execute(sql, (project_id, project_id)).fetchall()
            elapsed_ms = (time.perf_counter() - started) * 1000
            latencies.append(elapsed_ms)
            row_count = len(rows)

        p50_ms = _quantile(latencies, 0.5)
        p95_ms = _quantile(latencies, 0.95)
        return {
            "status": "ready" if (p95_ms or 0.0) <= PERF_TRIGGER_P95_MS else "review_cache",
            "row_count": row_count,
            "iterations": len(latencies),
            "p50_ms": round(p50_ms, 2) if p50_ms is not None else None,
            "p95_ms": round(p95_ms, 2) if p95_ms is not None else None,
            "trigger_threshold_ms": PERF_TRIGGER_P95_MS,
        }
    finally:
        conn.close()


def build_release_decision(report: dict) -> dict:
    threshold_results = []
    if report.get("device_inventory"):
        threshold_results.append(report["device_inventory"]["slot_code_threshold"])
    if report.get("fault_history"):
        threshold_results.append(report["fault_history"]["fault_type_threshold"])
        threshold_results.append(report["fault_history"]["timestamp_threshold"])

    statuses = {item["status"] for item in threshold_results if item["status"] != "skipped"}
    reasons = [item for item in threshold_results if item["status"] in {"dual_track", "block"}]

    if "block" in statuses:
        overall = "block"
    elif "dual_track" in statuses:
        overall = "dual_track"
    elif statuses:
        overall = "pass"
    else:
        overall = "insufficient_data"

    return {
        "overall": overall,
        "thresholds": threshold_results,
        "reasons": reasons,
    }


def render_markdown_summary(report: dict) -> str:
    lines = [
        "# Data Discovery Sprint Report",
        "",
        f"- Generated At: {report['generated_at']}",
        f"- Project: {report['project']}",
        f"- Source Type: {report['source_type']}",
        f"- Overall Decision: {report['release_decision']['overall']}",
        "",
        "## Threshold Summary",
        "",
    ]

    for item in report["release_decision"]["thresholds"]:
        rate = "N/A" if item["rate"] is None else f"{item['rate']:.4f}"
        lines.append(
            f"- `{item['metric']}`: status={item['status']}, decision={item['decision']}, rate={rate}"
        )

    device = report.get("device_inventory")
    if device:
        lines.extend(
            [
                "",
                "## Device Inventory",
                "",
                f"- Files Scanned: {device['files_scanned']}",
                f"- Camera Count: {device['camera_count']}",
                f"- Generated Slot Codes: {device['generated_slot_code_count']}",
                f"- Conflicting Rows: {device['conflicting_row_count']}",
                f"- Conflict Rate: {device['slot_code_conflict_rate']}",
            ]
        )

    fault = report.get("fault_history")
    if fault:
        lines.extend(
            [
                "",
                "## Fault History",
                "",
                f"- Files Scanned: {fault['files_scanned']}",
                f"- Row Count: {fault['row_count']}",
                f"- Fault Type Mapping Rate: {fault['fault_type_mapping_rate']}",
                f"- Timestamp Parse Rate: {fault['timestamp_parse_rate']}",
                f"- Station Match Breakdown: {json.dumps(fault['station_match_breakdown'], ensure_ascii=False)}",
            ]
        )

    performance = report.get("performance")
    if performance:
        lines.extend(
            [
                "",
                "## Slot Query Performance",
                "",
                f"- Status: {performance['status']}",
                f"- P50: {performance.get('p50_ms')}",
                f"- P95: {performance.get('p95_ms')}",
                f"- Threshold: {performance.get('trigger_threshold_ms')}",
            ]
        )

    return "\n".join(lines) + "\n"


def write_report_file(path: str | Path | None, content: str) -> None:
    if not path:
        return
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")


def run_discovery(
    *,
    project_code: str,
    source_type: str,
    device_sources: Iterable[str | Path] | None = None,
    fault_sources: Iterable[str | Path] | None = None,
    database: str | Path | None = None,
    type_mapping_path: str | Path | None = None,
    timezone_default: str = DEFAULT_TIMEZONE,
    performance_iterations: int = 25,
    report_path: str | Path | None = None,
    summary_path: str | Path | None = None,
) -> dict:
    if not device_sources and not fault_sources:
        raise ValueError("at least one of --device-source or --fault-source is required")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": project_code,
        "source_type": source_type,
        "timezone_default": timezone_default,
    }

    if device_sources:
        report["device_inventory"] = analyze_device_sources(paths=device_sources, project_code=project_code)
    if fault_sources:
        report["fault_history"] = analyze_fault_sources(
            paths=fault_sources,
            project_code=project_code,
            source_type=source_type,
            timezone_default=timezone_default,
            type_mapping_path=type_mapping_path,
            database=database,
        )

    report["performance"] = estimate_slot_query_performance(
        database=database,
        project_code=project_code,
        iterations=performance_iterations,
    )
    report["release_decision"] = build_release_decision(report)

    if report_path:
        write_report_file(report_path, json.dumps(report, ensure_ascii=False, indent=2))
    if summary_path:
        write_report_file(summary_path, render_markdown_summary(report))
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Data Discovery Sprint checks against sample files.")
    parser.add_argument("--project", required=True, help="Project code, for example unified")
    parser.add_argument("--source-type", default="import_excel", help="Source type used for station mapping and source_record_key semantics")
    parser.add_argument("--database", help="SQLite database path used for project catalog, station matching, and performance estimation")
    parser.add_argument("--device-source", action="append", default=[], help="Inventory workbook file or directory; can be passed multiple times")
    parser.add_argument("--fault-source", action="append", default=[], help="Historical fault CSV/XLSX file or directory; can be passed multiple times")
    parser.add_argument("--type-mapping", help="Optional CSV mapping file for historical fault type normalization")
    parser.add_argument("--timezone-default", default=DEFAULT_TIMEZONE, help="Timezone label used when source rows omit one")
    parser.add_argument("--performance-iterations", type=int, default=25, help="Number of repeated slot queries used for the P95 estimate")
    parser.add_argument("--report", help="Optional JSON report output path")
    parser.add_argument("--summary", help="Optional Markdown summary output path")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    report = run_discovery(
        project_code=args.project,
        source_type=args.source_type,
        device_sources=args.device_source,
        fault_sources=args.fault_source,
        database=args.database,
        type_mapping_path=args.type_mapping,
        timezone_default=args.timezone_default,
        performance_iterations=args.performance_iterations,
        report_path=args.report,
        summary_path=args.summary,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
