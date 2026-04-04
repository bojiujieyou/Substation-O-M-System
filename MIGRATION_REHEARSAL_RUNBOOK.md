# Migration Rehearsal Runbook

## 目标

在不触碰源库的前提下，完整演练一次 Phase 1 多项目迁移流程，确认：

- 迁移 dry-run 计划可以生成
- 迁移 apply 可以在副本上执行
- 关键校验项通过
- 演练产物完整可归档

本 Runbook 不替代正式上线步骤，它的作用是把高风险动作先在副本上跑通。

## 执行命令

```powershell
python rehearse_migration_v1.py `
  --database .\station_monitor.db `
  --output-dir .\migrations\rehearsal_runs
```

## 产物

执行后会生成：

- `*.rehearsal_*.db`
  - 用于演练的数据库副本
- `migration_dry_run_report.json`
  - 演练前 dry-run 计划
- `migration_apply_report.json`
  - 在副本上执行 apply 后的结果
- `*.rehearsal_pre_apply.db`
  - 演练副本在 apply 前的备份
- `migration_rehearsal_summary.json`
  - 汇总结论

## 需要检查的结论

重点查看 `migration_rehearsal_summary.json`：

- `conclusion.source_db_untouched`
  - 必须为 `true`
- `conclusion.rehearsal_apply_completed`
  - 必须为 `true`
- `post_validation.fault_reports_camera_id_orphans`
  - 必须为 `0`
- `post_validation.active_camera_slot_conflicts`
  - 必须为 `0`

再查看 `migration_apply_report.json`：

- `execution.cameras_rebuild`
  - 确认重建是否执行以及前后行数
- `execution.fault_backfill`
  - 确认 `camera_slot_id`、`project_id` 等回填结果
- `execution.added_fault_columns`
  - 确认新增列符合预期

## 演练通过标准

- 源库未被修改
- 副本迁移成功提交
- `fault_reports.camera_id` 无悬空
- 同槽位无多条 `active` 设备
- 报告和备份文件都成功落盘

## 演练后动作

如果演练通过：

- 归档演练 summary、dry-run 和 apply 报告
- 准备正式执行窗口

如果演练失败：

- 不进入正式迁移
- 先根据演练副本和报告定位失败步骤
- 修正脚本后重新演练

## 正式执行前仍需确认

- 最近一次生产库备份已生成并可恢复
- 正式窗口内无人继续写库
- 回滚联系人和恢复路径明确
- 演练产物已由实施人和评审人共同确认
