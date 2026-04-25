#!/bin/bash
set -e

echo "=== Station monitor platform startup ==="

mkdir -p "${APP_DATA_DIR:-/app/data}"

if [ -n "$DATABASE_URL" ]; then
    echo "[startup] PostgreSQL detected; initializing/verifying database schema..."
    python init_db.py
elif [ ! -f "$DATABASE_PATH" ]; then
    echo "[init] SQLite database not found; creating..."
    python init_db.py
else
    echo "[startup] SQLite database already exists: $DATABASE_PATH"
fi

echo "[startup] Reconciling admin initialization..."
python init_admin.py --username admin

echo "[startup] Launching application..."
exec "$@"
