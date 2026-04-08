#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Import latest station recorder/camera CSVs and worklog workbook."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from admin import _sync_station_project_cameras
from config import Config
from import_faults_worklog import import_worklog_file
from import_review_support import get_project_row, normalize_station_name, table_exists
from init_db import get_db_path
from parse_excel import parse_station_excel
from utils import backup_sqlite_database, create_db_connection


DEFAULT_PROJECT_CODE = "unified"
DEFAULT_CAMERA_IP_SOURCE_ROOT = Config.DATA_SOURCE_PATH
RECORDER_SOURCE_SYSTEM = "recorder_inventory_csv"
CAMERA_SOURCE_SYSTEM = "camera_inventory_csv"
COUNTY_NAMES = ("丽水", "云和", "庆元", "景宁", "松阳", "缙云", "遂昌", "青田", "龙泉", "莲都")
STATION_SUFFIX_TOKENS = ("变电站", "安防", "一键顺控", "集控站", "集控")
SPECIAL_STATION_LABELS = {
    "?洲变": "濛洲变",
}


def clean(value):
    return str(value or "").replace("\t", "").strip()


def clean_station_label(value):
    text = clean(value)
    text = SPECIAL_STATION_LABELS.get(text, text)
    text = re.sub(r"^\d+\s*[kK][vV]\s*", "", text)
    for token in STATION_SUFFIX_TOKENS:
        text = text.replace(token, "")
    return text.strip()


def infer_voltage(value):
    match = re.search(r"(\d+)\s*[kK][vV]", clean(value))
    return f"{match.group(1)}kV" if match else ""


def normalize_voltage(value):
    text = clean(value).lower().replace(" ", "")
    match = re.match(r"(\d+)kv", text)
    return f"{match.group(1)}kV" if match else clean(value)


def infer_county(area_path):
    text = clean(area_path)
    for county in COUNTY_NAMES:
        if county in text:
            return county
    return ""


def parse_station_name_from_path(area_path):
    text = clean(area_path)
    if not text:
        return ""
    return SPECIAL_STATION_LABELS.get(text.split("/")[-1].strip(), text.split("/")[-1].strip())


def parse_ip_port(value):
    text = clean(value)
    if not text:
        return "", None
    if ":" in text:
        host, port_text = text.rsplit(":", 1)
        port = int(port_text) if port_text.isdigit() else None
        return host.strip(), port
    return text, None


def to_int(value):
    text = clean(value)
    return int(text) if text.isdigit() else None


def extract_camera_index(name, channel_number):
    text = clean(name)
    match = re.search(r"#\s*(\d+)", text)
    if match:
        return match.group(1)
    if channel_number is not None:
        return str(channel_number)
    match = re.search(r"(\d+)", text)
    return match.group(1) if match else ""


def recorder_sort_key(recorder):
    name = recorder["recorder_name"]
    match = re.search(r"(\d+)\s*#?$", name)
    order = int(match.group(1)) if match else 9999
    return (order, name)


def normalize_camera_location(value):
    text = clean(value).lower()
    text = re.sub(r"^\s*(室内|室外)[-_/\\\s]*", "", text)
    text = re.sub(r"[\s_\-\\/,:;，。]+", "", text)
    return text


def iter_camera_ip_source_files(source_root):
    root = Path(source_root)
    if not root.exists():
        return []

    for pattern in ("*.xlsx", "*.xls"):
        for filepath in sorted(root.rglob(pattern)):
            name = filepath.name
            if name.startswith("~$"):
                continue
            if "冲突副本" in name:
                continue
            yield filepath


