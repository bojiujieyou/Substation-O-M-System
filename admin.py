# admin.py — 管理后台模块
"""
变电站数据管理：上传Excel、增删改查
"""
import os
import re
import shutil
import sqlite3
import logging
import json
import hashlib
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Blueprint, request, jsonify, session, current_app, render_template
from werkzeug.utils import secure_filename
from ai_fault_analysis import get_ai_runtime_status, probe_nvidia_health
from photo_indexer import run_full_index, run_incremental_index, get_photo_stats, list_unmatched, manual_match_photo
from project_access import get_project_by_code, projects_enabled, table_exists
from utils import get_db
from import_batch_summary import build_import_batch_summary

logger = logging.getLogger('station_monitor')

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ALLOWED_EXTENSIONS = {'xlsx', 'xls'}
IMPORT_TYPE_INVENTORY = 'inventory'
IMPORT_TYPE_DAILY_FAULT_SUMMARY = 'daily_fault_summary'

def require_admin(f):
    """管理员权限检查装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _normalize_admin_import_type(value):
    normalized = (value or '').strip().lower()
    if normalized == IMPORT_TYPE_DAILY_FAULT_SUMMARY:
        return IMPORT_TYPE_DAILY_FAULT_SUMMARY
    return IMPORT_TYPE_INVENTORY


def _resolve_upload_project(db):
    if projects_enabled(db):
        return _resolve_admin_project(db, request.form.get('project'))
    legacy_project = get_project_by_code(db, 'unified', include_inactive=False)
    if legacy_project:
        return legacy_project, None
    return None, (jsonify({'error': '未找到可用项目'}), 400)


def _upload_daily_fault_summary():
    from import_daily_fault_summary import DailyFaultSummaryParseError, import_daily_fault_summary_file

    db = get_db()
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    project, error = _resolve_upload_project(db)
    if error:
        return error

    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': '只支持xlsx/xls格式'}), 400

    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    upload_dir = os.path.join(os.path.dirname(current_app.config['DATABASE_PATH']), 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f'{timestamp}_{filename}')
    file.save(filepath)

    try:
        report = import_daily_fault_summary_file(
            filepath,
            project_code=project['code'],
            database_path=current_app.config['DATABASE_PATH'],
        )
        summary = report.get('summary') or {}
        payload = {
            'message': '每日故障汇总导入完成',
            'import_type': IMPORT_TYPE_DAILY_FAULT_SUMMARY,
            'project': project['code'],
            'batch_id': report.get('batch_id'),
            'result_url': f"/admin/import-batches/{report['batch_id']}" if report.get('batch_id') else None,
            'faults_added': int(summary.get('inserted') or 0),
            'queued_count': int(summary.get('queue_items_created') or 0),
            'proposal_count': int(summary.get('station_proposals_created') or 0),
            'duplicates_skipped': int(summary.get('duplicates_skipped') or 0),
            'source_date': report.get('source_date'),
            'ai_status': get_ai_runtime_status(),
        }
        return jsonify(payload)
    except Exception as e:
        logger.exception('upload_daily_fault_summary failed')
        status_code = 400 if isinstance(e, DailyFaultSummaryParseError) else 500
        return jsonify({'error': f'解析失败: {str(e)}'}), status_code
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)


def parse_excel_admin(filepath):
    """解析Excel文件，返回标准化数据"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from parse_excel import parse_station_excel, validate_station_inventory_data
    data = parse_station_excel(filepath)
    validate_station_inventory_data(data, filepath)
    return data


@admin_bp.route('/ai-status', methods=['GET'])
@require_admin
def admin_ai_status():
    return jsonify(probe_nvidia_health())


def _validate_inventory_upload(data, filepath):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from parse_excel import validate_station_inventory_data
    return validate_station_inventory_data(data, filepath)


def _create_excel_import_batch(cursor, project_id: int, file_count: int) -> int:
    cursor.execute(
        """
        INSERT INTO import_batches (project_id, source_type, mode, file_count, success_count, fail_count)
        VALUES (?, 'import_excel', 'best-effort', ?, 0, 0)
        """,
        (project_id, file_count),
    )
    return cursor.lastrowid



