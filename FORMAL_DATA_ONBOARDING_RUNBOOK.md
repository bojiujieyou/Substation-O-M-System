# 正式数据接入 Runbook

适用范围：
- 已完成 Phase 1 正式迁移
- 当前库结构校验结果为 `ready`
- 进入真实样本接入、正式批次导入和验收阶段

入口能力：
- Data Discovery：`data_discovery_sprint.py`
- 设备导入：`import_excel.py`
- 历史故障导入：`import_faults.py`
- 工单导入：`import_faults_worklog.py` / `full_import_worklog.py`
- 队列处理：`/admin/review-center`
- 批次审计：`inspect_import_batch.py`

## 一、正式接入前置条件

必须同时满足：

- 当前数据库已完成 Phase 1 迁移
- 当前数据库独立校验为 `ready`
- 已确认本轮接入的目标项目 `project_code`
- 已有最近一次可恢复的数据库备份
- 本轮处理责任人、复核人已明确

建议同时具备：

- 项目故障类型版本已创建并发布
- 相关运维账号和项目权限已准备好
- 通知策略已完成最小配置

## 二、本轮需要准备的数据

### 2.1 必需数据

1. 设备台账
   - 每个项目至少一份设备清单
   - 优先提供单站明细文件
   - 若是目录级数据，需按项目分开存放

2. 历史故障数据
   - Excel / CSV 均可
   - 至少包含：站点名、发生时间、故障类型、描述

3. 站名对照信息
   - 外部系统站名
   - 若有内部标准站名或对照表，必须一并提供

4. 故障类型映射表
   - 历史标签 -> `fault_type_code`
   - 无法一一映射时，也要先给出人工约定口径

### 2.2 强烈建议提供的数据

- 时间戳字段说明
  - 原始格式
  - 所属时区
- 外部设备编号规则说明
- 数据导出时间范围
- 本批次是否允许重复记录存在

## 三、正式执行顺序

按以下顺序执行，不要跳步：

1. 归档本轮原始样本
2. 跑 Data Discovery Sprint
3. 判断本轮是否放行
4. 如未放行，先修规则/映射后重跑 Discovery
5. 放行后先导入设备台账
6. 再导入历史故障 / 工单
7. 进入导入审查中心处理待确认队列
8. 处理完队列后做抽样复核
9. 跑批次审计
10. 形成本轮验收结论

## 四、推荐目录结构

```text
reports/
  onboarding_YYYY-MM-DD/
    raw_manifest.md
    discovery/
      data_discovery_report.json
      data_discovery_report.md
    import_devices/
      import_excel_report.json
    import_faults/
      import_faults_report.json
    import_worklog/
      worklog_report.json
    review/
      review_batch_notes.md
    audit/
      batch_XXX_audit.json
      fault_rows.csv
      review_rows.csv
      proposal_rows.csv
    acceptance/
      acceptance_checklist.md
```

## 五、Step 1：归档原始样本

先登记：

- 样本来源人
- 导出日期
- 覆盖项目
- 覆盖时间范围
- 原始文件路径
- 是否允许作为正式验收依据

建议形成 `raw_manifest.md`，避免后续混淆“测试样本”和“正式样本”。

## 六、Step 2：跑 Data Discovery Sprint

设备数据和故障数据都要跑。

```powershell
python data_discovery_sprint.py `
  --project inspection `
  --source-type import_excel `
  --database .\station_monitor.db `
  --device-source .\samples\inspection_devices `
  --fault-source .\samples\inspection_faults `
  --type-mapping .\samples\inspection_fault_type_mapping.csv `
  --report .\reports\onboarding_2026-04-03\discovery\data_discovery_report.json `
  --summary .\reports\onboarding_2026-04-03\discovery\data_discovery_report.md
```

必须确认：

- `slot_code_conflict_rate`
- `fault_type_mapping_rate`
- `timestamp_parse_rate`
- `station_match_breakdown`
- `release_decision.overall`

## 七、Step 3：放行判定

### 直接放行

满足以下条件可进入正式导入：