def load_camera_ip_reference(source_root):
    grouped = {}
    for filepath in iter_camera_ip_source_files(source_root):
        try:
            payload = parse_station_excel(str(filepath))
        except Exception:
            continue

        station = payload.get("station") or {}
        station_name = station.get("name")
        station_key = normalize_station_name(station_name)
        if not station_key:
            continue

        bucket = grouped.setdefault(
            station_key,
            {
                "station_name": station_name,
                "by_channel": defaultdict(list),
                "by_index": defaultdict(list),
                "by_location": defaultdict(list),
            },
        )

        for camera in payload.get("cameras") or []:
            ip_address = clean(camera.get("ip_address"))
            if not ip_address:
                continue

            channel_number = camera.get("channel_number")
            camera_index = clean(camera.get("camera_index"))
            location_key = normalize_camera_location(
                camera.get("location") or camera.get("location_desc")
            )
            camera_record = {
                "ip_address": ip_address,
                "camera_index": camera_index,
                "channel_number": channel_number,
                "location": clean(camera.get("location") or camera.get("location_desc")),
            }

            if channel_number is not None:
                bucket["by_channel"][channel_number].append(camera_record)
            if camera_index:
                bucket["by_index"][camera_index].append(camera_record)
            if location_key:
                bucket["by_location"][location_key].append(camera_record)

    return grouped


def resolve_camera_ip(reference, *, station_key, camera_name, channel_number):
    if not reference:
        return ""

    station_bucket = reference.get(station_key)
    if not station_bucket:
        return ""

    camera_index = extract_camera_index(camera_name, channel_number)
    location_key = normalize_camera_location(camera_name)
    if location_key:
        matches = station_bucket["by_location"].get(location_key, [])
        if len(matches) == 1:
            return matches[0]["ip_address"]

    if camera_index:
        matches = station_bucket["by_index"].get(camera_index, [])
        if location_key and len(matches) > 1:
            matches = [
                item for item in matches
                if normalize_camera_location(item.get("location")) == location_key
            ]
        if len(matches) == 1:
            return matches[0]["ip_address"]

    if channel_number is not None:
        matches = station_bucket["by_channel"].get(channel_number, [])
        if location_key and len(matches) > 1:
            matches = [
                item for item in matches
                if normalize_camera_location(item.get("location")) == location_key
            ]
        if camera_index and len(matches) > 1:
            matches = [
                item for item in matches
                if clean(item.get("camera_index")) == camera_index
            ]
        if len(matches) == 1:
            return matches[0]["ip_address"]

    return ""


def read_gbk_csv_rows(path):
    with Path(path).open("r", encoding="gbk", newline="") as handle:
        return list(csv.reader(handle))


def parse_station_csv(path):
    rows = read_gbk_csv_rows(path)
    grouped = {}
    for row in rows[1:]:
        if len(row) < 6:
            continue
        recorder_name = clean(row[0])
        area_path = clean(row[1])
        ip_address = clean(row[4])
        port = to_int(row[5])
        description = clean(row[8]) if len(row) > 8 else ""
        station_label = parse_station_name_from_path(area_path) or clean_station_label(recorder_name)
        station_key = normalize_station_name(station_label)
        if not station_key:
            continue

        bundle = grouped.setdefault(
            station_key,
            {
                "station_label": station_label,
                "county": infer_county(area_path),
                "voltage_level": infer_voltage(recorder_name) or infer_voltage(station_label),
                "recorders": [],
            },
        )
        if not bundle["county"]:
            bundle["county"] = infer_county(area_path)
        if not bundle["voltage_level"]:
            bundle["voltage_level"] = infer_voltage(recorder_name) or infer_voltage(station_label)
        bundle["recorders"].append(
            {
                "recorder_name": recorder_name or station_label,
                "ip_address": ip_address,
                "port": port,
                "description": description,
                "source_key": f"{recorder_name}|{ip_address}|{port or ''}",
            }
        )
    return grouped


def parse_camera_csv(path, camera_ip_reference=None):
    rows = read_gbk_csv_rows(path)
    grouped = {}
    for row in rows[1:]:
        if len(row) < 7:
            continue
        camera_name = clean(row[0])
        area_path = clean(row[1])
        recorder_name = clean(row[3])
        recorder_ip_address, recorder_port = parse_ip_port(row[4])
        recorder_code = clean(row[5])
        channel_number = to_int(row[6])
        station_label = parse_station_name_from_path(area_path)
        station_key = normalize_station_name(station_label)
        if not station_key or not camera_name:
            continue

        camera_ip_address = resolve_camera_ip(
            camera_ip_reference,
            station_key=station_key,
            camera_name=camera_name,
            channel_number=channel_number,
        )

        bundle = grouped.setdefault(
            station_key,
            {
                "station_label": station_label,
                "county": infer_county(area_path),
                "voltage_level": infer_voltage(recorder_name) or infer_voltage(station_label),
                "cameras": [],
            },
        )
        if not bundle["county"]:
            bundle["county"] = infer_county(area_path)
        if not bundle["voltage_level"]:
            bundle["voltage_level"] = infer_voltage(recorder_name) or infer_voltage(station_label)

        bundle["cameras"].append(
            {
                "camera_index": extract_camera_index(camera_name, channel_number),
                "area": "",
                "location": camera_name,
                "location_desc": camera_name,
                "ip_address": camera_ip_address,
                "channel_port": recorder_port,
                "channel_number": channel_number,
                "project_camera_code": recorder_code or camera_name,
                "recorder_name": recorder_name,
                "recorder_ip_address": recorder_ip_address,
                "recorder_port": recorder_port,
            }
        )
    return grouped