@admin_bp.route('/upload', methods=['POST'])
@require_admin
def upload_excel():
    """上传Excel文件并导入"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    county = (request.form.get('county') or '').strip()

    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '只支持xlsx/xls格式'}), 400

    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    upload_dir = os.path.join(os.path.dirname(current_app.config['DATABASE_PATH']), 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f'{timestamp}_{filename}')
    file.save(filepath)

    db = None
    batch_id = None
    report_path = None

    try:
        data = parse_excel_admin(filepath)
        _validate_inventory_upload(data, filepath)
        station_county = county or data['station'].get('county', '')

        db = get_db()
        cursor = db.cursor()

        cursor.execute("""
            INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, voltage_level) DO UPDATE SET
                county = CASE
                    WHEN excluded.county IS NOT NULL AND TRIM(excluded.county) <> '' THEN excluded.county
                    ELSE stations.county
                END,
                location = excluded.location,
                ip_range = excluded.ip_range,
                nvr_ip = excluded.nvr_ip,
                nvr_port = excluded.nvr_port,
                updated_at = CURRENT_TIMESTAMP
        """, (
            data['station']['name'],
            data['station']['voltage_level'],
            station_county,
            data['station']['location'],
            data['station']['ip_range'],
            data['station']['nvr_ip'],
            data['station']['nvr_port'],
        ))

        cursor.execute(
            "SELECT id FROM stations WHERE name = ? AND voltage_level = ?",
            (data['station']['name'], data['station']['voltage_level'])
        )
        station_id = cursor.fetchone()[0]

        cursor.execute("DELETE FROM cameras WHERE station_id = ?", (station_id,))

        cameras_added = 0
        for camera in data['cameras']:
            cursor.execute("""
                INSERT INTO cameras (station_id, camera_index, area, location_desc, ip_address, channel_port, channel_number)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                station_id,
                camera.get('camera_index', ''),
                camera.get('area', ''),
                camera.get('location', ''),
                camera.get('ip_address', ''),
                camera.get('channel_port'),
                camera.get('channel_number'),
            ))
            cameras_added += 1

        legacy_project = get_project_by_code(db, 'unified', include_inactive=False) if projects_enabled(db) else None
        if legacy_project and table_exists(db, 'import_batches'):
            batch_id = _create_excel_import_batch(cursor, legacy_project['id'], 1)
            report_dir = os.path.join(os.path.dirname(current_app.config['DATABASE_PATH']), 'import_reports')
            os.makedirs(report_dir, exist_ok=True)
            report_path = os.path.join(report_dir, f'import_excel_batch_{batch_id}.json')
            cursor.execute(
                """
                UPDATE import_batches
                SET success_count = 1, fail_count = 0, report_path = ?
                WHERE id = ?
                """,
                (report_path, batch_id),
            )

        db.commit()

        if batch_id is not None and report_path:
            report_payload = {
                'project': legacy_project['code'],
                'mode': 'best-effort',
                'dry_run': False,
                'file_count': 1,
                'station_count': 1,
                'camera_count': cameras_added,
                'success_count': 1,
                'fail_count': 0,
                'rows': [
                    {
                        'county': station_county,
                        'file': filename,
                        'filepath': filepath,
                        'status': 'imported',
                        'station_id': station_id,
                        'station': data['station']['name'],
                        'camera_rows': len(data['cameras']),
                        'cameras_added': cameras_added,
                    }
                ],
                'aborted': False,
            }
            Path(report_path).write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding='utf-8')

        logger.info(f"Excel imported: station={data['station']['name']}, cameras={cameras_added}, county={station_county}")

        payload = {
            'message': '导入成功',
            'import_type': IMPORT_TYPE_INVENTORY,
            'station': data['station']['name'],
            'station_id': station_id,
            'cameras_added': cameras_added,
        }
        if batch_id is not None and legacy_project:
            payload['project'] = legacy_project['code']
            payload['batch_id'] = batch_id
            payload['result_url'] = f"/admin/import-batches/{batch_id}"
        return jsonify(payload)

    except Exception as e:
        if db is not None:
            db.rollback()
        if report_path and os.path.exists(report_path):
            os.remove(report_path)
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from parse_excel import ExcelParseError
        status_code = 400 if isinstance(e, ExcelParseError) else 500
        return jsonify({'error': f'解析失败: {str(e)}'}), status_code
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

# ============================================================
# 变电站管理
# ============================================================

@admin_bp.route('/stations', methods=['GET'])
@require_admin
def list_stations():
    """获取所有变电站（带摄像头数量）"""
    db = get_db()
    cursor = db.cursor()
    project_code = (request.args.get('project') or '').strip()

    if _multi_project_camera_schema_enabled(db) and project_code and project_code != 'all':
        project, error = _resolve_admin_project(db, project_code)
        if error:
            return error

        rows = cursor.execute(
            """
            SELECT
                s.*,
                (
                    SELECT COUNT(*)
                    FROM cameras c
                    WHERE c.station_id = s.id
                      AND c.project_id = ?
                      AND c.status = 'active'
                ) AS camera_count
            FROM stations s
            WHERE EXISTS (
                SELECT 1
                FROM cameras c
                WHERE c.station_id = s.id
                  AND c.project_id = ?
                  AND c.status = 'active'
            )
               OR EXISTS (
                SELECT 1
                FROM fault_reports f
                WHERE f.station_id = s.id
                  AND f.project_id = ?
            )
            ORDER BY s.county, s.name
            """,
            (project['id'], project['id'], project['id']),
        ).fetchall()
    else:
        rows = cursor.execute(
            """
            SELECT s.*,
                   COUNT(c.id) as camera_count
            FROM stations s
            LEFT JOIN cameras c ON c.station_id = s.id
            GROUP BY s.id
            ORDER BY s.county, s.name
            """
        ).fetchall()

    stations = [dict(row) for row in rows]
    return jsonify({'stations': stations})

