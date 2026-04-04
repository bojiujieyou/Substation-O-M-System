# Import Batch Recovery Runbook

## 目标

当某次导入“成功写入后才发现整批有问题”时，先基于 `import_batch_id` 审计影响范围，再决定：

- 是否只处理待确认队列或站名提议
- 是否需要人工修正
- 是否应该停止继续导入
- 是否需要走数据库备份恢复

本 Runbook 遵循冻结设计稿的约束：

- `import_batch_id` 是批次级审计锚点
- V1 不承诺通用 UI 一键回滚
- 正式回滚主路径仍然是备份恢复

## 前提

需要以下信息：

- 目标数据库路径
- 目标批次 `import_batch_id`
- 本批次对应的导入报告路径，如果有

## 第一步：先做批次审计

```powershell
python inspect_import_batch.py `
  --database .\station_monitor.db `
  --batch-id 123 `
  --report .\reports\batch_123_audit.json `
  --export-dir .\reports\batch_123
```

输出物：

- `batch_123_audit.json`
- `fault_rows.csv`
- `review_rows.csv`
- `proposal_rows.csv`

## 第二步：判断影响范围

重点看：

- `summary.fault_rows`
- `summary.review_queue_rows`
- `summary.station_name_proposals`
- `summary.fault_status_breakdown`
- `summary.review_issue_type_breakdown`

### 场景 A：只有队列和提议，没有正式故障

特征：

- `fault_rows = 0`
- `review_queue_rows > 0` 或 `station_name_proposals > 0`

处理建议：

- 通常不需要备份恢复
- 优先人工处理或清理待确认队列、站名提议

### 场景 B：已写入正式故障，但问题只在少量记录

特征：

- `fault_rows > 0`
- 审计导出后可以明确定位具体记录

处理建议：

- 先停止继续导入
- 导出受影响记录做人工核对
- 如需修正，优先写专用修复脚本并复核
- 不建议直接手改数据库

### 场景 C：整批规则错误，正式故障已大量入库

特征：

- `fault_rows > 0`
- 且问题是系统性的，例如站名映射规则错、项目错、时区错、故障类型映射错

处理建议：

- 立即停止后续导入
- 优先走数据库备份恢复
- 恢复前保留本批次审计报告和导出 CSV

## 第三步：决定恢复路径

### 推荐使用备份恢复的情况

- 批次影响大量正式故障
- 错误属于规则级、系统级，而不是单条数据问题
- 人工修正成本高，且容易漏改

### 推荐人工处理或专用修复脚本的情况

- 只有少量记录异常
- 影响范围已通过审计导出清楚确认
- 错误类型可被安全地定点修复

## 备份恢复前检查

恢复前至少确认：

- 已停止继续导入
- 已保存本批次审计 JSON 和 CSV
- 已确认最近一次可用备份
- 已确认恢复窗口和影响范围

## 恢复后复核

恢复或修复完成后，至少做以下复核：

- 重新跑 `inspect_import_batch.py`，确认该批次不再残留异常数据
- 检查 `fault_reports` 是否仍存在对应 `source_batch_id`
- 检查 `fault_import_review_queue` 和 `station_name_mapping_proposals` 是否与预期一致
- 记录本次事件原因、处理方式和后续预防动作

## 结论规则

- 先审计，后恢复
- 先确认影响范围，后决定路径
- 规则级错误优先备份恢复
- 小范围问题优先专用修复，不做粗暴处理