def ensure_station_recorders_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS station_recorders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            recorder_name TEXT NOT NULL,
            ip_address TEXT,
            port INTEGER,
            description TEXT,
            source_type TEXT NOT NULL DEFAULT 'inventory_csv',
            source_key TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'retired')),
            retired_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(station_id, project_id, recorder_name, ip_address, port),
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_station_recorders_station_project
        ON station_recorders(station_id, project_id, status)
        """
    )


def upsert_station_external_name(conn, *, station_id, source_system, external_name):
    if not external_name or not table_exists(conn, "station_external_names"):
        return
    conn.execute(
        """
        INSERT INTO station_external_names (
            station_id, source_system, external_name, normalized_name, is_primary
        )
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(source_system, external_name) DO UPDATE SET
            station_id = excluded.station_id,
            normalized_name = excluded.normalized_name
        """,
        (
            station_id,
            source_system,
            external_name,
            normalize_station_name(external_name),
        ),
    )


def load_station_rows(conn):
    rows = conn.execute(
        """
        SELECT id, name, county, voltage_level, location, ip_range, nvr_ip, nvr_port
        FROM stations
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) if hasattr(row, "keys") else row for row in rows]


def resolve_existing_station(station_rows, *, station_label, voltage_level="", county=""):
    literal = clean(station_label).lower()
    literal_prefixed = f"{voltage_level}{clean_station_label(station_label)}".lower() if voltage_level else ""
    station_key = normalize_station_name(station_label)
    voltage_key = normalize_voltage(voltage_level)

    literal_matches = [row for row in station_rows if clean(row["name"]).lower() in {literal, literal_prefixed}]
    if len(literal_matches) == 1:
        return literal_matches[0]

    normalized_matches = [row for row in station_rows if normalize_station_name(row["name"]) == station_key]
    if voltage_key:
        voltage_matches = [
            row for row in normalized_matches if normalize_voltage(row["voltage_level"]) == voltage_key
        ]
        if len(voltage_matches) == 1:
            return voltage_matches[0]
        if county:
            county_voltage_matches = [row for row in voltage_matches if clean(row["county"]) == clean(county)]
            if len(county_voltage_matches) == 1:
                return county_voltage_matches[0]

    if county:
        county_matches = [row for row in normalized_matches if clean(row["county"]) == clean(county)]
        if len(county_matches) == 1:
            return county_matches[0]

    if len(normalized_matches) == 1:
        return normalized_matches[0]

    return None


def upsert_station(conn, *, station_label, voltage_level="", county=""):
    station_rows = load_station_rows(conn)
    existing = resolve_existing_station(
        station_rows,
        station_label=station_label,
        voltage_level=voltage_level,
        county=county,
    )
    if existing:
        target_name = existing["name"]
        target_voltage = existing["voltage_level"]
        conn.execute(
            """
            UPDATE stations
            SET county = CASE
                    WHEN COALESCE(county, '') = '' AND COALESCE(?, '') <> '' THEN ?
                    ELSE county
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (county, county, existing["id"]),
        )
        return existing["id"], target_name, target_voltage, False

    base_name = clean_station_label(station_label)
    preferred_name = f"{voltage_level}{base_name}" if voltage_level and not clean(station_label).startswith(voltage_level) else clean(station_label)
    conn.execute(
        """
        INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
        VALUES (?, ?, ?, '', '', '', NULL)
        ON CONFLICT(name, voltage_level) DO UPDATE SET
            county = CASE
                WHEN COALESCE(stations.county, '') = '' AND COALESCE(excluded.county, '') <> '' THEN excluded.county
                ELSE stations.county
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (preferred_name, voltage_level or infer_voltage(preferred_name), county),
    )
    row = conn.execute(
        "SELECT id, name, voltage_level FROM stations WHERE name = ? AND voltage_level = ?",
        (preferred_name, voltage_level or infer_voltage(preferred_name)),
    ).fetchone()
    return row["id"], row["name"], row["voltage_level"], True


