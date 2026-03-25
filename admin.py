# admin.py — 管理后台模块
"""
变电站数据管理：上传Excel、增删改查
"""
import os
import shutil
import sqlite3
import logging
from datetime import datetime
from functools import wraps
from flask import Blueprint, request, jsonify, g, current_app, session
from werkzeug.utils import secure_filename

logger = logging.getLogger('station_monitor')

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ALLOWED_EXTENSIONS = {'xlsx', 'xls'}

def require_admin(f):
    """管理员权限检查装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated

def get_db():
    """获取数据库连接"""
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE_PATH'])
        g.db.row_factory = sqlite3.Row
    return g.db

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def parse_excel_admin(filepath):
    """解析Excel文件，返回标准化数据"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from parse_excel import parse_station_excel
    return parse_station_excel(filepath)

# ============================================================
# 文件上传
# ============================================================

@admin_bp.route('/upload', methods=['POST'])
@require_admin
def upload_excel():
    """上传Excel文件并导入"""
    if 'file' not in request.files:
        return jsonify({'error': '没有文件'}), 400

    file = request.files['file']
    county = request.form.get('county', '')

    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '只支持xlsx/xls格式'}), 400

    if not county:
        return jsonify({'error': '请选择县区'}), 400

    # 保存上传文件
    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    upload_dir = os.path.join(os.path.dirname(current_app.config['DATABASE_PATH']), 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, f'{timestamp}_{filename}')
    file.save(filepath)

    try:
        # 解析Excel
        data = parse_excel_admin(filepath)
        data['station']['county'] = county

        db = get_db()
        cursor = db.cursor()

        # Upsert变电站
        cursor.execute("""
            INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name, voltage_level) DO UPDATE SET
                county = excluded.county,
                location = excluded.location,
                ip_range = excluded.ip_range,
                nvr_ip = excluded.nvr_ip,
                nvr_port = excluded.nvr_port,
                updated_at = CURRENT_TIMESTAMP
        """, (
            data['station']['name'],
            data['station']['voltage_level'],
            data['station']['county'],
            data['station']['location'],
            data['station']['ip_range'],
            data['station']['nvr_ip'],
            data['station']['nvr_port'],
        ))

        # 获取station_id
        cursor.execute("SELECT id FROM stations WHERE name = ? AND voltage_level = ?",
                       (data['station']['name'], data['station']['voltage_level']))
        station_id = cursor.fetchone()[0]

        # 删除旧摄像头
        cursor.execute("DELETE FROM cameras WHERE station_id = ?", (station_id,))

        # 插入新摄像头
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

        db.commit()
        logger.info(f"Excel imported: station={data['station']['name']}, cameras={cameras_added}, county={county}")

        return jsonify({
            'message': '导入成功',
            'station': data['station']['name'],
            'station_id': station_id,
            'cameras_added': cameras_added
        })

    except Exception as e:
        return jsonify({'error': f'解析失败: {str(e)}'}), 500
    finally:
        # 清理上传文件
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

    cursor.execute("""
        SELECT s.*,
               COUNT(c.id) as camera_count
        FROM stations s
        LEFT JOIN cameras c ON c.station_id = s.id
        GROUP BY s.id
        ORDER BY s.county, s.name
    """)

    stations = [dict(row) for row in cursor.fetchall()]
    return jsonify({'stations': stations})

@admin_bp.route('/stations/<int:station_id>', methods=['DELETE'])
@require_admin
def delete_station(station_id):
    """删除变电站及其摄像头"""
    db = get_db()
    cursor = db.cursor()

    # 检查是否存在
    cursor.execute("SELECT id, name FROM stations WHERE id = ?", (station_id,))
    station = cursor.fetchone()
    if not station:
        return jsonify({'error': '变电站不存在'}), 404

    # 删除摄像头
    cursor.execute("DELETE FROM cameras WHERE station_id = ?", (station_id,))

    # 删除变电站
    cursor.execute("DELETE FROM stations WHERE id = ?", (station_id,))

    db.commit()
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

    cursor.execute("""
        UPDATE stations SET
            name = COALESCE(?, name),
            voltage_level = COALESCE(?, voltage_level),
            county = COALESCE(?, county),
            location = COALESCE(?, location),
            ip_range = COALESCE(?, ip_range),
            nvr_ip = COALESCE(?, nvr_ip),
            nvr_port = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        data.get('name'),
        data.get('voltage_level'),
        data.get('county'),
        data.get('location'),
        data.get('ip_range'),
        data.get('nvr_ip'),
        data.get('nvr_port'),
        station_id
    ))

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
