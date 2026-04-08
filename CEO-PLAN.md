---
status: ACTIVE
---
# CEO Plan: 变电站图像监控运维平台

> 更新日期：2026-04-06
> 目的：把 `CEO-PLAN.md` 从早期立项快照重写为“基于当前仓库现实、可继续执行”的工程计划。

## 1. Vision / Product reality

### 1.1 当前产品现实
这个系统不是一个“让任何人拿手机 5 秒报故障”的轻应用，而是一个内部运维平台。

当前已经确认的产品现实：
- 首页首屏应该是监控总览，帮助管理人员先判断整体状态
- 故障录入的主流程已经不是首页主 CTA，而是标准化表格批量导入，由专人处理
- 导入不是附属脚本，而是正式的平台能力
- 导入入口应放在首页右上角常驻位置，融入当前顶栏框架
- UI 基调继续沿用当前 `style2`，不做大幅换皮

### 1.2 用户结果
平台的核心结果应聚焦在三件事：
- 管理人员打开首页，快速判断全局运行状态
- 运维人员和管理人员能按项目范围查看、处理、追踪故障
- 专门负责数据维护的人能完成可追踪、可回滚、可审查的批量导入

### 1.3 体验基线
- 首页情绪：稳定、可判断
- 导入失败和格式异常：协助修正，而不是只报错
- 冲突处理：先挂起，再确认，不做偷偷写库
- 视觉基线：延续 `static/design_variants/style2.css`

## 2. Confirmed scope

本阶段的计划不是重新发明系统，而是围绕当前仓库现实继续演进。

### 2.1 已确认范围
- 保持首页作为监控总览入口
- 把导入能力提升为正式平台工作流
- 把导入拆成两条正式子链路：
  - 台账导入
  - 故障批次导入
- 明确批次状态语义和页面状态语义
- 规划首页右上角常驻导入入口
- 规划成功、部分成功、格式错误、冲突挂起等正式状态页
- 保持现有认证、项目范围控制、审查中心、toast、项目切换等能力作为复用基线

### 2.2 不在本次计划里默认承诺的内容
- 不默认新增运行时 API
- 不默认重做整套导航结构
- 不默认替换当前 `style2` 的视觉体系
- 不把 `admin.py:/admin/upload` 直接升级成最终架构代表，而是视为当前轻量入口 / 遗留入口
- 不在本次文档重写中强制新增数据库表，只基于当前 `import_batches + report + review queue` 组织状态语言

## 3. Retired assumptions / 已废弃前提

以下旧前提已经失效，后续实现不得再按这些叙事推进：

1. `static/style.css` 是当前 UI 基线
   - 已失效。当前唯一设计基线是 `static/design_variants/style2.css`

2. 首页主目标是“手机 5 秒报故障”
   - 已失效。首页主目标已切换为监控总览

3. 导入只是 Phase B 以后再说的 Excel 上传界面
   - 已失效。导入已经是平台主流程之一，且仓库已有真实实现

4. 完整认证系统仍是 deferred
   - 已失效。`auth.py` 已实现 session 登录、登出、当前用户信息和限流

5. 日志规范化仍是 deferred
   - 已失效。`TODOS.md` 已记录为完成项

6. 当前仓库仍是 Bootstrap 初始骨架 / greenfield 文件树
   - 已失效。当前仓库已经具备多模块蓝图、项目范围控制、批次导入、审查中心、测试矩阵

7. 工程执行顺序仍应按 `config.py -> token -> init_db.py -> parse_excel.py` 这种从零搭建路径组织
   - 已失效。当前计划应从“现实基线 + 增量演进”出发，而不是从零开始搭建

## 4. Current codebase baseline

这里只记录当前真实模块边界，不再复述早期假想文件结构。

### 4.1 运行时与页面入口
- `app.py`：Flask 应用入口，注册各蓝图并承载主 API
- `templates/base.html`：全局应用壳，包含顶栏、项目切换、toast 容器、脚本基线
- `templates/index.html`：当前首页监控总览骨架

### 4.2 认证与访问控制
- `auth.py`：session 登录、登出、`/auth/me`、用户管理、限流
- `project_access.py`：项目可见范围、默认项目、写权限判断

### 4.3 导入与后台
- `admin.py`：当前轻量后台与上传入口
- `import_excel.py`：台账批次导入
- `import_faults.py`：故障批次导入
- `import_review_support.py`：导入批次、source record key、站名匹配、审查支持
- `admin_review.py`：导入审查队列与批量动作
- `templates/admin.html`：当前导入起点页的现实基线
- `templates/admin_review.html`：冲突处理 / 审查中心现实基线

### 4.4 数据库与平台约束
- `config.py`：数据库路径、运行配置
- `utils.py`：SQLite 连接统一配置、WAL、busy timeout、backup helper
- `init_db.py`：初始化数据库和基础 schema