@admin_bp.route('/stations', methods=['POST'])
@require_admin
def create_station():
    """新建变电站"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    required = ['name', 'voltage_level']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缺少必填字段: {field}'}), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data['name'],
        data['voltage_level'],
        data.get('county', ''),
        data.get('location', ''),
        data.get('ip_range', ''),
        data.get('nvr_ip', ''),
        data.get('nvr_port', ''),
    ))

    station_id = cursor.lastrowid
    db.commit()
    logger.info(f"Station created: id={station_id}, name={data['name']}")

    return jsonify({'message': '新建成功', 'station_id': station_id}), 201

@admin_bp.route('/stations/<int:station_id>', methods=['DELETE'])
@require_admin
def delete_station(station_id):
    """删除变电站及其摄像头"""
    db = get_db()
    station = db.execute("SELECT id, name FROM stations WHERE id = ?", (station_id,)).fetchone()
    if not station:
        return jsonify({'error': '变电站不存在'}), 404

    try:
        # 先删故障记录，避免 fault_reports.camera_id 引用待删除摄像头时触发外键失败。
        db.execute("DELETE FROM fault_reports WHERE station_id = ?", (station_id,))
        db.execute("DELETE FROM cameras WHERE station_id = ?", (station_id,))

        if table_exists(db, "station_recorders"):
            db.execute("DELETE FROM station_recorders WHERE station_id = ?", (station_id,))
        if table_exists(db, "station_external_names"):
            db.execute("DELETE FROM station_external_names WHERE station_id = ?", (station_id,))
        if table_exists(db, "station_aliases"):
            db.execute("DELETE FROM station_aliases WHERE station_id = ?", (station_id,))
        if table_exists(db, "photos"):
            db.execute("UPDATE photos SET station_id = NULL WHERE station_id = ?", (station_id,))
        if table_exists(db, "camera_slots"):
            db.execute("DELETE FROM camera_slots WHERE station_id = ?", (station_id,))
        if table_exists(db, "station_name_mapping_proposals"):
            db.execute(
                "UPDATE station_name_mapping_proposals SET candidate_station_id = NULL WHERE candidate_station_id = ?",
                (station_id,),
            )

        db.execute("DELETE FROM stations WHERE id = ?", (station_id,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        logger.exception("Station delete blocked by related records: id=%s, name=%s", station_id, station["name"])
        return jsonify({'error': '删除失败：该变电站仍有关联数据未清理，请稍后重试或联系管理员检查。'}), 409
    except Exception:
        db.rollback()
        logger.exception("Station delete failed unexpectedly: id=%s, name=%s", station_id, station["name"])
        return jsonify({'error': '删除失败：系统处理该变电站时发生异常。'}), 500

    logger.info(f"Station deleted: id={station_id}, name={station['name']}")
    return jsonify({'message': f'已删除变电站 {station["name"]}'})

@admin_bp.route('/stations/<int:station_id>', methods=['PUT'])
@require_admin
def update_station(station_id):
    """更新变电站信息"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id FROM stations WHERE id = ?", (station_id,))
    if not cursor.fetchone():
        return jsonify({'error': '变电站不存在'}), 404

    # 动态构建更新字段（只更新传入的字段）
    update_fields = []
    params = []
    for field in ('name', 'voltage_level', 'county', 'location', 'ip_range', 'nvr_ip', 'nvr_port'):
        if field in data:
            update_fields.append(f"{field} = ?")
            params.append(data[field])

    # 坐标字段单独处理（支持设置为NULL）
    if 'latitude' in data:
        update_fields.append("latitude = ?")
        params.append(data['latitude'])  # 可以是 None 来清除
    if 'longitude' in data:
        update_fields.append("longitude = ?")
        params.append(data['longitude'])

    if not update_fields:
        return jsonify({'error': '没有要更新的字段'}), 400

    update_fields.append("updated_at = CURRENT_TIMESTAMP")
    query = f"UPDATE stations SET {', '.join(update_fields)} WHERE id = ?"
    params.append(station_id)
    cursor.execute(query, params)

    db.commit()

    return jsonify({'message': '更新成功'})

# ============================================================
# 摄像头管理
# ============================================================

@admin_bp.route('/cameras/<int:camera_id>', methods=['DELETE'])
@require_admin
def delete_camera(camera_id):
    """删除摄像头"""
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id FROM cameras WHERE id = ?", (camera_id,))
    if not cursor.fetchone():
        return jsonify({'error': '摄像头不存在'}), 404

    cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
    db.commit()

    return jsonify({'message': '已删除摄像头'})

