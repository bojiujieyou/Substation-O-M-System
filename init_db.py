# init_db.py — 数据库初始化脚本
import argparse
import os
import shutil
from datetime import datetime
from config import Config
from utils import create_db_connection, enable_wal_mode

def get_db_path():
    return Config.DATABASE_PATH

def set_wal_mode(conn):
    """启用WAL模式（决策#6）"""
    enable_wal_mode(conn)

def init_db(force=False):
    """初始化数据库结构和索引

    Args:
        force: 如果数据库已存在，是否强制重建（会先备份）
    """
    db_path = get_db_path()

    # 如果数据库已存在，先删除（重新初始化）
    if os.path.exists(db_path):
        if not force:
            print(f"错误：数据库已存在: {db_path}")
            print("运行 'python init_db.py --force' 可以强制重建（会先自动备份）")
            return False

        # 强制模式：先备份
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f'{db_path}.backup_{timestamp}'
        try:
            shutil.copy2(db_path, backup_path)
            print(f"已备份现有数据库到: {backup_path}")
        except Exception as e:
            print(f"警告：备份现有数据库失败: {e}")
            # 继续尝试删除（不阻断）

        os.remove(db_path)
        # 同时删除WAL和SHM文件
        for ext in ('-wal', '-shm'):
            wal_path = db_path + ext
            if os.path.exists(wal_path):
                os.remove(wal_path)

    conn = create_db_connection(db_path, enable_wal=True)
    cursor = conn.cursor()

    # 变电站表
    cursor.execute("""
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
    """)

    # 摄像头表
    cursor.execute("""
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
    """)

    # 故障记录表（决策#7：三状态机）
    cursor.execute("""
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            idempotency_key TEXT UNIQUE,
            FOREIGN KEY (station_id) REFERENCES stations(id),
            FOREIGN KEY (camera_id) REFERENCES cameras(id)
        )
    """)

    # 用户表（简单认证）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'operator' CHECK(role IN ('admin', 'operator')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 照片索引表（仅保存元数据，不保存图片二进制）
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

    # 变电站别名表（用于提升照片匹配率）
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

    # 索引（决策#12）
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_station ON fault_reports(station_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_time ON fault_reports(created_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_idempotency ON fault_reports(idempotency_key);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_station ON cameras(station_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_ip ON cameras(ip_address);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_station_status ON photos(station_id, match_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_status_updated ON photos(match_status, updated_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_alias_alias ON station_aliases(alias);")

    conn.commit()
    conn.close()

    print(f"数据库初始化完成: {db_path}")
    print("- WAL模式已启用")
    print("- 索引已创建: fault_reports(station_id, created_at, idempotency_key), cameras(station_id, ip_address)")
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='初始化数据库')
    parser.add_argument('--force', action='store_true', help='强制重建数据库（会先自动备份现有数据）')
    args = parser.parse_args()

    success = init_db(force=args.force)
    if success is False:
        exit(1)