### 4.5 前端共享基线
- `static/design_variants/style2.css`：唯一生效视觉基线
- `static/utils.js`：`fetchJson`、`withProject`、`escapeHtml`、`AppProjectState`

### 4.6 当前测试基线
- `tests/test_import_excel.py`
- `tests/test_import_faults.py`
- `tests/test_admin_review.py`
- `tests/test_projects_api.py`
- `tests/test_api.py`
- `tests/test_utils_backup.py`

## 5. Existing reusable capabilities

这一节只写“仓库已经具备、后续应复用”的能力，避免把已存在事实又写成新需求。

### 5.1 后端复用能力
- `utils.create_db_connection()`：统一 SQLite 连接参数
- `utils.enable_wal_mode()` / `configure_sqlite_connection()`：WAL 与 busy timeout 约束
- `utils.backup_sqlite_database()`：写入前一致性备份基础能力
- `auth.py`：session 登录、当前用户信息、管理员用户管理
- `project_access.py`：项目范围读取与写权限控制
- `import_excel.py.run_batch_import()`：
  - 台账批次导入
  - `best-effort` / `full-rollback`
  - dry-run
  - report JSON
  - 与 `import_batches` 对接
- `import_faults.py.run_batch_import()`：
  - 故障批次导入
  - direct insert / queued review
  - fail-on 规则
  - dry-run
  - report JSON
- `import_review_support.py`：
  - `create_import_batch()`
  - `update_import_batch_stats()`
  - `build_source_record_key()`
  - 站名归一化与匹配辅助
- `admin_review.py`：
  - 批量指派站点
  - 批量套用映射
  - approve import
  - merge existing
  - reject

### 5.2 前端复用能力
- `templates/base.html`：
  - `top-bar-right` 顶栏右侧承载区
  - 全局项目切换器
  - toast 容器
  - 通用脚本装载点
- `static/utils.js`：
  - `fetchJson()`
  - `withProject()`
  - `escapeHtml()`
  - `AppProjectState`
- `templates/index.html`：
  - 监控总览、统计卡片、最近故障、快捷入口骨架
- `templates/admin.html`：
  - 上传表单
  - 项目选择联动模式
  - 上传反馈模式
- `templates/admin_review.html`：
  - 审查中心
  - proposal 列表
  - queue 列表
  - 批量处理交互

### 5.3 已存在事实 vs 后续增量

#### 已存在事实
- 已有登录认证
- 已有项目级访问控制
- 已有台账批次导入
- 已有故障批次导入
- 已有导入批次壳与 JSON 报告
- 已有审查中心和批量动作
- 已有首页监控总览
- 已有当前 UI 设计基线与前端共享工具
- 已有导入、审查、备份、项目 API 的测试覆盖

#### 后续增量
- 首页右上角常驻导入入口正式落地
- 导入状态页产品化，脱离“只靠 toast / 内嵌结果块”的表达
- 导入结果语义统一为正式状态词汇
- 冲突批次的整批挂起语义在页面和流程中显式表达
- 页面层测试补齐，覆盖状态页和入口位置

## 6. Import domain model and state machine

导入不再写成一个模糊的“Excel 上传”，而是正式的平台领域。

### 6.1 子链路 A：台账导入
- 来源：设备台账 Excel
- 当前实现基线：`import_excel.py`
- 当前数据模型：文件 -> 解析 -> 站点 / 摄像头 upsert -> 行结果 -> 批次统计 -> report JSON
- 当前已具备能力：
  - `import_batches`
  - `best-effort`
  - `full-rollback`
  - dry-run
  - 自动备份
  - report JSON

### 6.2 子链路 B：故障批次导入
- 来源：标准化故障表格 / 历史故障导入
- 当前实现基线：`import_faults.py`
- 当前数据模型：文件 -> 行级解析 / 校验 -> 直接入库或进入 review queue -> 批次统计 -> report JSON
- 当前已具备能力：
  - source record key
  - duplicate skip
  - queue item
  - 站名映射 proposal
  - approve / merge / reject
  - fail-on 规则

### 6.3 共享批次外壳
当前计划统一承认以下批次壳已经存在：
- `import_batches.id`
- `project_id`
- `source_type`
- `mode`
- `file_count`
- `success_count`
- `fail_count`
- `report_path`

本计划不在文档层面再发明一套新批次表。

### 6.4 行级结果语义
行级至少要覆盖以下结果词汇：
- `imported`
- `inserted`
- `queued`
- `failed`
- `duplicate-skip`
- `skip`

### 6.5 审查项状态语义
审查项至少覆盖：
- `pending`
- `approved`
- `rejected`
- `expired`

