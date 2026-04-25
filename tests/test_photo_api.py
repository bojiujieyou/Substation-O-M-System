# test_photo_api.py — 照片API与受控取图端点测试
import sqlite3
import base64
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from app import app
from config import Config
from init_db import init_db


def _with_csrf(client, kwargs):
    """自动为状态变更请求注入 CSRF token。"""
    headers = dict(kwargs.pop("headers", {}) or {})
    with client.session_transaction() as sess:
        token = sess.get("csrf_token")
    if token:
        headers.setdefault("X-CSRF-Token", token)
    kwargs["headers"] = headers
    return kwargs


def _patch_client(client):
    """给 test client 的状态变更方法自动注入 CSRF token。"""
    _orig_post = client.post
    _orig_put = client.put
    _orig_delete = client.delete

    def _post(url, **kwargs):
        return _orig_post(url, **_with_csrf(client, kwargs))

    def _put(url, **kwargs):
        return _orig_put(url, **_with_csrf(client, kwargs))

    def _delete(url, **kwargs):
        return _orig_delete(url, **_with_csrf(client, kwargs))

    client.post = _post
    client.put = _put
    client.delete = _delete
    return client


PNG_2X2 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP8z/D/PwMDAwMjI2MAAEmWBAOqYh8iAAAAAElFTkSuQmCC"
)


@pytest.fixture
def client(test_db):
    import config as config_module
    original_path = config_module.Config.DATABASE_PATH
    config_module.Config.DATABASE_PATH = test_db
    # Override app.config directly since app.config.from_object(Config)
    # copies DATABASE_PATH at import time (before monkeypatch runs)
    app_original_db_path = app.config['DATABASE_PATH']
    app.config['DATABASE_PATH'] = test_db

    app.config['TESTING'] = True
    init_db()

    with app.test_client() as c:
        _patch_client(c)
        yield c

    config_module.Config.DATABASE_PATH = original_path
    app.config['DATABASE_PATH'] = app_original_db_path


@pytest.fixture
def temp_photo_root(tmp_path, monkeypatch):
    root = tmp_path / "photo-root"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Config, "PHOTO_ROOT_PATH", str(root))
    return root


@pytest.fixture
def auth_client(client, test_db):
    """已登录的测试客户端"""
    import sqlite3
    from auth import hash_password

    # 直接在测试数据库中创建测试用户
    conn = sqlite3.connect(test_db)
    password_hash = hash_password('admin')
    conn.execute("""
        INSERT INTO users (username, password_hash, role)
        VALUES (?, ?, ?)
    """, ('testadmin', password_hash, 'admin'))
    conn.commit()
    conn.close()

    # 登录
    client.post('/auth/login', json={'username': 'testadmin', 'password': 'admin'})
    return client


def _seed_station_and_photo(station_name: str, station_id: int = 1):
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.execute(
        """
        INSERT INTO stations (id, name, voltage_level, county)
        VALUES (?, ?, '110kV', '丽水')
        """,
        (station_id, station_name),
    )
    conn.commit()
    return conn


def _write_test_image(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(32, 128, 224)).save(path, format="PNG")


def test_get_photo_groups_returns_grouped_and_unmatched(client):
    conn = _seed_station_and_photo("白云变电站", station_id=1)

    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method, county_hint)
        VALUES (?, ?, ?, ?, ?, 'matched', 'name_exact', ?)
        """,
        ("丽水/白云变电站/a.jpg", "E:/dummy/a.jpg", "a.jpg", ".jpg", 1, "丽水"),
    )
    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method, county_hint, station_hint, unmatched_reason)
        VALUES (?, ?, ?, ?, NULL, 'unmatched', 'none', ?, ?, 'no_station_match')
        """,
        ("丽水/未知站/b.jpg", "E:/dummy/b.jpg", "b.jpg", ".jpg", "丽水", "未知站"),
    )
    conn.commit()
    conn.close()

    response = client.get('/api/photos/groups')
    assert response.status_code == 200
    data = response.get_json()

    assert data['group_count'] == 1
    assert len(data['groups']) == 1
    assert data['groups'][0]['station_name'] == '白云变电站'
    assert len(data['groups'][0]['photos']) == 1

    assert data['unmatched_count'] == 1
    assert len(data['unmatched']) == 1
    assert data['unmatched'][0]['filename'] == 'b.jpg'


