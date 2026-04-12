import argparse
import hashlib
import os
import secrets

from config import Config
from project_access import table_exists
from utils import create_db_connection


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${digest}"


def init_admin(username="admin", password="Txjk@1234"):
    db_path = os.environ.get(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(__file__), "station_monitor.db"),
    )

    if Config.DATABASE_BACKEND != "postgresql" and not os.path.exists(db_path):
        print("错误: 数据库不存在，请先运行 python init_db.py")
        return False

    conn = create_db_connection(db_path, database_url=Config.DATABASE_URL)
    cursor = conn.cursor()

    if not table_exists(conn, "users"):
        print("错误: users 表不存在，请先运行 python init_db.py")
        conn.close()
        return False

    cursor.execute("SELECT id FROM users WHERE role = 'admin'")
    existing = cursor.fetchone()
    if existing:
        print(f"管理员已存在 (id={existing[0]})，跳过创建")
        conn.close()
        return True

    password_hash = hash_password(password)
    cursor.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
        (username, password_hash),
    )
    conn.commit()
    conn.close()

    print("管理员账户创建成功")
    print(f"  用户名: {username}")
    print(f"  密码: {password}")
    print("  提示: 请立即修改默认密码")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化管理员账户")
    parser.add_argument("--username", default="admin", help="管理员用户名")
    parser.add_argument("--password", default="Txjk@1234", help="管理员密码")
    args = parser.parse_args()

    init_admin(args.username, args.password)
