# migrate_photos.py — 一次性照片表结构迁移脚本
import sqlite3
from config import Config


def migrate_photos_schema():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.execute("PRAGMA busy_timeout=30000")
    cursor = conn.cursor()

    cursor.execute("""
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
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS station_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual', 'auto')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(station_id, alias),
            FOREIGN KEY (station_id) REFERENCES stations(id)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_station_status ON photos(station_id, match_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_status_updated ON photos(match_status, updated_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alias_alias ON station_aliases(alias)")

    conn.commit()
    conn.close()
    print(f"照片表结构迁移完成: {Config.DATABASE_PATH}")


if __name__ == '__main__':
    migrate_photos_schema()
