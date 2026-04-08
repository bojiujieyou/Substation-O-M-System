import os


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


class Config:
    DATABASE_PATH = os.environ.get(
        'DATABASE_PATH',
        os.path.join(os.path.dirname(__file__), 'station_monitor.db'),
    )

    SQLITE_WAL_MODE = True
    SQLITE_BUSY_TIMEOUT = 30000

    API_TOKEN = os.environ.get('API_TOKEN', '')
    if not API_TOKEN:
        import warnings

        warnings.warn(
            'API_TOKEN environment variable not set - /api/stations/<id>/password will reject all requests'
        )

    SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(32).hex())
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')

    DATA_SOURCE_PATH = os.environ.get(
        'DATA_SOURCE_PATH',
        r'e:\办公\图像监控\图像监控设备资料',
    )

    PHOTO_ROOT_PATH = os.environ.get('PHOTO_ROOT_PATH', r'e:\办公\图像监控\照片')
    PHOTO_INDEX_CRON_MINUTES = int(os.environ.get('PHOTO_INDEX_CRON_MINUTES', '15'))

    MAP_TILE_URL = os.environ.get(
        'MAP_TILE_URL',
        'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    )
    MAP_TILE_ATTRIBUTION = os.environ.get('MAP_TILE_ATTRIBUTION', '高德地图')
    MAP_TILE_SUBDOMAINS = _csv_env('MAP_TILE_SUBDOMAINS', '1,2,3,4')
    MAP_TILE_MAX_ZOOM = int(os.environ.get('MAP_TILE_MAX_ZOOM', '18'))

    MAP_TILE_FALLBACK_URL = os.environ.get(
        'MAP_TILE_FALLBACK_URL',
        'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    )
    MAP_TILE_FALLBACK_ATTRIBUTION = os.environ.get('MAP_TILE_FALLBACK_ATTRIBUTION', 'OpenStreetMap')
    MAP_TILE_FALLBACK_SUBDOMAINS = _csv_env('MAP_TILE_FALLBACK_SUBDOMAINS', 'a,b,c')
    MAP_TILE_FALLBACK_MAX_ZOOM = int(os.environ.get('MAP_TILE_FALLBACK_MAX_ZOOM', '18'))
