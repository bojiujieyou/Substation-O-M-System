# 变电站图像监控运维平台 — 全面代码审计报告

> 审计日期：2026-04-29  
> 审计范围：安全、代码质量、数据库、前端、部署  
> 代码规模：~22,000 行 Python + ~45 个 HTML 模板 + JS/CSS

---

## 一、总览

项目整体工程成熟度较高，核心安全机制（认证、CSRF、路径穿越防护、SQL 参数化）已到位，测试覆盖面广（42 个测试文件，~11,500 行），但存在若干需要关注的问题。

### 风险等级分布

| 等级 | 数量 | 说明 |
|------|------|------|
| 🔴 高 | 3 | 必须尽快修复 |
| 🟠 中 | 8 | 建议在下一迭代修复 |
| 🟡 低 | 10 | 可作为技术债务排期处理 |

---

## 二、安全审计

### 2.1 🔴 高：`.env` 文件含真实生产密钥已提交到仓库

**文件：** `.env`  
**问题：** `.env` 文件中包含真实的数据库密码、API Token、NVIDIA API Key 和 SECRET_KEY，且该文件存在于项目目录中。虽然 `.gitignore` 中有 `.env` 条目，但 `git status` 显示该文件已被 Git 跟踪（文件修改时间为 Apr 25，晚于 `.gitignore` 更新时间）。

```
DATABASE_URL=postgresql://station_monitor:Txjk%401234@...
API_TOKEN=2h7UPUGLrPCIUwl6zyEkKKM56YTYnY0BvfFLrNWI_LU
NVIDIA_API_KEY=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
SECRET_KEY=3c4140cfcb2aa6b526d92521466dddf2c5959a2d56c21021b7d293900344fa1c
```

**建议：**
1. 立即轮换所有已泄露的密钥（数据库密码、API Token、NVIDIA Key、Secret Key）
2. 从 Git 历史中移除 `.env`：`git rm --cached .env` 然后 `git filter-branch` 或使用 BFG Repo-Cleaner
3. 确认 `.env` 已被 `.gitignore` 正确忽略

---

### 2.2 🔴 高：PRAGMA table_info 存在 SQL 注入风险

**文件：** `app.py:470`, `admin.py:575`, `project_access.py:32`, `ai_fault_analysis.py:47`, `photo_indexer.py:35`, `photo_thumbnails.py:36` 等多处  
**问题：** `f"PRAGMA table_info({table_name})"` 使用 f-string 拼接表名。虽然当前 `table_name` 均来自硬编码字符串（如 `"fault_reports"`, `"cameras"`），但如果未来有用户输入流入 `table_name` 参数，将直接导致 SQL 注入。

```python
# app.py:470
rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()

# admin.py:592
db.execute(f"ALTER TABLE cameras ADD COLUMN {column_name} {column_type}")
```

**当前风险评估：** 所有调用点均使用硬编码表名，**暂无实际利用路径**。但 ALTER TABLE 语句中 `column_name` 和 `column_type` 同样使用 f-string，风险更高。

**建议：**
1. 对 `table_name` 添加白名单校验函数：只允许 `[A-Za-z_][A-Za-z0-9_]*` 格式
2. 对 `column_name` 做同样的白名单校验
3. 考虑将 `get_table_columns()` 函数统一到一个模块，避免散布在多个文件中

---

### 2.3 🔴 高：docker-compose.yml 中 PostgreSQL 默认密码为 `change_me`

**文件：** `docker-compose.yml:9`  
**问题：** PostgreSQL 容器使用 `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change_me}` 作为默认密码。如果用户忘记通过环境变量覆盖，数据库将以弱密码启动。

**建议：** 移除默认值，强制用户显式设置密码，或在 `docker-entrypoint.sh` 中添加启动检查。

---

### 2.4 🟠 中：Token 认证使用简单字符串比较

**文件：** `app.py:264`  
**问题：** `require_token` 装饰器使用 `token != Config.API_TOKEN` 进行比较，未使用常量时间比较（`hmac.compare_digest`），存在时序攻击（timing attack）风险。

```python
if token != Config.API_TOKEN:
    return jsonify({'error': '令牌无效或已过期'}), 403
```

**建议：** 改为 `hmac.compare_digest(token, Config.API_TOKEN)`（`hmac` 已导入但未用于此处）。

---

### 2.5 🟠 中：限流异常时默认放行

**文件：** `auth.py:86-91`, `auth.py:122-126`  
**问题：** `rate_limit_check()` 和 `rate_limit_record()` 在数据库操作异常时静默返回 `True`（放行），攻击者可以通过触发数据库异常来绕过限流。

```python
except Exception:
    try:
        db.rollback()
    except Exception:
        pass
    return True  # 异常时放行
```

**建议：** 在异常时返回 `False`（拒绝），宁可误拒不可放行。同时添加日志告警。

---

### 2.6 🟠 中：`admin_required` 仅检查 session 中的 role 字段

