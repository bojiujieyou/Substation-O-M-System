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

---

## Design Context

### Users
- 核心使用者以监控值班与后台监控人员为主，在监控中心或办公室场景持续查看平台。
- 他们通常处于全局观察、异常筛查、故障调度、导入审查、统计查看和跨项目判断等持续值守场景。
- 他们要完成的核心任务是：快速识别异常、准确定位站点/设备/故障、低认知负担地完成处置推进，并在多项目上下文中保持判断清晰。

### Brand Personality
- 品牌气质：可信冷静、高效明确、温和亲和。
- 整体语气应像可靠的工业运维控制台，而不是营销产品、炫技数据大屏或娱乐化应用。
- 需要传达的情绪是：稳定感、可控感、清晰感，同时降低传统工业系统的生硬压迫感。

### Aesthetic Direction
- 延续当前已落地的工业功能主义白底工具风，遵循 `DESIGN.md` 与当前基线样式 `static/design_variants/style2.css` 的方向。
- 当前视觉方向收敛为：克制工业风，仅维护浅色主题，不以深色模式为当前目标。
- 视觉上应保持克制、紧凑、结构化；颜色主要用于状态、优先级和操作引导，不用于装饰。
- 参考方向是工业监控 / SCADA / 运维控制台，但要更适合日常监控值班：避免过暗、过重、过拟物的“大屏风”，同时保留一定温和亲和度。
- 明确避免：玩具感、营销页感、花哨渐变、大面积炫技动效、强侵略性视觉、依赖 emoji 传达状态。

### Design Principles
1. 清晰优先于装饰：任何视觉决策都要先服务于信息辨识、状态判断和任务完成。
2. 稳定可信：界面应像生产工具而非展示页面，反馈要明确，层级要稳，避免情绪化设计。
3. 值班场景高效明确：关键监控信息、筛选条件和处置入口要始终容易扫描、容易定位、容易操作。
4. 温和而不生硬：在保持专业感的前提下，避免过重压迫感，让长时间值班浏览更舒适。
5. 复用现有设计系统：优先遵守 `DESIGN.md` 中已定义的颜色、间距、圆角、阴影、字体和动效规范，不另起一套视觉语言。