@admin_bp.route('/cameras', methods=['POST'])
@require_admin
def add_camera():
    """添加摄像头"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效数据'}), 400

    required = ['station_id', 'ip_address']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缺少必填字段: {field}'}), 400

    db = get_db()
    cursor = db.cursor()

    # 检查变电站是否存在
    cursor.execute("SELECT id FROM stations WHERE id = ?", (data['station_id'],))
    if not cursor.fetchone():
        return jsonify({'error': '变电站不存在'}), 404

    cursor.execute("""
        INSERT INTO cameras (station_id, camera_index, area, location_desc, ip_address, channel_port, channel_number)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data['station_id'],
        data.get('camera_index', ''),
        data.get('area', ''),
        data.get('location', ''),
        data.get('ip_address'),
        data.get('channel_port'),
        data.get('channel_number'),
    ))

    camera_id = cursor.lastrowid
    db.commit()

    return jsonify({'message': '添加成功', 'camera_id': camera_id}), 201

# ============================================================
# 批量操作
# ============================================================

def _get_table_columns(db, table_name):
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def ensure_camera_recorder_metadata_columns(db):
    if not table_exists(db, "cameras"):
        return

    columns = _get_table_columns(db, "cameras")
    required_columns = {
        "recorder_name": "TEXT",
        "recorder_ip_address": "TEXT",
        "recorder_port": "INTEGER",
    }
    for column_name, column_type in required_columns.items():
        if column_name in columns:
            continue
        db.execute(f"ALTER TABLE cameras ADD COLUMN {column_name} {column_type}")
        columns.add(column_name)


def _multi_project_camera_schema_enabled(db):
    camera_columns = _get_table_columns(db, "cameras")
    return (
        projects_enabled(db)
        and table_exists(db, "camera_slots")
        and {"project_id", "slot_id", "status"}.issubset(camera_columns)
    )


def _resolve_admin_project(db, project_code):
    project_code = (project_code or '').strip()
    if not project_code:
        return None, (jsonify({'error': '缂哄皯 project 鍙傛暟'}), 400)
    project = get_project_by_code(db, project_code, include_inactive=False)
    if not project:
        return None, (jsonify({'error': '椤圭洰涓嶅瓨鍦�'}), 404)
    return project, None


def _normalize_text(value):
    return (value or '').strip()


def _build_legacy_slot_suffix(area_key, location_key):
    seed = f"{area_key}|{location_key}"
    digest = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:10]
    compact = re.sub(r'\s+', '', seed)
    compact = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff#-]+', '_', compact).strip('_')
    compact = compact[:24] or 'NA'
    return f"{compact}_{digest}"


def _build_slot_code(camera, station_id, project_code, row_index):
    raw_slot_code = _normalize_text(camera.get('slot_code'))
    if raw_slot_code:
        return raw_slot_code

    channel_key = camera.get('channel_number')
    if channel_key in (None, ''):
        channel_key = _normalize_text(camera.get('camera_index')) or f"ROW{row_index + 1}"

    location_key = _normalize_text(camera.get('location') or camera.get('location_desc'))
    area_key = _normalize_text(camera.get('area'))
    suffix = _build_legacy_slot_suffix(area_key, location_key)
    return f"LEGACY_{project_code}_{station_id}_{channel_key}_{suffix}"