**文件：** `admin.py:30-37`, `auth.py:200-201`  
**问题：** `require_admin` 仅通过 `session.get('role') != 'admin'` 判断权限。Session 数据存储在客户端 Cookie 中（Flask 默认），如果 SECRET_KEY 泄露或被破解，攻击者可伪造 session 提权为 admin。

**建议：**
1. 确保生产环境 `SECRET_KEY` 足够强（当前 64 字符 hex 已足够）
2. 考虑在关键操作（如删除用户、重置密码）时增加二次验证

---

### 2.7 🟠 中：缺少 Content-Security-Policy 头

**文件：** `app.py:69-74`  
**问题：** 安全头设置了 `X-Frame-Options`、`X-Content-Type-Options` 等，但缺少 `Content-Security-Policy` 头。虽然当前未发现 XSS 漏洞（Jinja2 默认转义），但 CSP 是纵深防御的重要一环。

**建议：** 添加基本的 CSP 策略，至少限制 `script-src` 和 `style-src`。

---

### 2.8 🟡 低：Session Cookie 配置

**文件：** `config.py:102-104`  
**说明：** `SESSION_COOKIE_HTTPONLY = True` 已设置，`SESSION_COOKIE_SAMESITE = 'Lax'` 已设置，`SESSION_COOKIE_SECURE` 在生产环境强制为 True。配置合理，无需修改。

---

### 2.9 ✅ 做得好的地方

- **CSRF 保护**：双重提交 Cookie 模式实现完整，前端 `fetch` 拦截器自动注入 `X-CSRF-Token`
- **SQL 参数化**：绝大多数查询使用 `?` 占位符 + 参数元组，未发现 `%s` 或 `.format()` 拼接用户输入
- **路径穿越防护**：`is_path_under_root()` 函数正确使用 `resolve().relative_to()` 检查照片路径
- **密码哈希**：使用 scrypt（通过 werkzeug），支持旧版 SHA256 哈希的惰性迁移
- **XSS 防护**：模板中未发现 `|safe` 过滤器滥用，Jinja2 默认自动转义

---

## 三、代码质量审计

### 3.1 🟠 中：`app.py` 巨型文件（220KB / ~5,500 行）

**文件：** `app.py`  
**问题：** `app.py` 包含几乎所有 API 路由、业务逻辑、辅助函数，文件体积达 220KB。这导致：
- 难以快速定位代码
- Git 合并冲突频繁
- 代码审查困难

**已有改进：** 管理后台已拆分为 `admin_*.py` 蓝图模块，这是正确的方向。

**建议：** 按功能域继续拆分 `app.py`：
- `api_faults.py` — 故障相关 API
- `api_stations.py` — 站点相关 API  
- `api_photos.py` — 照片相关 API
- `api_statistics.py` — 统计报表 API
- `api_map.py` — 地图相关 API

---

### 3.2 🟠 中：`get_table_columns()` 重复实现

**问题：** 同一功能在 6+ 个文件中重复实现：

| 文件 | 行号 |
|------|------|
| `app.py` | 468-471 |
| `admin.py` | 575 |
| `project_access.py` | 29-33 |
| `ai_fault_analysis.py` | 47 |
| `photo_indexer.py` | 35 |
| `photo_thumbnails.py` | 36 |

**建议：** 统一到 `utils.py` 或 `project_access.py` 中，其他模块统一导入。

---

### 3.3 🟡 低：根目录脚本文件过多

**问题：** 项目根目录有 40+ 个 Python 文件，其中大量是一次性脚本（如 `extract_camera_location.py`、`create_coordinate_template.py`、`data_discovery_sprint.py`）。

**建议：** 将一次性脚本移入 `scripts/` 目录，保留核心运行时文件在根目录。

---

### 3.4 🟡 低：数据库备份文件散落在根目录

**问题：** 根目录有 20+ 个 `.db.backup_*` 和命名备份数据库文件（总计 ~30MB），虽然 `.gitignore` 已覆盖 `*.db.backup_*`，但其他命名格式的备份文件可能被意外提交。

**建议：** 将所有数据库备份移入 `backups/` 目录（已存在但未使用）。

---

### 3.5 ✅ 做得好的地方

- **蓝图架构**：管理后台已正确拆分为独立蓝图（`admin_bp`, `auth_bp`, `admin_fault_types_bp` 等）
- **错误处理**：有全局异常处理器和统一的 `api_error()` / `api_success()` 响应格式
- **日志**：使用标准 `logging` 模块，关键操作有日志记录
- **密码安全**：支持惰性哈希迁移（旧 SHA256 → 新 scrypt）
- **配置验证**：`validate_runtime_config()` 在生产环境强制检查必需配置

---

## 四、数据库审计

### 4.1 ✅ Schema 设计合理

**文件：** `init_db.py`  
**优点：**
- 表结构规范，字段类型合理
- 有适当的 UNIQUE 约束和 CHECK 约束
- 外键关系清晰（stations → cameras → fault_reports）
- 索引覆盖了主要查询路径

### 4.2 🟡 低：缺少复合索引优化

**问题：** 部分高频查询场景缺少复合索引：
- `fault_reports(status, created_at)` — 故障列表按状态+时间筛选
- `fault_reports(station_id, status)` — 站点下的故障筛选
- `cameras(station_id, ip_address)` — 站点下按 IP 查找摄像头

