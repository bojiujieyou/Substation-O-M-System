# CLAUDE.md — 变电站图像监控运维平台

> 项目：浙江丽水地区变电站图像监控运维平台
> 用途：本文件是AI助手的项目上下文参考

---

## 项目概述

- **类型：** 内部运维工具（Python Flask + SQLite，服务端模板 + 自定义 CSS/JS）
- **用户：** 运维人员（现场）+ 管理人员（监控）
- **目标：** 故障快速报修、摄像头查找、统计报表
- **部署：** Windows 10开发 / fnOS NAS生产环境

---

## 技术栈

| 层次 | 技术 | 路径 |
|------|------|------|
| 后端 | Python + Flask | `app.py`, `admin.py`, `auth.py`, `admin_projects.py`, `admin_fault_types.py`, `admin_notifications.py`, `admin_review.py`, `admin_user_access.py` |
| 数据库 | SQLite (WAL模式) | `station_monitor.db`, `migrations/` |
| 前端 | HTML + 自定义 CSS + 原生 JS | `templates/`, `static/`, `static/design_variants/style2.css`, `static/utils.js`, `static/photos.js` |
| 地图 | Leaflet.js + OpenStreetMap | CDN |
| 图表 | Chart.js | CDN |
| 部署 | Docker + docker-compose | `Dockerfile`, `docker-compose.yml` |

## 项目结构

```
变电站图像监控运维平台/
├── app.py                     # Flask应用入口 + 主API路由
├── admin.py                   # 管理后台总入口
├── auth.py                    # 用户认证API（session + 限流）
├── admin_projects.py          # 项目管理
├── admin_fault_types.py       # 故障类型管理
├── admin_notifications.py     # 通知管理
├── admin_review.py            # 审核队列
├── admin_user_access.py       # 用户项目权限管理
├── project_access.py          # 项目级访问控制
├── notification_runtime.py    # 通知运行时逻辑
├── photo_indexer.py           # 照片索引
├── config.py                  # 配置（密钥、数据库路径）
├── init_db.py                 # 数据库初始化
├── init_admin.py              # 管理员账户初始化
├── parse_excel.py             # Excel解析模块
├── migrations/                # 数据迁移脚本
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── stations.html
│   ├── fault_new.html
│   ├── faults.html
│   ├── statistics.html
│   ├── photos.html
│   ├── map.html
│   ├── login.html
│   ├── admin.html
│   ├── admin_projects.html
│   ├── admin_fault_types.html
│   ├── admin_notifications.html
│   ├── admin_review.html
│   ├── admin_user_access.html
│   └── design_variants/
├── static/
│   ├── design_variants/
│   │   ├── style1.css
│   │   ├── style2.css         # 当前实际生效设计基线
│   │   └── style3.css
│   ├── utils.js               # 全局工具（fetchJson / showToast / 项目联动）
│   ├── photos.js              # 照片页交互
│   ├── app.js
│   ├── bootstrap-5.3.3.bundle.min.js
│   └── chart-4.4.1.min.js
├── tests/                     # pytest 测试集
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 启动方式

```bash
# 1. 初始化数据库（首次）
python init_db.py

# 2. 导入数据（可选，已有数据可跳过）
python import_excel.py

# 3. 创建管理员账户
python init_admin.py

# 4. 启动服务
python app.py
# 访问 http://localhost:5000
# 默认管理员: admin / Txjk@1234
```

## 关键规范

- **API_TOKEN**: 设置环境变量 `set API_TOKEN=your-secret-token`，否则密码API无法工作
- **生产部署**: 使用 `docker-compose up -d`，勿直接运行 `python app.py`（Windows下可能导致数据库重置）
- **测试**: `python -m pytest tests/ -v`（使用独立测试数据库，不影响生产数据）
- **UI基线**: `static/design_variants/style2.css` 是当前唯一设计基线，`static/style.css` 不再作为当前UI参考
- **全局前端工具**: 优先复用 `static/utils.js` 中的 `fetchJson`、`withProject`、`escapeHtml`、`showToast`

---

## 设计系统

**必须阅读 DESIGN.md 后再做任何UI/视觉决策。**

所有字体、颜色、间距、阴影、圆角、动效规范都定义在 `DESIGN.md` 中。
不要偏离DESIGN.md中定义的规范，除非有用户明确授权。
