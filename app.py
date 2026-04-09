# app.py — Flask应用入口
import os
import sqlite3
import math
import logging
import json
import re
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, g, current_app, session, redirect, render_template, send_file
from ai_fault_analysis import ensure_ai_runtime_schema, normalize_camera_hint
from config import Config
from admin import (
    admin_bp,
    require_admin,
    upload_excel_scoped,
    delete_camera_scoped,
    add_camera_scoped,
    ensure_camera_recorder_metadata_columns,
)
from admin_fault_types import admin_fault_types_bp
from admin_notifications import admin_notifications_bp
from admin_projects import admin_projects_bp
from admin_review import admin_review_bp
from admin_user_access import admin_user_access_bp
from auth import auth_bp
from import_review_support import normalize_station_name
from notification_runtime import dispatch_notification_event
from photo_indexer import IMAGE_EXTENSIONS
from project_access import (
    can_user_write_project,
    can_user_access_project,
    get_default_project_code,
    get_project_by_code,
    get_visible_projects,
    projects_enabled,
    table_exists,
)
from utils import get_db, close_db, init_app

app = Flask(__name__)
app.config.from_object(Config)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['TEMPLATES_AUTO_RELOAD'] = True
# 本地HTTP调试默认关闭Secure；生产HTTPS可通过环境变量 SESSION_COOKIE_SECURE=true 开启
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1', 'yes')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# CSRF保护说明：
# 此内部工具使用 SameSite=Lax session cookies + admin角色检查作为主要防护。
# API端点使用JSON（非表单提交），不受CSRF影响。
# 如需开启CSRF保护，设置为True并为HTML表单添加csrf_token()。
app.config['WTF_CSRF_ENABLED'] = False

# 注册蓝图
app.register_blueprint(admin_bp)
app.register_blueprint(admin_fault_types_bp)
app.register_blueprint(admin_notifications_bp)
app.register_blueprint(admin_projects_bp)
app.register_blueprint(admin_review_bp)
app.register_blueprint(admin_user_access_bp)
app.register_blueprint(auth_bp)

app.view_functions['admin.upload_excel'] = require_admin(upload_excel_scoped)
app.view_functions['admin.delete_camera'] = require_admin(delete_camera_scoped)
app.view_functions['admin.add_camera'] = require_admin(add_camera_scoped)

# 注册utils的teardown
init_app(app)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('station_monitor')


def _build_map_tile_providers():
    providers = []

    primary = {
        'name': 'primary',
        'url': Config.MAP_TILE_URL,
        'attribution': Config.MAP_TILE_ATTRIBUTION,
        'subdomains': Config.MAP_TILE_SUBDOMAINS,
        'maxZoom': Config.MAP_TILE_MAX_ZOOM,
    }
    if primary['url']:
        providers.append(primary)

    fallback_url = (Config.MAP_TILE_FALLBACK_URL or '').strip()
    if fallback_url and fallback_url != primary['url']:
        providers.append(
            {
                'name': 'fallback',
                'url': fallback_url,
                'attribution': Config.MAP_TILE_FALLBACK_ATTRIBUTION,
                'subdomains': Config.MAP_TILE_FALLBACK_SUBDOMAINS,
                'maxZoom': Config.MAP_TILE_FALLBACK_MAX_ZOOM,
            }
        )

    return providers


@app.context_processor
def inject_map_tile_config():
    return {
        'map_tile_providers': _build_map_tile_providers(),
    }


def _safe_dispatch_fault_notification(db, fault_id: int, event_type: str):
    try:
        return dispatch_notification_event(db, fault_id, event_type, logger=logger)
    except Exception:
        logger.exception(
            "Failed to evaluate notification event: fault=%s event=%s",
            fault_id,
            event_type,
        )
        return None

# ============================================================
# Token认证中间件（决策#1, #2）
# ============================================================

def require_token(f):
    """Token认证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # 从请求头获取token
        auth_header = request.headers.get('Authorization', '')

        if not auth_header.startswith('Bearer '):
            return jsonify({'error': '未提供认证令牌'}), 401

        token = auth_header[7:]  # 去掉 "Bearer " 前缀

        if not token:
            return jsonify({'error': '令牌无效'}), 401

        if token != Config.API_TOKEN:
            return jsonify({'error': '令牌无效或已过期'}), 403

        return f(*args, **kwargs)
    return decorated

# ============================================================
# 通用API响应格式（决策#5）
# ============================================================

def api_error(message, status_code=400):
    """统一错误响应格式"""
    return jsonify({'error': message}), status_code

def api_success(data, status_code=200):
    """统一成功响应格式"""
    return jsonify(data), status_code


def normalize_photo_row(row):
    photo = dict(row)
    photo['is_image'] = (photo.get('ext', '').lower() in IMAGE_EXTENSIONS)
    return photo


def get_photo_root():
    return Path(Config.PHOTO_ROOT_PATH).resolve()


def is_path_under_root(file_path, root_path):
    try:
        file_path.resolve().relative_to(root_path.resolve())
        return True
    except ValueError:
        return False


def get_table_columns(db, table_name):
    """返回表字段集合，用于兼容旧 schema。"""
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def ensure_fault_report_soft_delete_schema(db):
    if not table_exists(db, "fault_reports"):
        return set()
    columns = get_table_columns(db, "fault_reports")
    required_columns = {
        "deleted_at": "TIMESTAMP",
        "deleted_by": "INTEGER",
    }
    missing = False
    for column_name, column_sql in required_columns.items():
        if column_name in columns:
            continue
        db.execute(f"ALTER TABLE fault_reports ADD COLUMN {column_name} {column_sql}")
        missing = True
    if missing:
        db.commit()
        columns = get_table_columns(db, "fault_reports")
    return columns


def build_fault_deleted_clause(fault_report_columns, *, alias="f", mode="active"):
    if "deleted_at" not in fault_report_columns:
        return " AND 1=0" if mode == "only" else ""
    column_name = f"{alias}.deleted_at" if alias else "deleted_at"
    if mode == "only":
        return f" AND {column_name} IS NOT NULL"
    if mode == "all":
        return ""
    return f" AND {column_name} IS NULL"


def get_fault_deleted_mode():
    raw_mode = str(request.args.get("deleted") or "").strip().lower()
    if raw_mode in {"only", "all"} and session.get("role") == "admin":
        return raw_mode
    return "active"


def _looks_like_fault_camera_location(value):
    text = str(value or "").strip()
    if not text or len(text) > 80:
        return False
    strong_markers = ("摄像机", "摄像头", "球机", "枪机", "半球", "云台")
    if any(marker in text for marker in strong_markers):
        return True
    has_channel_marker = "#" in text or bool(re.search(r"通道\s*\d+", text))
    location_markers = ("主变", "场地", "室", "门", "围墙", "东", "南", "西", "北")
    return has_channel_marker and any(marker in text for marker in location_markers)


def derive_fault_camera_location(description):
    text = str(description or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()
    text = re.sub(r"\s*\|\s*地点[:：].*$", "", text).strip()
    text = re.sub(r"\s*\|\s*区县[:：].*$", "", text).strip()
    text = re.sub(r"\s+", " ", text)

    direct_match = re.search(
        r"(?P<location>[\u4e00-\u9fffA-Za-z0-9#\-/_.（）()]+?(?:摄像机|摄像头|球机|枪机|半球|云台|通道\s*\d+))",
        text,
    )
    if direct_match:
        candidate = direct_match.group("location").strip(" |,，;；:：")
        if _looks_like_fault_camera_location(candidate):
            return candidate

    normalized = normalize_camera_hint(text)
    normalized = re.sub(r"(排查|维修|检修|恢复|更换|重启|处理|排障).*$", "", normalized).strip(" |,，;；:：")
    if _looks_like_fault_camera_location(normalized):
        return normalized
    return ""


def enrich_fault_camera_location(payload):
    camera_location = str(payload.get("camera_location") or "").strip()
    if camera_location:
        return payload
    fallback = derive_fault_camera_location(
        payload.get("camera_location_text") or payload.get("description")
    )
    if fallback:
        payload["camera_location"] = fallback
        if not str(payload.get("camera_location_text") or "").strip():
            payload["camera_location_text"] = fallback
    return payload


def project_access_denied():
    return jsonify({'error': '无权访问该项目', 'code': 'PROJECT_ACCESS_DENIED'}), 403


def get_current_user_project_context(db):
    user_id = session.get('user_id')
    role = session.get('role')
    visible_projects = get_visible_projects(
        db,
        user_id=user_id,
        role=role,
        include_inactive=False,
    )
    return {
        'user_id': user_id,
        'role': role,
        'projects': visible_projects,
        'default_project_code': get_default_project_code(visible_projects),
    }


def ensure_project_read_access(db, project_code: str):
    user_id = session.get('user_id')
    role = session.get('role')
    if role is None:
        return True
    return can_user_access_project(
        db,
        user_id=user_id,
        role=role,
        project_code=project_code,
    )


def ensure_project_write_access(db, project_code: str):
    user_id = session.get('user_id')
    role = session.get('role')
    if role is None:
        return True
    return can_user_write_project(
        db,
        user_id=user_id,
        role=role,
        project_code=project_code,
    )


def build_project_scope(db, requested_project_code: str | None = None):
    requested_project_code = (requested_project_code or '').strip()
    if not projects_enabled(db):
        return {
            'enabled': False,
            'project_ids': None,
            'requested_project': None,
            'projects': [],
        }, None

    role = session.get('role')
    user_id = session.get('user_id')
    include_inactive = bool(requested_project_code and requested_project_code != 'all')

    if role is None:
        visible_projects = get_visible_projects(
            db,
            user_id=None,
            role='admin',
            include_inactive=include_inactive,
        )
        for project in visible_projects:
            project['can_write'] = False
    else:
        visible_projects = get_visible_projects(
            db,
            user_id=user_id,
            role=role,
            include_inactive=include_inactive,
        )

    if requested_project_code and requested_project_code != 'all':
        requested_project = get_project_by_code(
            db,
            requested_project_code,
            include_inactive=True,
        )
        if not requested_project:
            return None, api_error('项目不存在', 404)
        if role is None:
            if not requested_project.get('is_active'):
                return None, api_error('项目不存在', 404)
        elif not any(project['code'] == requested_project_code for project in visible_projects):
            return None, project_access_denied()
        return {
            'enabled': True,
            'project_ids': [requested_project['id']],
            'requested_project': requested_project,
            'projects': visible_projects,
        }, None

    active_projects = [project for project in visible_projects if project.get('is_active')]
    return {
        'enabled': True,
        'project_ids': [project['id'] for project in active_projects],
        'requested_project': None,
        'projects': active_projects,
    }, None


def extend_params(params, extra):
    if extra:
        params.extend(extra)
    return params


FAULT_STATUS_SORT_SQL = """
CASE COALESCE(f.status, 'open')
    WHEN 'open' THEN 0
    WHEN 'handling' THEN 1
    WHEN 'closed' THEN 2
    ELSE 3
