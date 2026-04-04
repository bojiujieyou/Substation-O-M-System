"""Excel camera inventory importer.

This script imports station camera inventory workbooks into SQLite.
It supports both the legacy single-project schema and the frozen
multi-project slot/camera replacement model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from admin import _multi_project_camera_schema_enabled, _sync_station_project_cameras
from config import Config
from init_db import get_db_path
from parse_excel import ExcelParseError, parse_station_excel
from project_access import get_project_by_code, projects_enabled, table_exists
from utils import backup_sqlite_database, create_db_connection

DATA_SOURCE_PATH = Config.DATA_SOURCE_PATH
COUNTIES = ['丽水', '云和', '庆元', '景宁', '松阳', '缙云', '遂昌', '青田', '龙泉']


def get_connection(database_path: str | None = None):
    return create_db_connection(database_path or get_db_path(), row_factory=True, enable_wal=True)


def upsert_station(cursor, station_data):
    cursor.execute(
        """
        INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name, voltage_level) DO UPDATE SET
            county = excluded.county,
            location = excluded.location,
            ip_range = excluded.ip_range,
            nvr_ip = excluded.nvr_ip,
            nvr_port = excluded.nvr_port,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            station_data['name'],
            station_data['voltage_level'],
            station_data['county'],
            station_data.get('location', ''),
            station_data.get('ip_range', ''),
            station_data.get('nvr_ip', ''),
            station_data.get('nvr_port'),
        ),
    )
    cursor.execute(
        "SELECT id FROM stations WHERE name = ? AND voltage_level = ?",
        (station_data['name'], station_data['voltage_level']),
    )
    return cursor.fetchone()[0]


def upsert_camera_legacy(cursor, station_id, camera_data):
    cursor.execute(
        """
        INSERT INTO cameras (station_id, camera_index, area, location_desc, ip_address, channel_port, channel_number)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(station_id, camera_index, channel_number) DO UPDATE SET
            area = excluded.area,
            location_desc = excluded.location_desc,
            ip_address = excluded.ip_address,
            channel_port = excluded.channel_port
        """,
        (
            station_id,
            camera_data.get('camera_index', ''),
            camera_data.get('area', ''),
            camera_data.get('location_desc') or camera_data.get('location', ''),
            camera_data.get('ip_address', ''),
            camera_data.get('channel_port'),
            camera_data.get('channel_number'),
        ),
    )


def import_excel_file(filepath, county):
    try:
        data = parse_station_excel(filepath)
        data['station']['county'] = county
        return data, None
    except ExcelParseError as exc:
        return None, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"未知错误: {exc}"


def _iter_source_files(source_root: str, counties: list[str] | None):
    selected_counties = counties or COUNTIES
    for county in selected_counties:
        county_path = Path(source_root) / county
        if not county_path.exists():
            continue
        files = sorted(county_path.glob('*.xlsx')) + sorted(county_path.glob('*.xls'))
        for filepath in files:
            yield county, filepath


def _create_import_batch(cursor, project_id: int, mode: str, file_count: int):
    cursor.execute(
        """
        INSERT INTO import_batches (project_id, source_type, mode, file_count, success_count, fail_count)
        VALUES (?, 'import_excel', ?, ?, 0, 0)
        """,
        (project_id, mode, file_count),
    )
    return cursor.lastrowid


def _finalize_import_batch(cursor, batch_id: int, success_count: int, fail_count: int, report_path: str | None):
    cursor.execute(
        """
        UPDATE import_batches
        SET success_count = ?, fail_count = ?, report_path = ?
        WHERE id = ?
        """,
        (success_count, fail_count, report_path, batch_id),
    )


