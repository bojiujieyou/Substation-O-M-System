import argparse
import os

from auth import hash_password
from config import Config
from project_access import table_exists
from utils import create_db_connection


def _get_init_password(cli_password=None):
    if cli_password is not None:
        return cli_password or None
    env_password = os.environ.get("INIT_ADMIN_PASSWORD", "")
    return env_password or None


def init_admin(username="admin", password=None):
    password = _get_init_password(password)
    db_path = os.environ.get(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(__file__), "station_monitor.db"),
    )

    if Config.DATABASE_BACKEND != "postgresql" and not os.path.exists(db_path):
        print("Error: database does not exist; run `python init_db.py` first.")
        return False

    conn = create_db_connection(db_path, database_url=Config.DATABASE_URL)
    cursor = conn.cursor()

    try:
        if not table_exists(conn, "users"):
            print("Error: users table does not exist; run `python init_db.py` first.")
            return False

        cursor.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        existing = cursor.fetchone()
        if existing:
            print(f"Admin already exists (id={existing[0]}); skipping initialization.")
            return True

        if not password:
            print(
                "Error: no admin user exists yet, so INIT_ADMIN_PASSWORD must be set "
                "for initial admin creation."
            )
            return False

        print("No admin user found; creating initial admin account.")
        password_hash = hash_password(password)
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (username, password_hash),
        )
        conn.commit()

        print("Admin account created successfully.")
        print(f"  Username: {username}")
        print(f"  Password: {password}")
        print("  Reminder: change the default password immediately.")
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize the admin account")
    parser.add_argument("--username", default="admin", help="Admin username")
    parser.add_argument(
        "--password",
        default=None,
        help="Admin password; falls back to INIT_ADMIN_PASSWORD when omitted",
    )
    args = parser.parse_args()

    raise SystemExit(0 if init_admin(args.username, args.password) else 1)