END
"""


def build_project_in_clause(alias: str, project_ids: list[int] | None):
    if project_ids is None:
        return "", []
    if not project_ids:
        return " AND 1 = 0", []
    placeholders = ", ".join(["?"] * len(project_ids))
    return f" AND {alias}.project_id IN ({placeholders})", list(project_ids)


def _column_expr(columns, alias: str, name: str, fallback: str = "NULL"):
    if name in columns:
        return f"{alias}.{name}"
    return fallback


def _count_with_alias(db, table_name: str, alias: str, project_ids: list[int] | None, active_only: bool = False):
    columns = get_table_columns(db, table_name)
    query = f"SELECT COUNT(*) as count FROM {table_name} {alias} WHERE 1=1"
    params = []
    if 'project_id' in columns:
        project_sql, project_params = build_project_in_clause(alias, project_ids)
        query += project_sql
        params.extend(project_params)
    if active_only and 'status' in columns:
        query += f" AND {alias}.status = 'active'"
    return db.execute(query, params).fetchone()['count']


def _build_statistics_payload(db, year: int | None, requested_project_code: str | None):
    project_scope, error = build_project_scope(db, requested_project_code)
    if error:
        return None, error

    fault_report_columns = get_table_columns(db, "fault_reports")
    camera_columns = get_table_columns(db, "cameras")
    project_fault_type_columns = get_table_columns(db, "project_fault_types") if table_exists(db, "project_fault_types") else set()
    has_project_fault_types = {"version_id", "type_code", "semantic_group", "type_label"}.issubset(project_fault_type_columns)

    fault_scope_sql = ""
    fault_scope_params = []
    if project_scope['enabled'] and 'project_id' in fault_report_columns:
        fault_scope_sql, fault_scope_params = build_project_in_clause("f", project_scope['project_ids'])

    camera_scope_sql = ""
    camera_scope_params = []
    if project_scope['enabled'] and 'project_id' in camera_columns:
        camera_scope_sql, camera_scope_params = build_project_in_clause("c", project_scope['project_ids'])

    station_count = db.execute("SELECT COUNT(*) as count FROM stations").fetchone()['count']
    if project_scope['enabled'] and (('project_id' in fault_report_columns) or ('project_id' in camera_columns)):
        station_sources = []
        station_params = []
        if 'project_id' in camera_columns:
            active_sql = " AND c.status = 'active'" if 'status' in camera_columns else ""
            station_sources.append(
                f"SELECT c.station_id FROM cameras c WHERE 1=1{camera_scope_sql}{active_sql}"
            )
            station_params.extend(camera_scope_params)
        if 'project_id' in fault_report_columns:
            station_sources.append(
                f"SELECT f.station_id FROM fault_reports f WHERE 1=1{fault_scope_sql}"
            )
            station_params.extend(fault_scope_params)
        if station_sources:
            station_count = db.execute(
                "SELECT COUNT(DISTINCT station_id) as count FROM ("
                + " UNION ALL ".join(station_sources)
                + ") scoped_stations",
                station_params,
            ).fetchone()['count']

    camera_count = _count_with_alias(
        db,
        "cameras",
        "c",
        project_scope['project_ids'] if project_scope['enabled'] else None,
        active_only=True,
    )

    selected_fault_where = [f"1=1{fault_scope_sql}"]
    selected_fault_params = list(fault_scope_params)
    if year:
        selected_fault_where.append("strftime('%Y', f.created_at) = ?")
        selected_fault_params.append(str(year))
    selected_fault_where_sql = " AND ".join(selected_fault_where)

    fault_count = db.execute(
        f"SELECT COUNT(*) as count FROM fault_reports f WHERE {selected_fault_where_sql}",
        selected_fault_params,
    ).fetchone()['count']

    fault_this_month = db.execute(
        f"""
        SELECT COUNT(*) as count
        FROM fault_reports f
        WHERE 1=1{fault_scope_sql}
          AND strftime('%Y-%m', f.created_at) = strftime('%Y-%m', 'now')
        """,
        fault_scope_params,
    ).fetchone()['count']
    fault_this_year = db.execute(
        f"""
        SELECT COUNT(*) as count
        FROM fault_reports f
        WHERE 1=1{fault_scope_sql}
          AND strftime('%Y', f.created_at) = strftime('%Y', 'now')
        """,
        fault_scope_params,
    ).fetchone()['count']

    target_year = year or datetime.now().year
    monthly_data = {f"{target_year}-{month:02d}": 0 for month in range(1, 13)}
    monthly_rows = db.execute(
        f"""
        SELECT strftime('%Y-%m', f.created_at) as month, COUNT(*) as cnt
        FROM fault_reports f
        WHERE 1=1{fault_scope_sql}
          AND strftime('%Y', f.created_at) = ?
        GROUP BY month
        """,
        fault_scope_params + [str(target_year)],
    ).fetchall()
    for row in monthly_rows:
        monthly_data[row['month']] = row['cnt']

    available_year_rows = db.execute(
        f"""
        SELECT DISTINCT strftime('%Y', f.created_at) as year
        FROM fault_reports f
        WHERE 1=1{fault_scope_sql}
          AND f.created_at IS NOT NULL
        ORDER BY year DESC
        """,
        fault_scope_params,
    ).fetchall()
    available_years = [row['year'] for row in available_year_rows if row['year']]

    semantic_group_expr = "NULL"
    semantic_label_expr = "NULL"
    fault_type_join = ""
    if has_project_fault_types and {'fault_type_version_id', 'fault_type_code'}.issubset(fault_report_columns):
        fault_type_join = (
            " LEFT JOIN project_fault_types pft"
            " ON pft.version_id = f.fault_type_version_id"
            " AND pft.type_code = f.fault_type_code"
        )
        semantic_group_expr = "pft.semantic_group"
        semantic_label_expr = "pft.type_label"

    fault_type_code_expr = _column_expr(fault_report_columns, "f", "fault_type_code")
    fault_type_snapshot_expr = _column_expr(fault_report_columns, "f", "fault_type_label_snapshot")
    legacy_fault_type_expr = _column_expr(fault_report_columns, "f", "fault_type")

    semantic_key_expr = (
        f"COALESCE({semantic_group_expr}, NULLIF(TRIM({fault_type_code_expr}), ''), "
        f"NULLIF(TRIM({fault_type_snapshot_expr}), ''), NULLIF(TRIM({legacy_fault_type_expr}), ''), 'UNCLASSIFIED')"
    )
    semantic_label_select_expr = (
        f"COALESCE(NULLIF(TRIM({fault_type_snapshot_expr}), ''), NULLIF(TRIM({semantic_label_expr}), ''), "
        f"NULLIF(TRIM({legacy_fault_type_expr}), ''), NULLIF(TRIM({fault_type_code_expr}), ''), '未分类')"
    )

    fault_type_rows = db.execute(
        f"""
        SELECT
            {semantic_key_expr} AS semantic_group,
            {semantic_label_select_expr} AS fault_label,
            COUNT(*) AS count
        FROM fault_reports f
        {fault_type_join}
        WHERE {selected_fault_where_sql}
        GROUP BY semantic_group, fault_label
        ORDER BY count DESC, fault_label ASC
        """,
        selected_fault_params,
    ).fetchall()

    county_rows = db.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(s.county), ''), '未知') AS county, COUNT(*) AS count
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        WHERE {selected_fault_where_sql}
        GROUP BY county
        ORDER BY count DESC, county ASC
        """,
        selected_fault_params,
    ).fetchall()

    voltage_rows = db.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(s.voltage_level), ''), '其他') AS voltage_level, COUNT(*) AS count
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        WHERE {selected_fault_where_sql}
        GROUP BY voltage_level
        ORDER BY count DESC, voltage_level ASC
        """,
        selected_fault_params,
    ).fetchall()

    camera_location_expr = "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(c.area), ''), '未命名设备')"
    camera_ranking_rows = db.execute(
        f"""
        SELECT
            f.camera_id,
            {camera_location_expr} AS camera_location,
            s.name AS station_name,
            COUNT(*) AS fault_count
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        WHERE {selected_fault_where_sql}
          AND f.camera_id IS NOT NULL
        GROUP BY f.camera_id, camera_location, s.name
        ORDER BY fault_count DESC, f.camera_id ASC
        LIMIT 5
        """,
        selected_fault_params,
    ).fetchall()

    status_expr = _column_expr(fault_report_columns, "f", "status", "'open'")
    handling_started_expr = _column_expr(fault_report_columns, "f", "handling_started_at")
    closed_at_expr = _column_expr(fault_report_columns, "f", "closed_at")

    kpi_row = db.execute(
        f"""
        SELECT
            SUM(CASE WHEN {status_expr} = 'open' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN {status_expr} = 'handling' THEN 1 ELSE 0 END) AS handling_count,
            SUM(CASE WHEN {status_expr} = 'closed' THEN 1 ELSE 0 END) AS closed_count,
            COUNT(CASE WHEN {handling_started_expr} IS NOT NULL THEN 1 END) AS response_sample_count,
            COUNT(CASE WHEN {closed_at_expr} IS NOT NULL THEN 1 END) AS close_sample_count,
            AVG(CASE
                WHEN {handling_started_expr} IS NOT NULL
                THEN (julianday({handling_started_expr}) - julianday(f.created_at)) * 86400.0
            END) AS avg_response_seconds,
            AVG(CASE
                WHEN {closed_at_expr} IS NOT NULL
                THEN (julianday({closed_at_expr}) - julianday(f.created_at)) * 86400.0
            END) AS avg_close_seconds
        FROM fault_reports f
        WHERE {selected_fault_where_sql}
        """,
        selected_fault_params,
    ).fetchone()

    requested_project = project_scope.get('requested_project')

    return {
        'stations': station_count,
        'cameras': camera_count,
        'faults': fault_count,
        'faults_this_month': fault_this_month,
        'faults_this_year': fault_this_year,
        'fault_rate': round((fault_count / camera_count) * 100, 2) if camera_count else 0,
        'target_year': target_year,
        'selected_year': year,
        'available_years': available_years,
        'project_scope': {
            'requested_project': requested_project['code'] if requested_project else 'all',
            'visible_projects': [project['code'] for project in project_scope['projects']],
        },
        'monthly_trend': [
            {'month': month, 'count': count}
            for month, count in sorted(monthly_data.items())
        ],
        'fault_type_distribution': [dict(row) for row in fault_type_rows],
        'county_distribution': [dict(row) for row in county_rows],
        'voltage_distribution': [dict(row) for row in voltage_rows],
        'camera_ranking': [dict(row) for row in camera_ranking_rows],
        'kpi': {
            'open_count': kpi_row['open_count'] or 0,
            'handling_count': kpi_row['handling_count'] or 0,
            'closed_count': kpi_row['closed_count'] or 0,
            'response_sample_count': kpi_row['response_sample_count'] or 0,
            'close_sample_count': kpi_row['close_sample_count'] or 0,
            'avg_response_seconds': round(kpi_row['avg_response_seconds'], 2) if kpi_row['avg_response_seconds'] is not None else None,
            'avg_close_seconds': round(kpi_row['avg_close_seconds'], 2) if kpi_row['avg_close_seconds'] is not None else None,
        },
    }, None


def _build_statistics_detail_rows(db, project_scope: dict, year: int | None):
    fault_report_columns = get_table_columns(db, "fault_reports")
    has_projects = table_exists(db, "projects")
    has_project_fault_types = table_exists(db, "project_fault_types")
    has_users = table_exists(db, "users")

    fault_scope_sql = ""
    fault_scope_params = []
    if project_scope['enabled'] and 'project_id' in fault_report_columns:
        fault_scope_sql, fault_scope_params = build_project_in_clause("f", project_scope['project_ids'])

    detail_where = [f"1=1{fault_scope_sql}"]
    detail_params = list(fault_scope_params)
    if year:
        detail_where.append("strftime('%Y', f.created_at) = ?")
        detail_params.append(str(year))
    detail_where_sql = " AND ".join(detail_where)

    project_join = " LEFT JOIN projects p ON p.id = f.project_id" if has_projects and 'project_id' in fault_report_columns else ""
    fault_type_join = ""
    if has_project_fault_types and {'fault_type_version_id', 'fault_type_code'}.issubset(fault_report_columns):
        fault_type_join = (
            " LEFT JOIN project_fault_types pft"
            " ON pft.version_id = f.fault_type_version_id"
            " AND pft.type_code = f.fault_type_code"
        )
    assigned_user_join = " LEFT JOIN users u ON u.id = f.assigned_to" if has_users and 'assigned_to' in fault_report_columns else ""

    fault_type_code_expr = _column_expr(fault_report_columns, "f", "fault_type_code")
    fault_type_snapshot_expr = _column_expr(fault_report_columns, "f", "fault_type_label_snapshot")
    legacy_fault_type_expr = _column_expr(fault_report_columns, "f", "fault_type")

    semantic_group_expr = "pft.semantic_group" if fault_type_join else "NULL"
    semantic_label_expr = "pft.type_label" if fault_type_join else "NULL"
    fault_label_expr = (
        f"COALESCE(NULLIF(TRIM({fault_type_snapshot_expr}), ''), NULLIF(TRIM({semantic_label_expr}), ''), "
        f"NULLIF(TRIM({legacy_fault_type_expr}), ''), NULLIF(TRIM({fault_type_code_expr}), ''), '未分类')"
    )
    semantic_key_expr = (
        f"COALESCE({semantic_group_expr}, NULLIF(TRIM({fault_type_code_expr}), ''), "
        f"NULLIF(TRIM({fault_type_snapshot_expr}), ''), NULLIF(TRIM({legacy_fault_type_expr}), ''), 'UNCLASSIFIED')"
    )

    select_fields = [
        "f.id",
        "s.name AS station_name",
        "s.voltage_level",
        "s.county",
        "COALESCE(p.code, '') AS project_code" if project_join else "'' AS project_code",
        "COALESCE(p.name, '') AS project_name" if project_join else "'' AS project_name",
        "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(c.area), ''), '') AS camera_location",
        "COALESCE(NULLIF(TRIM(c.area), ''), '') AS camera_area",
        _column_expr(fault_report_columns, "f", "camera_slot_id") + " AS camera_slot_id",
        _column_expr(fault_report_columns, "f", "project_device_code") + " AS project_device_code",
        f"{semantic_key_expr} AS semantic_group",
        f"{fault_type_code_expr} AS fault_type_code",
        f"{fault_label_expr} AS fault_label",
        _column_expr(fault_report_columns, "f", "description") + " AS description",
        _column_expr(fault_report_columns, "f", "status", "'open'") + " AS status",
        _column_expr(fault_report_columns, "f", "reporter_name") + " AS reporter_name",
        _column_expr(fault_report_columns, "f", "reporter_contact") + " AS reporter_contact",
        _column_expr(fault_report_columns, "f", "created_at") + " AS created_at",
        _column_expr(fault_report_columns, "f", "handling_started_at") + " AS handling_started_at",
        _column_expr(fault_report_columns, "f", "closed_at") + " AS closed_at",
        _column_expr(fault_report_columns, "f", "source_type", "''") + " AS source_type",
        _column_expr(fault_report_columns, "f", "source_batch_id") + " AS source_batch_id",
        _column_expr(fault_report_columns, "f", "source_record_key") + " AS source_record_key",
        _column_expr(fault_report_columns, "f", "source_time_raw") + " AS source_time_raw",
        _column_expr(fault_report_columns, "f", "source_timezone") + " AS source_timezone",
        _column_expr(fault_report_columns, "f", "system_type") + " AS system_type",
        _column_expr(fault_report_columns, "f", "handler_name") + " AS handler_name",
        _column_expr(fault_report_columns, "f", "handler_note") + " AS handler_note",
        _column_expr(fault_report_columns, "f", "assigned_to") + " AS assigned_to",
        "COALESCE(u.username, '') AS assigned_to_username" if assigned_user_join else "'' AS assigned_to_username",
        _column_expr(fault_report_columns, "f", "tags_json") + " AS tags_json",
    ]

    rows = db.execute(
        f"""
        SELECT {', '.join(select_fields)}
        FROM fault_reports f
        JOIN stations s ON s.id = f.station_id
        LEFT JOIN cameras c ON c.id = f.camera_id
        {project_join}
        {fault_type_join}
        {assigned_user_join}
        WHERE {detail_where_sql}
        ORDER BY f.created_at DESC, f.id DESC
        """,
        detail_params,
    ).fetchall()
    return [dict(row) for row in rows]


def parse_tags_json(raw_value):
    if not raw_value:
        return []
    if isinstance(raw_value, list):
        candidates = raw_value
    else:
        try:
            parsed = json.loads(raw_value)
        except (TypeError, ValueError):
            parsed = str(raw_value).split(",")
        candidates = parsed if isinstance(parsed, list) else str(raw_value).split(",")

    seen = set()
    tags = []
    for item in candidates:
        tag = str(item or "").strip()
        if not tag:
            continue
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        tags.append(tag)
    return tags


def normalize_tags_payload(raw_value):
    if isinstance(raw_value, str):
        return parse_tags_json([part.strip() for part in raw_value.split(",")])
    return parse_tags_json(raw_value)


def build_station_recorders_payload(db, station_id, project_scope):
    if not table_exists(db, "station_recorders"):
        return []

    recorder_columns = get_table_columns(db, "station_recorders")
    if "project_id" not in recorder_columns:
        return []

    query = """
        SELECT
            sr.id,
            sr.station_id,
            sr.project_id,
            sr.recorder_name,
            sr.ip_address,
            sr.port,
            sr.description,
            sr.source_type,
            sr.source_key,
            sr.status,
            p.code AS project_code,
            p.name AS project_name,
            p.short_name AS project_short_name,
            p.color AS project_color,
            p.sort_order AS project_sort_order
        FROM station_recorders sr
        LEFT JOIN projects p ON p.id = sr.project_id
        WHERE sr.station_id = ?
          AND sr.status = 'active'
    """
    params = [station_id]
    if project_scope['enabled']:
        project_sql, project_params = build_project_in_clause("sr", project_scope['project_ids'])
        query += project_sql
        params.extend(project_params)
    query += """
        ORDER BY
            COALESCE(p.sort_order, 0),
            COALESCE(sr.recorder_name, ''),
            COALESCE(sr.ip_address, ''),
            COALESCE(sr.port, 0),
            sr.id
    """
    return [dict(row) for row in db.execute(query, params).fetchall()]


def build_station_slots_payload(db, station_id, project_scope):
    ensure_camera_recorder_metadata_columns(db)
    camera_columns = get_table_columns(db, "cameras")
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    station_recorders = build_station_recorders_payload(db, station_id, project_scope)

    if not (
        table_exists(db, "camera_slots")
        and 'slot_id' in camera_columns
        and 'project_id' in camera_columns
    ):
        return []

    slot_query = """
        SELECT
            s.id AS slot_id,
            s.slot_code,
            s.station_id,
            s.project_id,
            s.location_desc,
            s.area,
            s.channel_number,
            p.code AS project_code,
            p.name AS project_name,
            p.short_name AS project_short_name,
            p.color AS project_color,
            p.sort_order AS project_sort_order,
            c.id AS current_camera_id,
            c.project_camera_code AS current_project_camera_code,
            c.camera_index AS current_camera_index,
            c.ip_address AS current_ip_address,
            c.channel_port AS current_channel_port,
            c.channel_number AS current_channel_number,
            c.recorder_name AS current_recorder_name,
            c.recorder_ip_address AS current_recorder_ip_address,
            c.recorder_port AS current_recorder_port,
            c.area AS current_area,
            c.location_desc AS current_location_desc,
            c.created_at AS current_created_at
        FROM camera_slots s
        LEFT JOIN projects p ON p.id = s.project_id
        LEFT JOIN cameras c
            ON c.slot_id = s.id
           AND c.status = 'active'
        WHERE s.station_id = ?
    """
    slot_params = [station_id]
    if project_scope['enabled']:
        project_sql, project_params = build_project_in_clause("s", project_scope['project_ids'])
        slot_query += project_sql
        slot_params.extend(project_params)
    slot_query += """
        ORDER BY
            COALESCE(p.sort_order, 0),
            COALESCE(s.area, ''),
            COALESCE(s.location_desc, ''),
            COALESCE(s.channel_number, 0),
            s.id
    """
    slot_rows = db.execute(slot_query, slot_params).fetchall()
    if not slot_rows:
        return []

    slot_ids = [row['slot_id'] for row in slot_rows]
    placeholders = ", ".join(["?"] * len(slot_ids))

    history_query = f"""
        SELECT
            c.id,
            c.slot_id,
            c.project_id,
            c.project_camera_code,
            c.camera_index,
            c.area,
            c.location_desc,
            c.ip_address,
            c.channel_port,
            c.channel_number,
            c.recorder_name,
            c.recorder_ip_address,
            c.recorder_port,
            c.status,
            c.replaced_by_camera_id,
            c.retired_at,
            c.created_at
        FROM cameras c
        WHERE c.slot_id IN ({placeholders})
          AND c.status != 'active'
        ORDER BY COALESCE(c.retired_at, c.created_at) DESC, c.id DESC
    """
    history_rows = db.execute(history_query, slot_ids).fetchall()
    history_map = {slot_id: [] for slot_id in slot_ids}
    for row in history_rows:
        history_map.setdefault(row['slot_id'], []).append(dict(row))

    recorders_by_project = {}
    recorder_by_exact = {}
    recorder_by_ip = {}
    recorder_by_name = {}
    for recorder in station_recorders:
        project_id = recorder.get('project_id')
        recorders_by_project.setdefault(project_id, []).append(recorder)
        recorder_name_key = normalize_station_name(recorder.get('recorder_name'))
        if recorder_name_key:
            recorder_by_name.setdefault((project_id, recorder_name_key), []).append(recorder)

        ip_key = str(recorder.get('ip_address') or '').strip()
        if not ip_key:
            continue

        port_value = recorder.get('port')
        if port_value is not None:
            recorder_by_exact[(project_id, ip_key, port_value)] = recorder

        ip_bucket_key = (project_id, ip_key)
        recorder_by_ip.setdefault(ip_bucket_key, []).append(recorder)

    def match_slot_recorder(project_id, current_camera, history_cameras):
        candidates = []
        if current_camera:
            candidates.append(current_camera)
        candidates.extend(history_cameras or [])

        for camera in candidates:
            recorder_name_key = normalize_station_name(camera.get('recorder_name'))
            if recorder_name_key:
                named_recorders = recorder_by_name.get((project_id, recorder_name_key), [])
                if len(named_recorders) == 1:
                    return named_recorders[0]

                recorder_ip_key = str(camera.get('recorder_ip_address') or '').strip()
                recorder_port = camera.get('recorder_port')
                for recorder in named_recorders:
                    if recorder_ip_key and recorder_ip_key != str(recorder.get('ip_address') or '').strip():
                        continue
                    if recorder_port is not None and recorder_port != recorder.get('port'):
                        continue
                    return recorder

            ip_key = str(camera.get('ip_address') or '').strip()
            if not ip_key:
                continue

            port_value = camera.get('channel_port')
            if port_value is not None:
                recorder = recorder_by_exact.get((project_id, ip_key, port_value))
                if recorder:
                    return recorder

            recorder_bucket = recorder_by_ip.get((project_id, ip_key), [])
            if len(recorder_bucket) == 1:
                return recorder_bucket[0]

        project_recorders = recorders_by_project.get(project_id, [])
        if len(project_recorders) == 1:
            return project_recorders[0]
        return None

    fault_count_map = {slot_id: 0 for slot_id in slot_ids}
    recent_faults_map = {slot_id: [] for slot_id in slot_ids}
    if 'camera_slot_id' in fault_report_columns:
        fault_query = f"""
            SELECT
                f.camera_slot_id AS slot_id,
                COUNT(*) AS fault_count,
                MAX(f.created_at) AS last_fault_at
            FROM fault_reports f
            WHERE f.station_id = ?
              AND f.camera_slot_id IN ({placeholders})
        """
        fault_params = [station_id, *slot_ids]
        if project_scope['enabled'] and 'project_id' in fault_report_columns:
            project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
            fault_query += project_sql
            fault_params.extend(project_params)
        fault_query += " GROUP BY f.camera_slot_id"
        for row in db.execute(fault_query, fault_params).fetchall():
            fault_count_map[row['slot_id']] = row['fault_count']

        recent_fault_query = f"""
            SELECT
                f.id,
                f.camera_slot_id AS slot_id,
                COALESCE(f.fault_type_label_snapshot, f.fault_type) AS fault_label,
                COALESCE(f.description, '') AS description,
                f.status,
                f.created_at,
                f.closed_at,
                COALESCE(f.handler_note, '') AS handler_note
            FROM fault_reports f
            WHERE f.station_id = ?
              AND f.camera_slot_id IN ({placeholders})
        """
        recent_fault_params = [station_id, *slot_ids]
        if project_scope['enabled'] and 'project_id' in fault_report_columns:
            project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
            recent_fault_query += project_sql
            recent_fault_params.extend(project_params)
        recent_fault_query += " ORDER BY f.created_at DESC, f.id DESC"
        for row in db.execute(recent_fault_query, recent_fault_params).fetchall():
            bucket = recent_faults_map.setdefault(row['slot_id'], [])
            if len(bucket) < 3:
                bucket.append(dict(row))

    slots = []
    for row in slot_rows:
        row_dict = dict(row)
        current_camera = None
        if row['current_camera_id']:
            current_camera = {
                'id': row['current_camera_id'],
                'project_camera_code': row['current_project_camera_code'],
                'camera_index': row['current_camera_index'],
                'ip_address': row['current_ip_address'],
                'channel_port': row['current_channel_port'],
                'channel_number': row['current_channel_number'],
                'recorder_name': row['current_recorder_name'],
                'recorder_ip_address': row['current_recorder_ip_address'],
                'recorder_port': row['current_recorder_port'],
                'area': row['current_area'],
                'location_desc': row['current_location_desc'],
                'created_at': row['current_created_at'],
                'status': 'active',
            }

        matched_recorder = match_slot_recorder(
            row['project_id'],
            current_camera,
            history_map.get(row['slot_id'], []),
        )

        slots.append({
            'slot_id': row['slot_id'],
            'slot_code': row['slot_code'],
            'station_id': row['station_id'],
            'project_id': row['project_id'],
            'project_code': row['project_code'],
            'project_name': row['project_name'],
            'project_short_name': row['project_short_name'],
            'project_color': row['project_color'],
            'project_sort_order': row['project_sort_order'],
            'location_desc': row['location_desc'],
            'area': row['area'],
            'channel_number': row['channel_number'],
            'current_camera': current_camera,
            'history_cameras': history_map.get(row['slot_id'], []),
            'history_camera_count': len(history_map.get(row['slot_id'], [])),
            'fault_count': fault_count_map.get(row['slot_id'], 0),
            'recent_faults': recent_faults_map.get(row['slot_id'], []),
            'recorder': matched_recorder,
        })
    return slots

# ============================================================
# API: 项目列表
# ============================================================

@app.route('/api/projects', methods=['GET'])
def get_projects_api():
    """获取项目列表与当前用户可见范围"""
    db = get_db()
    context = get_current_user_project_context(db)
    if context['user_id']:
        projects = context['projects']
    else:
        projects = get_visible_projects(db, user_id=None, role='admin', include_inactive=False)
        for project in projects:
            project['can_write'] = False

    return api_success({
        'projects': projects,
        'default_project_code': get_default_project_code(projects),
        'multi_project_enabled': projects_enabled(db),
    })


# ============================================================
# API: 项目故障类型
# ============================================================

@app.route('/api/projects/<string:project_code>/fault-types', methods=['GET'])
def get_project_fault_types(project_code):
    """获取项目当前已发布故障类型"""
    db = get_db()
    project = get_project_by_code(db, project_code, include_inactive=True)
    if not project:
        return api_error('项目不存在', 404)
    if not ensure_project_read_access(db, project_code):
        return project_access_denied()

    if not table_exists(db, 'project_fault_types') or not table_exists(db, 'project_fault_type_versions'):
        return api_success({
            'project': project,
            'fault_types': [],
        })

    if not project.get('fault_type_version_id'):
        return api_success({
            'project': project,
            'fault_types': [],
        })

    rows = db.execute(
        """
        SELECT type_code, type_label, semantic_group, sort_order, is_active
        FROM project_fault_types
        WHERE version_id = ?
        ORDER BY sort_order, id
        """,
        (project['fault_type_version_id'],),
    ).fetchall()

    return api_success({
        'project': project,
        'fault_types': [dict(row) for row in rows],
    })


# ============================================================
# API: 统计概览
# ============================================================

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """首页统计数据"""
    db = get_db()
    year = request.args.get('year', type=int)

    # 变电站数量（不受年份影响）
    station_count = db.execute("SELECT COUNT(*) as count FROM stations").fetchone()['count']

    # 摄像头数量（不受年份影响）
    camera_count = db.execute("SELECT COUNT(*) as count FROM cameras").fetchone()['count']

    # 故障报修数量（支持年份筛选）
    if year:
        fault_count = db.execute(
            "SELECT COUNT(*) as count FROM fault_reports WHERE strftime('%Y', created_at) = ?",
            (str(year),)
        ).fetchone()['count']
    else:
        fault_count = db.execute("SELECT COUNT(*) as count FROM fault_reports").fetchone()['count']

    # 本月故障数量
    fault_this_month = db.execute("""
        SELECT COUNT(*) as count FROM fault_reports
        WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
    """).fetchone()['count']

    # 本年故障数量
    fault_this_year = db.execute("""
        SELECT COUNT(*) as count FROM fault_reports
        WHERE strftime('%Y', created_at) = strftime('%Y', 'now')
    """).fetchone()['count']

    return api_success({
        'stations': station_count,
        'cameras': camera_count,
        'faults': fault_count,
        'faults_this_month': fault_this_month,
        'faults_this_year': fault_this_year
    })

# ============================================================
# API: 统计导出Excel
# ============================================================

@app.route('/api/statistics/export', methods=['GET'])
def export_statistics():
    """导出统计报表为Excel"""
    year = request.args.get('year', type=int)
    db = get_db()

    # 概览数据
    station_count = db.execute("SELECT COUNT(*) as count FROM stations").fetchone()['count']
    camera_count = db.execute("SELECT COUNT(*) as count FROM cameras").fetchone()['count']

    if year:
        fault_count = db.execute(
            "SELECT COUNT(*) as count FROM fault_reports WHERE strftime('%Y', created_at) = ?",
            (str(year),)
        ).fetchone()['count']
    else:
        fault_count = db.execute("SELECT COUNT(*) as count FROM fault_reports").fetchone()['count']

    # 月度故障趋势
    monthly_data = {}
    if year:
        for m in range(1, 13):
            key = f"{year}-{m:02d}"
            monthly_data[key] = 0
        rows = db.execute("""
            SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt
            FROM fault_reports WHERE strftime('%Y', created_at) = ?
            GROUP BY month
        """, (str(year),)).fetchall()
        for row in rows:
            monthly_data[row['month']] = row['cnt']
    else:
        now_year = datetime.now().year
        for m in range(1, 13):
            key = f"{now_year}-{m:02d}"
            monthly_data[key] = 0
        rows = db.execute("""
            SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt
            FROM fault_reports WHERE strftime('%Y', created_at) = ?
            GROUP BY month
        """, (str(now_year),)).fetchall()
        for row in rows:
            monthly_data[row['month']] = row['cnt']

    # 故障记录明细
    query = """
        SELECT f.id, s.name as station_name, s.voltage_level, s.county,
               c.area as camera_area, c.location_desc as camera_location,
               f.fault_type, f.description, f.status,
               f.reporter_name, f.reporter_contact,
               f.created_at, f.closed_at, f.handler_name, f.handler_note
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
    """
    params = []
    if year:
        query += " WHERE strftime('%Y', f.created_at) = ?"
        params.append(str(year))
    query += " ORDER BY f.created_at DESC"
    faults = db.execute(query, params).fetchall()

    # 县区统计
    county_data = {}
    for f in faults:
        county = f['county'] or '未知'
        county_data[county] = county_data.get(county, 0) + 1

    # 电压等级统计
    voltage_data = {}
    for f in faults:
        vl = f['voltage_level'] or '其他'
        voltage_data[vl] = voltage_data.get(vl, 0) + 1

    # 生成Excel
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from io import BytesIO

        wb = Workbook()

        # 样式
        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='2f67f6', end_color='2f67f6', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # Sheet 1: 概览
        ws1 = wb.active
        ws1.title = '概览'
        overview = [
            ('变电站总数', station_count),
            ('摄像头总数', camera_count),
            ('故障报修总数', fault_count),
            ('故障率', f"{(fault_count/camera_count*100):.2f}%" if camera_count > 0 else '0%'),
        ]
        ws1.append(['指标', '数值'])
        for k, v in overview:
            ws1.append([k, v])
        ws1.column_dimensions['A'].width = 20
        ws1.column_dimensions['B'].width = 15

        # Sheet 2: 月度趋势
        ws2 = wb.create_sheet('月度趋势')
        ws2.append(['月份', '故障数量'])
        for month, cnt in sorted(monthly_data.items()):
            ws2.append([month, cnt])
        ws2.column_dimensions['A'].width = 15
        ws2.column_dimensions['B'].width = 15

        # Sheet 3: 故障明细
        ws3 = wb.create_sheet('故障明细')
        headers = ['ID', '变电站', '电压等级', '县区', '摄像头位置', '故障类型',
                   '描述', '状态', '报修人', '联系方式', '报修时间', '关闭时间', '处理人', '处理备注']
        ws3.append(headers)
        for f in faults:
            ws3.append([
                f['id'], f['station_name'] or '', f['voltage_level'] or '', f['county'] or '',
                f['camera_location'] or f['camera_area'] or '', f['fault_type'] or '',
                f['description'] or '', f['status'] or '', f['reporter_name'] or '',
                f['reporter_contact'] or '', f['created_at'] or '', f['closed_at'] or '',
                f['handler_name'] or '', f['handler_note'] or ''
            ])
        for col in ws3.columns:
            ws3.column_dimensions[col[0].column_letter].width = 15

        # Sheet 4: 县区统计
        ws4 = wb.create_sheet('县区统计')
        ws4.append(['县区', '故障数量'])
        for county, cnt in sorted(county_data.items(), key=lambda x: -x[1]):
            ws4.append([county, cnt])
        ws4.column_dimensions['A'].width = 15
        ws4.column_dimensions['B'].width = 15

        # Sheet 5: 电压等级统计
        ws5 = wb.create_sheet('电压等级统计')
        ws5.append(['电压等级', '故障数量'])
        for vl, cnt in sorted(voltage_data.items(), key=lambda x: -x[1]):
            ws5.append([vl, cnt])
        ws5.column_dimensions['A'].width = 15
        ws5.column_dimensions['B'].width = 15

        # 输出
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"统计报表_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.exception('export_statistics failed')
        return api_error(f'导出失败: {e}', 500)

# ============================================================
# API: 变电站列表
# ============================================================

@app.route('/api/stations', methods=['GET'])
def get_stations():
    """获取变电站列表"""
    db = get_db()

    # 支持按县区筛选
    county = request.args.get('county')
    query = "SELECT id, name, voltage_level, county, location, latitude, longitude FROM stations"
    params = []

    if county:
        query += " WHERE county = ?"
        params.append(county)

    query += " ORDER BY county, name"

    rows = db.execute(query, params).fetchall()

    return api_success({
        'stations': [dict(row) for row in rows],
        'total': len(rows)
    })

# ============================================================
# API: 变电站详情
# ============================================================

@app.route('/api/stations/<int:station_id>', methods=['GET'])
def get_station(station_id):
    """获取变电站详情（含JOIN）"""
    db = get_db()

    # 获取变电站信息
    station = db.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM cameras WHERE station_id = s.id) as camera_count,
               (SELECT COUNT(*) FROM fault_reports WHERE station_id = s.id) as fault_count
        FROM stations s
        WHERE s.id = ?
    """, (station_id,)).fetchone()

    if not station:
        return api_error('变电站不存在', 404)

    # 获取摄像头列表
    cameras = db.execute("""
        SELECT * FROM cameras WHERE station_id = ?
        ORDER BY camera_index, channel_number
    """, (station_id,)).fetchall()

    return api_success({
        'station': dict(station),
        'cameras': [dict(c) for c in cameras]
    })


