# CLAUDE.md — 变电站图像监控运维平台

> 项目：浙江丽水地区变电站图像监控运维平台
> 用途：本文件是AI助手的项目上下文参考

---

## 项目概述

- **类型：** 内部运维工具（Python Flask + SQLite + Bootstrap）
- **用户：** 运维人员（现场）+ 管理人员（监控）
- **目标：** 故障快速报修、摄像头查找、统计报表
- **部署：** Windows 10开发 / fnOS NAS生产环境

---

## 技术栈

| 层次 | 技术 | 路径 |
|------|------|------|
| 后端 | Python + Flask | `app.py`, `admin.py`, `auth.py` |
| 数据库 | SQLite (WAL模式) | `station_monitor.db` |
| 前端 | HTML + CSS + JS | `templates/`, `static/` |
| 地图 | Leaflet.js + OpenStreetMap | CDN |
| 图表 | Chart.js | CDN |
| 部署 | Docker + docker-compose | `Dockerfile`, `docker-compose.yml` |

## 项目结构

```
变电站图像监控运维平台/
├── app.py              # Flask应用入口 + API路由
├── admin.py            # 管理后台API（需admin角色）
├── auth.py             # 用户认证API（session + 限流）
├── config.py           # 配置（密钥、数据库路径）
├── init_db.py          # 数据库初始化
├── init_admin.py       # 管理员账户初始化
├── parse_excel.py      # Excel解析模块
├── requirements.txt     # Python依赖
├── templates/
│   ├── base.html       # 基础模板
│   ├── index.html      # 首页
│   ├── stations.html   # 变电站列表
│   ├── fault_new.html  # 故障报修
│   ├── faults.html     # 故障记录
│   ├── statistics.html # 统计报表
│   ├── map.html        # 地图页
│   ├── login.html      # 登录页
│   └── admin.html      # 管理后台
├── static/
│   ├── bootstrap-5.3.3.min.css
│   ├── bootstrap-5.3.3.bundle.min.js
│   ├── chart-4.4.1.min.js
│   ├── style.css
│   └── app.js
├── tests/
│   ├── conftest.py        # 测试配置（隔离数据库）
│   ├── test_api.py       # API集成测试
│   ├── test_parse_excel.py
│   └── test_import.py
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
# 默认管理员: admin / admin123
```

## 关键规范

- **API_TOKEN**: 设置环境变量 `set API_TOKEN=your-secret-token`，否则密码API无法工作
- **生产部署**: 使用 `docker-compose up -d`，勿直接运行 `python app.py`（Windows下可能导致数据库重置）
- **测试**: `python -m pytest tests/ -v`（使用独立测试数据库，不影响生产数据）

---

## 设计系统

**必须阅读 DESIGN.md 后再做任何UI/视觉决策。**

所有字体、颜色、间距、阴影、圆角、动效规范都定义在 `DESIGN.md` 中。
不要偏离DESIGN.md中定义的规范，除非有用户明确授权。
