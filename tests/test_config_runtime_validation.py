import importlib
import sys
from datetime import timedelta

import pytest

import config as original_config_module


@pytest.fixture(autouse=True)
def restore_config_module():
    original_module = sys.modules.get("config", original_config_module)
    yield
    sys.modules["config"] = original_module


def _reload_config_module(monkeypatch, **env):
    managed_keys = [
        "APP_ENV",
        "FLASK_ENV",
        "STATION_MONITOR_ENV",
        "REQUIRE_STRICT_CONFIG",
        "DATABASE_URL",
        "API_TOKEN",
        "SECRET_KEY",
        "DATABASE_PATH",
        "APP_DATA_DIR",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_SAMESITE",
        "PERMANENT_SESSION_LIFETIME_SECONDS",
    ]
    for key in managed_keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("config", None)
    import config

    return importlib.reload(config)


def test_validate_runtime_config_skips_non_production(monkeypatch):
    config_module = _reload_config_module(
        monkeypatch,
        APP_ENV="development",
    )

    config_module.validate_runtime_config()


def test_validate_runtime_config_requires_secure_values_in_production(monkeypatch):
    config_module = _reload_config_module(
        monkeypatch,
        APP_ENV="production",
    )

    with pytest.raises(RuntimeError) as exc_info:
        config_module.validate_runtime_config()

    message = str(exc_info.value)
    assert "DATABASE_URL" in message
    assert "API_TOKEN" in message
    assert "SECRET_KEY" in message


def test_validate_runtime_config_accepts_complete_production_config(monkeypatch):
    config_module = _reload_config_module(
        monkeypatch,
        APP_ENV="production",
        DATABASE_URL="postgresql://user:pass@127.0.0.1:5432/station_monitor",
        API_TOKEN="token-value",
        SECRET_KEY="super-secret-key",
    )

    config_module.validate_runtime_config()


def test_config_forces_secure_session_cookie_in_production(monkeypatch):
    config_module = _reload_config_module(
        monkeypatch,
        APP_ENV="production",
        DATABASE_URL="postgresql://user:pass@127.0.0.1:5432/station_monitor",
        API_TOKEN="token-value",
        SECRET_KEY="super-secret-key",
        SESSION_COOKIE_SECURE="false",
        PERMANENT_SESSION_LIFETIME_SECONDS="7200",
    )

    assert config_module.Config.IS_PRODUCTION is True
    assert config_module.Config.SESSION_COOKIE_SECURE is True
    assert config_module.Config.PERMANENT_SESSION_LIFETIME == timedelta(hours=2)
    assert config_module.Config.SESSION_COOKIE_HTTPONLY is True
    assert config_module.Config.SESSION_COOKIE_SAMESITE == "Lax"