@app.route('/api/stations/<int:station_id>/slots', methods=['GET'])
def get_station_slots(station_id):
    """获取站点槽位列表"""
    db = get_db()
    station = db.execute(
        "SELECT id, name FROM stations WHERE id = ?",
        (station_id,),
    ).fetchone()
    if not station:
        return api_error('变电站不存在', 404)

    project_scope = {
        'enabled': False,
        'project_ids': None,
        'requested_project': None,
        'projects': [],
    }
    recorders = build_station_recorders_payload(
        db,
        station_id,
        project_scope,
    )
    slots = build_station_slots_payload(
        db,
        station_id,
        project_scope,
    )
    return api_success({
        'station': dict(station),
        'slots': slots,
        'recorders': recorders,
        'total': len(slots),
    })

# ============================================================
# API: 密码查询（需Token认证，决策#1）
# ============================================================

@app.route('/api/stations/<int:station_id>/password', methods=['GET'])
@require_token
def get_station_password(station_id):
    """获取变电站密码（需认证）"""
    db = get_db()

    station = db.execute("SELECT id, name, nvr_ip, nvr_port FROM stations WHERE id = ?",
                         (station_id,)).fetchone()

    if not station:
        return api_error('变电站不存在', 404)

    # 注意：实际密码存储需要单独字段，这里返回NVR连接信息作为示例
    # 完整实现需要在stations表增加password字段
    return api_success({
        'station_id': station['id'],
        'station_name': station['name'],
        'nvr_ip': station['nvr_ip'],
        'nvr_port': station['nvr_port'],
        'message': '密码字段需要扩展数据库'
    })

