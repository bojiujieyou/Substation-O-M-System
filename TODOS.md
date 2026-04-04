# TODOS - 变电站图像监控运维平台

> 工程评审后补充的Phase B/C待办项

---

### [~] 建立设计系统（设计token）
**What:** 通过design-consultation生成DESIGN.md，定义色彩+字体+间距+阴影+圆角+动效token

**Why:** 当前计划缺少基础设计规范，实现者会随机做决定（卡片阴影/表格行高/按钮hover效果等）

**Pros:** 统一视觉语言，减少实现歧义，加快开发速度

**Cons:** 需要额外时间投入

**Context:** 设计评审（/plan-design-review）发现：CEO-PLAN无DESIGN.md，所有设计token缺失

**Depends on:** 无

**Status:** ✅ COMPLETED — DESIGN.md已生成（2026-03-25）

---

### [~] 补充交互状态规范
**What:** 在CEO-PLAN中增加完整的交互状态表（7个功能 x 6种状态）

**Why:** 当前计划只定义了正常流程，加载/空/错误状态缺失。运维人员会遇到加载中显示空白、报错时显示浏览器默认error等问题

**Pros:** 覆盖所有用户体验路径，避免生产环境问题

**Cons:** 增加计划文档量

**Context:** 设计评审产出：缺失状态包括loading/error/empty/幂等冲突/404等

**Depends on:** 无

**Status:** ⚠️ PARTIAL — 设计评审中已定义，Phase A实现中部分覆盖

---

### [ ] Phase A移动端响应式支持
**What:** 在Phase A就支持移动端布局，而非推迟到Phase B

**Why:** 运维人员可能用手机快速报故障，这是核心场景。手机是"5秒完成报故障"的关键设备

**Pros:** 覆盖移动使用场景，符合"5秒完成报故障"目标

**Cons:** Phase A工作量增加约30%

**Context:** 设计评审产出：当前计划完全未指定移动端响应式策略

**Depends on:** 设计系统（DESIGN.md）

**Status:** ❌ NOT DONE — 响应式规范已在DESIGN.md定义，但未在HTML中实现

---

### [x] Phase A ARIA无障碍实现
**What:** 在 base.html 中添加 ARIA landmarks（role="banner"/"navigation"/"main"）、aria-label 和 skip-link，在 style.css 中添加显式 focus 样式

**Why:** 无障碍是DESIGN.md的明确要求，当前模板缺少 role="banner/navigation/main"，键盘用户无法有效导航。运维人员中有视觉障碍者需要屏幕阅读器支持

**Pros:** 满足DESIGN.md accessibility规范，支持屏幕阅读器用户，符合《网站可及性标准》要求

**Cons:** 改动HTML模板，不影响功能

**Context:** 设计评审（/plan-design-review）产出：无障碍评分5/10，ARIA landmarks完全缺失，Tab焦点样式虽有CSS定义但模板未使用

**Depends on:** 无

**Status:** ✅ DONE — 2026-03-25（/design-review修复完成，base.html已添加ARIA landmarks + skip-link + focus-visible，fault_new.html/stations.html/admin.html/faults.html所有模态框已添加role="dialog" aria-modal aria-labelledby）

---

## Phase B（已完成）

### [x] Excel管理上传界面
**What:** 创建一个Web界面，让运维人员上传Excel文件来增删改变电站台账数据

**Status:** ✅ COMPLETED
- `admin.py` — 管理后台API（/admin/upload, /admin/stations, /admin/cameras）
- `templates/admin.html` — 管理后台Web界面
- 数据库upsert逻辑：站名+电压等级唯一键，重复导入自动覆盖

---

### [x] 完整用户认证系统
**What:** 实现完整的用户登录认证（而非仅密码API的简单token）

**Status:** ✅ COMPLETED
- `auth.py` — Session-based认证（login/logout/me）
- 用户管理API：/auth/users（GET/POST/DELETE，需admin角色）
- `templates/login.html` — 登录页面
- 限流保护：同一IP 5次/5分钟
- Session Cookie安全标志：Secure + HttpOnly + SameSite=Lax

---

### [x] 日志规范化
**What:** 建立统一的日志格式和日志级别规范

**Status:** ✅ COMPLETED
- Python `logging`模块统一配置
- 结构化日志格式：`时间 级别 名称: 消息`
- 关键操作记录：故障提交、状态更新、数据导入/删除

---

## Phase C

### [ ] 生产级NAS部署准备
**What:** fnOS NAS的生产级部署：数据库自动备份（每日）、UPS监控、硬件故障告警、Docker镜像版本管理

**Why:** 当前Phase A只做了基本Docker部署，没有备份和监控。如果NAS断电或硬盘损坏，数据会丢失。运维人员需要一个可信赖的生产系统

**Pros:** 数据安全、系统高可用、故障可追溯

**Cons:** 需要运维知识（cron定时任务、UPS集成）、可能的额外硬件成本

**Context:** Phase A在Windows 10开发和fnOS测试。Phase C（或明确的生产化需求）需要考虑数据安全和系统监控

**Depends on:** Phase A + Phase B完成

---

## Phase A 立即可做的低成本项（不需等待Phase B/C）

| 项目 | 成本 | 理由 |
|------|------|------|
| 导入前自动备份SQLite | ✅ 已完成 | 已抽到公共备份 helper，并接入 `import_excel.py` / `import_faults.py` / `import_faults_worklog.py` / `full_import_worklog.py` / `import_coordinates.py` |
| precheck报告格式不一致文件 | ✅ 已完成 | `precheck_excel.py` 现已输出统一结构化结果，并支持 `--json-out` 导出报告 |
| Docker健康检查 | ✅ 已完成 | `Dockerfile` 与 `docker-compose.yml` 已配置健康检查 |

---

## 安全审计遗留（2026-03-25）

以下为CSO审计发现但未修复的项目：

| 项目 | 严重性 | 状态 | 说明 |
|------|--------|------|------|
| CSRF保护 | MEDIUM | ⚠️ 暂未启用 | 内部工具+SameSite=Lux，已记录风险 |
| 密码哈希 | LOW | ⚠️ SHA256 | 建议迁移至bcrypt，内部工具可接受 |
| 账户锁定 | INFO | ⚠️ 依赖IP限流 | IP限流已缓解暴力破解风险 |

---

*最后更新：2026-03-25（设计评审后：补充ARIA无障碍实现TODO）*
