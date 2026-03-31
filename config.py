# config.py — 变电站图像监控运维平台
import os

class Config:
    # 数据库
    DATABASE_PATH = os.environ.get('DATABASE_PATH', os.path.join(os.path.dirname(__file__), 'station_monitor.db'))

    # SQLite WAL模式配置（决策#6）
    SQLITE_WAL_MODE = True
    SQLITE_BUSY_TIMEOUT = 30000  # 30秒

    # API Token（环境变量存储，决策#1）
    # 必须设置环境变量 API_TOKEN，否则token认证无法工作
    API_TOKEN = os.environ.get('API_TOKEN', '')
    if not API_TOKEN:
        import warnings
        warnings.warn("API_TOKEN environment variable not set — /api/stations/<id>/password will reject all requests")

    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(32).hex())
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')

    # 数据源路径
    DATA_SOURCE_PATH = os.environ.get('DATA_SOURCE_PATH', r'e:\办公\图像监控\图像监控设备资料')

    # 照片目录与索引策略（开发机与fnOS部署均可通过环境变量覆盖）
    PHOTO_ROOT_PATH = os.environ.get('PHOTO_ROOT_PATH', r'e:\办公\图像监控\照片')
    PHOTO_INDEX_CRON_MINUTES = int(os.environ.get('PHOTO_INDEX_CRON_MINUTES', '15'))