# ============================================================
# API: 摄像头列表
# ============================================================

@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    """获取摄像头列表"""
    db = get_db()

    # 支持按变电站筛选
    station_id = request.args.get('station_id', type=int)

    query = """
        SELECT c.*, s.name as station_name, s.voltage_level
        FROM cameras c
        JOIN stations s ON c.station_id = s.id
    """
    params = []

    if station_id:
        query += " WHERE c.station_id = ?"
        params.append(station_id)

    query += " ORDER BY s.county, s.name, c.camera_index, c.channel_number"

    rows = db.execute(query, params).fetchall()

    return api_success({
        'cameras': [dict(row) for row in rows],
        'total': len(rows)
    })

# ============================================================
# API: 按IP查摄像头
# ============================================================

@app.route('/api/cameras/by-ip', methods=['GET'])
def get_camera_by_ip():
    """按IP地址查询摄像头"""
    ip = request.args.get('ip', '').strip()

    if not ip:
        return api_error('未提供IP地址')

    db = get_db()

    camera = db.execute("""
        SELECT c.*, s.name as station_name, s.voltage_level, s.county
        FROM cameras c
        JOIN stations s ON c.station_id = s.id
        WHERE c.ip_address = ?
    """, (ip,)).fetchone()

    if not camera:
        return api_error('该IP暂未录入系统，请选择变电站手动报修', 404)

    return api_success({'camera': dict(camera)})

# ============================================================
# API: 故障提交（决策#7：幂等键）
# ============================================================

@app.route('/api/faults', methods=['POST'])
def create_fault():
    """提交故障报修"""
    data = request.get_json()
    if not data:
        return api_error('请求体无效')

    logger.info(f"Fault report: station={data.get('station_id')}, type={data.get('fault_type')}, reporter={data.get('reporter_name')}")

    # 必填字段验证
    required = ['station_id', 'fault_type', 'reporter_name']
    for field in required:
        if not data.get(field):
            return api_error(f'缺少必填字段: {field}')

    db = get_db()
    fault_report_columns = get_table_columns(db, "fault_reports")
    camera_columns = get_table_columns(db, "cameras")
    project = None
    project_id = None
    project_code = (data.get('project') or '').strip()
    camera_id = data.get('camera_id')
    camera_row = None
    camera_slot_id = None
    project_device_code = None
    fault_type_label_snapshot = data.get('fault_type')
    fault_type_version_id = None

    if camera_id:
        select_fields = ["id", "station_id"]
        for field_name in ('project_id', 'slot_id', 'project_camera_code', 'camera_index'):
            if field_name in camera_columns:
                select_fields.append(field_name)
        camera_row = db.execute(
            f"SELECT {', '.join(select_fields)} FROM cameras WHERE id = ?",
            (camera_id,),
        ).fetchone()
        if not camera_row:
            return api_error('摄像头不存在', 404)

    if project_code:
        project = get_project_by_code(db, project_code, include_inactive=False)
        if not project:
            return api_error('项目不存在', 404)
        if not ensure_project_read_access(db, project['code']):
            return project_access_denied()
        project_id = project['id']
    elif 'project_id' in fault_report_columns and projects_enabled(db):
        if camera_row and 'project_id' in camera_columns and camera_row['project_id']:
            project_id = camera_row['project_id']
        if project_id is None:
            default_project_code = get_default_project_code(
                get_visible_projects(
                    db,
                    user_id=session.get('user_id'),
                    role=session.get('role') or 'admin',
                    include_inactive=False,
                )
            ) or 'unified'
            project = get_project_by_code(db, default_project_code, include_inactive=False)
            project_id = project['id'] if project else None

    if project is None and project_id is not None and projects_enabled(db):
        project_row = db.execute(
            "SELECT code FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project_row:
            project = get_project_by_code(db, project_row['code'], include_inactive=True)

    if projects_enabled(db) and project_id is not None:
        if not project or not project.get('is_active'):
            return api_error('椤圭洰涓嶅瓨鍦ㄦ垨宸插仠鐢?', 404)
        if not ensure_project_write_access(db, project['code']):
            return project_access_denied()

    # 验证变电站存在
    station = db.execute("SELECT id, name FROM stations WHERE id = ?",
                         (data['station_id'],)).fetchone()
    if not station:
        return api_error('变电站不存在', 404)

    if camera_row:
        if camera_row['station_id'] != data['station_id']:
            return api_error('摄像头与变电站不匹配', 400)
        camera_project_id = camera_row['project_id'] if 'project_id' in camera_columns else None
        if project_id is not None and 'project_id' in camera_columns and camera_project_id not in (None, project_id):
            return api_error('摄像头与项目不匹配', 400)
        if 'slot_id' in camera_columns:
            camera_slot_id = camera_row['slot_id']
        if 'project_camera_code' in camera_columns and camera_row['project_camera_code']:
            project_device_code = camera_row['project_camera_code']
        elif 'camera_index' in camera_columns:
            project_device_code = camera_row['camera_index']

    if data.get('fault_type_code') and project and project.get('fault_type_version_id'):
        fault_type_row = db.execute(
            """
            SELECT type_label
            FROM project_fault_types
            WHERE version_id = ?
              AND type_code = ?
              AND is_active = 1
            """,
            (project['fault_type_version_id'], data.get('fault_type_code')),
        ).fetchone()
        if not fault_type_row:
            return api_error('故障类型不存在或未发布', 400)
        fault_type_label_snapshot = fault_type_row['type_label']
        fault_type_version_id = project['fault_type_version_id']

    # 计算幂等键（决策#7）
    # 幂等键 = camera_id + FLOOR(report_time / 300秒)
    report_time = data.get('report_time')

    if camera_id:
        # 使用当前时间计算5分钟窗口（即使没有report_time也用camera_id做幂等）
        import time
        current_time = int(report_time or time.time())
        window = math.floor(current_time / 300)
        idempotency_key = f"{camera_id}_{window}"
    else:
        # 使用IP文本的哈希
        import hashlib
        ip_text = data.get('camera_ip_free_text', '')
        if ip_text:
            idempotency_key = hashlib.md5(ip_text.encode()).hexdigest()[:16]
        else:
            idempotency_key = None

    # 检查幂等冲突
    if idempotency_key:
        existing = db.execute("""
            SELECT id FROM fault_reports WHERE idempotency_key = ?
        """, (idempotency_key,)).fetchone()

        if existing:
            return api_error('该摄像头5分钟内有报修记录，请勿重复提交', 409)

    # 插入故障记录
    try:
        insert_columns = [
            'station_id', 'camera_id', 'fault_type', 'description',
            'reporter_name', 'reporter_contact', 'status', 'idempotency_key'
        ]
        insert_values = [
            data['station_id'],
            data.get('camera_id'),
            data['fault_type'],
            data.get('description', ''),
            data['reporter_name'],
            data.get('reporter_contact', ''),
            'open',
            idempotency_key
        ]

        if 'project_id' in fault_report_columns:
            insert_columns.append('project_id')
            insert_values.append(project_id)
        if 'camera_slot_id' in fault_report_columns:
            insert_columns.append('camera_slot_id')
            insert_values.append(camera_slot_id)
        if 'fault_type_label_snapshot' in fault_report_columns:
            insert_columns.append('fault_type_label_snapshot')
            insert_values.append(fault_type_label_snapshot)
        if 'fault_type_code' in fault_report_columns and data.get('fault_type_code'):
            insert_columns.append('fault_type_code')
            insert_values.append(data.get('fault_type_code'))
        if 'fault_type_version_id' in fault_report_columns:
            insert_columns.append('fault_type_version_id')
            insert_values.append(fault_type_version_id)
        if 'project_device_code' in fault_report_columns:
            insert_columns.append('project_device_code')
            insert_values.append(project_device_code)

        placeholders = ', '.join(['?'] * len(insert_columns))
        cursor = db.execute(
            f"""
            INSERT INTO fault_reports ({', '.join(insert_columns)})
            VALUES ({placeholders})
            """,
            insert_values,
        )
        db.commit()

        fault_id = cursor.lastrowid
        _safe_dispatch_fault_notification(db, fault_id, 'fault_created')

        return api_success({
            'fault_id': fault_id,
            'message': '故障报修提交成功'
        }, 201)

    except Exception as e:
        return api_error(f'提交失败: {e}', 500)

# ============================================================
# API: 故障列表
# ============================================================

@app.route('/api/faults', methods=['GET'])
def get_faults():
    """获取故障记录列表（分页）"""
    db = get_db()

    # 支持筛选
    ensure_ai_runtime_schema(db)
    deleted_mode = get_fault_deleted_mode()
    status = request.args.get('status')
    station_id = request.args.get('station_id', type=int)
    year = request.args.get('year', type=int)
    project_code = request.args.get('project', '').strip()
    source_type = request.args.get('source_type', '').strip()

    # 分页参数
    page = max(request.args.get('page', default=1, type=int), 1)
    page_size = request.args.get('page_size', default=50, type=int)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    # 构建WHERE条件（用于两个查询）
    where_clause = " WHERE 1=1"
    count_where = " WHERE 1=1"
    params = []
    fault_report_columns = get_table_columns(db, "fault_reports")

    if status:
        where_clause += " AND f.status = ?"
        count_where += " AND f.status = ?"
        params.append(status)

    if station_id:
        where_clause += " AND f.station_id = ?"
        count_where += " AND f.station_id = ?"
        params.append(station_id)

    if year:
        where_clause += " AND strftime('%Y', f.created_at) = ?"
        count_where += " AND strftime('%Y', f.created_at) = ?"
        params.append(str(year))

    if source_type:
        if 'source_type' not in fault_report_columns:
            return api_error('source_type filter unavailable', 409)
        where_clause += " AND f.source_type = ?"
        count_where += " AND f.source_type = ?"
        params.append(source_type)

    if project_code and project_code != 'all':
        if not projects_enabled(db) or 'project_id' not in fault_report_columns:
            return api_error('当前数据库尚未启用项目筛选', 409)
        project = get_project_by_code(db, project_code, include_inactive=True)
        if not project:
            return api_error('项目不存在', 404)
        if not ensure_project_read_access(db, project_code):
            return project_access_denied()
        where_clause += " AND f.project_id = ?"
        count_where += " AND f.project_id = ?"
        params.append(project['id'])

    deleted_clause = build_fault_deleted_clause(fault_report_columns, alias="f", mode=deleted_mode)
    where_clause += deleted_clause
    count_where += deleted_clause

    # 分离的COUNT查询（不JOIN cameras表，避免列名问题）
    count_query = f"""
        SELECT COUNT(*) as total
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        {count_where}
    """
    total_row = db.execute(count_query, params).fetchone()
    total = total_row['total'] if total_row else 0

    # 主查询
    query = f"""
        SELECT f.*, s.name as station_name, s.voltage_level,
               COALESCE(NULLIF(TRIM(c.area), ''), NULLIF(TRIM(cs.area), ''), '') as camera_area,
               COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(cs.location_desc), ''), NULLIF(TRIM(f.camera_location_text), ''), '') as camera_location
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        LEFT JOIN camera_slots cs ON f.camera_slot_id = cs.id
        {where_clause}
        ORDER BY {FAULT_STATUS_SORT_SQL}, f.created_at DESC, f.id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([page_size, offset])

    rows = db.execute(query, params).fetchall()

    return api_success({
        'faults': [enrich_fault_camera_location(dict(row)) for row in rows],
        'total': total,
        'page': page,
        'page_size': page_size,
        'deleted_mode': deleted_mode,
    })

# ============================================================
# API: 故障状态更新（决策#7：三状态机）
# ============================================================

@app.route('/api/faults/<int:fault_id>/status', methods=['PUT'])
def update_fault_status(fault_id):
    """更新故障状态"""
    data = request.get_json()
    if not data or 'status' not in data:
        return api_error('未提供状态')

    new_status = data['status']
    valid_statuses = ['open', 'handling', 'closed']

    if new_status not in valid_statuses:
        return api_error(f'无效状态，可选值: {valid_statuses}')

    # 状态转换验证（决策#7）
    db = get_db()
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    deleted_clause = build_fault_deleted_clause(fault_report_columns, alias="", mode="active")

    fault = db.execute(f"SELECT id, status FROM fault_reports WHERE id = ?{deleted_clause}",
                        (fault_id,)).fetchone()

    if not fault:
        return api_error('故障记录不存在', 404)

    current_status = fault['status']

    # 状态转换规则
    valid_transitions = {
        'open': ['handling', 'closed'],
        'handling': ['closed'],
        'closed': []  # 已关闭的不能转换
    }

    if new_status not in valid_transitions.get(current_status, []):
        return api_error(f'不能从 {current_status} 转换为 {new_status}')

    # handling→closed需要处理人和备注、设备信息
    if new_status == 'closed' and current_status == 'handling':
        handler_name = data.get('handler_name')
        handler_note = data.get('handler_note')
        equipment_type = data.get('equipment_type', '')
        equipment_quantity = data.get('equipment_quantity', 0)

        if not handler_name or not handler_note:
            return api_error('关闭故障需要提供处理人姓名和处理备注')

        if {"equipment_type", "equipment_quantity"}.issubset(fault_report_columns):
            db.execute("""
                UPDATE fault_reports
                SET status = 'closed', handler_name = ?, handler_note = ?,
                    equipment_type = ?, equipment_quantity = ?,
                    closed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (handler_name, handler_note, equipment_type, equipment_quantity, fault_id))
        else:
            db.execute("""
                UPDATE fault_reports
                SET status = 'closed', handler_name = ?, handler_note = ?,
                    closed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (handler_name, handler_note, fault_id))
    else:
        db.execute("""
            UPDATE fault_reports
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (new_status, fault_id))

    db.commit()
    logger.info(f"Fault status updated: id={fault_id}, {current_status} -> {new_status}")
    if new_status == 'closed':
        _safe_dispatch_fault_notification(db, fault_id, 'fault_closed')

    return api_success({'message': f'状态已更新为 {new_status}'})

