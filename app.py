# app.py — Flask应用入口
import os
import sqlite3
import math
import logging
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, g, current_app, session, redirect, render_template, send_file
from config import Config
from admin import admin_bp
from auth import auth_bp
from photo_indexer import IMAGE_EXTENSIONS
from utils import get_db, close_db, init_app

app = Flask(__name__)
app.config.from_object(Config)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())
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
app.register_blueprint(auth_bp)

# 注册utils的teardown
init_app(app)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('station_monitor')

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

    # 验证变电站存在
    station = db.execute("SELECT id, name FROM stations WHERE id = ?",
                         (data['station_id'],)).fetchone()
    if not station:
        return api_error('变电站不存在', 404)

    # 计算幂等键（决策#7）
    # 幂等键 = camera_id + FLOOR(report_time / 300秒)
    camera_id = data.get('camera_id')
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
        cursor = db.execute("""
            INSERT INTO fault_reports (
                station_id, camera_id, fault_type, description,
                reporter_name, reporter_contact, status, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
        """, (
            data['station_id'],
            data.get('camera_id'),
            data['fault_type'],
            data.get('description', ''),
            data['reporter_name'],
            data.get('reporter_contact', ''),
            idempotency_key
        ))
        db.commit()

        fault_id = cursor.lastrowid

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
    status = request.args.get('status')
    station_id = request.args.get('station_id', type=int)
    year = request.args.get('year', type=int)

    # 分页参数
    page = max(request.args.get('page', default=1, type=int), 1)
    page_size = request.args.get('page_size', default=50, type=int)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    # 构建WHERE条件（用于两个查询）
    where_clause = " WHERE 1=1"
    count_where = " WHERE 1=1"
    params = []

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
               c.area as camera_area, c.location_desc as camera_location
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        {where_clause}
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([page_size, offset])

    rows = db.execute(query, params).fetchall()

    return api_success({
        'faults': [dict(row) for row in rows],
        'total': total,
        'page': page,
        'page_size': page_size
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

    fault = db.execute("SELECT id, status FROM fault_reports WHERE id = ?",
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

    # handling→closed需要处理人和备注
    if new_status == 'closed' and current_status == 'handling':
        handler_name = data.get('handler_name')
        handler_note = data.get('handler_note')

        if not handler_name or not handler_note:
            return api_error('关闭故障需要提供处理人姓名和处理备注')

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

    return api_success({'message': f'状态已更新为 {new_status}'})


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
    return render_template('faults.html')

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
# 启动
# ============================================================

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=Config.DEBUG)
