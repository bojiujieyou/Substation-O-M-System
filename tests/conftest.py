import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_station_monitor.db"
    yield str(db_path)

    for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if candidate.exists():
            candidate.unlink()


@pytest.fixture(autouse=True)
def use_test_db(test_db, monkeypatch):
    import config

    monkeypatch.setattr(config.Config, "DATABASE_PATH", test_db)
