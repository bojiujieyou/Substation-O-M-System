# auth.py — 用户认证模块
"""
用户登录认证系统
"""
import hashlib
import secrets
import sqlite3
import time
from datetime import datetime
from flask import Blueprint, request, jsonify, g, session

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# 简单的内存限流（生产环境建议用Redis）
_login_attempts = {}  # {ip: (count, reset_time)}

def rate_limit_check(ip, max_attempts=5, window_seconds=300):
    """检查是否超过登录尝试次数限制"""
    now = time.time()
    if ip in _login_attempts:
        count, reset_time = _login_attempts[ip]
        if now > reset_time:
            _login_attempts[ip] = (0, now + window_seconds)
        elif count >= max_attempts:
            return False
    else:
        _login_attempts[ip] = (0, now + window_seconds)
    return True

def rate_limit_record(ip):
    """记录一次失败的登录尝试"""
    now = time.time()
    if ip in _login_attempts:
        count, reset_time = _login_attempts[ip]
        if now > reset_time:
            count = 0
            reset_time = now + 300
        _login_attempts[ip] = (count + 1, reset_time)
    else:
        _login_attempts[ip] = (1, now + 300)

def get_db():
    """获取数据库连接"""
    from flask import current_app
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE_PATH'])
        g.db.row_factory = sqlite3.Row
    return g.db

def hash_password(password, salt=None):
    """密码哈希（使用SHA256+盐）"""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"

def verify_password(password, stored):
    """验证密码"""
    try:
        salt, _ = stored.split('$')
        return hash_password(password, salt) == stored
    except:
        return False

# ============================================================
# 登录
# ============================================================

@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    # 限流检查
    client_ip = request.remote_addr or '127.0.0.1'
    if not rate_limit_check(client_ip):
        return jsonify({'error': '登录尝试次数过多，请在5分钟后重试'}), 429

    data = request.get_json()
    if not data:
        return jsonify({'error': '无效请求'}), 400

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()

    if not user or not verify_password(password, user['password_hash']):
        rate_limit_record(client_ip)
        return jsonify({'error': '用户名或密码错误'}), 401

    # 创建会话
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']

    return jsonify({
        'message': '登录成功',
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user['role']
        }
    })

# ============================================================
# 登出
# ============================================================

@auth_bp.route('/logout', methods=['POST'])
def logout():
    """用户登出"""
    session.clear()
    return jsonify({'message': '已退出登录'})

# ============================================================
# 当前用户
# ============================================================

@auth_bp.route('/me', methods=['GET'])
def me():
    """获取当前登录用户"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': '未登录'}), 401

    return jsonify({
        'user': {
            'id': session.get('user_id'),
            'username': session.get('username'),
            'role': session.get('role')
        }
    })

# ============================================================
# 用户管理（管理员）
# ============================================================

@auth_bp.route('/users', methods=['GET'])
def list_users():
    """获取用户列表（仅管理员）"""
    if session.get('role') != 'admin':
        return jsonify({'error': '需要管理员权限'}), 403

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, username, role, created_at FROM users ORDER BY created_at DESC")
    users = cursor.fetchall()

    return jsonify({'users': users})

@auth_bp.route('/users', methods=['POST'])
def create_user():
    """创建用户（仅管理员）"""
    if session.get('role') != 'admin':
        return jsonify({'error': '需要管理员权限'}), 403

    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'operator')

    if not username or not password:
        return jsonify({'error': '用户名和密码不能为空'}), 400

    if role not in ('admin', 'operator'):
        role = 'operator'

    db = get_db()
    cursor = db.cursor()

    # 检查用户名是否已存在
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        return jsonify({'error': '用户名已存在'}), 409

    # 创建用户
    password_hash = hash_password(password)
    cursor.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        (username, password_hash, role, datetime.now().isoformat())
    )
    db.commit()

    return jsonify({'message': '用户创建成功', 'user_id': cursor.lastrowid}), 201

@auth_bp.route('/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    """删除用户（仅管理员）"""
    if session.get('role') != 'admin':
        return jsonify({'error': '需要管理员权限'}), 403

    if user_id == session.get('user_id'):
        return jsonify({'error': '不能删除自己'}), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()

    return jsonify({'message': '用户已删除'})
