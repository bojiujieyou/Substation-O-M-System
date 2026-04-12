# 变电站图像监控运维平台

面向变电站图像监控运维场景的一体化内部平台，用于统一管理站点、摄像机、故障记录、照片资料、统计分析、后台导入和项目权限控制。

项目基于 Flask 构建，支持 SQLite 与 PostgreSQL 双后端，适合在 Windows 内网、Docker 环境或 NAS 场景中部署。它不是泛化的工单系统，而是围绕“变电站图像监控运维日常工作”长期打磨的专用生产工具。

## 项目亮点

- 一个平台打通站点、设备、故障、照片、统计和后台导入
- 支持台账导入、故障汇总导入、导入审查和批次结果追踪
- 支持多项目并行、项目访问控制和项目级故障类型版本管理
- 支持照片自动匹配站点、人工纠偏和缩略图混合存储
- 支持 SQLite 快速落地，也支持 PostgreSQL 作为正式运行后端
- 已提供图形控制面板，可替代传统 bat 菜单进行启停和日志查看

## 这个仓库解决什么问题

在实际运维工作里，常见的问题不是“没有系统”，而是数据和流程散落在多个地方：

- 站点、摄像机、录像机、照片目录、Excel 台账分散维护
- 报修、处理、关闭、恢复没有统一的闭环记录
- 故障名称依赖现场口径，难以长期统计和归一化
- 每日汇总、工作记录和历史台账需要反复导入、校对和确认
- 管理人员需要按项目、区县、电压等级、故障类型快速筛选和复盘

这个平台的目标，就是把这些“分散但高频”的运维工作统一进一个清晰、稳定、可追溯的内网系统中。

## 适合谁使用

- 监控值班人员
- 后台运维与故障处理人员
- 需要审查导入结果的管理人员
- 需要按项目、区县或故障类型查看统计报表的负责人
- 负责内网部署、数据库迁移和运行维护的实施人员

## 核心能力

### 1. 站点与设备管理

- 管理变电站基础信息，包括站名、电压等级、区县、位置、经纬度
- 管理摄像机、槽位、通道、项目设备编号等资产信息
- 支持后台编辑站点、补坐标、添加摄像头、替换设备
- 列表页可快速筛选高风险站点、近期故障站点和异常摄像机

### 2. 故障报修与闭环处理

- 新建故障报修单
- 按站点、摄像机、状态、故障类型等维度筛选
- 支持故障处理、关闭、恢复、删除与回收
- 支持“待现场确认”类故障，允许先报修、后细化故障类型
- 处理结果可沉淀在故障记录中，便于后续检索和统计

### 3. 故障类型版本管理

- 按项目维护故障类型版本
- 支持故障类型编码、语义分组和标签快照
- 支持导入数据和标准化故障类型映射
- 适合不同项目逐步统一现场术语和平台术语

### 4. Excel 导入与审查流程

- 支持台账导入
- 支持故障汇总导入
- 支持导入审查中心，对待确认数据进行人工确认
- 导入完成后自动生成结果摘要
- 可与项目、区县、站点、故障类型联动

### 5. 照片资料管理

- 扫描照片根目录并建立索引
- 自动按路径、站点名、别名等规则匹配到变电站
- 支持未匹配照片人工关联
- 支持按站点分组浏览照片
- 预览支持缩放、拖拽、切换上一张/下一张

当前采用“混合存储”方案：

- 数据库存储照片元数据
- 数据库存储缩略图
- 原始照片保留在磁盘
- 列表卡片优先使用数据库缩略图
- 打开大图时优先读取原始文件
- 若原始文件缺失，则自动回退到数据库缩略图

### 6. 统计分析与导出

- 首页与统计页展示站点数、设备数、故障数、故障率
- 支持按年份统计
- 支持故障类型、区县、电压等级等维度分析
- 支持 Excel 统计报表导出

### 7. 项目化与权限控制

- 支持多项目并行
- 支持项目可见范围控制
- 支持管理员与普通运维角色
- 支持项目级访问授权、项目级故障类型、项目级通知配置

### 8. 图形控制面板

仓库已提供图形控制面板：

- [`control_panel.pyw`](./control_panel.pyw)

它用于替代传统 bat 菜单，支持：

- 后台隐藏启动
- 普通启动
- 停止服务
- 重启服务
- 打开浏览器
- 打开项目目录
- 查看运行日志与错误日志
- 自动刷新服务状态、监听 PID、健康检查状态

`.pyw` 方式启动时不会额外弹出 Python 控制台窗口。

## 系统架构