def run_batch_import(
    *,
    database: str | None = None,
    source_root: str | None = None,
    project_code: str = 'unified',
    mode: str = 'best-effort',
    dry_run: bool = False,
    report_path: str | Path | None = None,
    counties: list[str] | None = None,
):
    source_root = source_root or DATA_SOURCE_PATH
    files = list(_iter_source_files(source_root, counties))
    conn = get_connection(database)
    report = {
        'project': project_code,
        'mode': mode,
        'dry_run': dry_run,
        'file_count': len(files),
        'station_count': 0,
        'camera_count': 0,
        'success_count': 0,
        'fail_count': 0,
        'rows': [],
        'aborted': False,
    }

    try:
        cursor = conn.cursor()
        conn.execute("BEGIN")
        multi_project_enabled = _multi_project_camera_schema_enabled(conn)
        project = None
        if multi_project_enabled or projects_enabled(conn):
            project = get_project_by_code(conn, project_code, include_inactive=False)
            if not project:
                raise ValueError(f"项目不存在或未启用: {project_code}")

        batch_id = None
        if (
            not dry_run
            and project
            and table_exists(conn, "import_batches")
        ):
            batch_id = _create_import_batch(cursor, project['id'], mode, len(files))

        for county, filepath in files:
            cursor.execute("SAVEPOINT import_file")
            row_report = {
                'county': county,
                'file': filepath.name,
                'filepath': str(filepath),
                'status': 'pending',
            }
            data, error = import_excel_file(str(filepath), county)
            if error:
                cursor.execute("ROLLBACK TO SAVEPOINT import_file")
                cursor.execute("RELEASE SAVEPOINT import_file")
                row_report['status'] = 'failed'
                row_report['error'] = error
                report['fail_count'] += 1
                report['rows'].append(row_report)
                if mode == 'full-rollback':
                    report['aborted'] = True
                    conn.rollback()
                    break
                continue

            try:
                station_id = upsert_station(cursor, data['station'])
                row_report['station_id'] = station_id
                row_report['station'] = data['station']['name']
                if multi_project_enabled and project:
                    metrics = _sync_station_project_cameras(conn, station_id, project, data['cameras'])
                    row_report.update(metrics)
                    report['camera_count'] += (
                        metrics.get('cameras_added', 0)
                        + metrics.get('cameras_updated', 0)
                        + metrics.get('cameras_replaced', 0)
                    )
                else:
                    for camera in data['cameras']:
                        upsert_camera_legacy(cursor, station_id, camera)
                    row_report['cameras_upserted'] = len(data['cameras'])
                    report['camera_count'] += len(data['cameras'])
                row_report['status'] = 'imported'
                row_report['camera_rows'] = len(data['cameras'])
                report['station_count'] += 1
                report['success_count'] += 1
                report['rows'].append(row_report)
                cursor.execute("RELEASE SAVEPOINT import_file")
            except Exception as exc:
                cursor.execute("ROLLBACK TO SAVEPOINT import_file")
                cursor.execute("RELEASE SAVEPOINT import_file")
                row_report['status'] = 'failed'
                row_report['error'] = str(exc)
                report['fail_count'] += 1
                report['rows'].append(row_report)
                if mode == 'full-rollback':
                    report['aborted'] = True
                    conn.rollback()
                    break

        if report['aborted']:
            pass
        elif dry_run:
            conn.rollback()
        else:
            if batch_id is not None:
                _finalize_import_batch(cursor, batch_id, report['success_count'], report['fail_count'], str(report_path) if report_path else None)
            conn.commit()

        if report_path:
            report_file = Path(report_path)
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        conn.close()

    return report


def parse_args():
    parser = argparse.ArgumentParser(description='导入变电站摄像头 Excel 清单')
    parser.add_argument('--database', help='可选：指定数据库文件路径')
    parser.add_argument('--source-root', default=DATA_SOURCE_PATH, help='Excel 根目录')
    parser.add_argument('--project', default='unified', help='项目 code，默认 unified')
    parser.add_argument('--mode', choices=['best-effort', 'full-rollback'], default='best-effort')
    parser.add_argument('--dry-run', action='store_true', help='只预演，不提交数据库')
    parser.add_argument('--report', help='写入 JSON 导入报告')
    parser.add_argument('--county', action='append', dest='counties', help='仅导入指定县区，可重复传入')
    return parser.parse_args()


def main():
    args = parse_args()
    db_path = args.database or get_db_path()

    print("=" * 60)
    print("变电站数据导入")
    print("=" * 60)
    print(f"数据库: {db_path}")
    print(f"项目: {args.project}")
    print(f"模式: {args.mode}")
    print(f"Dry Run: {'是' if args.dry_run else '否'}")

    if not args.dry_run:
        backup_path = backup_sqlite_database(db_path, label='import_excel')
        if backup_path:
            print(f"[备份] 已创建数据库备份: {backup_path}")

    report = run_batch_import(
        database=db_path,
        source_root=args.source_root,
        project_code=args.project,
        mode=args.mode,
        dry_run=args.dry_run,
        report_path=args.report,
        counties=args.counties,
    )

    print()
    print("=" * 60)
    print("导入完成")
    print("=" * 60)
    print(f"文件数: {report['file_count']}")
    print(f"成功: {report['success_count']}")
    print(f"失败: {report['fail_count']}")
    print(f"站点处理数: {report['station_count']}")
    print(f"摄像头处理数: {report['camera_count']}")
    if report['aborted']:
        print("结果: 已按 full-rollback 回滚")
    elif args.dry_run:
        print("结果: 已按 dry-run 回滚")
    if args.report:
        print(f"报告: {args.report}")

    if report['fail_count']:
        for item in report['rows'][:10]:
            if item['status'] == 'failed':
                print(f"- {item['file']}: {item.get('error', 'unknown error')}")

    return 1 if report['fail_count'] and args.mode == 'full-rollback' else 0


if __name__ == '__main__':
    raise SystemExit(main())