# ============================================================
# API: 故障记录删除（仅admin）
# ============================================================

@app.route('/api/faults/<int:fault_id>', methods=['DELETE'])
@require_admin
def delete_fault(fault_id):
    """删除故障记录（仅admin）"""
    db = get_db()
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)

    fault = db.execute("SELECT id, deleted_at FROM fault_reports WHERE id = ?",
                        (fault_id,)).fetchone()

    if not fault:
        return api_error('故障记录不存在', 404)

    if fault['deleted_at']:
        return api_success({'message': '故障记录已在回收站', 'fault_id': fault_id})

    db.execute(
        """
        UPDATE fault_reports
        SET deleted_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP,
            deleted_by = ?
        WHERE id = ?
        """,
        (session.get('user_id'), fault_id),
    )
    db.commit()
    logger.info(f"Fault soft-deleted: id={fault_id}")

    return api_success({'message': '故障记录已移入回收站', 'fault_id': fault_id})


@app.route('/api/faults/<int:fault_id>/restore', methods=['POST'])
@require_admin
def restore_fault(fault_id):
    db = get_db()
    ensure_fault_report_soft_delete_schema(db)

    fault = db.execute("SELECT id, deleted_at FROM fault_reports WHERE id = ?", (fault_id,)).fetchone()
    if not fault:
        return api_error('故障记录不存在', 404)
    if not fault['deleted_at']:
        return api_success({'message': '故障记录无需恢复', 'fault_id': fault_id})

    db.execute(
        """
        UPDATE fault_reports
        SET deleted_at = NULL,
            deleted_by = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (fault_id,),
    )
    db.commit()
    logger.info(f"Fault restored from trash: id={fault_id}")

    return api_success({'message': '故障记录已恢复', 'fault_id': fault_id})


@app.route('/api/faults/<int:fault_id>', methods=['PUT'])
def update_fault(fault_id):
    db = get_db()
    ensure_ai_runtime_schema(db)
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    payload = request.get_json(silent=True) or {}
    if not payload:
        return api_error('无可更新内容')

    select_fields = ["id", "status"]
    if 'project_id' in fault_report_columns:
        select_fields.append("project_id")
    deleted_clause = build_fault_deleted_clause(fault_report_columns, alias="", mode="active")
    fault = db.execute(
        f"SELECT {', '.join(select_fields)} FROM fault_reports WHERE id = ?{deleted_clause}",
        (fault_id,),
    ).fetchone()
    if not fault:
        return api_error('fault not found', 404)

    if projects_enabled(db) and 'project_id' in fault_report_columns and fault['project_id']:
        project_row = db.execute(
            "SELECT code FROM projects WHERE id = ?",
            (fault['project_id'],),
        ).fetchone()
        if project_row and not ensure_project_write_access(db, project_row['code']):
            return project_access_denied()

    editable_fields = [
        ('fault_type', 'fault_type'),
        ('description', 'description'),
        ('camera_location_text', 'camera_location_text'),
        ('reporter_name', 'reporter_name'),
        ('reporter_contact', 'reporter_contact'),
        ('handler_name', 'handler_name'),
        ('handler_note', 'handler_note'),
        ('equipment_type', 'equipment_type'),
        ('equipment_quantity', 'equipment_quantity'),
    ]

    update_fields = []
    update_params = []
    for request_key, column_name in editable_fields:
        if request_key not in payload or column_name not in fault_report_columns:
            continue
        value = payload.get(request_key)
        if column_name == 'equipment_quantity':
            try:
                value = int(value or 0)
            except (TypeError, ValueError):
                return api_error('equipment_quantity must be an integer')
        elif value is None:
            value = ''
        elif isinstance(value, str):
            value = value.strip()
        update_fields.append(f"{column_name} = ?")
        update_params.append(value)

    if 'fault_type' in payload and 'fault_type_label_snapshot' in fault_report_columns:
        update_fields.append("fault_type_label_snapshot = ?")
        update_params.append(str(payload.get('fault_type') or '').strip())

    if not update_fields:
        return api_error('没有可更新字段')

    update_fields.append("updated_at = CURRENT_TIMESTAMP")
    update_params.append(fault_id)
    db.execute(
        f"UPDATE fault_reports SET {', '.join(update_fields)} WHERE id = ?",
        update_params,
    )
    db.commit()
    return api_success({'message': '故障记录已更新', 'fault_id': fault_id})

# ============================================================
# API: 故障详情（GET）
# ============================================================

@app.route('/api/faults/<int:fault_id>/detail', methods=['GET'])
def get_fault_detail(fault_id):
    """获取故障完整详情"""
    db = get_db()
    ensure_ai_runtime_schema(db)
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    deleted_mode = get_fault_deleted_mode()
    has_camera_slots = table_exists(db, "camera_slots")
    camera_area_expr = "COALESCE(NULLIF(TRIM(c.area), ''), '')"
    camera_location_expr = "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(f.camera_location_text), ''), '')"
    camera_slot_join = ""
    if has_camera_slots:
        camera_area_expr = "COALESCE(NULLIF(TRIM(c.area), ''), NULLIF(TRIM(cs.area), ''), '')"
        camera_location_expr = (
            "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(cs.location_desc), ''), "
            "NULLIF(TRIM(f.camera_location_text), ''), '')"
        )
        camera_slot_join = "LEFT JOIN camera_slots cs ON f.camera_slot_id = cs.id"
    deleted_clause = build_fault_deleted_clause(fault_report_columns, alias="f", mode=deleted_mode)
    fault = db.execute("""
        SELECT f.*, s.name as station_name,
               c.camera_index,
               {camera_area_expr} as camera_area,
               {camera_location_expr} as camera_location,
               c.ip_address as camera_ip
        FROM fault_reports f
        LEFT JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        {camera_slot_join}
        WHERE f.id = ?{deleted_clause}
    """.format(
        camera_area_expr=camera_area_expr,
        camera_location_expr=camera_location_expr,
        camera_slot_join=camera_slot_join,
        deleted_clause=deleted_clause,
    ), (fault_id,)).fetchone()

    if not fault:
        return api_error('故障记录不存在', 404)

    return api_success({'fault': enrich_fault_camera_location(dict(fault))})


# ============================================================
# API: 照片查询与分组
# ============================================================

@app.route('/api/photos', methods=['GET'])
def get_photos():
    """获取照片平铺列表"""
    db = get_db()

    station_id = request.args.get('station_id', type=int)
    county = request.args.get('county', '').strip()
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()
    page = max(request.args.get('page', default=1, type=int), 1)
    page_size = request.args.get('page_size', default=60, type=int)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    where = ["1=1"]
    params = []

    if station_id:
        where.append("p.station_id = ?")
        params.append(station_id)

    if county:
        where.append("(p.county_hint = ? OR s.county = ?)")
        params.extend([county, county])

    if status in ('matched', 'unmatched', 'ignored'):
        where.append("p.match_status = ?")
        params.append(status)

    if keyword:
        where.append("(p.filename LIKE ? OR p.rel_path LIKE ? OR p.station_hint LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])

    where_sql = " AND ".join(where)

    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM photos p
        LEFT JOIN stations s ON p.station_id = s.id
        WHERE {where_sql}
        """,
        params
    ).fetchone()

    rows = db.execute(
        f"""
        SELECT p.*, s.name AS station_name, s.county AS station_county, s.voltage_level
        FROM photos p
        LEFT JOIN stations s ON p.station_id = s.id
        WHERE {where_sql}
        ORDER BY p.file_mtime DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset]
    ).fetchall()

    return api_success({
        'photos': [normalize_photo_row(row) for row in rows],
        'total': total_row['total'] if total_row else 0,
        'page': page,
        'page_size': page_size,
    })


@app.route('/api/photos/groups', methods=['GET'])
def get_photo_groups():
    """按变电站分组返回照片"""
    db = get_db()

    county = request.args.get('county', '').strip()
    station_id = request.args.get('station_id', type=int)
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()
    limit_per_group = request.args.get('limit_per_group', default=120, type=int)
    limit_per_group = min(max(limit_per_group, 1), 300)

    where = ["1=1"]
    params = []

    if county:
        where.append("(p.county_hint = ? OR s.county = ?)")
        params.extend([county, county])

    if station_id:
        where.append("p.station_id = ?")
        params.append(station_id)

    if status in ('matched', 'unmatched', 'ignored'):
        where.append("p.match_status = ?")
        params.append(status)

    if keyword:
        where.append("(p.filename LIKE ? OR p.rel_path LIKE ? OR p.station_hint LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])

    where_sql = " AND ".join(where)

    rows = db.execute(
        f"""
        SELECT p.*, s.name AS station_name, s.county AS station_county
        FROM photos p
        LEFT JOIN stations s ON p.station_id = s.id
        WHERE {where_sql}
        ORDER BY p.match_status DESC, s.name, p.file_mtime DESC, p.id DESC
        """,
        params
    ).fetchall()

    grouped = {}
    unmatched = []

    for row in rows:
        photo = normalize_photo_row(row)
        if photo['match_status'] == 'matched' and photo.get('station_id'):
            key = str(photo['station_id'])
            if key not in grouped:
                grouped[key] = {
                    'station_id': photo['station_id'],
                    'station_name': photo.get('station_name') or '未知变电站',
                    'county': photo.get('station_county') or photo.get('county_hint') or '',
                    'photos': []
                }
            if len(grouped[key]['photos']) < limit_per_group:
                grouped[key]['photos'].append(photo)
        elif photo['match_status'] == 'unmatched':
            if len(unmatched) < limit_per_group:
                unmatched.append(photo)

    groups = sorted(grouped.values(), key=lambda g_: (g_['county'], g_['station_name']))

    return api_success({
        'groups': groups,
        'unmatched': unmatched,
        'group_count': len(groups),
        'unmatched_count': len([r for r in rows if r['match_status'] == 'unmatched'])
    })


@app.route('/photos/file/<int:photo_id>', methods=['GET'])
def get_photo_file(photo_id):
    """受控图片访问端点（photo_id映射 + root约束，防止路径穿越）"""
    # 需要登录才能访问照片
    if 'user_id' not in session:
        return api_error('请先登录', 401)

    db = get_db()
    row = db.execute("SELECT id, abs_path, ext FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not row:
        return api_error('照片不存在', 404)

    root = get_photo_root()
    file_path = Path(row['abs_path']).resolve()

    # 必须位于PHOTO_ROOT_PATH内，防止path traversal
    if not is_path_under_root(file_path, root):
        logger.warning(f"Blocked photo traversal attempt: photo_id={photo_id}, path={file_path}")
        return api_error('非法路径访问', 403)

    if not file_path.exists() or not file_path.is_file():
        return api_error('照片文件不存在', 404)

    ext = (row['ext'] or '').lower()
    if ext not in IMAGE_EXTENSIONS:
        return api_error('文件类型不支持', 400)

    return send_file(str(file_path), conditional=True)


def get_stats_scoped():
    db = get_db()
    year = request.args.get('year', type=int)
    payload, error = _build_statistics_payload(db, year, request.args.get('project'))
    if error:
        return error
    return api_success(payload)


def get_stations_scoped():
    db = get_db()
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    county = request.args.get('county')
    query = """
        SELECT DISTINCT s.id, s.name, s.voltage_level, s.county, s.location, s.latitude, s.longitude
        FROM stations s
        WHERE 1=1
    """
    params = []
    if county:
        query += " AND s.county = ?"
        params.append(county)

    camera_columns = get_table_columns(db, "cameras")
    fault_report_columns = get_table_columns(db, "fault_reports")
    visibility_checks = []
    if project_scope['enabled'] and 'project_id' in camera_columns:
        project_sql, project_params = build_project_in_clause("c", project_scope['project_ids'])
        status_sql = " AND c.status = 'active'" if 'status' in camera_columns else ""
        visibility_checks.append(
            f"EXISTS (SELECT 1 FROM cameras c WHERE c.station_id = s.id{project_sql}{status_sql})"
        )
        params.extend(project_params)
    if project_scope['enabled'] and 'project_id' in fault_report_columns:
        project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
        visibility_checks.append(
            f"EXISTS (SELECT 1 FROM fault_reports f WHERE f.station_id = s.id{project_sql})"
        )
        params.extend(project_params)
    if visibility_checks:
        query += " AND (" + " OR ".join(visibility_checks) + ")"

    query += " ORDER BY s.county, s.name"
    rows = db.execute(query, params).fetchall()
    return api_success({
        'stations': [dict(row) for row in rows],
        'total': len(rows)
    })


def get_station_scoped(station_id):
    db = get_db()
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    station = db.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    if not station:
        return api_error('鍙樼數绔欎笉瀛樺湪', 404)

    station_payload = dict(station)
    camera_columns = get_table_columns(db, "cameras")
    fault_report_columns = get_table_columns(db, "fault_reports")

    camera_query = "SELECT * FROM cameras c WHERE c.station_id = ?"
    camera_params = [station_id]
    if project_scope['enabled'] and 'project_id' in camera_columns:
        project_sql, project_params = build_project_in_clause("c", project_scope['project_ids'])
        camera_query += project_sql
        camera_params.extend(project_params)
    if 'status' in camera_columns:
        camera_query += " AND c.status = 'active'"
    camera_query += """
        ORDER BY
            CASE
                WHEN c.channel_number IS NOT NULL THEN c.channel_number
                WHEN TRIM(COALESCE(c.camera_index, '')) GLOB '[0-9]*' THEN CAST(TRIM(c.camera_index) AS INTEGER)
                ELSE 2147483647
            END,
            CASE
                WHEN TRIM(COALESCE(c.camera_index, '')) = '' THEN 1
                ELSE 0
            END,
            c.camera_index,
            c.id
    """
    cameras = db.execute(camera_query, camera_params).fetchall()

    fault_count_query = "SELECT COUNT(*) AS count FROM fault_reports f WHERE f.station_id = ?"
    fault_count_params = [station_id]
    if project_scope['enabled'] and 'project_id' in fault_report_columns:
        project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
        fault_count_query += project_sql
        fault_count_params.extend(project_params)
    fault_count = db.execute(fault_count_query, fault_count_params).fetchone()['count']

    station_payload['camera_count'] = len(cameras)
    station_payload['fault_count'] = fault_count
    if project_scope['enabled'] and station_payload['camera_count'] == 0 and station_payload['fault_count'] == 0:
        return api_error('鍙樼數绔欎笉瀛樺湪', 404)

    return api_success({
        'station': station_payload,
        'cameras': [dict(camera) for camera in cameras]
    })


def get_station_slots_scoped(station_id):
    db = get_db()
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    station = db.execute("SELECT * FROM stations WHERE id = ?", (station_id,)).fetchone()
    if not station:
        return api_error('变电站不存在', 404)

    fault_report_columns = get_table_columns(db, "fault_reports")
    camera_columns = get_table_columns(db, "cameras")
    slots = build_station_slots_payload(db, station_id, project_scope)

    if project_scope['enabled']:
        visible_camera = False
        visible_fault = False
        if 'project_id' in camera_columns:
            camera_query = "SELECT 1 FROM cameras c WHERE c.station_id = ?"
            camera_params = [station_id]
            project_sql, project_params = build_project_in_clause("c", project_scope['project_ids'])
            camera_query += project_sql
            camera_params.extend(project_params)
            if 'status' in camera_columns:
                camera_query += " AND c.status = 'active'"
            visible_camera = db.execute(camera_query + " LIMIT 1", camera_params).fetchone() is not None
        if 'project_id' in fault_report_columns:
            fault_query = "SELECT 1 FROM fault_reports f WHERE f.station_id = ?"
            fault_params = [station_id]
            project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
            fault_query += project_sql
            fault_params.extend(project_params)
            visible_fault = db.execute(fault_query + " LIMIT 1", fault_params).fetchone() is not None
        if not slots and not visible_camera and not visible_fault:
            return api_error('变电站不存在', 404)

    return api_success({
        'station': dict(station),
        'slots': slots,
        'total': len(slots),
    })


def get_cameras_scoped():
    db = get_db()
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    station_id = request.args.get('station_id', type=int)
    query = """
        SELECT c.*, s.name as station_name, s.voltage_level
        FROM cameras c
        JOIN stations s ON c.station_id = s.id
        WHERE 1=1
    """
    params = []
    if station_id:
        query += " AND c.station_id = ?"
        params.append(station_id)

    camera_columns = get_table_columns(db, "cameras")
    if project_scope['enabled'] and 'project_id' in camera_columns:
        project_sql, project_params = build_project_in_clause("c", project_scope['project_ids'])
        query += project_sql
        params.extend(project_params)
    if 'status' in camera_columns:
        query += " AND c.status = 'active'"

    query += " ORDER BY s.county, s.name, c.camera_index, c.channel_number"
    rows = db.execute(query, params).fetchall()
    return api_success({
        'cameras': [dict(row) for row in rows],
        'total': len(rows)
    })


def get_camera_by_ip_scoped():
    ip = request.args.get('ip', '').strip()
    if not ip:
        return api_error('鏈彁渚汭P鍦板潃')

    db = get_db()
    camera_columns = get_table_columns(db, "cameras")
    if not (projects_enabled(db) and 'project_id' in camera_columns):
        return get_camera_by_ip()
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    query = """
        SELECT c.*, s.name as station_name, s.voltage_level, s.county
        FROM cameras c
        JOIN stations s ON c.station_id = s.id
        WHERE c.ip_address = ?
    """
    params = [ip]
    if project_scope['enabled'] and 'project_id' in camera_columns:
        project_sql, project_params = build_project_in_clause("c", project_scope['project_ids'])
        query += project_sql
        params.extend(project_params)
    if 'status' in camera_columns:
        query += " AND c.status = 'active'"

    camera = db.execute(query, params).fetchone()
    if not camera:
        return api_error('璇P鏆傛湭褰曞叆绯荤粺锛岃閫夋嫨鍙樼數绔欐墜鍔ㄦ姤淇�', 404)
    return api_success({'camera': dict(camera)})


def create_fault_scoped():
    data = request.get_json()
    if not data:
        return api_error('璇锋眰浣撴棤鏁�')

    required = ['station_id', 'fault_type', 'reporter_name']
    for field in required:
        if not data.get(field):
            return api_error(f'缂哄皯蹇呭～瀛楁: {field}')

    db = get_db()
    fault_report_columns = get_table_columns(db, "fault_reports")
    camera_columns = get_table_columns(db, "cameras")
    project_code = (data.get('project') or '').strip()

    if project_code:
        project = get_project_by_code(db, project_code, include_inactive=False)
        if not project:
            return api_error('椤圭洰涓嶅瓨鍦�', 404)
        if not ensure_project_write_access(db, project['code']):
            return project_access_denied()
    elif 'project_id' in fault_report_columns and projects_enabled(db):
        camera_id = data.get('camera_id')
        if camera_id and 'project_id' in camera_columns:
            row = db.execute("SELECT project_id FROM cameras WHERE id = ?", (camera_id,)).fetchone()
            if row and row['project_id']:
                project_row = db.execute(
                    "SELECT code FROM projects WHERE id = ?",
                    (row['project_id'],),
                ).fetchone()
                if project_row and not ensure_project_write_access(db, project_row['code']):
                    return project_access_denied()
        else:
            visible_projects = get_visible_projects(
                db,
                user_id=session.get('user_id'),
                role=session.get('role') or 'admin',
                include_inactive=False,
            )
            default_project_code = get_default_project_code(visible_projects) or 'unified'
            if not ensure_project_write_access(db, default_project_code):
                return project_access_denied()

    return create_fault()


def get_faults_scoped():
    db = get_db()
    ensure_ai_runtime_schema(db)
    deleted_mode = get_fault_deleted_mode()
    status = request.args.get('status')
    station_id = request.args.get('station_id', type=int)
    year = request.args.get('year', type=int)
    project_code = request.args.get('project', '').strip()
    source_type = request.args.get('source_type', '').strip()
    page = max(request.args.get('page', default=1, type=int), 1)
    page_size = request.args.get('page_size', default=50, type=int)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    has_camera_slots = table_exists(db, "camera_slots")
    project_scope, error = build_project_scope(db, project_code)
    if error:
        return error

    where_clause = " WHERE 1=1"
    params = []
    if status:
        where_clause += " AND f.status = ?"
        params.append(status)
    if station_id:
        where_clause += " AND f.station_id = ?"
        params.append(station_id)
    if year:
        where_clause += " AND strftime('%Y', f.created_at) = ?"
        params.append(str(year))
    if source_type:
        if 'source_type' not in fault_report_columns:
            return api_error('source_type filter unavailable', 409)
        where_clause += " AND f.source_type = ?"
        params.append(source_type)
    if project_scope['enabled'] and 'project_id' in fault_report_columns:
        project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
        where_clause += project_sql
        params.extend(project_params)
    where_clause += build_fault_deleted_clause(fault_report_columns, alias="f", mode=deleted_mode)

    count_query = f"""
        SELECT COUNT(*) as total
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        {where_clause}
    """
    total_row = db.execute(count_query, list(params)).fetchone()
    total = total_row['total'] if total_row else 0

    camera_area_expr = "COALESCE(NULLIF(TRIM(c.area), ''), '')"
    camera_location_expr = "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(f.camera_location_text), ''), '')"
    camera_slot_join = ""
    if has_camera_slots:
        camera_area_expr = "COALESCE(NULLIF(TRIM(c.area), ''), NULLIF(TRIM(cs.area), ''), '')"
        camera_location_expr = (
            "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(cs.location_desc), ''), "
            "NULLIF(TRIM(f.camera_location_text), ''), '')"
        )
        camera_slot_join = "LEFT JOIN camera_slots cs ON f.camera_slot_id = cs.id"

    query = f"""
        SELECT f.*, s.name as station_name, s.voltage_level,
               {camera_area_expr} as camera_area,
               {camera_location_expr} as camera_location
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        {camera_slot_join}
        {where_clause}
        ORDER BY {FAULT_STATUS_SORT_SQL}, f.created_at DESC, f.id DESC
        LIMIT ? OFFSET ?
    """
    rows = db.execute(query, params + [page_size, offset]).fetchall()
    return api_success({
        'faults': [enrich_fault_camera_location(dict(row)) for row in rows],
        'total': total,
        'page': page,
        'page_size': page_size,
        'deleted_mode': deleted_mode,
    })


def update_fault_status_scoped(fault_id):
    data = request.get_json()
    if not data or 'status' not in data:
        return api_error('鏈彁渚涚姸鎬�')

    new_status = data['status']
    valid_statuses = ['open', 'handling', 'closed']
    if new_status not in valid_statuses:
        return api_error(f'鏃犳晥鐘舵€侊紝鍙€夊€� {valid_statuses}')

    db = get_db()
    fault_report_columns = get_table_columns(db, "fault_reports")
    if not (projects_enabled(db) and 'project_id' in fault_report_columns):
        return update_fault_status(fault_id)
    select_fields = ["id", "status"]
    if 'project_id' in fault_report_columns:
        select_fields.append("project_id")
    fault = db.execute(
        f"SELECT {', '.join(select_fields)} FROM fault_reports WHERE id = ?{build_fault_deleted_clause(fault_report_columns, alias='', mode='active')}",
        (fault_id,),
    ).fetchone()
    if not fault:
        return api_error('鏁呴殰璁板綍涓嶅瓨鍦�', 404)

    if 'project_id' in fault_report_columns and fault['project_id']:
        project_row = db.execute(
            "SELECT code FROM projects WHERE id = ?",
            (fault['project_id'],),
        ).fetchone()
        if project_row and not ensure_project_write_access(db, project_row['code']):
            return project_access_denied()

    valid_transitions = {
        'open': ['handling', 'closed'],
        'handling': ['closed'],
        'closed': [],
    }
    current_status = fault['status']
    if new_status not in valid_transitions.get(current_status, []):
        return api_error(f'涓嶈兘浠�{current_status} 杞崲涓�{new_status}')

    update_fields = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
    update_params = [new_status]
    if new_status == 'handling':
        if 'handling_started_at' in fault_report_columns:
            update_fields.append("handling_started_at = COALESCE(handling_started_at, CURRENT_TIMESTAMP)")
        if 'assigned_to' in fault_report_columns and session.get('user_id'):
            update_fields.append("assigned_to = ?")
            update_params.append(session.get('user_id'))
    if new_status == 'closed':
        update_fields.append("closed_at = CURRENT_TIMESTAMP")
        if 'handler_name' in fault_report_columns and data.get('handler_name'):
            update_fields.append("handler_name = ?")
            update_params.append(data.get('handler_name'))
        if 'handler_note' in fault_report_columns and data.get('handler_note'):
            update_fields.append("handler_note = ?")
            update_params.append(data.get('handler_note'))
        if 'equipment_type' in fault_report_columns:
            update_fields.append("equipment_type = ?")
            update_params.append(data.get('equipment_type', ''))
        if 'equipment_quantity' in fault_report_columns:
            update_fields.append("equipment_quantity = ?")
            update_params.append(data.get('equipment_quantity', 0))

    update_params.append(fault_id)
    db.execute(
        f"UPDATE fault_reports SET {', '.join(update_fields)} WHERE id = ?",
        update_params,
    )
    db.commit()
    logger.info(f"Fault status updated: id={fault_id}, {current_status} -> {new_status}")
    if new_status == 'closed':
        _safe_dispatch_fault_notification(db, fault_id, 'fault_closed')
    return api_success({'message': f'鐘舵€佸凡鏇存柊涓�{new_status}'})


def get_fault_detail_scoped(fault_id):
    db = get_db()
    ensure_ai_runtime_schema(db)
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    deleted_mode = get_fault_deleted_mode()
    has_camera_slots = table_exists(db, "camera_slots")
    assigned_user_select = ""
    assigned_user_join = ""
    if 'assigned_to' in fault_report_columns and table_exists(db, "users"):
        assigned_user_select = ", u.username as assigned_to_username"
        assigned_user_join = " LEFT JOIN users u ON f.assigned_to = u.id"
    camera_area_expr = "COALESCE(NULLIF(TRIM(c.area), ''), '')"
    camera_location_expr = "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(f.camera_location_text), ''), '')"
    camera_slot_join = ""
    if has_camera_slots:
        camera_area_expr = "COALESCE(NULLIF(TRIM(c.area), ''), NULLIF(TRIM(cs.area), ''), '')"
        camera_location_expr = (
            "COALESCE(NULLIF(TRIM(c.location_desc), ''), NULLIF(TRIM(cs.location_desc), ''), "
            "NULLIF(TRIM(f.camera_location_text), ''), '')"
        )
        camera_slot_join = " LEFT JOIN camera_slots cs ON f.camera_slot_id = cs.id"
    fault = db.execute(
        f"""
        SELECT f.*, s.name as station_name,
               c.camera_index,
               {camera_area_expr} as camera_area,
               {camera_location_expr} as camera_location,
               c.ip_address as camera_ip
               {assigned_user_select}
        FROM fault_reports f
        LEFT JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        {camera_slot_join}
        {assigned_user_join}
        WHERE f.id = ?{build_fault_deleted_clause(fault_report_columns, alias='f', mode=deleted_mode)}
        """,
        (fault_id,),
    ).fetchone()
    if not fault:
        return api_error('鏁呴殰璁板綍涓嶅瓨鍦�', 404)

    if 'project_id' in fault_report_columns and fault['project_id']:
        project_row = db.execute("SELECT code FROM projects WHERE id = ?", (fault['project_id'],)).fetchone()
        if project_row and not ensure_project_read_access(db, project_row['code']):
            return project_access_denied()

    payload = dict(fault)
    enrich_fault_camera_location(payload)
    if 'tags_json' in fault_report_columns:
        payload['tags'] = parse_tags_json(payload.get('tags_json'))
    payload['slot_history'] = []
    payload['slot_history_count'] = 0
    if 'camera_slot_id' in fault_report_columns and payload.get('camera_slot_id'):
        history_query = """
            SELECT
                f.id,
                f.camera_slot_id,
                COALESCE(f.fault_type_label_snapshot, f.fault_type) AS fault_label,
                f.status,
                f.created_at,
                f.closed_at
            FROM fault_reports f
            WHERE f.camera_slot_id = ?
              AND f.id != ?
        """
        history_params = [payload['camera_slot_id'], fault_id]
        if 'project_id' in fault_report_columns and payload.get('project_id'):
            history_query += " AND f.project_id = ?"
            history_params.append(payload['project_id'])
        history_query += build_fault_deleted_clause(fault_report_columns, alias="f", mode="active")
        history_query += " ORDER BY f.created_at DESC, f.id DESC LIMIT 5"
        slot_history = [dict(row) for row in db.execute(history_query, history_params).fetchall()]
        payload['slot_history'] = slot_history
        payload['slot_history_count'] = len(slot_history)
    return api_success({'fault': payload})


@app.route('/api/fault-tags', methods=['GET'])
def get_fault_tag_suggestions():
    db = get_db()
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    if 'tags_json' not in fault_report_columns:
        return api_success({'tags': []})

    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    query = "SELECT tags_json FROM fault_reports f WHERE f.tags_json IS NOT NULL AND TRIM(f.tags_json) != ''"
    params = []
    query += build_fault_deleted_clause(fault_report_columns, alias="f", mode="active")
    if project_scope['enabled'] and 'project_id' in fault_report_columns:
        project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
        query += project_sql
        params.extend(project_params)

    search = (request.args.get('q') or '').strip().lower()
    tags = []
    seen = set()
    for row in db.execute(query, params).fetchall():
        row_tags = parse_tags_json(row['tags_json'] if hasattr(row, 'keys') else row[0])
        for tag in row_tags:
            if search and search not in tag.lower():
                continue
            lowered = tag.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            tags.append(tag)
    tags.sort(key=lambda item: item.lower())
    return api_success({'tags': tags})


@app.route('/api/faults/<int:fault_id>/tags', methods=['PUT'])
def update_fault_tags(fault_id):
    db = get_db()
    fault_report_columns = ensure_fault_report_soft_delete_schema(db)
    if 'tags_json' not in fault_report_columns:
        return api_error('tags feature unavailable', 409)

    payload = request.get_json(silent=True) or {}
    tags = normalize_tags_payload(payload.get('tags', []))

    fault = db.execute(
        f"SELECT id, project_id FROM fault_reports WHERE id = ?{build_fault_deleted_clause(fault_report_columns, alias='', mode='active')}",
        (fault_id,),
    ).fetchone()
    if not fault:
        return api_error('fault not found', 404)

    if projects_enabled(db) and 'project_id' in fault_report_columns and fault['project_id']:
        project_row = db.execute(
            "SELECT code FROM projects WHERE id = ?",
            (fault['project_id'],),
        ).fetchone()
        if project_row and not ensure_project_write_access(db, project_row['code']):
            return project_access_denied()

    db.execute(
        "UPDATE fault_reports SET tags_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(tags, ensure_ascii=False), fault_id),
    )
    db.commit()
    return api_success({'fault_id': fault_id, 'tags': tags})


app.view_functions['get_stats'] = get_stats_scoped
app.view_functions['get_stations'] = get_stations_scoped
app.view_functions['get_station'] = get_station_scoped
app.view_functions['get_station_slots'] = get_station_slots_scoped
app.view_functions['get_cameras'] = get_cameras_scoped
app.view_functions['get_camera_by_ip'] = get_camera_by_ip_scoped
app.view_functions['create_fault'] = create_fault_scoped
app.view_functions['get_faults'] = get_faults_scoped
app.view_functions['update_fault_status'] = update_fault_status_scoped
app.view_functions['get_fault_detail'] = get_fault_detail_scoped


def _photos_project_scope_enabled(db):
    photo_columns = get_table_columns(db, "photos")
    return projects_enabled(db) and 'project_id' in photo_columns, photo_columns


def _apply_photo_project_filter(db, base_where, params, requested_project_code):
    enabled, photo_columns = _photos_project_scope_enabled(db)
    if not enabled:
        return base_where, params, None, None

    project_scope, error = build_project_scope(db, requested_project_code)
    if error:
        return None, None, None, error

    where = list(base_where)
    scoped_params = list(params)
    if project_scope['enabled']:
        project_sql, project_params = build_project_in_clause("p", project_scope['project_ids'])
        if project_sql:
            where.append(project_sql.strip().removeprefix("AND ").strip())
            scoped_params.extend(project_params)
    return where, scoped_params, project_scope, None


def export_statistics_scoped():
    db = get_db()
    fault_report_columns = get_table_columns(db, "fault_reports")
    camera_columns = get_table_columns(db, "cameras")
    if not (
        projects_enabled(db)
        and 'project_id' in fault_report_columns
        and 'project_id' in camera_columns
    ):
        return export_statistics()

    year = request.args.get('year', type=int)
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    station_sources = []
    station_params = []
    project_sql, project_params = build_project_in_clause("c", project_scope['project_ids'])
    station_sources.append(
        f"SELECT c.station_id FROM cameras c WHERE 1=1{project_sql} AND c.status = 'active'"
    )
    station_params.extend(project_params)
    project_sql, project_params = build_project_in_clause("f", project_scope['project_ids'])
    station_sources.append(
        f"SELECT f.station_id FROM fault_reports f WHERE 1=1{project_sql}"
    )
    station_params.extend(project_params)
    station_count = db.execute(
        "SELECT COUNT(DISTINCT station_id) as count FROM ("
        + " UNION ALL ".join(station_sources)
        + ") scoped_stations",
        station_params,
    ).fetchone()['count']

    camera_sql, camera_params = build_project_in_clause("c", project_scope['project_ids'])
    camera_count = db.execute(
        f"SELECT COUNT(*) as count FROM cameras c WHERE 1=1{camera_sql} AND c.status = 'active'",
        camera_params,
    ).fetchone()['count']

    fault_base_sql, fault_base_params = build_project_in_clause("f", project_scope['project_ids'])
    fault_count_query = f"SELECT COUNT(*) as count FROM fault_reports f WHERE 1=1{fault_base_sql}"
    if year:
        fault_count = db.execute(
            fault_count_query + " AND strftime('%Y', f.created_at) = ?",
            fault_base_params + [str(year)],
        ).fetchone()['count']
    else:
        fault_count = db.execute(fault_count_query, fault_base_params).fetchone()['count']

    monthly_data = {}
    target_year = year or datetime.now().year
    for month in range(1, 13):
        monthly_data[f"{target_year}-{month:02d}"] = 0
    monthly_rows = db.execute(
        f"""
        SELECT strftime('%Y-%m', f.created_at) as month, COUNT(*) as cnt
        FROM fault_reports f
        WHERE 1=1{fault_base_sql} AND strftime('%Y', f.created_at) = ?
        GROUP BY month
        """,
        fault_base_params + [str(target_year)],
    ).fetchall()
    for row in monthly_rows:
        monthly_data[row['month']] = row['cnt']

    faults = db.execute(
        f"""
        SELECT f.id, s.name as station_name, s.voltage_level, s.county,
               c.area as camera_area, c.location_desc as camera_location,
               f.fault_type, f.description, f.status,
               f.reporter_name, f.reporter_contact,
               f.created_at, f.closed_at, f.handler_name, f.handler_note
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        WHERE 1=1{fault_base_sql}
        {'AND strftime(\'%Y\', f.created_at) = ?' if year else ''}
        ORDER BY f.created_at DESC
        """,
        fault_base_params + ([str(year)] if year else []),
    ).fetchall()

    county_data = {}
    voltage_data = {}
    for fault in faults:
        county = fault['county'] or '未知'
        county_data[county] = county_data.get(county, 0) + 1
        voltage = fault['voltage_level'] or '其他'
        voltage_data[voltage] = voltage_data.get(voltage, 0) + 1

    try:
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Border, Font, PatternFill, Side

        wb = Workbook()
        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='2f67f6', end_color='2f67f6', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        ws1 = wb.active
        ws1.title = '概览'
        overview = [
            ('变电站总数', station_count),
            ('摄像头总数', camera_count),
            ('故障报修总数', fault_count),
            ('故障率', f"{(fault_count / camera_count * 100):.2f}%" if camera_count > 0 else '0%'),
        ]
        ws1.append(['指标', '数值'])
        for key, value in overview:
            ws1.append([key, value])
        for cell in ws1[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.border = thin_border
        ws1.column_dimensions['A'].width = 20
        ws1.column_dimensions['B'].width = 15

        ws2 = wb.create_sheet('月度趋势')
        ws2.append(['月份', '故障数量'])
        for month, count in sorted(monthly_data.items()):
            ws2.append([month, count])

        ws3 = wb.create_sheet('故障明细')
        headers = ['ID', '变电站', '电压等级', '县区', '摄像头位置', '故障类型',
                   '描述', '状态', '报修人', '联系方式', '报修时间', '关闭时间', '处理人', '处理备注']
        ws3.append(headers)
        for fault in faults:
            ws3.append([
                fault['id'], fault['station_name'] or '', fault['voltage_level'] or '', fault['county'] or '',
                fault['camera_location'] or fault['camera_area'] or '', fault['fault_type'] or '',
                fault['description'] or '', fault['status'] or '', fault['reporter_name'] or '',
                fault['reporter_contact'] or '', fault['created_at'] or '', fault['closed_at'] or '',
                fault['handler_name'] or '', fault['handler_note'] or ''
            ])

        ws4 = wb.create_sheet('县区统计')
        ws4.append(['县区', '故障数量'])
        for county, count in sorted(county_data.items(), key=lambda item: -item[1]):
            ws4.append([county, count])

        ws5 = wb.create_sheet('电压等级统计')
        ws5.append(['电压等级', '故障数量'])
        for voltage, count in sorted(voltage_data.items(), key=lambda item: -item[1]):
            ws5.append([voltage, count])

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"统计报表_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.exception('export_statistics_scoped failed')
        return api_error(f'导出失败: {e}', 500)


def get_photos_scoped():
    db = get_db()
    enabled, _ = _photos_project_scope_enabled(db)
    if not enabled:
        return get_photos()

    station_id = request.args.get('station_id', type=int)
    county = request.args.get('county', '').strip()
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()
    page = max(request.args.get('page', default=1, type=int), 1)
    page_size = request.args.get('page_size', default=60, type=int)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    where = ["1=1"]
    params = []
    if station_id:
        where.append("p.station_id = ?")
        params.append(station_id)
    if county:
        where.append("(p.county_hint = ? OR s.county = ?)")
        params.extend([county, county])
    if status in ('matched', 'unmatched', 'ignored'):
        where.append("p.match_status = ?")
        params.append(status)
    if keyword:
        kw = f"%{keyword}%"
        where.append("(p.filename LIKE ? OR p.rel_path LIKE ? OR p.station_hint LIKE ?)")
        params.extend([kw, kw, kw])

    where, params, _, error = _apply_photo_project_filter(
        db,
        where,
        params,
        request.args.get('project'),
    )
    if error:
        return error

    where_sql = " AND ".join(where)
    total_row = db.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM photos p
        LEFT JOIN stations s ON p.station_id = s.id
        WHERE {where_sql}
        """,
        params,
    ).fetchone()
    rows = db.execute(
        f"""
        SELECT p.*, s.name AS station_name, s.county AS station_county, s.voltage_level
        FROM photos p
        LEFT JOIN stations s ON p.station_id = s.id
        WHERE {where_sql}
        ORDER BY p.file_mtime DESC, p.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()
    return api_success({
        'photos': [normalize_photo_row(row) for row in rows],
        'total': total_row['total'] if total_row else 0,
        'page': page,
        'page_size': page_size,
    })


def get_photo_groups_scoped():
    db = get_db()
    enabled, _ = _photos_project_scope_enabled(db)
    if not enabled:
        return get_photo_groups()

    county = request.args.get('county', '').strip()
    station_id = request.args.get('station_id', type=int)
    status = request.args.get('status', '').strip()
    keyword = request.args.get('keyword', '').strip()
    limit_per_group = request.args.get('limit_per_group', default=120, type=int)
    limit_per_group = min(max(limit_per_group, 1), 300)

    where = ["1=1"]
    params = []
    if county:
        where.append("(p.county_hint = ? OR s.county = ?)")
        params.extend([county, county])
    if station_id:
        where.append("p.station_id = ?")
        params.append(station_id)
    if status in ('matched', 'unmatched', 'ignored'):
        where.append("p.match_status = ?")
        params.append(status)
    if keyword:
        kw = f"%{keyword}%"
        where.append("(p.filename LIKE ? OR p.rel_path LIKE ? OR p.station_hint LIKE ?)")
        params.extend([kw, kw, kw])

    where, params, _, error = _apply_photo_project_filter(
        db,
        where,
        params,
        request.args.get('project'),
    )
    if error:
        return error

    rows = db.execute(
        f"""
        SELECT p.*, s.name AS station_name, s.county AS station_county
        FROM photos p
        LEFT JOIN stations s ON p.station_id = s.id
        WHERE {' AND '.join(where)}
        ORDER BY p.match_status DESC, s.name, p.file_mtime DESC, p.id DESC
        """,
        params,
    ).fetchall()

    grouped = {}
    unmatched = []
    for row in rows:
        photo = normalize_photo_row(row)
        if photo['match_status'] == 'matched' and photo.get('station_id'):
            key = str(photo['station_id'])
            if key not in grouped:
                grouped[key] = {
                    'station_id': photo['station_id'],
                    'station_name': photo.get('station_name') or '未知变电站',
                    'county': photo.get('station_county') or photo.get('county_hint') or '',
                    'photos': []
                }
            if len(grouped[key]['photos']) < limit_per_group:
                grouped[key]['photos'].append(photo)
        elif photo['match_status'] == 'unmatched':
            if len(unmatched) < limit_per_group:
                unmatched.append(photo)

    groups = sorted(grouped.values(), key=lambda item: (item['county'], item['station_name']))
    return api_success({
        'groups': groups,
        'unmatched': unmatched,
        'group_count': len(groups),
        'unmatched_count': len([row for row in rows if row['match_status'] == 'unmatched'])
    })


def get_photo_file_scoped(photo_id):
    db = get_db()
    enabled, _ = _photos_project_scope_enabled(db)
    if not enabled:
        return get_photo_file(photo_id)

    if 'user_id' not in session:
        return api_error('请先登录', 401)

    row = db.execute(
        "SELECT id, abs_path, ext, project_id FROM photos WHERE id = ?",
        (photo_id,),
    ).fetchone()
    if not row:
        return api_error('照片不存在', 404)

    if row['project_id']:
        project_row = db.execute("SELECT code FROM projects WHERE id = ?", (row['project_id'],)).fetchone()
        if project_row and not ensure_project_read_access(db, project_row['code']):
            return project_access_denied()

    root = get_photo_root()
    file_path = Path(row['abs_path']).resolve()
    if not is_path_under_root(file_path, root):
        logger.warning(f"Blocked photo traversal attempt: photo_id={photo_id}, path={file_path}")
        return api_error('非法路径访问', 403)
    if not file_path.exists() or not file_path.is_file():
        return api_error('照片文件不存在', 404)

    ext = (row['ext'] or '').lower()
    if ext not in IMAGE_EXTENSIONS:
        return api_error('文件类型不支持', 400)
    return send_file(str(file_path), conditional=True)


def export_statistics_scoped():
    db = get_db()
    fault_report_columns = get_table_columns(db, "fault_reports")
    camera_columns = get_table_columns(db, "cameras")
    if not (
        projects_enabled(db)
        and 'project_id' in fault_report_columns
        and 'project_id' in camera_columns
    ):
        return export_statistics()

    year = request.args.get('year', type=int)
    project_scope, error = build_project_scope(db, request.args.get('project'))
    if error:
        return error

    payload, error = _build_statistics_payload(db, year, request.args.get('project'))
    if error:
        return error
    detail_rows = _build_statistics_detail_rows(db, project_scope, year)

    try:
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Border, Font, PatternFill, Side

        wb = Workbook()
        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='2f67f6', end_color='2f67f6', fill_type='solid')
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        ws1 = wb.active
        ws1.title = '概览'
        overview = [
            ('项目范围', payload['project_scope']['requested_project']),
            ('统计年份', payload['target_year']),
            ('变电站总数', payload['stations']),
            ('当前设备总数', payload['cameras']),
            ('故障总数', payload['faults']),
            ('故障率', f"{payload['fault_rate']:.2f}%"),
            ('本月故障数', payload['faults_this_month']),
            ('本年故障数', payload['faults_this_year']),
        ]
        ws1.append(['指标', '值'])
        for key, value in overview:
            ws1.append([key, value])

        ws2 = wb.create_sheet('KPI')
        ws2.append(['指标', '值'])
        ws2.append(['打开中', payload['kpi']['open_count']])
        ws2.append(['处理中', payload['kpi']['handling_count']])
        ws2.append(['已关闭', payload['kpi']['closed_count']])
        ws2.append(['响应样本数', payload['kpi']['response_sample_count']])
        ws2.append(['关闭样本数', payload['kpi']['close_sample_count']])
        ws2.append(['平均响应时长(秒)', payload['kpi']['avg_response_seconds']])
        ws2.append(['平均关闭时长(秒)', payload['kpi']['avg_close_seconds']])

        ws3 = wb.create_sheet('月度趋势')
        ws3.append(['月份', '故障数量'])
        for item in payload['monthly_trend']:
            ws3.append([item['month'], item['count']])

        ws4 = wb.create_sheet('故障语义统计')
        ws4.append(['semantic_group', '故障类型', '数量'])
        for item in payload['fault_type_distribution']:
            ws4.append([item['semantic_group'], item['fault_label'], item['count']])

        ws5 = wb.create_sheet('县区统计')
        ws5.append(['县区', '故障数量'])
        for item in payload['county_distribution']:
            ws5.append([item['county'], item['count']])

        ws6 = wb.create_sheet('电压等级统计')
        ws6.append(['电压等级', '故障数量'])
        for item in payload['voltage_distribution']:
            ws6.append([item['voltage_level'], item['count']])

        ws7 = wb.create_sheet('故障明细')
        common_columns = [
            ('ID', 'id'),
            ('项目编码', 'project_code'),
            ('项目名称', 'project_name'),
            ('变电站', 'station_name'),
            ('电压等级', 'voltage_level'),
            ('县区', 'county'),
            ('摄像头位置', 'camera_location'),
            ('项目设备编号', 'project_device_code'),
            ('semantic_group', 'semantic_group'),
            ('故障类型编码', 'fault_type_code'),
            ('故障类型名称', 'fault_label'),
            ('描述', 'description'),
            ('状态', 'status'),
            ('报修人', 'reporter_name'),
            ('联系方式', 'reporter_contact'),
            ('创建时间', 'created_at'),
            ('开始处理时间', 'handling_started_at'),
            ('关闭时间', 'closed_at'),
        ]
        admin_columns = [
            ('槽位ID', 'camera_slot_id'),
            ('来源类型', 'source_type'),
            ('来源批次', 'source_batch_id'),
            ('来源幂等键', 'source_record_key'),
            ('原始时间', 'source_time_raw'),
            ('原始时区', 'source_timezone'),
            ('遗留系统类型', 'system_type'),
            ('处理人ID', 'assigned_to'),
            ('处理人账号', 'assigned_to_username'),
            ('历史处理人', 'handler_name'),
            ('处理备注', 'handler_note'),
            ('标签JSON', 'tags_json'),
        ]
        export_columns = common_columns + (admin_columns if session.get('role') == 'admin' else [])
        ws7.append([header for header, _ in export_columns])
        for row in detail_rows:
            ws7.append([row.get(key, '') for _, key in export_columns])

        for sheet in [ws1, ws2, ws3, ws4, ws5, ws6, ws7]:
            for cell in sheet[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.border = thin_border
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = 18

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"statistics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.exception('export_statistics_scoped failed')
        return api_error(f'导出失败: {e}', 500)


app.view_functions['export_statistics'] = export_statistics_scoped
app.view_functions['get_photos'] = get_photos_scoped
app.view_functions['get_photo_groups'] = get_photo_groups_scoped
app.view_functions['get_photo_file'] = get_photo_file_scoped


# ============================================================
# 健康检查
# ============================================================

@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return api_success({'status': 'ok'})

# ============================================================
# 页面路由
# ============================================================

@app.route('/')
def index():
    """首页"""
    return render_template('index.html')

@app.route('/stations')
def stations():
    """变电站列表页"""
    return render_template('stations.html')

@app.route('/fault/new')
def fault_new():
    """故障报修页"""
    return render_template('fault_new.html')

@app.route('/faults')
def faults():
    """故障记录页"""
    return render_template(
        'faults.html',
        is_admin=session.get('role') == 'admin',
        can_edit_tags=bool(session.get('user_id')),
    )

@app.route('/statistics')
def statistics():
    """统计页面"""
    return render_template('statistics.html')

@app.route('/map')
def map_page():
    """地图页面"""
    return render_template('map.html')

@app.route('/photos')
def photos_page():
    """照片分类页"""
    return render_template('photos.html')

@app.route('/login')
def login_page():
    """登录页面"""
    return render_template('login.html')

@app.route('/admin')
def admin_page():
    """管理后台页面"""
    if session.get('role') != 'admin':
        return redirect('/login')
    return render_template('admin.html')

# ============================================================
# 设计变体路由
# ============================================================

@app.route('/design')
def design_variants():
    """设计变体选择页"""
    return render_template('design_variants/index.html')

@app.route('/design/<style>')
def design_variant_index(style):
    """设计变体首页 - 三种风格预览"""
    valid_styles = ['style1', 'style2', 'style3']
    if style not in valid_styles:
        return "Invalid style", 404

    template_map = {
        'style1': 'design_variants/index_style1.html',
        'style2': 'index.html',
        'style3': 'design_variants/index_style3.html'
    }

    return render_template(template_map[style])

@app.route('/design/<style>/stations')
def design_variant_stations(style):
    """设计变体 - 变电站列表页"""
    if style not in ['style1', 'style2', 'style3']:
        return "Invalid style", 404
    return render_template('stations.html')

@app.route('/design/<style>/fault/new')
def design_variant_fault_new(style):
    """设计变体 - 故障报修页"""
    if style not in ['style1', 'style2', 'style3']:
        return "Invalid style", 404
    return render_template('fault_new.html')

@app.route('/design/<style>/faults')
def design_variant_faults(style):
    """设计变体 - 故障记录页"""
    if style not in ['style1', 'style2', 'style3']:
        return "Invalid style", 404
    return render_template(
        'faults.html',
        is_admin=session.get('role') == 'admin',
        can_edit_tags=bool(session.get('user_id')),
    )

@app.route('/design/<style>/statistics')
def design_variant_statistics(style):
    """设计变体 - 统计报表页"""
    if style not in ['style1', 'style2', 'style3']:
        return "Invalid style", 404
    return render_template('statistics.html')

@app.route('/design/<style>/map')
def design_variant_map(style):
    """设计变体 - 地图页"""
    if style not in ['style1', 'style2', 'style3']:
        return "Invalid style", 404
    return render_template('map.html')

@app.route('/design/<style>/photos')
def design_variant_photos(style):
    """设计变体 - 照片页"""
    if style not in ['style1', 'style2', 'style3']:
        return "Invalid style", 404
    return render_template('photos.html')

# ============================================================
# 启动
# ============================================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=Config.DEBUG)
