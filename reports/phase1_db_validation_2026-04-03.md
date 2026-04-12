# Phase 1 DB Validation Summary

- Database: `D:\station-monitor\station_monitor.db`
- Current version: `0`
- Target version: `1`
- Foreign keys: `1`
- Overall status: `not_migrated`
- Structural missing count: `37`
- Critical failure count: `0`

## Missing Structure

- Tables: `camera_slots, fault_import_review_queue, import_batches, project_fault_type_versions, project_fault_types, project_notification_configs, project_notification_policies, projects, schema_migrations, station_external_names, station_name_mapping_proposals, user_project_scopes`
- Views: `v_camera_slots_with_current_camera`
- Indexes: `idx_cameras_one_active_per_slot, idx_fault_source_record_key`
- Cameras columns: `project_camera_code, project_id, replaced_by_camera_id, retired_at, slot_id, status`
- Fault report columns: `assigned_to, camera_slot_id, fault_type_code, fault_type_label_snapshot, fault_type_version_id, handling_started_at, project_device_code, project_id, source_batch_id, source_record_key, source_time_raw, source_timezone, source_type, tags_json`
- Photo columns: `project_hint, project_id`

## Next Actions

- 先执行 Phase 1 迁移演练或正式迁移，再重新校验。
- 补齐缺失表：camera_slots, fault_import_review_queue, import_batches, project_fault_type_versions, project_fault_types, project_notification_configs, project_notification_policies, projects, schema_migrations, station_external_names, station_name_mapping_proposals, user_project_scopes
- `cameras` 尚未达到冻结版结构，需完成重建步骤。
- `fault_reports` 尚未完成新增字段补齐。
- `photos.project_id/project_hint` 尚未补齐。