def sync_station_recorders(conn, *, station_id, project_id, recorders):
    ensure_station_recorders_table(conn)
    metrics = {
        "recorders_added": 0,
        "recorders_updated": 0,
        "recorders_retired": 0,
    }
    existing_rows = conn.execute(
        """
        SELECT id, recorder_name, ip_address, port
        FROM station_recorders
        WHERE station_id = ? AND project_id = ? AND status = 'active'
        """,
        (station_id, project_id),
    ).fetchall()
    existing_by_key = {
        (clean(row["recorder_name"]), clean(row["ip_address"]), row["port"]): row["id"]
        for row in existing_rows
    }
    seen_ids = set()

    for recorder in recorders:
        key = (clean(recorder["recorder_name"]), clean(recorder["ip_address"]), recorder["port"])
        recorder_id = existing_by_key.get(key)
        if recorder_id is not None:
            conn.execute(
                """
                UPDATE station_recorders
                SET description = ?, source_key = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (recorder["description"], recorder["source_key"], recorder_id),
            )
            seen_ids.add(recorder_id)
            metrics["recorders_updated"] += 1
            continue

        cursor = conn.execute(
            """
            INSERT INTO station_recorders (
                station_id, project_id, recorder_name, ip_address, port,
                description, source_type, source_key, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'inventory_csv', ?, 'active')
            """,
            (
                station_id,
                project_id,
                recorder["recorder_name"],
                recorder["ip_address"],
                recorder["port"],
                recorder["description"],
                recorder["source_key"],
            ),
        )
        seen_ids.add(cursor.lastrowid)
        metrics["recorders_added"] += 1

    retire_ids = [
        row["id"]
        for row in existing_rows
        if row["id"] not in seen_ids
    ]
    if retire_ids:
        placeholders = ", ".join(["?"] * len(retire_ids))
        conn.execute(
            f"""
            UPDATE station_recorders
            SET status = 'retired',
                retired_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            retire_ids,
        )
        metrics["recorders_retired"] += len(retire_ids)

    primary = sorted(recorders, key=recorder_sort_key)[0] if recorders else None
    if primary:
        conn.execute(
            """
            UPDATE stations
            SET nvr_ip = ?, nvr_port = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (primary["ip_address"], primary["port"], station_id),
        )
    return metrics


def import_inventory_bundle(
    *,
    station_csv,
    camera_csv,
    database_path,
    project_code=DEFAULT_PROJECT_CODE,
    camera_ip_source_root=DEFAULT_CAMERA_IP_SOURCE_ROOT,
    dry_run=False,
):
    conn = create_db_connection(database_path, row_factory=True, enable_wal=True)
    project = get_project_row(conn, project_code)
    if not project:
        raise ValueError(f"Project not found: {project_code}")

    camera_ip_reference = load_camera_ip_reference(camera_ip_source_root)
    station_groups = parse_station_csv(station_csv)
    camera_groups = parse_camera_csv(camera_csv, camera_ip_reference=camera_ip_reference)

    report = {
        "project": project_code,
        "station_csv": str(Path(station_csv).resolve()),
        "camera_csv": str(Path(camera_csv).resolve()),
        "camera_ip_source_root": str(Path(camera_ip_source_root).resolve()),
        "camera_ip_station_seen": len(camera_ip_reference),
        "dry_run": dry_run,
        "stations_seen": len(station_groups),
        "camera_station_seen": len(camera_groups),
        "stations_created": 0,
        "stations_matched": 0,
        "recorders_added": 0,
        "recorders_updated": 0,
        "recorders_retired": 0,
        "cameras_added": 0,
        "cameras_updated": 0,
        "cameras_replaced": 0,
        "cameras_retired": 0,
        "rows": [],
    }

    try:
        conn.execute("BEGIN")
        station_keys = sorted(set(station_groups) | set(camera_groups))
        for station_key in station_keys:
            station_info = station_groups.get(station_key, {})
            camera_info = camera_groups.get(station_key, {})
            station_label = (
                station_info.get("station_label")
                or camera_info.get("station_label")
                or station_key
            )
            voltage_level = station_info.get("voltage_level") or camera_info.get("voltage_level") or ""
            county = station_info.get("county") or camera_info.get("county") or ""
            recorders = station_info.get("recorders", [])
            cameras = camera_info.get("cameras", [])

            station_id, station_name, _, created = upsert_station(
                conn,
                station_label=station_label,
                voltage_level=voltage_level,
                county=county,
            )
            report["stations_created" if created else "stations_matched"] += 1
            upsert_station_external_name(
                conn,
                station_id=station_id,
                source_system=RECORDER_SOURCE_SYSTEM,
                external_name=station_label,
            )
            upsert_station_external_name(
                conn,
                station_id=station_id,
                source_system=CAMERA_SOURCE_SYSTEM,
                external_name=station_label,
            )

            row_report = {
                "station_label": station_label,
                "station_name": station_name,
                "station_id": station_id,
                "created": created,
                "recorder_count": len(recorders),
                "camera_count": len(cameras),
            }

            if recorders:
                recorder_metrics = sync_station_recorders(
                    conn,
                    station_id=station_id,
                    project_id=project["id"],
                    recorders=recorders,
                )
                row_report.update(recorder_metrics)
                for key, value in recorder_metrics.items():
                    report[key] += value

            if cameras:
                camera_metrics = _sync_station_project_cameras(conn, station_id, project, cameras)
                row_report.update(camera_metrics)
                for key in ("cameras_added", "cameras_updated", "cameras_replaced", "cameras_retired"):
                    report[key] += camera_metrics.get(key, 0)

            report["rows"].append(row_report)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    return report


def parse_args():
    parser = argparse.ArgumentParser(description="Import latest recorder/camera CSVs and worklog workbook")
    parser.add_argument("--station-csv", required=True, help="Path to latest 变电站.csv")
    parser.add_argument("--camera-csv", required=True, help="Path to latest 摄像头.csv")
    parser.add_argument(
        "--camera-ip-root",
        default=DEFAULT_CAMERA_IP_SOURCE_ROOT,
        help="Root directory containing per-station device Excel files with real camera IPs",
    )
    parser.add_argument("--worklog", required=True, help="Path to latest 工作记录.xlsx")
    parser.add_argument("--database", default=get_db_path(), help="SQLite database path")
    parser.add_argument("--project", default=DEFAULT_PROJECT_CODE, help="Project code for inventory import")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write database")
    parser.add_argument("--skip-backup", action="store_true", help="Skip automatic backup before write import")
    parser.add_argument("--report", help="Optional JSON report output path")
    return parser.parse_args()


def main():
    args = parse_args()
    database_path = Path(args.database).resolve()
    report_path = Path(args.report).resolve() if args.report else None

    if not args.dry_run and not args.skip_backup:
        backup_path = backup_sqlite_database(database_path, label="latest_bundle")
        if backup_path:
            print(f"[backup] {backup_path}")

    inventory_report = import_inventory_bundle(
        station_csv=args.station_csv,
        camera_csv=args.camera_csv,
        database_path=database_path,
        project_code=args.project,
        camera_ip_source_root=args.camera_ip_root,
        dry_run=args.dry_run,
    )
    worklog_report = import_worklog_file(
        source_file=args.worklog,
        database_path=database_path,
        dry_run=args.dry_run,
    )

    report = {
        "database": str(database_path),
        "dry_run": args.dry_run,
        "inventory": inventory_report,
        "worklog": {
            key: worklog_report[key]
            for key in [
                "inserted",
                "duplicates_skipped",
                "queue_items_created",
                "station_proposals_created",
                "rows_skipped",
                "fail_count",
            ]
        },
    }

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
