#!/bin/bash
set -e

echo "=== 变电站监控平台启动 ==="

mkdir -p "${APP_DATA_DIR:-/app/data}"

if [ -n "$DATABASE_URL" ]; then
    echo "[启动] 检测到 PostgreSQL DATABASE_URL，初始化/校验数据库结构..."
    python init_db.py
    echo "[启动] 初始化管理员账户..."
    python init_admin.py --username admin --password Txjk@1234
elif [ ! -f "$DATABASE_PATH" ]; then
    echo "[初始化] SQLite 数据库不存在，正在创建..."
    python init_db.py
    echo "[初始化] 初始化管理员账户..."
    python init_admin.py --username admin --password Txjk@1234
else
    echo "[启动] SQLite 数据库已存在: $DATABASE_PATH"
fi

echo "[启动] 应用服务..."
exec "$@"
