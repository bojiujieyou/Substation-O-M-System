# app.py — Flask应用入口
import os
import sqlite3
import math
import logging
from functools import wraps
from flask import Flask, request, jsonify, g, session, redirect
from config import Config
from admin import admin_bp
from auth import auth_bp

app = Flask(__name__)
app.config.from_object(Config)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['SESSION_COOKIE_SECURE'] = True
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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('station_monitor')

# ============================================================
# 数据库连接管理
# ============================================================

def get_db():
    """获取数据库连接（请求级）"""
    if 'db' not in g:
        g.db = sqlite3.connect(Config.DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
        # WAL模式已在init_db中设置，这里仅确保
        g.db.execute("PRAGMA busy_timeout=30000")
    return g.db

@app.teardown_appcontext
def close_db(exception):
    """请求结束后关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

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

# ============================================================
# API: 统计概览
# ============================================================

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """首页统计数据"""
    db = get_db()

    # 变电站数量
    station_count = db.execute("SELECT COUNT(*) as count FROM stations").fetchone()['count']

    # 摄像头数量
    camera_count = db.execute("SELECT COUNT(*) as count FROM cameras").fetchone()['count']

    # 故障报修数量（所有）
    fault_count = db.execute("SELECT COUNT(*) as count FROM fault_reports").fetchone()['count']

    # 本月故障数量
    fault_this_month = db.execute("""
        SELECT COUNT(*) as count FROM fault_reports
        WHERE strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')
    """).fetchone()['count']

    return api_success({
        'stations': station_count,
        'cameras': camera_count,
        'faults': fault_count,
        'faults_this_month': fault_this_month
    })

# ============================================================
# API: 变电站列表
# ============================================================

@app.route('/api/stations', methods=['GET'])
def get_stations():
    """获取变电站列表"""
    db = get_db()

    # 支持按县区筛选
    county = request.args.get('county')
    query = "SELECT id, name, voltage_level, county, location FROM stations"
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
    """获取故障记录列表"""
    db = get_db()

    # 支持筛选
    status = request.args.get('status')
    station_id = request.args.get('station_id', type=int)

    query = """
        SELECT f.*, s.name as station_name, s.voltage_level,
               c.area as camera_area, c.location_desc as camera_location
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        LEFT JOIN cameras c ON f.camera_id = c.id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND f.status = ?"
        params.append(status)

    if station_id:
        query += " AND f.station_id = ?"
        params.append(station_id)

    query += " ORDER BY f.created_at DESC"

    rows = db.execute(query, params).fetchall()

    return api_success({
        'faults': [dict(row) for row in rows],
        'total': len(rows)
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
# 健康检查
# ============================================================

@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return api_success({'status': 'ok'})

# ============================================================
# 页面路由
# ============================================================

from flask import render_template

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
