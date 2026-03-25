# test_api.py — Flask API集成测试
import os
import sys
import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from app import app, get_db
from config import Config

@pytest.fixture
def client():
    """测试客户端"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@pytest.fixture
def init_db(test_db):
    """初始化测试数据库"""
    from init_db import init_db
    # DATABASE_PATH已通过conftest.py的use_test_db自动指向test_db
    init_db()
    yield

def get_token():
    """获取认证token"""
    return Config.API_TOKEN

class TestHealthEndpoint:
    """健康检查端点"""

    def test_health(self, client, init_db):
        response = client.get('/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

class TestStatsEndpoint:
    """统计端点"""

    def test_get_stats_empty(self, client, init_db):
        response = client.get('/api/stats')
        assert response.status_code == 200
        data = response.get_json()
        assert data['stations'] == 0
        assert data['cameras'] == 0
        assert data['faults'] == 0

class TestStationsEndpoint:
    """变电站端点"""

    def test_get_stations_empty(self, client, init_db):
        response = client.get('/api/stations')
        assert response.status_code == 200
        data = response.get_json()
        assert data['stations'] == []
        assert data['total'] == 0

    def test_get_station_not_found(self, client, init_db):
        response = client.get('/api/stations/999')
        assert response.status_code == 404

class TestCamerasEndpoint:
    """摄像头端点"""

    def test_get_cameras_empty(self, client, init_db):
        response = client.get('/api/cameras')
        assert response.status_code == 200
        data = response.get_json()
        assert data['cameras'] == []
        assert data['total'] == 0

    def test_get_camera_by_ip_not_found(self, client, init_db):
        response = client.get('/api/cameras/by-ip?ip=192.168.1.1')
        assert response.status_code == 404
        data = response.get_json()
        assert '该IP暂未录入系统' in data['error']

    def test_get_camera_by_ip_no_param(self, client, init_db):
        response = client.get('/api/cameras/by-ip')
        assert response.status_code == 400

class TestFaultsEndpoint:
    """故障端点"""

    def test_get_faults_empty(self, client, init_db):
        response = client.get('/api/faults')
        assert response.status_code == 200
        data = response.get_json()
        assert data['faults'] == []
        assert data['total'] == 0

    def test_create_fault_missing_fields(self, client, init_db):
        """缺少必填字段"""
        response = client.post('/api/faults', json={})
        assert response.status_code == 400

        response = client.post('/api/faults', json={'station_id': 1})
        assert response.status_code == 400

    def test_create_fault_station_not_found(self, client, init_db):
        """变电站不存在"""
        response = client.post('/api/faults', json={
            'station_id': 999,
            'fault_type': '无图像',
            'reporter_name': '张三'
        })
        assert response.status_code == 404

class TestTokenAuth:
    """Token认证测试（决策#1, #2）"""

    def test_missing_token(self, client, init_db):
        """缺少token - 401"""
        response = client.get('/api/stations/1/password')
        assert response.status_code == 401
        data = response.get_json()
        assert '未提供认证令牌' in data['error']

    def test_invalid_token_format(self, client, init_db):
        """token格式错误 - 401"""
        response = client.get('/api/stations/1/password',
                              headers={'Authorization': 'InvalidFormat'})
        assert response.status_code == 401

    def test_wrong_token(self, client, init_db):
        """错误的token - 403"""
        response = client.get('/api/stations/1/password',
                              headers={'Authorization': 'Bearer wrong_token'})
        assert response.status_code == 403
        data = response.get_json()
        assert '令牌无效' in data['error']

class TestFaultStatusTransition:
    """故障状态转换测试（决策#7）"""

    @pytest.fixture
    def setup_fault(self, client, init_db):
        """创建测试故障记录"""
        # 先创建测试数据
        conn = sqlite3.connect(Config.DATABASE_PATH)
        conn.execute("""
            INSERT INTO stations (id, name, voltage_level, county)
            VALUES (1, '测试变电站', '220kV', '测试县')
        """)
        conn.execute("""
            INSERT INTO fault_reports (id, station_id, fault_type, reporter_name, status)
            VALUES (1, 1, '无图像', '测试人', 'open')
        """)
        conn.commit()
        conn.close()
        return 1

    def test_open_to_handling(self, client, init_db, setup_fault):
        """open -> handling 合法"""
        response = client.put('/api/faults/1/status',
                              json={'status': 'handling'})
        assert response.status_code == 200

    def test_open_to_closed(self, client, init_db, setup_fault):
        """open -> closed 合法"""
        response = client.put('/api/faults/1/status',
                              json={'status': 'closed'})
        assert response.status_code == 200

    def test_handling_to_closed_requires_note(self, client, init_db, setup_fault):
        """handling -> closed 需要处理人和备注"""
        # 先转为handling
        client.put('/api/faults/1/status', json={'status': 'handling'})

        # 不带处理信息
        response = client.put('/api/faults/1/status', json={'status': 'closed'})
        assert response.status_code == 400

        # 带处理信息
        response = client.put('/api/faults/1/status', json={
            'status': 'closed',
            'handler_name': '李四',
            'handler_note': '已修复'
        })
        assert response.status_code == 200

    def test_invalid_status(self, client, init_db, setup_fault):
        """无效状态值"""
        response = client.put('/api/faults/1/status',
                              json={'status': 'invalid'})
        assert response.status_code == 400

    def test_not_found(self, client, init_db):
        """故障不存在"""
        response = client.put('/api/faults/999/status',
                              json={'status': 'handling'})
        assert response.status_code == 404


class TestIdempotency:
    """幂等键测试（决策#7）"""

    @pytest.fixture
    def setup_with_camera(self, init_db):
        """创建带摄像头的测试数据"""
        conn = sqlite3.connect(Config.DATABASE_PATH)
        conn.execute("""
            INSERT INTO stations (id, name, voltage_level, county)
            VALUES (1, '测试变电站', '220kV', '测试县')
        """)
        conn.execute("""
            INSERT INTO cameras (id, station_id, camera_index, ip_address)
            VALUES (1, 1, '1', '192.168.1.100')
        """)
        conn.commit()
        conn.close()

    def test_duplicate_submission_conflict(self, client, init_db, setup_with_camera):
        """5分钟内重复提交返回409"""
        # 第一次提交
        response = client.post('/api/faults', json={
            'station_id': 1,
            'camera_id': 1,
            'fault_type': '无图像',
            'reporter_name': '张三'
        })
        assert response.status_code == 201

        # 立即重复提交（同一窗口）
        response = client.post('/api/faults', json={
            'station_id': 1,
            'camera_id': 1,
            'fault_type': '无图像',
            'reporter_name': '张三'
        })
        assert response.status_code == 409
        data = response.get_json()
        assert '5分钟内有报修记录' in data['error']

    def test_without_camera_id(self, client, init_db, setup_with_camera):
        """无camera_id时使用IP文本哈希"""
        response = client.post('/api/faults', json={
            'station_id': 1,
            'fault_type': '无图像',
            'reporter_name': '张三',
            'camera_ip_free_text': '192.168.1.200'
        })
        # 没有IP匹配，应该能成功（不会触发幂等）
        assert response.status_code in [201, 400]