```mermaid
flowchart LR
    U[监控值班 / 运维人员] --> W[Web UI<br/>stations / faults / photos / admin]
    W --> A[Flask 应用<br/>app.py]
    A --> B[业务模块<br/>admin / auth / review / projects / fault types]
    B --> D[(SQLite 或 PostgreSQL)]
    B --> P[照片目录<br/>PHOTO_ROOT_PATH]
    B --> E[Excel 导入源<br/>DATA_SOURCE_PATH]
    B --> R[导入审查 / 结果摘要]
    B --> N[通知与运行逻辑]
```

## 典型运维流程

```mermaid
flowchart TD
    S[导入台账 / 故障汇总 / 照片索引] --> C[系统自动匹配站点、设备、项目]
    C --> Q{是否完全识别?}
    Q -- 是 --> F[写入站点 / 摄像机 / 故障 / 照片记录]
    Q -- 否 --> R[进入导入审查或人工关联]
    R --> F
    F --> O[运维人员查看站点与故障列表]
    O --> H[处理故障 / 关闭故障 / 补充故障类型]
    H --> T[统计页与报表导出]
    F --> P[照片页按站点查看资料]
```

## 页面预览

当前 README 先补充页面说明和流程图，真实截图建议后续放入 [`docs/screenshots/`](./docs/screenshots/README.md)。

主要页面包括：

- `/stations`
  - 站点列表、风险筛选、快速跳转到故障和地图
- `/fault/new`
  - 新建故障报修，支持项目、站点、摄像机等维度录入
- `/faults`
  - 故障记录列表、状态流转、处理与关闭
- `/statistics`
  - 站点、设备、故障、故障类型、区县、电压等级统计
- `/map`
  - 站点地图定位
- `/photos`
  - 照片按站点分组浏览、未匹配照片人工关联、预览缩放
- `/admin`
  - 导入、审查、设备维护、项目管理、故障类型版本管理
- `/design/style2/*`
  - 当前主要设计基线页面

## 技术栈

### 后端

- Python 3
- Flask
- Flask-WTF
- openpyxl / xlrd
- psutil
- Pillow
- psycopg

### 前端

- 服务端模板渲染
- 原生 JavaScript
- 自定义 CSS
- Chart.js
- Leaflet

### 数据库

- SQLite
- PostgreSQL

项目已具备 SQLite / PostgreSQL 双后端兼容能力。开发期可直接使用 SQLite，正式内网部署可切换 PostgreSQL。

## 目录结构

```text
变电站图像监控运维平台/
├─ app.py                          # Flask 应用入口与主要 API
├─ admin.py                        # 后台导入、站点管理、设备管理
├─ auth.py                         # 登录认证
├─ admin_projects.py               # 项目中心
├─ admin_fault_types.py            # 故障类型版本管理
├─ admin_notifications.py          # 通知配置
├─ admin_review.py                 # 导入审查中心
├─ admin_user_access.py            # 用户项目授权
├─ project_access.py               # 项目访问控制
├─ notification_runtime.py         # 通知运行逻辑
├─ db.py                           # SQLite / PostgreSQL 兼容适配
├─ utils.py                        # 数据库连接与运行时工具
├─ init_db.py                      # 数据库初始化
├─ init_admin.py                   # 初始化管理员账号
├─ photo_indexer.py                # 照片索引与自动匹配
├─ photo_thumbnails.py             # 照片缩略图生成与持久化
├─ import_daily_fault_summary.py   # 故障汇总导入
├─ import_excel.py                 # 台账导入相关逻辑
├─ migrate_sqlite_to_postgres.py   # SQLite -> PostgreSQL 迁移脚本
├─ control_panel.pyw               # 图形控制面板
├─ start.bat                       # 普通启动脚本
├─ start_hidden.bat                # 后台隐藏启动脚本
├─ control.bat                     # 兼容保留的批处理控制入口
├─ templates/                      # 页面模板
├─ static/                         # 前端脚本与样式
├─ tests/                          # pytest 测试
├─ docs/                           # 运维与设计文档
└─ docker-compose.yml              # Docker / PostgreSQL 部署示例
```

## 主要接口与页面路由

部分关键接口与页面：

- `GET /api/stats`
- `GET /api/stations`
- `GET /api/stations/<station_id>`
- `GET /api/stations/<station_id>/slots`
- `POST /api/faults`
- `GET /api/faults`
- `PUT /api/faults/<fault_id>/status`
- `GET /api/photos`
- `GET /api/photos/groups`
- `GET /photos/file/<photo_id>`
- `GET /photos/thumb/<photo_id>`
- `/stations`
- `/fault/new`
- `/faults`
- `/statistics`
- `/map`
- `/photos`
- `/admin`

## 快速开始

### 方式一：Windows 本地直接运行

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

#### 2. 初始化数据库

```bash
python init_db.py
```