def test_get_photo_groups_can_default_to_fault_stations(client):
    conn = _seed_station_and_photo("白云变电站", station_id=1)
    conn.execute(
        """
        INSERT INTO stations (id, name, voltage_level, county)
        VALUES (?, ?, '110kV', '丽水')
        """,
        (2, "南山变电站"),
    )
    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method, county_hint)
        VALUES
            (?, ?, ?, ?, ?, 'matched', 'name_exact', ?),
            (?, ?, ?, ?, ?, 'matched', 'name_exact', ?)
        """,
        (
            "丽水/白云变电站/a.jpg", "E:/dummy/a.jpg", "a.jpg", ".jpg", 1, "丽水",
            "丽水/南山变电站/b.jpg", "E:/dummy/b.jpg", "b.jpg", ".jpg", 2, "丽水",
        ),
    )
    conn.execute(
        """
        INSERT INTO fault_reports (station_id, fault_type, reporter_name, status)
        VALUES (?, '离线', 'tester', 'open')
        """,
        (1,),
    )
    conn.commit()
    conn.close()

    response = client.get('/api/photos/groups?has_fault=1')
    assert response.status_code == 200
    data = response.get_json()

    assert data['group_count'] == 1
    assert len(data['groups']) == 1
    assert data['groups'][0]['station_id'] == 1
    assert data['groups'][0]['station_name'] == '白云变电站'


def test_get_photo_groups_fault_filter_ignores_closed_faults(client):
    conn = _seed_station_and_photo("白云变电站", station_id=1)
    conn.execute(
        """
        INSERT INTO stations (id, name, voltage_level, county)
        VALUES (?, ?, '110kV', '丽水')
        """,
        (2, "南山变电站"),
    )
    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method, county_hint)
        VALUES
            (?, ?, ?, ?, ?, 'matched', 'name_exact', ?),
            (?, ?, ?, ?, ?, 'matched', 'name_exact', ?)
        """,
        (
            "丽水/白云变电站/a.jpg", "E:/dummy/a.jpg", "a.jpg", ".jpg", 1, "丽水",
            "丽水/南山变电站/b.jpg", "E:/dummy/b.jpg", "b.jpg", ".jpg", 2, "丽水",
        ),
    )
    conn.execute(
        """
        INSERT INTO fault_reports (station_id, fault_type, reporter_name, status)
        VALUES
            (?, '离线', 'tester', 'closed'),
            (?, '离线', 'tester', 'handling')
        """,
        (1, 2),
    )
    conn.commit()
    conn.close()

    response = client.get('/api/photos/groups?has_fault=1')
    assert response.status_code == 200
    data = response.get_json()

    assert data['group_count'] == 1
    assert data['groups'][0]['station_id'] == 2
    assert data['groups'][0]['station_name'] == '南山变电站'


def test_get_photo_file_requires_auth(client, temp_photo_root):
    """未登录时返回401"""
    response = client.get('/photos/file/1')
    assert response.status_code == 401
    data = response.get_json()
    assert '请先登录' in data['error']


def test_get_photo_file_rejects_path_traversal_like_db_row(auth_client, temp_photo_root):
    conn = _seed_station_and_photo("穿越测试站", station_id=1)

    outside = temp_photo_root.parent / "outside.jpg"
    outside.write_bytes(b"outside")

    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method)
        VALUES (?, ?, ?, ?, ?, 'matched', 'manual')
        """,
        ("fake/outside.jpg", str(outside), "outside.jpg", ".jpg", 1),
    )
    photo_id = conn.execute("SELECT id FROM photos WHERE filename='outside.jpg'").fetchone()[0]
    conn.commit()
    conn.close()

    response = auth_client.get(f'/photos/file/{photo_id}')
    assert response.status_code == 403
    data = response.get_json()
    assert '非法路径访问' in data['error']


def test_get_photo_file_returns_image_under_root(auth_client, temp_photo_root):
    conn = _seed_station_and_photo("站内图片测试站", station_id=1)

    image_path = temp_photo_root / "丽水" / "站内图片测试站" / "ok.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = b"\xff\xd8\xff\xd9"
    image_path.write_bytes(image_bytes)

    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method)
        VALUES (?, ?, ?, ?, ?, 'matched', 'name_exact')
        """,
        ("丽水/站内图片测试站/ok.jpg", str(image_path), "ok.jpg", ".jpg", 1),
    )
    photo_id = conn.execute("SELECT id FROM photos WHERE filename='ok.jpg'").fetchone()[0]
    conn.commit()
    conn.close()

    response = auth_client.get(f'/photos/file/{photo_id}')
    assert response.status_code == 200
    assert response.data == image_bytes


def test_get_photo_thumbnail_requires_auth(client):
    response = client.get('/photos/thumb/1')
    assert response.status_code == 401
    data = response.get_json()
    assert '请先登录' in data['error']


def test_get_photo_thumbnail_returns_generated_image(auth_client, temp_photo_root):
    conn = _seed_station_and_photo("Thumbnail Station", station_id=1)

    image_path = temp_photo_root / "Lishui" / "Thumbnail Station" / "thumb.png"
    _write_test_image(image_path)

    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method, file_mtime)
        VALUES (?, ?, ?, ?, ?, 'matched', 'name_exact', ?)
        """,
        ("Lishui/Thumbnail Station/thumb.png", str(image_path), "thumb.png", ".png", 1, str(image_path.stat().st_mtime)),
    )
    photo_id = conn.execute("SELECT id FROM photos WHERE filename='thumb.png'").fetchone()[0]
    conn.commit()
    conn.close()

    response = auth_client.get(f'/photos/thumb/{photo_id}')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('image/')
    assert len(response.data) > 0


def test_get_photo_file_falls_back_to_db_thumbnail_when_source_missing(auth_client, temp_photo_root):
    conn = _seed_station_and_photo("Fallback Station", station_id=1)

    image_path = temp_photo_root / "Lishui" / "Fallback Station" / "fallback.png"
    _write_test_image(image_path)
    file_mtime = str(image_path.stat().st_mtime)

    conn.execute(
        """
        INSERT INTO photos (rel_path, abs_path, filename, ext, station_id, match_status, match_method, file_mtime)
        VALUES (?, ?, ?, ?, ?, 'matched', 'name_exact', ?)
        """,
        ("Lishui/Fallback Station/fallback.png", str(image_path), "fallback.png", ".png", 1, file_mtime),
    )
    photo_id = conn.execute("SELECT id FROM photos WHERE filename='fallback.png'").fetchone()[0]
    conn.commit()
    conn.close()

    thumb_response = auth_client.get(f'/photos/thumb/{photo_id}')
    assert thumb_response.status_code == 200
    thumb_bytes = thumb_response.data
    thumb_type = thumb_response.headers['Content-Type']

    image_path.unlink()

    response = auth_client.get(f'/photos/file/{photo_id}')
    assert response.status_code == 200
    assert response.data == thumb_bytes
    assert response.headers['Content-Type'] == thumb_type