### 6.6 批次结果语义
面向产品与页面时，批次结果统一表达为：
- 成功
- 部分成功
- 待处理冲突
- 失败

### 6.7 核心护栏
- 只要一个批次包含待确认冲突，该批次在产品语义上就应保持挂起
- 先确认冲突，再决定是否视为最终入库完成
- 不允许把冲突批次伪装成“成功，只是有几条待处理”

## 7. Page architecture and UX states

### 7.1 首页
首页继续基于 `templates/index.html` 的监控总览结构推进：
- 保留总览、统计、最近故障、快捷入口骨架
- 弱化“新建报修”作为首页首要 CTA 的角色
- 在 `templates/base.html` 的 `top-bar-right` 区域放置首页右上角常驻导入入口
- 导入入口必须接入当前项目上下文，不绕开 `withProject()` 与全局项目切换

### 7.2 导入起点页
- 导入应被定义为正式流程起点
- v1 复用 `templates/admin.html` 现有上传区和项目选择模式
- `admin.py:/admin/upload` 在计划里视为轻量入口 / 遗留入口，不把它误写为最终平台架构

### 7.3 结果摘要页
用于全量成功批次：
- 展示批次统计
- 展示成功数量 / 失败数量 / 文件数
- 提供报告入口
- 语义上明确“本批次已完成”

### 7.4 部分成功页
用于 mixed results：
- 不再只靠 toast 或页面内零散结果块
- 分组展示成功项、失败项、后续动作
- 用户能一眼看懂“哪些已经写入，哪些没写入，接下来该做什么”

### 7.5 格式错误页
用于文件结构或字段级错误：
- 错误粒度至少到“行 + 字段 + 原因”
- 禁止只返回“解析失败”“导入失败”这类粗粒度信息
- 页面基调是协助修正，而不是纯技术报错

### 7.6 冲突处理页
基于 `templates/admin_review.html` 和 `admin_review.py` 的现实能力扩展：
- 动作至少包括：
  - 指派站点
  - 套用映射
  - 导入
  - 并单
  - 驳回
- 页面语义必须显式表达：整批挂起，先确认冲突，再决定是否入库完成

### 7.7 移动端策略
- 不是把桌面布局硬压缩，而是按优先级重排为单列
- 首屏先展示核心状态块、错误块、主 CTA
- 继续遵守 `DESIGN.md` 已定义的移动端和触控目标规范

## 8. Public contract / 对外契约变化

这次文档重写的重点是统一页面状态契约和批次状态词汇，而不是承诺马上追加新的后端 API。

### 8.1 本次文档明确的正式交付物
- 首页右上角常驻导入入口
- 结果摘要页
- 部分成功页
- 格式错误页
- 冲突处理页

### 8.2 契约层统一词汇
后续实现必须统一使用以下产品语义：
- 成功
- 部分成功
- 待处理冲突
- 失败

并把这些语义映射到：
- 批次统计
- report 展示
- review queue
- 页面标题与 CTA

### 8.3 非承诺项
- 本文档不默认要求新增 API 路由
- 本文档不默认要求推翻当前导入脚本与后台实现
- 本文档先统一语言与页面契约，后续实现按这些契约补齐界面和测试

## 9. Testing and verification matrix

新版测试矩阵按“批次导入状态流”组织，而不是只围绕旧的 happy path。

### 9.1 脚本 / 单元层
| 场景 | 当前状态 | 证据 |
|---|---|---|
| 台账导入成功 | 已有覆盖 | `tests/test_import_excel.py` |
| 同槽位替换摄像头 | 已有覆盖 | `tests/test_import_excel.py` |
| dry-run 不写库 | 已有覆盖 | `tests/test_import_excel.py`, `tests/test_import_faults.py` |
| full rollback 全量回滚 | 已有覆盖 | `tests/test_import_excel.py` |
| 故障批次直接入库 | 已有覆盖 | `tests/test_import_faults.py` |
| 无外部记录键时进入队列 | 已有覆盖 | `tests/test_import_faults.py` |
| JSON report 输出 | 部分覆盖 | 代码已有实现，需补更明确断言 |
| 自动备份 | 已有覆盖 | `tests/test_utils_backup.py` |
| 格式错误按行/字段/原因输出 | 尚缺 | 需新增页面层或报告层断言 |

### 9.2 批次 / 审查层
| 场景 | 当前状态 | 证据 |
|---|---|---|
| 创建 `import_batches` | 部分覆盖 | 导入代码已实现，建议补显式断言 |
| 更新 success / fail 统计 | 部分覆盖 | 代码已实现，建议补显式断言 |
| review queue 创建 | 已有覆盖 | `tests/test_import_faults.py`, `tests/test_admin_review.py` |
| 站名映射提议查询与审批 | 已有覆盖 | `tests/test_admin_review.py` |
| 批量指派站点 / 套用映射 | 已有覆盖 | `tests/test_admin_review.py` |
| approve import / merge / reject | 已有覆盖 | `tests/test_admin_review.py` |
| 冲突批次整批挂起语义 | 尚缺 | 需新增更高层流程断言 |