def _ensure_camera_slot(cursor, station_id, project_id, project_code, camera, row_index):
    location_desc = _normalize_text(camera.get('location') or camera.get('location_desc'))
    area = _normalize_text(camera.get('area'))
    channel_number = camera.get('channel_number')
    slot_code = _build_slot_code(camera, station_id, project_code, row_index)

    row = cursor.execute(
        """
        SELECT id
        FROM camera_slots
        WHERE station_id = ? AND project_id = ? AND slot_code = ?
        """,
        (station_id, project_id, slot_code),
    ).fetchone()
    if row:
        return row['id'], slot_code

    row = cursor.execute(
        """
        SELECT id
        FROM camera_slots
        WHERE station_id = ?
          AND project_id = ?
          AND location_desc = ?
          AND area = ?
          AND ((channel_number IS NULL AND ? IS NULL) OR channel_number = ?)
        """,
        (station_id, project_id, location_desc, area, channel_number, channel_number),
    ).fetchone()
    if row:
        return row['id'], slot_code

    cursor.execute(
        """
        INSERT INTO camera_slots (slot_code, station_id, project_id, location_desc, area, channel_number)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (slot_code, station_id, project_id, location_desc, area, channel_number),
    )
    return cursor.lastrowid, slot_code


def _camera_matches(active_camera, camera):
    return (
        _normalize_text(active_camera['camera_index']) == _normalize_text(camera.get('camera_index'))
        and _normalize_text(active_camera['area']) == _normalize_text(camera.get('area'))
        and _normalize_text(active_camera['location_desc']) == _normalize_text(camera.get('location') or camera.get('location_desc'))
        and _normalize_text(active_camera['ip_address']) == _normalize_text(camera.get('ip_address'))
        and active_camera['channel_port'] == camera.get('channel_port')
        and active_camera['channel_number'] == camera.get('channel_number')
        and _normalize_text(active_camera['recorder_name']) == _normalize_text(camera.get('recorder_name'))
        and _normalize_text(active_camera['recorder_ip_address']) == _normalize_text(camera.get('recorder_ip_address'))
        and active_camera['recorder_port'] == camera.get('recorder_port')
    )


def _insert_camera_instance(cursor, slot_id, station_id, project_id, camera):
    cursor.execute(
        """
        INSERT INTO cameras (
            slot_id, station_id, project_id, project_camera_code, camera_index,
            area, location_desc, ip_address, channel_port, channel_number,
            recorder_name, recorder_ip_address, recorder_port, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            slot_id,
            station_id,
            project_id,
            camera.get('project_camera_code'),
            camera.get('camera_index', ''),
            _normalize_text(camera.get('area')),
            _normalize_text(camera.get('location') or camera.get('location_desc')),
            camera.get('ip_address', ''),
            camera.get('channel_port'),
            camera.get('channel_number'),
            _normalize_text(camera.get('recorder_name')),
            _normalize_text(camera.get('recorder_ip_address')),
            camera.get('recorder_port'),
        ),
    )
    return cursor.lastrowid


def _replace_active_camera(cursor, active_camera, replacement_camera):
    cursor.execute(
        """
        UPDATE cameras
        SET status = 'replaced',
            retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP),
            replaced_by_camera_id = NULL
        WHERE id = ?
        """,
        (active_camera['id'],),
    )
    new_camera_id = _insert_camera_instance(
        cursor,
        active_camera['slot_id'],
        active_camera['station_id'],
        active_camera['project_id'],
        replacement_camera,
    )
    cursor.execute(
        "UPDATE cameras SET replaced_by_camera_id = ? WHERE id = ?",
        (new_camera_id, active_camera['id']),
    )
    return new_camera_id


def _sync_station_project_cameras(db, station_id, project, cameras):
    ensure_camera_recorder_metadata_columns(db)
    cursor = db.cursor()
    project_id = project['id']
    project_code = project['code']
    processed_camera_ids = set()
    metrics = {
        'cameras_added': 0,
        'cameras_updated': 0,
        'cameras_replaced': 0,
        'cameras_retired': 0,
    }

    for row_index, camera in enumerate(cameras):
        slot_id, _ = _ensure_camera_slot(cursor, station_id, project_id, project_code, camera, row_index)
        location_desc = _normalize_text(camera.get('location') or camera.get('location_desc'))
        area = _normalize_text(camera.get('area'))
        active_camera = cursor.execute(
            "SELECT * FROM cameras WHERE slot_id = ? AND status = 'active'",
            (slot_id,),
        ).fetchone()

        if active_camera and _camera_matches(active_camera, camera):
            cursor.execute(
                """
                UPDATE cameras
                SET station_id = ?, project_id = ?, camera_index = ?, area = ?, location_desc = ?,
                    ip_address = ?, channel_port = ?, channel_number = ?,
                    recorder_name = ?, recorder_ip_address = ?, recorder_port = ?
                WHERE id = ?
                """,
                (
                    station_id,
                    project_id,
                    camera.get('camera_index', ''),
                    area,
                    location_desc,
                    camera.get('ip_address', ''),
                    camera.get('channel_port'),
                    camera.get('channel_number'),
                    _normalize_text(camera.get('recorder_name')),
                    _normalize_text(camera.get('recorder_ip_address')),
                    camera.get('recorder_port'),
                    active_camera['id'],
                ),
            )
            processed_camera_ids.add(active_camera['id'])
            metrics['cameras_updated'] += 1
            continue

        normalized_camera = {
            **camera,
            'area': area,
            'location_desc': location_desc,
            'location': location_desc,
        }
        if active_camera:
            new_camera_id = _replace_active_camera(cursor, active_camera, normalized_camera)
        else:
            new_camera_id = _insert_camera_instance(
                cursor,
                slot_id,
                station_id,
                project_id,
                normalized_camera,
            )
        processed_camera_ids.add(new_camera_id)
        if active_camera:
            metrics['cameras_replaced'] += 1
        else:
            metrics['cameras_added'] += 1

    retire_query = """
        UPDATE cameras
        SET status = 'retired',
            retired_at = CURRENT_TIMESTAMP
        WHERE station_id = ?
          AND project_id = ?
          AND status = 'active'
    """
    retire_params = [station_id, project_id]
    if processed_camera_ids:
        placeholders = ", ".join(["?"] * len(processed_camera_ids))
        retire_query += f" AND id NOT IN ({placeholders})"
        retire_params.extend(sorted(processed_camera_ids))
    before = db.total_changes
    cursor.execute(retire_query, retire_params)
    metrics['cameras_retired'] = db.total_changes - before
    return metrics


