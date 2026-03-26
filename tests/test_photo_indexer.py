# test_photo_indexer.py — 照片索引模块单元测试
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


def _insert_station(conn, station_id: int, name: str, county: str = "丽水"):
    conn.execute(
        """
        INSERT INTO stations (id, name, voltage_level, county)
        VALUES (?, ?, '110kV', ?)
        """,
        (station_id, name, county),
    )
    conn.commit()


def test_index_photos_classifies_matched_unmatched_and_ignored(db_conn, photo_root):
    _insert_station(db_conn, 1, "测试变电站")

    _write_file(photo_root / "丽水" / "测试变电站" / "a.jpg", b"img-a")
    _write_file(photo_root / "丽水" / "未知站" / "b.jpg", b"img-b")
    _write_file(photo_root / "丽水" / "测试变电站" / "note.txt", b"text")

    stats = index_photos(db_conn, full_rebuild=True)

    assert stats["total_files"] == 3
    assert stats["indexed_images"] == 2
    assert stats["matched"] == 1
    assert stats["unmatched"] == 1
    assert stats["ignored_non_image"] == 1

    rows = db_conn.execute(
        "SELECT filename, match_status, station_id, match_method, unmatched_reason FROM photos ORDER BY filename"
    ).fetchall()

    by_name = {r["filename"]: dict(r) for r in rows}

    assert by_name["a.jpg"]["match_status"] == "matched"
    assert by_name["a.jpg"]["station_id"] == 1

    assert by_name["b.jpg"]["match_status"] == "unmatched"
    assert by_name["b.jpg"]["station_id"] is None

    assert by_name["note.txt"]["match_status"] == "ignored"
    assert by_name["note.txt"]["unmatched_reason"] == "non_image"


def test_manual_match_persists_alias_for_future_index(db_conn, photo_root):
    _insert_station(db_conn, 1, "桃园变电站")

    _write_file(photo_root / "丽水" / "桃园站" / "first.jpg", b"first")

    index_photos(db_conn, full_rebuild=True)

    row = db_conn.execute(
        "SELECT id FROM photos WHERE filename = 'first.jpg'"
    ).fetchone()
    assert row is not None

    manual_match_photo(db_conn, row["id"], 1, alias_text="桃园站")

    matched_row = db_conn.execute(
        "SELECT station_id, match_status, match_method FROM photos WHERE id = ?",
        (row["id"],),
    ).fetchone()
    assert matched_row["station_id"] == 1
    assert matched_row["match_status"] == "matched"

    alias_row = db_conn.execute(
        "SELECT station_id, alias FROM station_aliases WHERE alias = ?",
        ("桃园站",),
    ).fetchone()
    assert alias_row is not None
    assert alias_row["station_id"] == 1

    _write_file(photo_root / "丽水" / "桃园站" / "second.jpg", b"second")
    index_photos(db_conn, full_rebuild=False)

    second = db_conn.execute(
        "SELECT station_id, match_status, match_method FROM photos WHERE filename = 'second.jpg'"
    ).fetchone()
    assert second is not None
    assert second["station_id"] == 1
    assert second["match_status"] == "matched"
    assert second["match_method"] == "alias"


def test_incremental_index_removes_deleted_files(db_conn, photo_root):
    _insert_station(db_conn, 1, "清水变电站")

    target = photo_root / "丽水" / "清水变电站" / "to-delete.jpg"
    _write_file(target, b"x")

    index_photos(db_conn, full_rebuild=True)
    count_before = db_conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert count_before == 1

    os.remove(target)
    index_photos(db_conn, full_rebuild=False)

    count_after = db_conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert count_after == 0
