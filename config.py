import os
from datetime import timedelta

from db import get_database_backend, get_default_app_data_dir


def _is_truthy(value: str | None) -> bool:
    return str(value or '').strip().lower() in ('true', '1', 'yes', 'on')


def is_production_environment() -> bool:
    explicit_env = (
        os.environ.get('APP_ENV')
        or os.environ.get('FLASK_ENV')
        or os.environ.get('STATION_MONITOR_ENV')
        or ''
    ).strip().lower()
    if explicit_env in ('production', 'prod'):
        return True
    if explicit_env in ('development', 'dev', 'test', 'testing', 'local'):
        return False
    return _is_truthy(os.environ.get('REQUIRE_STRICT_CONFIG'))


def validate_runtime_config() -> None:
    if not is_production_environment():
        return

    missing = []
    if not Config.DATABASE_URL:
        missing.append('DATABASE_URL')
    if not Config.API_TOKEN:
        missing.append('API_TOKEN')

    raw_secret_key = (os.environ.get('SECRET_KEY', '') or '').strip()
    if not raw_secret_key or raw_secret_key.lower() in {'change_me', 'changeme', 'secret', 'default'}:
        missing.append('SECRET_KEY')

    if missing:
        raise RuntimeError(
            'Refusing to start in production without required secure configuration: '
            + ', '.join(missing)
        )


def _session_lifetime() -> timedelta:
    raw_value = (os.environ.get('PERMANENT_SESSION_LIFETIME_SECONDS') or '28800').strip()
    try:
        seconds = int(raw_value)
    except ValueError as exc:
        raise RuntimeError('PERMANENT_SESSION_LIFETIME_SECONDS must be a positive integer') from exc

    if seconds <= 0:
        raise RuntimeError('PERMANENT_SESSION_LIFETIME_SECONDS must be a positive integer')

    return timedelta(seconds=seconds)


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


class Config:
    BASE_DIR = os.path.dirname(__file__)
    DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
    DATABASE_PATH = os.environ.get(
        'DATABASE_PATH',
        os.path.join(BASE_DIR, 'station_monitor.db'),
    )
    DATABASE_BACKEND = get_database_backend(database_url=DATABASE_URL)
    APP_DATA_DIR = os.environ.get(
        'APP_DATA_DIR',
        get_default_app_data_dir(BASE_DIR, database_url=DATABASE_URL, database_path=DATABASE_PATH),
    )

    SQLITE_WAL_MODE = True
    SQLITE_BUSY_TIMEOUT = 30000

    API_TOKEN = os.environ.get('API_TOKEN', '')
    if not API_TOKEN:
        import warnings

        warnings.warn(
            'API_TOKEN environment variable not set - /api/stations/<id>/password will reject all requests'
        )

    _SECRET_KEY_ENV = os.environ.get('SECRET_KEY', '')
    if _SECRET_KEY_ENV:
        SECRET_KEY = _SECRET_KEY_ENV
    else:
        import warnings
        warnings.warn(
            'SECRET_KEY not set — sessions will invalidate on every restart. '
            'Set SECRET_KEY in production to avoid this.'
        )
        SECRET_KEY = os.urandom(32).hex()

    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 'yes')
    IS_PRODUCTION = is_production_environment()
    PERMANENT_SESSION_LIFETIME = _session_lifetime()
    SESSION_COOKIE_SECURE = True if IS_PRODUCTION else _is_truthy(os.environ.get('SESSION_COOKIE_SECURE'))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax').strip() or 'Lax'

    DATA_SOURCE_PATH = os.environ.get('DATA_SOURCE_PATH', '')

    PHOTO_ROOT_PATH = os.environ.get('PHOTO_ROOT_PATH', '')
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