def upload_excel_scoped():
    import_type = _normalize_admin_import_type(request.form.get('import_type'))
    if import_type == IMPORT_TYPE_DAILY_FAULT_SUMMARY:
        return _upload_daily_fault_summary()

    db = get_db()
    if not _multi_project_camera_schema_enabled(db):
        return upload_excel()

    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    county = (request.form.get('county') or '').strip()
    project, error = _resolve_admin_project(db, request.form.get('project'))
    if error:
        return error

    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': '只支持xlsx/xls格式'}), 400

    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    upload_dir = os.path.join(os.path.dirname(current_app.config['DATABASE_PATH']), 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f'{timestamp}_{filename}')
    file.save(filepath)

    report_dir = os.path.join(os.path.dirname(current_app.config['DATABASE_PATH']), 'import_reports')
    batch_id = None
    report_path = None

    try:
        data = parse_excel_admin(filepath)
        _validate_inventory_upload(data, filepath)
        station_county = county or data['station'].get('county', '')
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, voltage_level) DO UPDATE SET
                county = CASE
                    WHEN excluded.county IS NOT NULL AND TRIM(excluded.county) <> '' THEN excluded.county
                    ELSE stations.county
                END,
                location = excluded.location,
                ip_range = excluded.ip_range,
                nvr_ip = excluded.nvr_ip,
                nvr_port = excluded.nvr_port,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                data['station']['name'],
                data['station']['voltage_level'],
                station_county,
                data['station']['location'],
                data['station']['ip_range'],
                data['station']['nvr_ip'],
                data['station']['nvr_port'],
            ),
        )
        cursor.execute(
            "SELECT id FROM stations WHERE name = ? AND voltage_level = ?",
            (data['station']['name'], data['station']['voltage_level']),
        )
        station_id = cursor.fetchone()[0]
        metrics = _sync_station_project_cameras(db, station_id, project, data['cameras'])

        if table_exists(db, 'import_batches'):
            batch_id = _create_excel_import_batch(cursor, project['id'], 1)
            os.makedirs(report_dir, exist_ok=True)
            report_path = os.path.join(report_dir, f'import_excel_batch_{batch_id}.json')
            cursor.execute(
                """
                UPDATE import_batches
                SET success_count = 1, fail_count = 0, report_path = ?
                WHERE id = ?
                """,
                (report_path, batch_id),
            )

        db.commit()

        if batch_id is not None and report_path:
            report_payload = {
                'project': project['code'],
                'mode': 'best-effort',
                'dry_run': False,
                'file_count': 1,
                'station_count': 1,
                'camera_count': metrics.get('cameras_added', 0) + metrics.get('cameras_updated', 0) + metrics.get('cameras_replaced', 0),
                'success_count': 1,
                'fail_count': 0,
                'rows': [
                    {
                        'county': station_county,
                        'file': filename,
                        'filepath': filepath,
                        'status': 'imported',
                        'station_id': station_id,
                        'station': data['station']['name'],
                        'camera_rows': len(data['cameras']),
                        **metrics,
                    }
                ],
                'aborted': False,
            }
            Path(report_path).write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding='utf-8')

        payload = {
            'message': '导入成功',
            'import_type': IMPORT_TYPE_INVENTORY,
            'station': data['station']['name'],
            'station_id': station_id,
            'project': project['code'],
            **metrics,
        }
        if batch_id is not None:
            payload['batch_id'] = batch_id
            payload['result_url'] = f"/admin/import-batches/{batch_id}"
        return jsonify(payload)
    except Exception as e:
        db.rollback()
        if report_path and os.path.exists(report_path):
            os.remove(report_path)
        logger.exception('upload_excel_scoped failed')
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from parse_excel import ExcelParseError
        status_code = 400 if isinstance(e, ExcelParseError) else 500
        return jsonify({'error': f'解析失败: {str(e)}'}), status_code
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)


