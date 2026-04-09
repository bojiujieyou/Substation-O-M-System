# 多项目整合 Schema 基线审计

- 审计时间：2026-04-02
- 审计范围：
  - live DB：`station_monitor.db`
  - 代码基线：`init_db.py`
  - 目标设计：`docs/design/设计文档.md`（v4.5 冻结版）
- 结论：Phase 1 可以启动，但必须先按“新建表 / 字段存在性检查 / 表重建”三类拆开执行，不能把所有变更都当成普通 `ALTER TABLE`。

## 1. 当前实际 Schema 概览

| 表 | 当前行数 | 说明 |
|---|---:|---|
| `stations` | 96 | 站点主表 |
| `cameras` | 1363 | 旧版摄像头表 |
| `fault_reports` | 437 | 旧版故障表 |
| `photos` | 527 | 照片索引表 |
| `station_aliases` | 1 | 本地别名兜底表 |
| `users` | 1 | 用户表 |

完整性快查结果：

- `fault_reports.camera_id` 悬空数：`0`
- `fault_reports.station_id` 悬空数：`0`
- `cameras.station_id` 悬空数：`0`
- `photos.station_id` 悬空数：`0`
- `station_aliases.station_id` 悬空数：`0`

补充发现：

- live `fault_reports` 比 `docs/design/设计文档.md` 的 3.0 基线多一个遗留字段：`camera_location_text`
- `fault_reports.status`、`fault_reports.closed_at`、`fault_reports.system_type`、`fault_reports.idempotency_key` 已存在，迁移时必须跳过重复 `ADD COLUMN`
- `utils.py` 当前只设置了 `busy_timeout`，尚未统一开启 `PRAGMA foreign_keys = ON`

## 2. 逐表差异结论

### 2.1 需要新建的表

以下表在 live DB 中不存在，应由 Phase 1 直接创建：

- `projects`
- `project_fault_type_versions`
- `project_fault_types`
- `camera_slots`
- `project_notification_policies`
- `project_notification_configs`
- `user_project_scopes`
- `station_external_names`
- `station_name_mapping_proposals`
- `fault_import_review_queue`
- `import_batches`
- `schema_migrations`

### 2.2 可以保留不动的现有表

以下表当前无需重建：

- `stations`
- `users`
- `station_aliases`

说明：

- `station_aliases` 当前字段为 `id, station_id, alias, source, created_at`，与冻结设计的基线预期一致
- 这些表后续如需补充索引，可单独处理，不应阻塞 Phase 1 主迁移

### 2.3 需要字段存在性检查后再加列的表

#### `fault_reports`

冻结设计要求新增，但当前 live DB 尚不存在的字段：

- `project_id`
- `camera_slot_id`
- `assigned_to`
- `fault_type_code`
- `fault_type_label_snapshot`
- `fault_type_version_id`
- `source_type`
- `source_batch_id`
- `source_record_key`
- `project_device_code`
- `handling_started_at`
- `source_time_raw`
- `source_timezone`
- `tags_json`

冻结设计已存在、不得重复 `ADD COLUMN` 的字段：

- `status`
- `closed_at`
- `system_type`
- `idempotency_key`

需要保留的遗留字段：

- `fault_type`
- `handler_name`
- `handler_note`
- `camera_location_text`

附加动作：

- 创建部分唯一索引 `idx_fault_source_record_key`
- 历史迁移时：
  - `fault_type -> fault_type_label_snapshot` 必须先完整保留
  - `handler_name` 仅在能唯一命中 `users.username` 时回填 `assigned_to`

### 2.4 必须重建的表

#### `cameras`

`cameras` 不能走普通 `ALTER TABLE`，建议重建，理由如下：

- 目标设计要求新增：`slot_id`
- 目标设计要求新增：`project_id`
- 目标设计要求新增：`project_camera_code`
- 目标设计要求新增：`status`
- 目标设计要求新增：`replaced_by_camera_id`
- 目标设计要求新增：`retired_at`
- 需要建立部分唯一索引：同一 `slot_id` 仅允许一条 `status='active'`
- `project_id` 在目标模型中是稳定业务字段，直接 `ADD COLUMN ... NOT NULL` 不适合当前 SQLite 基线

重建时的硬约束：

- 必须显式保留原 `id`，不能依赖新的自增值
- 必须先完成 `projects` 与 `camera_slots` 的准备，再给旧设备分配 `project_id` / `slot_id`
- 必须在重建后校验：
  - `fault_reports.camera_id` 无悬空
  - 同槽位仅一条 `active`

## 3. 当前实际字段清单

### 3.1 `stations`

`id, name, voltage_level, county, location, ip_range, nvr_ip, nvr_port, created_at, updated_at, latitude, longitude`

### 3.2 `cameras`

`id, station_id, camera_index, area, location_desc, ip_address, channel_port, channel_number, created_at`

### 3.3 `fault_reports`

`id, station_id, camera_id, fault_type, description, reporter_name, reporter_contact, status, handler_name, handler_note, closed_at, created_at, updated_at, idempotency_key, system_type, camera_location_text`

### 3.4 `photos`

`id, rel_path, abs_path, filename, ext, size_bytes, file_mtime, county_hint, station_hint, station_id, match_status, match_method, unmatched_reason, captured_at, first_seen_at, last_seen_at, updated_at`

### 3.5 `station_aliases`

`id, station_id, alias, source, created_at`

### 3.6 `users`

`id, username, password_hash, role, created_at`

## 4. Phase 1 分类清单

### 4.1 直接创建

- `projects`
- `project_fault_type_versions`
- `project_fault_types`
- `camera_slots`
- `project_notification_policies`
- `project_notification_configs`
- `user_project_scopes`
- `station_external_names`
- `station_name_mapping_proposals`
- `fault_import_review_queue`
- `import_batches`
- `schema_migrations`

### 4.2 字段存在性检查后执行

- `fault_reports.project_id`
- `fault_reports.camera_slot_id`
- `fault_reports.assigned_to`
- `fault_reports.fault_type_code`
- `fault_reports.fault_type_label_snapshot`
- `fault_reports.fault_type_version_id`
- `fault_reports.source_type`
- `fault_reports.source_batch_id`
- `fault_reports.source_record_key`
- `fault_reports.project_device_code`
- `fault_reports.handling_started_at`
- `fault_reports.source_time_raw`
- `fault_reports.source_timezone`
- `fault_reports.tags_json`
- `idx_fault_source_record_key`

### 4.3 重建执行

- `cameras`

## 5. 建议迁移顺序

1. 统一数据库连接入口，确保所有新迁移连接显式执行 `PRAGMA foreign_keys = ON`
2. 备份并校验 `station_monitor.db`
3. 创建 `schema_migrations`
4. 创建 `projects` 并写入初始项目数据
5. 创建 `camera_slots` 与其他新表
6. 重建 `cameras`，复制数据时显式保留原 `id`
7. 回填 `fault_reports.camera_slot_id`、`project_id` 等新增字段
8. 为 `fault_reports` 增量加列，并建立 `idx_fault_source_record_key`
9. 做完整性校验并生成报告

## 6. 实施风险

- `cameras` 重建是最高风险步骤，必须以备份恢复作为正式回滚路径
- `fault_reports` 不能盲目照抄设计稿中的 `ALTER TABLE` 列表，否则会在 `status` / `closed_at` 上直接失败
- `camera_location_text` 是 live-only 遗留字段，V1 不应在迁移中静默丢弃
- 当前代码库里存在多处 `sqlite3.connect(...)` 直接连库入口，后续需要收拢到统一连接工厂，否则 `foreign_keys` 很容易失效
