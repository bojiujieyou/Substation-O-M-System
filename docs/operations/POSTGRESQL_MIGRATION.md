# PostgreSQL 切换说明

## 1. 先备份现有 SQLite

当前已手动备份：

- `backups/station_monitor_pre_postgres_20260412_130744.db`

迁移脚本也会再自动备份一次源库。

## 2. 安装依赖

```powershell
pip install -r requirements.txt
```

## 3. 配置环境变量

示例：

```powershell
$env:DATABASE_URL = "postgresql://station_monitor:change_me@127.0.0.1:5432/station_monitor"
$env:APP_DATA_DIR = "E:\项目\变电站图像监控运维平台\data"
```

说明：

- `DATABASE_URL` 用于 PostgreSQL 连接
- `APP_DATA_DIR` 用于上传文件、导入报告等运行时文件
- `DATABASE_PATH` 仍可保留，但 PostgreSQL 模式下主要作为兼容字段

## 4. 初始化 PostgreSQL 空库

```powershell
python init_db.py --force
python init_admin.py --username admin --password Txjk@1234
```

## 5. 将 SQLite 数据迁入 PostgreSQL

```powershell
python migrate_sqlite_to_postgres.py `
  --source .\station_monitor.db `
  --target-url "postgresql://station_monitor:change_me@127.0.0.1:5432/station_monitor" `
  --report .\reports\postgres_migration_20260412.json
```

默认会清空 PostgreSQL `public` schema 后重建。

## 6. 启动应用

```powershell
python app.py
```

或 Docker：

```powershell
docker compose up -d --build
```

## 7. 回退

如果切换后发现问题：

1. 清空 `DATABASE_URL`
2. 恢复使用 SQLite
3. 将 `DATABASE_PATH` 指回原库或备份库
