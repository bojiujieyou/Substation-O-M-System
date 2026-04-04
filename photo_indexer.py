# photo_indexer.py - photo indexing and station/project matching
import os
from datetime import datetime
from pathlib import Path

from config import Config
from utils import create_db_connection


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
KNOWN_COUNTIES = {"丽水", "云和", "庆元", "景宁", "松阳", "缙云", "遂昌", "青田", "龙泉"}


def normalize_name(value):
    if not value:
        return ""
    text = str(value).strip().lower()
    for token in [" ", "_", "-", "（", "）", "(", ")", "【", "】", "[", "]", "变电站", "站"]:
        text = text.replace(token, "")
    return text


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _get_columns(conn, table_name):
    if not _table_exists(conn, table_name):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT NOT NULL UNIQUE,
            abs_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            ext TEXT NOT NULL,
            size_bytes INTEGER,
            file_mtime TEXT,
            county_hint TEXT,
            station_hint TEXT,
            station_id INTEGER,
            match_status TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('matched', 'unmatched', 'ignored')),
            match_method TEXT NOT NULL DEFAULT 'none' CHECK(match_method IN ('name_exact', 'alias', 'manual', 'none')),
            unmatched_reason TEXT,
            captured_at TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS station_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual', 'auto')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(station_id, alias),
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )
        """
    )

    photo_columns = _get_columns(conn, "photos")
    if "project_id" not in photo_columns:
        conn.execute("ALTER TABLE photos ADD COLUMN project_id INTEGER")
    if "project_hint" not in photo_columns:
        conn.execute("ALTER TABLE photos ADD COLUMN project_hint TEXT")


def _load_stations(conn):
    rows = conn.execute("SELECT id, name FROM stations").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "norm": normalize_name(row["name"]),
        }
        for row in rows
    ]


def _load_aliases(conn):
    alias_map = {}
    if not _table_exists(conn, "station_aliases"):
        return alias_map

    rows = conn.execute("SELECT station_id, alias FROM station_aliases").fetchall()
    for row in rows:
        normalized = normalize_name(row["alias"])
        if normalized:
            alias_map[normalized] = row["station_id"]
    return alias_map


def _load_projects(conn):
    if not _table_exists(conn, "projects"):
        return []

    rows = conn.execute(
        """
        SELECT id, code, name, short_name
        FROM projects
        WHERE is_active = 1
        ORDER BY sort_order, id
        """
    ).fetchall()
    projects = []
    for row in rows:
        aliases = {
            normalize_name(row["code"]),
            normalize_name(row["name"]),
            normalize_name(row["short_name"]),
        }
        aliases.discard("")
        projects.append(
            {
                "id": row["id"],
                "code": row["code"],
                "aliases": aliases,
            }
        )
    return projects


def _extract_hints(rel_path, projects):
    rel = rel_path.replace("\\", "/")
    parts = [part for part in rel.split("/") if part]
    county_hint = ""
    station_hint = ""
    project_hint = ""

    for part in parts:
        if part in KNOWN_COUNTIES:
            county_hint = part
            break

    normalized_parts = [(part, normalize_name(part)) for part in parts]
    for _, normalized in normalized_parts:
        if not normalized:
            continue
        for project in projects:
            if normalized in project["aliases"]:
                project_hint = project["code"]
                break
        if project_hint:
            break

    if len(parts) >= 2:
        station_hint = parts[-2]

    return county_hint, station_hint, project_hint


def _match_station(station_hint, stations, alias_map):
    normalized_hint = normalize_name(station_hint)
    if not normalized_hint:
        return None, "none", "empty_hint"

    if normalized_hint in alias_map:
        return alias_map[normalized_hint], "alias", None

    exact_matches = [station for station in stations if station["norm"] == normalized_hint]
    if len(exact_matches) == 1:
        return exact_matches[0]["id"], "name_exact", None

    contains_matches = [
        station
        for station in stations
        if normalized_hint in station["norm"] or station["norm"] in normalized_hint
    ]
    if len(contains_matches) == 1:
        return contains_matches[0]["id"], "name_exact", None

    if len(contains_matches) > 1:
        return None, "none", "ambiguous_station"

    return None, "none", "no_station_match"


def _infer_project_id(conn, station_id, project_hint):
    if not _table_exists(conn, "projects"):
        return None

    if project_hint:
        row = conn.execute(
            "SELECT id FROM projects WHERE code = ?",
            (project_hint,),
        ).fetchone()
        if row:
            return row["id"]

    if not station_id or not _table_exists(conn, "cameras"):
        return None

    camera_columns = _get_columns(conn, "cameras")
    if "project_id" not in camera_columns:
        return None

    query = "SELECT DISTINCT project_id FROM cameras WHERE station_id = ? AND project_id IS NOT NULL"
    params = [station_id]
    if "status" in camera_columns:
        query += " AND status = 'active'"
    rows = conn.execute(query, params).fetchall()
    project_ids = [row["project_id"] for row in rows if row["project_id"] is not None]
    if len(project_ids) == 1:
        return project_ids[0]
    return None


def _iter_photo_files(root_path):
    for base, _, files in os.walk(root_path):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            abs_path = os.path.join(base, filename)
            yield abs_path, filename, ext, ext in IMAGE_EXTENSIONS


def index_photos(conn, full_rebuild=False):
    _ensure_tables(conn)

    root = Path(Config.PHOTO_ROOT_PATH).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"PHOTO_ROOT_PATH 不存在或不是目录: {root}")

    stations = _load_stations(conn)
    alias_map = _load_aliases(conn)
    projects = _load_projects(conn)
    photo_columns = _get_columns(conn, "photos")
    supports_photo_project = {"project_id", "project_hint"}.issubset(photo_columns)

    if full_rebuild:
        conn.execute("DELETE FROM photos")

    seen_rel_paths = set()
    now_text = datetime.now().isoformat(timespec="seconds")
    stats = {
        "total_files": 0,
        "indexed_images": 0,
        "matched": 0,
        "unmatched": 0,
        "ignored_non_image": 0,
    }

    for abs_path, filename, ext, is_image in _iter_photo_files(str(root)):
        stats["total_files"] += 1
        try:
            rel_path = Path(abs_path).resolve().relative_to(root).as_posix()
        except Exception:
            continue

        seen_rel_paths.add(rel_path)

        try:
            stat_result = os.stat(abs_path)
            size_bytes = stat_result.st_size
            file_mtime = datetime.fromtimestamp(stat_result.st_mtime).isoformat(timespec="seconds")
        except OSError:
            size_bytes = None
            file_mtime = None

        county_hint, station_hint, project_hint = _extract_hints(rel_path, projects)

        if not is_image:
            stats["ignored_non_image"] += 1
            if supports_photo_project:
                conn.execute(
                    """
                    INSERT INTO photos (
                        rel_path, abs_path, filename, ext, size_bytes, file_mtime,
                        county_hint, station_hint, station_id, match_status, match_method,
                        unmatched_reason, project_id, project_hint, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'ignored', 'none', 'non_image', ?, ?, ?, ?)
                    ON CONFLICT(rel_path) DO UPDATE SET
                        abs_path = excluded.abs_path,
                        filename = excluded.filename,
                        ext = excluded.ext,
                        size_bytes = excluded.size_bytes,
                        file_mtime = excluded.file_mtime,
                        county_hint = excluded.county_hint,
                        station_hint = excluded.station_hint,
                        station_id = NULL,
                        match_status = 'ignored',
                        match_method = 'none',
                        unmatched_reason = 'non_image',
                        project_id = excluded.project_id,
                        project_hint = excluded.project_hint,
                        last_seen_at = excluded.last_seen_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        rel_path,
                        abs_path,
                        filename,
                        ext,
                        size_bytes,
                        file_mtime,
                        county_hint,
                        station_hint,
                        None,
                        project_hint,
                        now_text,
                        now_text,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO photos (
                        rel_path, abs_path, filename, ext, size_bytes, file_mtime,
                        county_hint, station_hint, station_id, match_status, match_method,
                        unmatched_reason, last_seen_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'ignored', 'none', 'non_image', ?, ?)
                    ON CONFLICT(rel_path) DO UPDATE SET
                        abs_path = excluded.abs_path,
                        filename = excluded.filename,
                        ext = excluded.ext,
                        size_bytes = excluded.size_bytes,
                        file_mtime = excluded.file_mtime,
                        county_hint = excluded.county_hint,
                        station_hint = excluded.station_hint,
                        station_id = NULL,
                        match_status = 'ignored',
                        match_method = 'none',
                        unmatched_reason = 'non_image',
                        last_seen_at = excluded.last_seen_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        rel_path,
                        abs_path,
                        filename,
                        ext,
                        size_bytes,
                        file_mtime,
                        county_hint,
                        station_hint,
                        now_text,
                        now_text,
                    ),
                )
            continue

        station_id, match_method, unmatched_reason = _match_station(station_hint, stations, alias_map)
        match_status = "matched" if station_id else "unmatched"
        project_id = _infer_project_id(conn, station_id, project_hint) if supports_photo_project else None

        if match_status == "matched":
            stats["matched"] += 1
        else:
            stats["unmatched"] += 1
        stats["indexed_images"] += 1

        if supports_photo_project:
            conn.execute(
                """
                INSERT INTO photos (
                    rel_path, abs_path, filename, ext, size_bytes, file_mtime,
                    county_hint, station_hint, station_id, match_status, match_method,
                    unmatched_reason, project_id, project_hint, last_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    abs_path = excluded.abs_path,
                    filename = excluded.filename,
                    ext = excluded.ext,
                    size_bytes = excluded.size_bytes,
                    file_mtime = excluded.file_mtime,
                    county_hint = excluded.county_hint,
                    station_hint = excluded.station_hint,
                    station_id = excluded.station_id,
                    match_status = excluded.match_status,
                    match_method = excluded.match_method,
                    unmatched_reason = excluded.unmatched_reason,
                    project_id = excluded.project_id,
                    project_hint = excluded.project_hint,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    rel_path,
                    abs_path,
                    filename,
                    ext,
                    size_bytes,
                    file_mtime,
                    county_hint,
                    station_hint,
                    station_id,
                    match_status,
                    match_method,
                    unmatched_reason,
                    project_id,
                    project_hint,
                    now_text,
                    now_text,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO photos (
                    rel_path, abs_path, filename, ext, size_bytes, file_mtime,
                    county_hint, station_hint, station_id, match_status, match_method,
                    unmatched_reason, last_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    abs_path = excluded.abs_path,
                    filename = excluded.filename,
                    ext = excluded.ext,
                    size_bytes = excluded.size_bytes,
                    file_mtime = excluded.file_mtime,
                    county_hint = excluded.county_hint,
                    station_hint = excluded.station_hint,
                    station_id = excluded.station_id,
                    match_status = excluded.match_status,
                    match_method = excluded.match_method,
                    unmatched_reason = excluded.unmatched_reason,
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    rel_path,
                    abs_path,
                    filename,
                    ext,
                    size_bytes,
                    file_mtime,
                    county_hint,
                    station_hint,
                    station_id,
                    match_status,
                    match_method,
                    unmatched_reason,
                    now_text,
                    now_text,
                ),
            )

    if seen_rel_paths:
        placeholders = ",".join("?" for _ in seen_rel_paths)
        conn.execute(f"DELETE FROM photos WHERE rel_path NOT IN ({placeholders})", tuple(seen_rel_paths))
    else:
        conn.execute("DELETE FROM photos")

    conn.commit()
    return stats


def get_photo_stats(conn):
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN match_status='matched' THEN 1 ELSE 0 END) AS matched,
            SUM(CASE WHEN match_status='unmatched' THEN 1 ELSE 0 END) AS unmatched,
            SUM(CASE WHEN match_status='ignored' THEN 1 ELSE 0 END) AS ignored
        FROM photos
        """
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "matched": row["matched"] or 0,
        "unmatched": row["unmatched"] or 0,
        "ignored": row["ignored"] or 0,
    }


