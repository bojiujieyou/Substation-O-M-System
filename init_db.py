# init_db.py — 数据库初始化脚本
import sqlite3
import os
from config import Config

def get_db_path():
    return Config.DATABASE_PATH

def set_wal_mode(conn):
    """启用WAL模式（决策#6）"""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")

def init_db():
    """初始化数据库结构和索引"""
    db_path = get_db_path()

    # 如果数据库已存在，先删除（重新初始化）
    if os.path.exists(db_path):
        os.remove(db_path)
        # 同时删除WAL和SHM文件
        for ext in ('-wal', '-shm'):
            wal_path = db_path + ext
            if os.path.exists(wal_path):
                os.remove(wal_path)

    conn = sqlite3.connect(db_path)
    set_wal_mode(conn)
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
            fault_type TEXT NOT NULL,
            description TEXT,
            reporter_name TEXT,
            reporter_contact TEXT,
            status TEXT DEFAULT 'open' CHECK(status IN ('open', 'handling', 'closed')),
            handler_name TEXT,
            handler_note TEXT,
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

    # 索引（决策#12）
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_station ON fault_reports(station_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_report_time ON fault_reports(created_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fault_idempotency ON fault_reports(idempotency_key);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_station ON cameras(station_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_camera_ip ON cameras(ip_address);")

    conn.commit()
    conn.close()

    print(f"数据库初始化完成: {db_path}")
    print("- WAL模式已启用")
    print("- 索引已创建: fault_reports(station_id, created_at, idempotency_key), cameras(station_id, ip_address)")

if __name__ == '__main__':
    init_db()