def delete_camera_scoped(camera_id):
    db = get_db()
    if not _multi_project_camera_schema_enabled(db):
        return delete_camera(camera_id)

    cursor = db.cursor()
    camera = cursor.execute("SELECT id FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    if not camera:
        return jsonify({'error': '鎽勫儚澶翠笉瀛樺湪'}), 404

    cursor.execute(
        """
        UPDATE cameras
        SET status = 'retired',
            retired_at = COALESCE(retired_at, CURRENT_TIMESTAMP)
        WHERE id = ?
        """,
        (camera_id,),
    )
    db.commit()
    return jsonify({'message': '宸插仠鐢ㄦ憚鍍忓ご'})


def add_camera_scoped():
    db = get_db()
    if not _multi_project_camera_schema_enabled(db):
        return add_camera()

    ensure_camera_recorder_metadata_columns(db)
    data = request.get_json()
    if not data:
        return jsonify({'error': '鏃犳晥鏁版嵁'}), 400

    required = ['station_id', 'ip_address']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'缂哄皯蹇呭～瀛楁: {field}'}), 400

    project, error = _resolve_admin_project(db, data.get('project') or data.get('project_code'))
    if error:
        return error

    cursor = db.cursor()
    station = cursor.execute("SELECT id FROM stations WHERE id = ?", (data['station_id'],)).fetchone()
    if not station:
        return jsonify({'error': '鍙樼數绔欎笉瀛樺湪'}), 404

    slot_id, slot_code = _ensure_camera_slot(
        cursor,
        data['station_id'],
        project['id'],
        project['code'],
        data,
        0,
    )
    active_camera = cursor.execute(
        "SELECT * FROM cameras WHERE slot_id = ? AND status = 'active'",
        (slot_id,),
    ).fetchone()
    if active_camera:
        if _camera_matches(active_camera, data):
            return jsonify({
                'message': '鎽勫儚澶村凡瀛樺湪',
                'camera_id': active_camera['id'],
                'slot_id': slot_id,
                'slot_code': slot_code,
            })
        return jsonify({'error': '璇ユЫ浣嶅凡鏈夊湪鐢ㄨ澶囷紝璇蜂娇鐢ㄦ洿鎹㈡祦绋�'}), 409

    camera_id = _insert_camera_instance(
        cursor,
        slot_id,
        data['station_id'],
        project['id'],
        data,
    )
    db.commit()
    return jsonify({
        'message': '娣诲姞鎴愬姛',
        'camera_id': camera_id,
        'slot_id': slot_id,
        'slot_code': slot_code,
    }), 201
 
 
@admin_bp.route('/cameras/<int:camera_id>/replace', methods=['POST'])
@require_admin
def replace_camera_scoped(camera_id):
    db = get_db()
    if not _multi_project_camera_schema_enabled(db):
        return jsonify({'error': 'multi-project camera replacement is not enabled'}), 400

    ensure_camera_recorder_metadata_columns(db)
    data = request.get_json(silent=True) or {}
    if not data.get('ip_address'):
        return jsonify({'error': 'missing required field: ip_address'}), 400

    cursor = db.cursor()
    old_camera = cursor.execute(
        """
        SELECT c.*, p.code AS project_code
        FROM cameras c
        LEFT JOIN projects p ON p.id = c.project_id
        WHERE c.id = ?
        """,
        (camera_id,),
    ).fetchone()
    if not old_camera:
        return jsonify({'error': 'camera not found'}), 404
    if old_camera['status'] != 'active':
        return jsonify({'error': 'only active cameras can be replaced'}), 409
    if not old_camera['project_id']:
        return jsonify({'error': 'camera is missing project ownership'}), 409

    requested_project = _normalize_text(data.get('project') or data.get('project_code'))
    if requested_project and requested_project != _normalize_text(old_camera['project_code']):
        return jsonify({'error': 'replacement camera must stay in the same project'}), 400

    replacement_camera = {
        'project_camera_code': data.get('project_camera_code'),
        'camera_index': data.get('camera_index', old_camera['camera_index']),
        'area': data.get('area', old_camera['area']),
        'location_desc': data.get('location_desc', old_camera['location_desc']),
        'location': data.get('location') if 'location' in data else old_camera['location_desc'],
        'ip_address': data.get('ip_address'),
        'channel_port': data.get('channel_port', old_camera['channel_port']),
        'channel_number': data.get('channel_number', old_camera['channel_number']),
        'recorder_name': data.get('recorder_name', old_camera['recorder_name']),
        'recorder_ip_address': data.get('recorder_ip_address', old_camera['recorder_ip_address']),
        'recorder_port': data.get('recorder_port', old_camera['recorder_port']),
    }

    try:
        db.execute("BEGIN")
        slot_id = old_camera['slot_id']
        if not slot_id:
            slot_id, _ = _ensure_camera_slot(
                cursor,
                old_camera['station_id'],
                old_camera['project_id'],
                old_camera['project_code'],
                old_camera,
                0,
            )
            cursor.execute("UPDATE cameras SET slot_id = ? WHERE id = ?", (slot_id, camera_id))
            old_camera = cursor.execute(
                """
                SELECT c.*, p.code AS project_code
                FROM cameras c
                LEFT JOIN projects p ON p.id = c.project_id
                WHERE c.id = ?
                """,
                (camera_id,),
            ).fetchone()
        new_camera_id = _replace_active_camera(cursor, old_camera, replacement_camera)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception('replace_camera_scoped failed')
        return jsonify({'error': 'failed to replace camera'}), 500

    return jsonify({
        'message': 'camera replaced',
        'old_camera_id': old_camera['id'],
        'new_camera_id': new_camera_id,
        'slot_id': slot_id,
        'project': old_camera['project_code'],
    }), 201