**建议：** 根据实际慢查询日志添加复合索引。

### 4.3 🟡 低：`deleted_at` 软删除未索引

**文件：** `init_db.py:127`  
**问题：** `fault_reports` 使用 `deleted_at` 字段实现软删除，但未在该字段上建索引。如果数据量增大，筛选未删除记录的查询将变慢。

### 4.4 ✅ 做得好的地方

- WAL 模式配置正确
- `busy_timeout` 设置为 30 秒，合理
- `PRAGMA foreign_keys = ON` 已启用
- PostgreSQL 兼容层设计良好（`db.py` 的 `CompatCursor` 和 `PostgresCompatConnection`）

---

## 五、前端审计

### 5.1 ✅ XSS 防护到位

- Jinja2 模板默认自动转义，未发现 `|safe` 滤器滥用
- JavaScript 中使用 `escapeHtml()` 函数处理动态内容
- Toast 消息使用 `textContent` 而非 `innerHTML` 插入

### 5.2 🟡 低：base.html 中内联大量 JS

**文件：** `templates/base.html:217-447`  
**问题：** 约 230 行 JavaScript 直接内嵌在 `base.html` 的 `<script>` 标签中，包括全局搜索、导航高亮、侧栏切换、Toast 工具、CSRF 拦截器等。

**建议：** 将这些代码提取到 `static/app.js`，在 `base.html` 中只保留 CSRF token 注入的必要内联代码。

### 5.3 🟡 低：全局搜索加载全量数据

**文件：** `templates/base.html:343-395`  
**问题：** 搜索功能先加载 `/api/stations` 和 `/api/faults` 的全量数据，再在客户端过滤。当数据量增大时会有性能问题。

**建议：** 实现服务端搜索 API（如 `/api/search?q=keyword`），返回匹配的前 N 条结果。

### 5.4 ✅ 做得好的地方

- `utils.js` 提供了统一的 `fetchJson`、`escapeHtml`、`showToast` 工具函数
- CSRF token 通过 fetch 拦截器自动注入
- 响应式设计（侧栏折叠、移动端适配）

---

## 六、部署与运维审计

### 6.1 🟠 中：Docker 镜像以 root 运行

**文件：** `Dockerfile`  
**问题：** 容器以 root 用户运行应用，不符合最小权限原则。

**建议：** 在 Dockerfile 中添加非 root 用户：
```dockerfile
RUN useradd -m appuser
USER appuser
```

### 6.2 🟠 中：docker-compose.yml 密码管理

**文件：** `docker-compose.yml:39`  
**问题：** `INIT_ADMIN_PASSWORD` 默认值为 `change_me_admin_password`，且环境变量直接明文传入容器。

**建议：** 使用 Docker secrets 或 `.env` 文件管理敏感信息，移除所有 `change_me` 默认值。

### 6.3 ✅ 做得好的地方

- Gunicorn 配置合理（2 workers、max_requests 限制、优雅重启）
- 健康检查配置完善（PostgreSQL 和应用均有）
- `docker-entrypoint.sh` 自动初始化数据库和管理员
- `validate_runtime_config()` 在生产环境拒绝弱配置启动
- `.env.example` 提供了完整的配置模板

---

## 七、测试覆盖

### 7.1 ✅ 测试体系成熟

- 42 个测试文件，总计约 11,500 行测试代码
- 覆盖了核心功能：认证、CSRF、API、导入、审核、通知、权限等
- 有独立的 `conftest.py` 管理测试夹具
- 使用独立测试数据库，不影响生产数据

### 7.2 🟡 低：缺少性能测试

**建议：** 对关键 API（故障列表、统计报表、搜索）添加性能基准测试，防止回归。

---

## 八、优先修复建议

按紧迫程度排序：

1. **立即** — 轮换 `.env` 中所有已泄露的密钥，从 Git 历史中移除
2. **短期** — 为 `get_table_columns()` 和 ALTER TABLE 语句添加表名/列名白名单校验
3. **短期** — Token 比较改用 `hmac.compare_digest`
4. **短期** — 限流异常时改为拒绝而非放行
5. **中期** — 继续拆分 `app.py` 为多个蓝图模块
6. **中期** — Docker 容器添加非 root 用户
7. **中期** — 添加 Content-Security-Policy 头
8. **长期** — 实现服务端搜索 API
9. **长期** — 清理根目录脚本文件和数据库备份
10. **长期** — 添加复合索引优化高频查询

---

## 九、总结

项目在安全基础方面做得相当扎实——CSRF 双重提交、路径穿越防护、SQL 参数化、密码哈希迁移、生产配置验证都已到位。测试覆盖面广，蓝图架构方向正确。

主要风险集中在**密钥管理**（.env 泄露）和**纵深防御**（PRAGMA f-string、缺少 CSP、Token 时序攻击）。这些问题修复成本低但安全收益高，建议优先处理。

代码层面最大的技术债务是 `app.py` 的巨型文件，建议按功能域逐步拆分。
