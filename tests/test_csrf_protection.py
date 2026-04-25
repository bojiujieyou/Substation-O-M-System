"""CSRF 保护测试 — 验证双重提交 Cookie 模式"""
import pytest

from app import app
from init_db import init_db


@pytest.fixture
def csrf_client(tmp_path):
    import config as config_module

    db_path = str(tmp_path / "test_csrf.db")
    original_path = config_module.Config.DATABASE_PATH
    original_app_path = app.config["DATABASE_PATH"]
    config_module.Config.DATABASE_PATH = db_path
    app.config["DATABASE_PATH"] = db_path
    app.config["TESTING"] = True

    init_db(force=True)

    # 插入测试用户
    from utils import create_db_connection
    from werkzeug.security import generate_password_hash

    conn = create_db_connection(db_path)
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        ("testadmin", generate_password_hash("adminpass", method="scrypt"), "admin"),
    )
    conn.commit()
    conn.close()

    with app.test_client() as c:
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config["DATABASE_PATH"] = original_app_path


def _login(client):
    return client.post(
        "/auth/login",
        json={"username": "testadmin", "password": "adminpass"},
    )


# ============================================================
# 基本行为
# ============================================================


def test_get_requests_not_blocked_by_csrf(csrf_client):
    """GET 请求不需要 CSRF token"""
    resp = csrf_client.get("/")
    assert resp.status_code != 403


def test_post_without_csrf_token_rejected(csrf_client):
    """未登录时 POST 没有 token 应当被拒绝（403 或 401）"""
    resp = csrf_client.post("/auth/users", json={"username": "x", "password": "y"})
    # 未登录走 guest 限制返回 302 或 401；如果有 csrf_token 检查则 403
    assert resp.status_code in (302, 401, 403)


def test_login_endpoint_exempt_from_csrf(csrf_client):
    """登录接口本身应豁免 CSRF"""
    resp = _login(csrf_client)
    assert resp.status_code == 200
    assert "user" in resp.get_json()


def test_logged_in_post_without_csrf_rejected(csrf_client):
    """登录后 POST 不带 X-CSRF-Token 应被拒绝"""
    _login(csrf_client)
    resp = csrf_client.post(
        "/auth/users",
        json={"username": "newuser", "password": "newpass"},
    )
    assert resp.status_code == 403
    assert "CSRF" in resp.get_json().get("error", "") or "csrf" in resp.get_json().get("error", "").lower()


def test_logged_in_post_with_valid_csrf_accepted(csrf_client):
    """登录后带正确 CSRF token 的请求应通过 CSRF 检查"""
    login_resp = _login(csrf_client)
    assert login_resp.status_code == 200

    # 从 session 获取 csrf_token
    with csrf_client.session_transaction() as sess:
        csrf_token = sess.get("csrf_token")

    assert csrf_token is not None, "登录后 session 中应有 csrf_token"

    resp = csrf_client.post(
        "/auth/users",
        json={"username": "newuser", "password": "newpass123"},
        headers={"X-CSRF-Token": csrf_token},
    )
    # 可能是 201（创建成功）、403（权限不足）、409（已存在）等
    # 但不应该是 403 CSRF 相关的错误
    if resp.status_code == 403:
        data = resp.get_json()
        assert "CSRF" not in data.get("error", ""), "不应因 CSRF 被拒绝"


def test_logged_in_post_with_wrong_csrf_rejected(csrf_client):
    """登录后带错误 CSRF token 的请求应被拒绝"""
    _login(csrf_client)

    resp = csrf_client.post(
        "/auth/users",
        json={"username": "newuser", "password": "newpass123"},
        headers={"X-CSRF-Token": "wrong_token_value"},
    )
    assert resp.status_code == 403


def test_csrf_token_created_on_login(csrf_client):
    """登录后 session 中自动创建 csrf_token"""
    _login(csrf_client)
    with csrf_client.session_transaction() as sess:
        token = sess.get("csrf_token")
    assert token is not None
    assert len(token) == 64  # token_hex(32) -> 64 chars
