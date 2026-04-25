# auth.py — 用户认证模块
"""
用户登录认证系统
"""
import hashlib
import hmac
import secrets
import time
from datetime import datetime
from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash
from project_access import (
    get_default_project_code,
    get_projects,
    get_user_project_scope_rows,
    get_visible_projects,
    projects_enabled,
    project_scopes_enabled,
)
from utils import get_db

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

PASSWORD_HASH_METHOD = "scrypt"

RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300


def ensure_login_attempts_table(db) -> None:
    """确保 login_attempts 表存在（自修复模式，兼容老数据库）"""
    try:
        db.execute("SELECT 1 FROM login_attempts LIMIT 1")
        return
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass

    db.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            window_start REAL NOT NULL,
            PRIMARY KEY (ip)
        )
    """)
    db.commit()


def rate_limit_check(ip: str, max_attempts: int = RATE_LIMIT_MAX_ATTEMPTS, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> bool:
    """检查是否超过登录尝试次数限制（数据库持久化，失败时放行）"""
    try:
        db = get_db()
        ensure_login_attempts_table(db)
        now = time.time()
        row = db.execute(
            "SELECT attempt_count, window_start FROM login_attempts WHERE ip = ?",
            (ip,),
        ).fetchone()

        if row is None:
            db.execute(
                "INSERT INTO login_attempts (ip, attempt_count, window_start) VALUES (?, 0, ?)",
                (ip, now),
            )
            db.commit()
            return True

        count = row["attempt_count"]
        window_start = row["window_start"]

        if now - window_start > window_seconds:
            db.execute(
                "UPDATE login_attempts SET attempt_count = 0, window_start = ? WHERE ip = ?",
                (now, ip),
            )
            db.commit()
            return True

        if count >= max_attempts:
            return False

        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return True


def rate_limit_record(ip: str) -> None:
    """记录一次失败的登录尝试（数据库持久化，失败时静默）"""
    try:
        db = get_db()
        ensure_login_attempts_table(db)
        now = time.time()

        row = db.execute(
            "SELECT attempt_count, window_start FROM login_attempts WHERE ip = ?",
            (ip,),
        ).fetchone()

        if row is not None and now - row["window_start"] <= RATE_LIMIT_WINDOW_SECONDS:
            db.execute(
                "UPDATE login_attempts SET attempt_count = attempt_count + 1 WHERE ip = ?",
                (ip,),
            )
        elif row is not None:
            db.execute(
                "UPDATE login_attempts SET attempt_count = 1, window_start = ? WHERE ip = ?",
                (now, ip),
            )
        else:
            db.execute(
                "INSERT INTO login_attempts (ip, attempt_count, window_start) VALUES (?, 1, ?)",
                (ip, now),
            )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _hash_legacy_password(password, salt=None):
    """旧版 salt$sha256 哈希，仅用于兼容校验和惰性迁移。"""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def is_legacy_password_hash(stored):
    """识别历史 salt$sha256 格式。"""
    if not isinstance(stored, str):
        return False

    parts = stored.split("$")
    if len(parts) != 2:
        return False

    salt, digest = parts
    return (
        len(salt) == 32
        and len(digest) == 64
        and all(ch in "0123456789abcdef" for ch in salt.lower())
        and all(ch in "0123456789abcdef" for ch in digest.lower())
    )


def password_needs_rehash(stored):
    return is_legacy_password_hash(stored)


def hash_password(password, salt=None):
    """默认生成强哈希；保留 salt 参数以兼容旧格式测试/校验。"""
    if salt is not None:
        return _hash_legacy_password(password, salt)
    return generate_password_hash(password, method=PASSWORD_HASH_METHOD)

def verify_password(password, stored):
    """验证密码"""
    if not isinstance(stored, str) or not stored:
        return False

    if is_legacy_password_hash(stored):
        salt, _ = stored.split("$", 1)
        return hmac.compare_digest(_hash_legacy_password(password, salt), stored)

    try:
        return check_password_hash(stored, password)
    except (TypeError, ValueError):
        return False


def upgrade_password_hash(db, user_id, password):
    """重写为当前强哈希方案。"""
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(password), user_id),
    )
    db.commit()


def verify_and_upgrade_password(db, user, password):
    """兼容旧哈希验证，并在成功登录后完成惰性迁移。"""
    if not verify_password(password, user["password_hash"]):
        return False

    if password_needs_rehash(user["password_hash"]):
        upgrade_password_hash(db, user["id"], password)

    return True


def _admin_required():
    return session.get('role') == 'admin'


def _build_user_payload(db, user):
    projects = get_visible_projects(
        db,
        user_id=user['id'],
        role=user['role'],
        include_inactive=False,
    )
    return {
        'id': user['id'],
        'username': user['username'],
        'role': user['role'],
        'projects': projects,
        'default_project_code': get_default_project_code(projects),
    }

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

    if not user or not verify_and_upgrade_password(db, user, password):
        rate_limit_record(client_ip)
        return jsonify({'error': '用户名或密码错误'}), 401

    # 创建会话
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)

    return jsonify({
        'message': '登录成功',
        'user': _build_user_payload(db, user)
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

    db = get_db()
    user = {
        'id': session.get('user_id'),
        'username': session.get('username'),
        'role': session.get('role')
    }

    return jsonify({
        'user': _build_user_payload(db, user)
    })

# ============================================================
# 用户管理（管理员）
# ============================================================

@auth_bp.route('/users', methods=['GET'])
def list_users():
    """获取用户列表（仅管理员）"""
    if not _admin_required():
        return jsonify({'error': '需要管理员权限'}), 403

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, username, role, created_at FROM users ORDER BY created_at DESC")
    users = [dict(row) for row in cursor.fetchall()]

    return jsonify({'users': users})

@auth_bp.route('/users', methods=['POST'])
def create_user():
    """创建用户（仅管理员）"""
    if not _admin_required():
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
    if not _admin_required():
        return jsonify({'error': '需要管理员权限'}), 403

    if user_id == session.get('user_id'):
        return jsonify({'error': '不能删除自己'}), 400

    db = get_db()
    cursor = db.cursor()

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()

    return jsonify({'message': '用户已删除'})


@auth_bp.route('/users/<int:user_id>/password', methods=['PUT'])
def reset_user_password(user_id):
    """重置用户密码（仅管理员）"""
    if not _admin_required():
        return jsonify({'error': '需要管理员权限'}), 403

    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    if not isinstance(password, str) or not password.strip():
        return jsonify({'error': '新密码不能为空'}), 400

    db = get_db()
    user = db.execute(
        "SELECT id, username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user:
        return jsonify({'error': '用户不存在'}), 404

    upgrade_password_hash(db, user_id, password)

    return jsonify({
        'message': f'已更新用户 {user["username"]} 的密码',
        'user_id': user_id,
    })


@auth_bp.route('/users/<int:user_id>/projects', methods=['GET'])
def get_user_projects(user_id):
    """获取用户项目授权（仅管理员）"""
    if not _admin_required():
        return jsonify({'error': '需要管理员权限'}), 403

    db = get_db()
    user = db.execute(
        "SELECT id, username, role FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user:
        return jsonify({'error': '用户不存在'}), 404

    all_projects = get_projects(db, include_inactive=True)
    if user['role'] == 'admin':
        projects = []
        for project in all_projects:
            item = dict(project)
            item['assigned'] = True
            item['can_write'] = True
            item['inherited_admin'] = True
            projects.append(item)
    else:
        scope_map = {
            row['project_id']: row
            for row in get_user_project_scope_rows(db, user['id'])
        }
        projects = []
        for project in all_projects:
            item = dict(project)
            scope = scope_map.get(project['id'])
            item['assigned'] = scope is not None
            item['can_write'] = bool(scope['can_write']) if scope else False
            item['inherited_admin'] = False
            projects.append(item)

    return jsonify({
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user['role'],
        },
        'multi_project_enabled': projects_enabled(db),
        'project_scope_enabled': project_scopes_enabled(db),
        'projects': projects,
    })


@auth_bp.route('/users/<int:user_id>/projects', methods=['PUT'])
def update_user_projects(user_id):
    """更新用户项目授权（仅管理员）"""
    if not _admin_required():
        return jsonify({'error': '需要管理员权限'}), 403

    db = get_db()
    if not projects_enabled(db) or not project_scopes_enabled(db):
        return jsonify({'error': '当前数据库尚未启用项目授权能力'}), 409

    user = db.execute(
        "SELECT id, username, role FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    if user['role'] == 'admin':
        return jsonify({'error': 'admin 角色不需要单独配置项目授权'}), 400

    data = request.get_json(silent=True) or {}
    raw_projects = data.get('projects')
    if not isinstance(raw_projects, list):
        return jsonify({'error': 'projects 必须是数组'}), 400

    all_projects = get_projects(db, include_inactive=True)
    valid_ids = {project['id'] for project in all_projects}
    project_code_to_id = {project['code']: project['id'] for project in all_projects}

    normalized = []
    seen_project_ids = set()
    for item in raw_projects:
        if not isinstance(item, dict):
            return jsonify({'error': 'projects 数组元素必须是对象'}), 400
        project_id = item.get('project_id')
        if project_id is None and item.get('project_code'):
            project_id = project_code_to_id.get(item.get('project_code'))
        if project_id not in valid_ids:
            return jsonify({'error': f'无效项目: {item.get("project_code") or project_id}'}), 400
        if project_id in seen_project_ids:
            return jsonify({'error': f'重复项目授权: {project_id}'}), 400
        seen_project_ids.add(project_id)
        normalized.append((project_id, 1 if item.get('can_write') else 0))

    db.execute("DELETE FROM user_project_scopes WHERE user_id = ?", (user_id,))
    for project_id, can_write in normalized:
        db.execute(
            """
            INSERT INTO user_project_scopes (user_id, project_id, can_write)
            VALUES (?, ?, ?)
            """,
            (user_id, project_id, can_write),
        )
    db.commit()

    updated_projects = [
        {
            'project_id': project_id,
            'can_write': bool(can_write),
        }
        for project_id, can_write in normalized
    ]
    return jsonify({
        'message': '项目授权已更新',
        'user_id': user_id,
        'projects': updated_projects,
    })