- `release_decision.overall = pass`
- 无阻断项

### 双轨推进

满足以下条件可边导入边处理：

- `release_decision.overall = dual_track`
- 人工处理量已可估算
- 责任人和完成时限已明确

### 阻断

以下情况不得进入正式导入：

- `release_decision.overall = block`
- 时间戳可解析率不达标
- 站名无法稳定归属
- 故障类型映射率过低

## 八、Step 4：正式导入设备台账

按项目单独执行，不要多个项目混在一个批次里。

```powershell
python import_excel.py `
  --database .\station_monitor.db `
  --source-root .\samples\inspection_devices `
  --project inspection `
  --mode best-effort `
  --report .\reports\onboarding_2026-04-03\import_devices\import_excel_report.json
```

执行后重点确认：

- `success_count`
- `fail_count`
- `cameras_added`
- `cameras_updated`
- `cameras_replaced`
- `cameras_retired`

若存在残留 `updated/replaced`，先用：

```powershell
python build_inventory_review_checklist.py `
  --dry-run-report .\reports\onboarding_2026-04-03\import_devices\import_excel_report.json `
  --json-out .\reports\onboarding_2026-04-03\import_devices\inventory_review.json `
  --md-out .\reports\onboarding_2026-04-03\import_devices\inventory_review.md `
  --csv-out .\reports\onboarding_2026-04-03\import_devices\inventory_review.csv
```

## 九、Step 5：正式导入历史故障 / 工单

先历史故障，再工单增量。

历史故障：

```powershell
python import_faults.py batch `
  --database .\station_monitor.db `
  --project inspection `
  --source .\samples\inspection_faults.xlsx `
  --type-mapping .\samples\inspection_fault_type_mapping.csv `
  --report .\reports\onboarding_2026-04-03\import_faults\import_faults_report.json
```

工单导入：

```powershell
python import_faults_worklog.py `
  --database .\station_monitor.db `
  --project inspection `
  --source .\samples\inspection_worklog.xlsx `
  --report .\reports\onboarding_2026-04-03\import_worklog\worklog_report.json
```

必须确认：

- 正式入库数
- 被幂等拦截数
- 入队列数
- 站名提议数
- fail-on 阻断情况

## 十、Step 6：处理待确认队列

按 [REVIEW_CENTER_RUNBOOK.md](/e:/项目/变电站图像监控运维平台/REVIEW_CENTER_RUNBOOK.md) 执行。

顺序固定：

1. 先处理站名提议
2. 再处理队列站点指派
3. 再做导入 / 并单 / 驳回
4. 最后做抽样复核

本轮必须留下：

- 处理责任人
- 批量操作范围
- 抽样复核结论
- 遗留问题清单

## 十一、Step 7：批次审计

每个正式批次都要做一次审计归档。

```powershell
python inspect_import_batch.py `
  --database .\station_monitor.db `
  --batch-id 123 `
  --report .\reports\onboarding_2026-04-03\audit\batch_123_audit.json `
  --export-dir .\reports\onboarding_2026-04-03\audit\batch_123
```

重点确认：

- 是否仍有异常待确认记录
- 是否有明显错误写入正式表
- 是否需要进入恢复流程

如有整批系统性错误，转 [IMPORT_BATCH_RECOVERY_RUNBOOK.md](/e:/项目/变电站图像监控运维平台/IMPORT_BATCH_RECOVERY_RUNBOOK.md)。

## 十二、Step 8：形成验收结论

验收结论至少要写清：

- 本轮项目
- 样本范围
- 放行结论
- 导入结果摘要
- 队列处理结果摘要
- 是否存在遗留阻断项
- 是否允许进入下一项目或下一批次

## 十三、推荐执行节奏

建议按项目推进，而不是三套系统一起上：

1. 先选一个项目做完整闭环
2. 完成后复盘规则和映射
3. 再接第二个项目
4. 最后再做三项目汇总验收

原因：

- 可以尽早暴露映射规则问题
- 可以减少跨项目并发带来的定位成本
- 可以让站名映射和故障类型映射逐步沉淀
