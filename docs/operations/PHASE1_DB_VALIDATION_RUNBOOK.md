# Phase 1 DB Validation Runbook

## 目标

在任意时点对目标 SQLite 库做一次独立结构校验，回答两个问题：

- 当前库距离 Phase 1 冻结版还差多少
- 如果已经迁移，关键完整性检查是否通过

这个 Runbook 可用于三类场景：

- 正式迁移前：确认当前库尚缺哪些结构
- 正式迁移后：确认结构与关键完整性是否达标
- 问题排查时：快速判断库处于 `not_migrated / partial / ready / failed` 哪种状态

## 执行命令

```powershell
python validate_phase1_db.py `
  --database .\station_monitor.db `
  --report .\reports\phase1_db_validation.json `
  --summary .\reports\phase1_db_validation.md
```

## 产物

- `phase1_db_validation.json`
  - 机器可读校验报告
- `phase1_db_validation.md`
  - 适合评审和归档的摘要

## 重点关注

- `summary.status`
  - `not_migrated`：还没进入 Phase 1 结构
  - `partial`：部分结构已具备，但未完全达标
  - `ready`：已满足 Phase 1 结构与关键完整性要求
  - `failed`：存在关键完整性问题，需要先修复
- `tables.missing`
  - 看是否还有冻结版必需表未创建
- `columns.cameras.missing`
  - 若不为空，通常说明 `cameras` 重建还没完成
- `critical_checks`
  - 尤其关注：
    - `fault_reports_camera_id_orphans`
    - `notification_config_policy_orphans`
- `post_validation`
  - 迁移后重点确认：
    - `active_camera_slot_conflicts = 0`
    - `fault_reports_camera_id_orphans = 0`

## 通过标准

- 正式迁移前：
  - 能生成报告
  - 缺口与预期一致，没有额外未知结构异常
- 正式迁移后：
  - `summary.status = ready`
  - `structural_missing_count = 0`
  - `critical_failure_count = 0`

## 推荐串联方式

1. 先跑 `rehearse_migration_v1.py`
2. 再对演练副本跑 `validate_phase1_db.py`
3. 正式窗口迁移后，对正式库再跑一次 `validate_phase1_db.py`
4. 把 rehearsal summary、正式 migration report、正式 validation report 一并归档
