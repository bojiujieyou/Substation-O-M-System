import argparse
import os
import shutil
from datetime import datetime

from config import Config
from utils import create_db_connection, enable_wal_mode


def get_db_path():
    return Config.DATABASE_PATH


def set_wal_mode(conn):
    if Config.DATABASE_BACKEND != "postgresql":
        enable_wal_mode(conn)


def _reset_sqlite_database(db_path: str, *, force: bool) -> bool:
    if not os.path.exists(db_path):
        return True
    if not force:
        print(f"错误：数据库已存在 {db_path}")
        print("运行 'python init_db.py --force' 可以强制重建（会先自动备份）")
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    try:
        shutil.copy2(db_path, backup_path)
        print(f"已备份现有数据库到 {backup_path}")
    except Exception as exc:
        print(f"警告：备份现有数据库失败: {exc}")

    os.remove(db_path)
    for ext in ("-wal", "-shm"):
        wal_path = db_path + ext
        if os.path.exists(wal_path):
            os.remove(wal_path)
    return True


def _reset_postgres_database(conn, *, force: bool):
    if not force:
        return
    cursor = conn.cursor()
    cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
    cursor.execute("CREATE SCHEMA public")
    conn.commit()


def init_db(force=False):
    db_path = get_db_path()

    if Config.DATABASE_BACKEND == "postgresql":
        conn = create_db_connection(
            db_path,
            database_url=Config.DATABASE_URL,
            enable_wal=False,
        )
        _reset_postgres_database(conn, force=force)
    else:
        if _reset_sqlite_database(db_path, force=force) is False:
            return False
        conn = create_db_connection(
            db_path,
            database_url=Config.DATABASE_URL,
            enable_wal=True,
        )

    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            voltage_level TEXT,
            county TEXT,
            location TEXT,
            ip_range TEXT,
            nvr_ip TEXT,
            nvr_port INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            latitude REAL,
            longitude REAL,
            UNIQUE(name, voltage_level)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            camera_index TEXT,
            area TEXT,
            location_desc TEXT,
            ip_address TEXT,
            channel_port INTEGER,
            channel_number INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (station_id) REFERENCES stations(id),
            UNIQUE(station_id, camera_index, channel_number)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fault_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            camera_id INTEGER,
            system_type TEXT,
            fault_type TEXT NOT NULL,
            description TEXT,
            reporter_name TEXT,
            reporter_contact TEXT,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'handling', 'closed')),
            handler_name TEXT,
            handler_note TEXT,
            equipment_type TEXT,
            equipment_quantity INTEGER DEFAULT 0,
            deleted_at TIMESTAMP,
            deleted_by INTEGER,
            closed_at TIMESTAMP,
            planned_handle_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            idempotency_key TEXT UNIQUE,
            fault_owner_type TEXT,
            is_batch_impact INTEGER,
            root_cause_type TEXT,
            impact_camera_count INTEGER,
            FOREIGN KEY (station_id) REFERENCES stations(id),
            FOREIGN KEY (camera_id) REFERENCES cameras(id)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS fault_report_cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fault_report_id INTEGER NOT NULL,
            camera_id INTEGER NOT NULL,
            camera_slot_id INTEGER,
            project_id INTEGER,
            project_device_code TEXT,
            camera_label TEXT,
            recovery_state TEXT DEFAULT 'pending' CHECK(recovery_state IN ('pending', 'resolved', 'self_recovered')),
            affects_statistics INTEGER DEFAULT 1,
            detail_fault_reason TEXT,
            detail_resolution TEXT,
            detail_note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fault_report_id, camera_id),
            FOREIGN KEY (fault_report_id) REFERENCES fault_reports(id) ON DELETE CASCADE,
            FOREIGN KEY (camera_id) REFERENCES cameras(id)
        )
        """
    )


    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'operator' CHECK(role IN ('admin', 'operator')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT NOT NULL UNIQUE,
            abs_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            ext TEXT NOT NULL,
            size_bytes INTEGER,
            file_mtime TEXT,
            thumbnail_data BYTEA,
            thumbnail_content_type TEXT,
            thumbnail_width INTEGER,
            thumbnail_height INTEGER,
            thumbnail_source_mtime TEXT,
            thumbnail_generated_at TIMESTAMP,
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

    cursor.execute(
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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_station ON fault_reports(station_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_time ON fault_reports(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_idempotency ON fault_reports(idempotency_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_owner_type ON fault_reports(fault_owner_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_root_cause ON fault_reports(root_cause_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_cameras_fault ON fault_report_cameras(fault_report_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_cameras_camera ON fault_report_cameras(camera_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_cameras_recovery ON fault_report_cameras(recovery_state)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_cameras_affects_stats ON fault_report_cameras(affects_statistics)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_station ON cameras(station_id)")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_ip ON cameras(ip_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_station_status ON photos(station_id, match_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_status_updated ON photos(match_status, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alias_alias ON station_aliases(alias)")

    # 复合索引：优化高频查询
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_status_time ON fault_reports(status, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_station_status ON fault_reports(station_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_station_ip ON cameras(station_id, ip_address)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            ip TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            window_start REAL NOT NULL,
            PRIMARY KEY (ip)
        )
        """
    )

    conn.commit()
    conn.close()

    if Config.DATABASE_BACKEND == "postgresql":
        print(f"数据库初始化完成: {Config.DATABASE_URL}")
    else:
        print(f"数据库初始化完成: {db_path}")
        print("- WAL模式已启用")
    print("- 已创建基础表与索引")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化数据库")
    parser.add_argument("--force", action="store_true", help="强制重建数据库（会先自动备份现有SQLite库，或重置PostgreSQL public schema）")
    args = parser.parse_args()

    success = init_db(force=args.force)
    if success is False:
        raise SystemExit(1)