#### 3. 初始化管理员账号

```bash
python init_admin.py --username admin --password change_me_admin_password
```

提示：默认密码仅用于首次初始化，实际使用时请尽快修改。

#### 4. 启动服务

普通方式：

```bash
python app.py
```

或者直接双击：

- [`control_panel.pyw`](./control_panel.pyw)
- [`start.bat`](./start.bat)
- [`start_hidden.bat`](./start_hidden.bat)

默认访问地址：

```text
http://127.0.0.1:5000
```

若服务监听在 `0.0.0.0:5000`，同网段其他电脑也可以通过本机 IP 访问。

### 方式二：Docker 部署

仓库提供了 `docker-compose.yml` 示例，适合 PostgreSQL + App 一起运行的场景。

```bash
docker compose up -d --build
```

部署前请先准备 `.env` 或对应环境变量，确保数据库连接、密钥、照片目录挂载路径正确。

## 环境变量

常见变量如下：

- `DATABASE_URL`
  - PostgreSQL 连接字符串
- `DATABASE_PATH`
  - SQLite 数据库路径
- `APP_DATA_DIR`
  - 运行期数据目录
- `PHOTO_ROOT_PATH`
  - 照片根目录
- `DATA_SOURCE_PATH`
  - 台账 / 设备资料目录
- `API_TOKEN`
  - 某些接口依赖的访问令牌
- `SECRET_KEY`
  - Flask session 密钥
- `INIT_ADMIN_PASSWORD`
  - 首次初始化管理员账号时使用的默认密码
- `FLASK_DEBUG`
  - 是否启用调试模式

示例：

```env
DATABASE_URL=postgresql://station_monitor:change_me@127.0.0.1:5432/station_monitor
APP_DATA_DIR=D:\station-monitor\data
PHOTO_ROOT_PATH=D:\station-monitor\photos
DATA_SOURCE_PATH=D:\station-monitor\source_docs
API_TOKEN=your_secret_token_here
SECRET_KEY=change_me
INIT_ADMIN_PASSWORD=change_me_admin_password
FLASK_DEBUG=False
```

## PostgreSQL 迁移

项目已支持从 SQLite 迁移到 PostgreSQL。

典型流程：

1. 备份当前 SQLite
2. 准备 PostgreSQL 数据库和账号
3. 配置 `DATABASE_URL`
4. 初始化 PostgreSQL 结构
5. 执行迁移脚本
6. 重启应用并验证

相关文档见：

- [`docs/operations/POSTGRESQL_MIGRATION.md`](./docs/operations/POSTGRESQL_MIGRATION.md)

## 照片存储策略

照片模块重点是兼顾数据库可管理性与磁盘原图可用性。

当前方案：

- 数据库存照片元数据
- 数据库存缩略图
- 原始照片继续放磁盘
- 列表页优先走缩略图接口
- 预览页优先走原图接口
- 原图缺失时自动回退到数据库缩略图

这套设计既避免了把全部原图写进数据库造成膨胀，也能在目录轻微漂移时维持页面可用性。

## 测试

项目已包含 pytest 测试集，覆盖故障、项目、照片、导入等关键路径。

运行全部测试：

```bash
pytest -q
```

只跑照片相关测试：

```bash
pytest -q tests/test_photo_api.py tests/test_photo_indexer.py
```

## 适用场景

- 变电站图像监控内网运维平台
- 摄像机 / 录像机 / 故障 / 照片资料一体化管理
- Excel 为主的数据来源，需要持续导入和人工审查
- 已有照片资料目录，希望建立站点级归档与检索能力
- 需要从 SQLite 平滑升级到 PostgreSQL

## 当前仓库特点

- 不是空架子，已经落了大量业务逻辑
- 支持从“先跑起来”到“逐步规范化”的演进
- 对内网 Windows 环境友好
- 保留 Docker 化与 PostgreSQL 正式部署路径
- 对照片、导入、故障闭环三块做了较深的本地化定制

## 使用建议

- 单机验证或本地调试，优先 SQLite
- 正式内网长期运行，优先 PostgreSQL
- 使用图形控制面板替代传统批处理菜单
- 照片目录和导入源目录建议放在稳定路径，不要频繁搬迁
- 首次上线前先完成管理员密码、密钥和数据库连接信息替换

## 后续扩展方向

- 托盘常驻与最小化后台管理
- 控制面板打包为 `.exe`
- 故障通知渠道扩展
- 更细颗粒度的角色与权限
- 更完善的导入审查回放与批次对比
- 更完整的站点照片自动识别与匹配策略

## 许可证与说明

该项目当前更偏内部使用仓库。若需要对外开源、补充许可证、脱敏示例数据或编写对外版文档，建议另行整理发布材料。