admin_bp.view_functions['upload_excel'] = upload_excel_scoped
admin_bp.view_functions['delete_camera'] = delete_camera_scoped
admin_bp.view_functions['add_camera'] = add_camera_scoped

@admin_bp.route('/import-batches/<int:batch_id>', methods=['GET'])
@require_admin
def import_batch_result_page(batch_id):
    return render_template('admin_import_batch_result.html', batch_id=batch_id)


@admin_bp.route('/import-batches/<int:batch_id>/summary', methods=['GET'])
@require_admin
def import_batch_result_summary(batch_id):
    try:
        summary = build_import_batch_summary(
            database=current_app.config['DATABASE_PATH'],
            batch_id=batch_id,
        )
    except RuntimeError as exc:
        message = str(exc)
        if 'not found' in message:
            return jsonify({'error': '导入批次不存在'}), 404
        return jsonify({'error': message}), 409
    return jsonify(summary)


@admin_bp.route('/backup', methods=['POST'])
@require_admin
def backup_db():
    """手动备份数据库"""
    db_path = current_app.config['DATABASE_PATH']
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = f'{db_path}.{timestamp}.bak'

    try:
        shutil.copy2(db_path, backup_path)
        return jsonify({'message': '备份成功', 'backup': backup_path})
    except Exception as e:
        return jsonify({'error': f'备份失败: {str(e)}'}), 500


# ============================================================
# 照片索引管理
# ============================================================

@admin_bp.route('/photos/reindex', methods=['POST'])
@require_admin
def photo_reindex():
    """照片索引重建/增量刷新"""
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'incremental')
    project_code = request.args.get('project', '')

    try:
        if mode == 'full':
            stats = run_full_index(current_app.config['DATABASE_PATH'])
        else:
            mode = 'incremental'
            stats = run_incremental_index(current_app.config['DATABASE_PATH'])

        db = get_db()
        summary = get_photo_stats(db, project_code=project_code)

        return jsonify({
            'message': f'照片索引{ "全量重建" if mode == "full" else "增量刷新" }完成',
            'mode': mode,
            'scan_stats': stats,
            'summary': summary,
            'photo_root': current_app.config.get('PHOTO_ROOT_PATH', ''),
            'cron_minutes': current_app.config.get('PHOTO_INDEX_CRON_MINUTES', 15)
        })
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception('photo_reindex failed')
        return jsonify({'error': f'索引失败: {str(e)}'}), 500


@admin_bp.route('/photos/stats', methods=['GET'])
@require_admin
def photo_stats():
    """获取照片索引统计"""
    db = get_db()
    project_code = request.args.get('project', '')
    try:
        stats = get_photo_stats(db, project_code=project_code)
        return jsonify({
            'stats': stats,
            'photo_root': current_app.config.get('PHOTO_ROOT_PATH', ''),
            'cron_minutes': current_app.config.get('PHOTO_INDEX_CRON_MINUTES', 15)
        })
    except sqlite3.OperationalError:
        return jsonify({
            'stats': {'total': 0, 'matched': 0, 'unmatched': 0, 'ignored': 0},
            'photo_root': current_app.config.get('PHOTO_ROOT_PATH', ''),
            'cron_minutes': current_app.config.get('PHOTO_INDEX_CRON_MINUTES', 15)
        })


@admin_bp.route('/photos/unmatched', methods=['GET'])
@require_admin
def photo_unmatched():
    """获取未匹配照片列表"""
    db = get_db()
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    project_code = request.args.get('project', '')
    limit = min(max(limit, 1), 300)
    offset = max(offset, 0)

    rows = list_unmatched(db, limit=limit, offset=offset, project_code=project_code)
    return jsonify({'photos': rows, 'limit': limit, 'offset': offset})


@admin_bp.route('/photos/<int:photo_id>/match', methods=['PUT'])
@require_admin
def photo_manual_match(photo_id):
    """手动关联未匹配照片到变电站，并可写入别名"""
    data = request.get_json(silent=True) or {}
    station_id = data.get('station_id')
    alias = data.get('alias', '')
    project_code = request.args.get('project', '')

    if not station_id:
        return jsonify({'error': '缺少 station_id'}), 400

    db = get_db()
    try:
        manual_match_photo(db, photo_id, int(station_id), alias, project_code=project_code)
        return jsonify({'message': '手动关联成功'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        logger.exception('photo_manual_match failed')
        return jsonify({'error': f'关联失败: {str(e)}'}), 500