### 9.3 页面层
| 场景 | 当前状态 | 证据 |
|---|---|---|
| 首页监控总览骨架存在 | 已有现实基线 | `templates/index.html` |
| 首页右上角常驻导入入口 | 尚缺 | 需要新增实现与页面断言 |
| 结果摘要页 | 尚缺 | 需要新增页面实现 |
| 部分成功页 | 尚缺 | 需要新增页面实现 |
| 格式错误页 | 尚缺 | 需要新增页面实现 |
| 冲突处理页正式化 | 部分覆盖 | `templates/admin_review.html` 现有能力可复用 |
| 移动端首页优先级重排 | 部分覆盖 | 当前已有响应式基础，需补页面断言 |

### 9.4 回归层
| 场景 | 当前状态 | 证据 |
|---|---|---|
| 项目切换参数与可见范围 | 已有覆盖 | `tests/test_projects_api.py` |
| 认证 / 当前用户信息 | 已有覆盖 | `tests/test_projects_api.py`, `auth.py` |
| 管理后台基础能力 | 部分覆盖 | 现有页面与上传逻辑，仍可补页面断言 |
| 首页监控总览不回退 | 尚缺 | 需新增页面回归断言 |
| 统计页 / 首页不受导入改动影响 | 尚缺 | 需新增回归测试 |
| toast 反馈链路 | 尚缺 | 需新增前端层断言 |
| API 基础能力与幂等行为 | 已有覆盖 | `tests/test_api.py` |

## 10. Risks and guardrails

### 10.1 主要风险
1. 继续按旧计划实现，导致产品方向回退到“快速报故障首页”
2. 把导入继续当成临时脚本，导致页面状态与批次语义长期缺位
3. 把冲突当作局部异常处理，导致用户无法判断整批是否真正完成
4. 页面实现绕开 `base.html`、`style2.css`、`static/utils.js`，造成体验割裂
5. 只补功能不补页面层测试，后续改动容易把首页和导入状态页弄坏

### 10.2 护栏
- 首页主目标始终是监控总览
- 导入入口必须走顶栏右上角常驻入口，而不是散落在后台深处
- 冲突批次在确认前保持挂起
- 页面与状态词汇必须统一，不允许后端叫一种、前端写一种、文档再写一种
- 新页面默认复用 `base.html`、`style2.css`、`static/utils.js`
- 后续实现前优先补页面层和流程层测试，不只做脚本测试

## 11. Execution order

建议后续执行顺序如下：

1. **Product reality + 已废弃前提**
   - 先统一产品叙事，阻止继续按旧世界观推进

2. **当前基线 + 可复用能力**
   - 明确哪些是现成能力，避免重复建设

3. **导入领域模型与状态机**
   - 统一台账导入、故障批次导入、批次结果、审查状态

4. **首页 IA 与导入入口**
   - 规划首页右上角常驻导入入口
   - 明确“新建报修”降级，不再作为首页主入口

5. **导入状态页与冲突处理流**
   - 结果摘要页
   - 部分成功页
   - 格式错误页
   - 冲突处理页

6. **测试矩阵、验收标准、风险护栏**
   - 先补高价值页面断言和流程断言
   - 确保首页总览与导入工作流同时成立

## 12. 验收标准

文档重写完成后，至少满足以下标准：
- 不再把 `static/style.css` 写成当前基线
- 不再把认证、导入、日志写成 deferred
- 不再把导入写成单一模糊的“Excel 上传”
- 明确首页主目标是监控总览
- 明确导入入口位于首页右上角常驻区域
- 明确区分台账导入与故障批次导入
- 明确写出“冲突批次在确认前保持挂起”
- 测试矩阵可映射到当前真实测试文件

## 13. 关键证据文件

- `CLAUDE.md`
- `DESIGN.md`
- `TODOS.md`
- `app.py`
- `auth.py`
- `project_access.py`
- `admin.py`
- `admin_review.py`
- `import_excel.py`
- `import_faults.py`
- `import_review_support.py`
- `utils.py`
- `templates/base.html`
- `templates/index.html`
- `templates/admin.html`
- `templates/admin_review.html`
- `static/utils.js`
- `static/design_variants/style2.css`
- `tests/test_import_excel.py`
- `tests/test_import_faults.py`
- `tests/test_admin_review.py`
- `tests/test_projects_api.py`
- `tests/test_api.py`
- `tests/test_utils_backup.py`
