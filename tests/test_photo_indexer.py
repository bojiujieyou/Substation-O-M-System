import os
import sqlite3
from pathlib import Path

import pytest

from config import Config
from init_db import init_db
from photo_indexer import index_photos, manual_match_photo


@pytest.fixture
def db_conn(test_db):
    init_db()
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def photo_root(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Config, "PHOTO_ROOT_PATH", str(root))
    return root


def _write_file(path: Path, content: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _insert_station(conn, station_id: int, name: str, county: str = "Lishui"):
    conn.execute(
        """
        INSERT INTO stations (id, name, voltage_level, county)
        VALUES (?, ?, '110kV', ?)
        """,
        (station_id, name, county),
    )
    conn.commit()


def _seed_projects(conn):
    conn.execute(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        INSERT INTO projects (id, code, name, short_name, sort_order, is_active)
        VALUES
            (1, 'unified', 'Unified Platform', 'Unified', 1, 1),
            (2, 'inspection', 'Inspection', 'Inspect', 2, 1)
        """
    )
    conn.commit()


def _enable_camera_project_fields(conn):
    conn.execute("ALTER TABLE cameras ADD COLUMN project_id INTEGER")
    conn.execute("ALTER TABLE cameras ADD COLUMN status TEXT DEFAULT 'active'")
    conn.commit()


def test_index_photos_classifies_matched_unmatched_and_ignored(db_conn, photo_root):
    _insert_station(db_conn, 1, "Alpha Station")

    _write_file(photo_root / "Lishui" / "Alpha Station" / "a.jpg", b"img-a")
    _write_file(photo_root / "Lishui" / "Unknown Station" / "b.jpg", b"img-b")
    _write_file(photo_root / "Lishui" / "Alpha Station" / "note.txt", b"text")

    stats = index_photos(db_conn, full_rebuild=True)

    assert stats["total_files"] == 3
    assert stats["indexed_images"] == 2
    assert stats["matched"] == 1
    assert stats["unmatched"] == 1
    assert stats["ignored_non_image"] == 1

    rows = db_conn.execute(
        "SELECT filename, match_status, station_id, match_method, unmatched_reason FROM photos ORDER BY filename"
    ).fetchall()
    by_name = {row["filename"]: dict(row) for row in rows}

    assert by_name["a.jpg"]["match_status"] == "matched"
    assert by_name["a.jpg"]["station_id"] == 1

    assert by_name["b.jpg"]["match_status"] == "unmatched"
    assert by_name["b.jpg"]["station_id"] is None

    assert by_name["note.txt"]["match_status"] == "ignored"
    assert by_name["note.txt"]["unmatched_reason"] == "non_image"


def test_manual_match_persists_alias_for_future_index(db_conn, photo_root):
    _insert_station(db_conn, 1, "Peach Garden Station")

    _write_file(photo_root / "Lishui" / "Peach Garden Alias" / "first.jpg", b"first")

    index_photos(db_conn, full_rebuild=True)

    row = db_conn.execute(
        "SELECT id FROM photos WHERE filename = 'first.jpg'"
    ).fetchone()
    assert row is not None

    manual_match_photo(db_conn, row["id"], 1, alias_text="Peach Garden Alias")

    matched_row = db_conn.execute(
        "SELECT station_id, match_status, match_method FROM photos WHERE id = ?",
        (row["id"],),
    ).fetchone()
    assert matched_row["station_id"] == 1
    assert matched_row["match_status"] == "matched"

    alias_row = db_conn.execute(
        "SELECT station_id, alias FROM station_aliases WHERE alias = ?",
        ("Peach Garden Alias",),
    ).fetchone()
    assert alias_row is not None
    assert alias_row["station_id"] == 1

    _write_file(photo_root / "Lishui" / "Peach Garden Alias" / "second.jpg", b"second")
    index_photos(db_conn, full_rebuild=False)

    second = db_conn.execute(
        "SELECT station_id, match_status, match_method FROM photos WHERE filename = 'second.jpg'"
    ).fetchone()
    assert second is not None
    assert second["station_id"] == 1
    assert second["match_status"] == "matched"
    assert second["match_method"] == "alias"


def test_incremental_index_removes_deleted_files(db_conn, photo_root):
    _insert_station(db_conn, 1, "Clear Water Station")

    target = photo_root / "Lishui" / "Clear Water Station" / "to-delete.jpg"
    _write_file(target, b"x")

    index_photos(db_conn, full_rebuild=True)
    count_before = db_conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert count_before == 1

    os.remove(target)
    index_photos(db_conn, full_rebuild=False)

    count_after = db_conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert count_after == 0


def test_index_photos_persists_project_hint_and_project_id(db_conn, photo_root):
    _seed_projects(db_conn)
    _insert_station(db_conn, 1, "Bravo Station")

    _write_file(photo_root / "unified" / "Lishui" / "Bravo Station" / "alpha.jpg", b"img")

    stats = index_photos(db_conn, full_rebuild=True)
    assert stats["matched"] == 1

    row = db_conn.execute(
        """
        SELECT station_id, project_id, project_hint, match_status
        FROM photos
        WHERE filename = 'alpha.jpg'
        """
    ).fetchone()
    assert row is not None
    assert row["station_id"] == 1
    assert row["project_hint"] == "unified"
    assert row["project_id"] == 1
    assert row["match_status"] == "matched"


def test_manual_match_photo_backfills_project_id_from_unique_station_project(db_conn, photo_root):
    _seed_projects(db_conn)
    _enable_camera_project_fields(db_conn)
    _insert_station(db_conn, 1, "Charlie Station")
    db_conn.execute(
        """
        INSERT INTO cameras (
            station_id, camera_index, area, location_desc, ip_address, channel_port, channel_number, project_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "cam-1", "Area A", "North Gate", "10.0.0.1", 8000, 1, 2, "active"),
    )
    db_conn.commit()

    _write_file(photo_root / "misc" / "Charlie Alias" / "manual.jpg", b"img")
    index_photos(db_conn, full_rebuild=True)

    photo = db_conn.execute(
        "SELECT id, project_id, match_status FROM photos WHERE filename = 'manual.jpg'"
    ).fetchone()
    assert photo is not None
    assert photo["project_id"] is None
    assert photo["match_status"] == "unmatched"

    manual_match_photo(db_conn, photo["id"], 1, alias_text="Charlie Alias")

    matched = db_conn.execute(
        """
        SELECT station_id, project_id, project_hint, match_status, match_method
        FROM photos
        WHERE id = ?
        """,
        (photo["id"],),
    ).fetchone()
    assert matched is not None
    assert matched["station_id"] == 1
    assert matched["project_id"] == 2
    assert matched["project_hint"] == ""
    assert matched["match_status"] == "matched"
    assert matched["match_method"] == "manual"