def list_unmatched(conn, limit=100, offset=0):
    rows = conn.execute(
        """
        SELECT id, rel_path, filename, ext, county_hint, station_hint, unmatched_reason, file_mtime
        FROM photos
        WHERE match_status = 'unmatched'
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def manual_match_photo(conn, photo_id, station_id, alias_text=None):
    station = conn.execute("SELECT id FROM stations WHERE id = ?", (station_id,)).fetchone()
    if not station:
        raise ValueError("变电站不存在")

    photo = conn.execute(
        "SELECT id, station_hint, project_hint FROM photos WHERE id = ?",
        (photo_id,),
    ).fetchone()
    if not photo:
        raise ValueError("照片不存在")

    photo_columns = _get_columns(conn, "photos")
    if {"project_id", "project_hint"}.issubset(photo_columns):
        project_id = _infer_project_id(conn, station_id, photo["project_hint"])
        conn.execute(
            """
            UPDATE photos
            SET station_id = ?,
                project_id = ?,
                match_status = 'matched',
                match_method = 'manual',
                unmatched_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (station_id, project_id, photo_id),
        )
    else:
        conn.execute(
            """
            UPDATE photos
            SET station_id = ?,
                match_status = 'matched',
                match_method = 'manual',
                unmatched_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (station_id, photo_id),
        )

    alias_candidate = alias_text.strip() if isinstance(alias_text, str) else ""
    if not alias_candidate:
        alias_candidate = (photo["station_hint"] or "").strip()

    if alias_candidate:
        conn.execute(
            """
            INSERT INTO station_aliases (station_id, alias, source)
            VALUES (?, ?, 'manual')
            ON CONFLICT(station_id, alias) DO NOTHING
            """,
            (station_id, alias_candidate),
        )

    conn.commit()


def run_full_index(db_path=None):
    target_db = db_path or Config.DATABASE_PATH
    conn = create_db_connection(target_db, row_factory=True)
    try:
        return index_photos(conn, full_rebuild=True)
    finally:
        conn.close()


def run_incremental_index(db_path=None):
    target_db = db_path or Config.DATABASE_PATH
    conn = create_db_connection(target_db, row_factory=True)
    try:
        return index_photos(conn, full_rebuild=False)
    finally:
        conn.close()
