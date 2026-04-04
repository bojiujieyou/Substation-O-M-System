#!/bin/bash
set -e

echo "=== 变电站监控平台启动 ==="

# 创建必要目录
mkdir -p /app/data

# 如果数据库不存在，初始化
if [ ! -f "$DATABASE_PATH" ]; then
    echo "[初始化] 数据库不存在，正在创建..."
    python init_db.py
    echo "[初始化] 管理员账户..."
    python init_admin.py --username admin --password Txjk@1234
else
    echo "[启动] 数据库已存在: $DATABASE_PATH"
fi

echo "[启动] 应用服务..."
exec "$@"
