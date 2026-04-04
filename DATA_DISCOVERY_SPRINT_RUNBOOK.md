# Data Discovery Sprint Runbook

## 目标

在正式全量导入前，用真实样本数据验证冻结设计稿中的放行条件：

- 三系统字段结构差异是否已知
- 站名归一化是否足够稳定
- `slot_code` 生成冲突率是否可接受
- 历史故障类型映射率是否可接受
- 时间戳解析率与时区是否可接受
- 槽位查询是否需要进入缓存评审

## 输入物

至少准备以下一类样本：

- 设备样本：项目设备 Excel 清单目录或单文件
- 故障样本：历史故障 CSV/XLSX 导出

建议同时准备：

- 当前目标数据库副本
- 历史故障类型映射表 `mapping.csv`

## 执行命令

```powershell
python data_discovery_sprint.py `
  --project unified `
  --source-type import_excel `
  --database .\station_monitor.db `
  --device-source .\samples\devices `
  --fault-source .\samples\faults `
  --type-mapping .\samples\fault_type_mapping.csv `
  --report .\reports\data_discovery_report.json `
  --summary .\reports\data_discovery_report.md
```

参数说明：

- `--project`
  - 目标项目 `code`
- `--source-type`
  - 站名匹配和 `source_record_key` 语义使用的来源类型
- `--database`
  - 用于项目故障类型目录、站名映射和槽位查询性能估算
- `--device-source`
  - 可重复传入；支持目录或单文件
- `--fault-source`
  - 可重复传入；支持目录或单文件
- `--type-mapping`
  - 可选；用于评估历史故障类型自动映射率
- `--report`
  - JSON 全量报告
- `--summary`
  - Markdown 摘要报告

## 输出物

- `data_discovery_report.json`
  - 机器可读明细
- `data_discovery_report.md`
  - 评审摘要

报告至少关注以下字段：

- `device_inventory.slot_code_conflict_rate`
- `fault_history.fault_type_mapping_rate`
- `fault_history.timestamp_parse_rate`
- `fault_history.station_match_breakdown`
- `fault_history.top_unresolved_station_names`
- `performance.p95_ms`
- `release_decision.overall`

## 放行规则

### `slot_code` 冲突率

- `<= 5%`
  - 允许进入后续正式接入
- `> 5% 且 <= 15%`
  - 允许双轨推进：补规则 + 局部人工清洗
- `> 15%`
  - 阻断全量导入

### 历史故障类型自动映射率

- `>= 90%`
  - 允许自动导入 + 少量人工确认
- `>= 75% 且 < 90%`
  - 允许自动导入 + 待确认队列双轨
- `< 75%`
  - 阻断全量接入

### 时间戳可解析率

- `>= 95%`
  - 才允许进入正式统计与幂等流程
- `< 95%`
  - 阻断进入正式统计逻辑

### 槽位查询性能

- `P95 <= 200ms`
  - 保持当前方案
- `P95 > 200ms`
  - 进入缓存/预聚合评审

## 执行后动作

如果 `release_decision.overall = pass`：

- 可以进入 Phase 5 真实批次导入

如果 `release_decision.overall = dual_track`：

- 保留自动导入
- 同时补站名映射、类型映射或 `slot_code` 规则
- 明确人工处理量和责任人

如果 `release_decision.overall = block`：

- 不进入正式数据接入
- 先修正规则、映射或时间字段清洗策略
- 修正后重新跑 Discovery 报告

## 结果归档

本轮 Sprint 结束时，至少归档：

- 样本来源说明
- 报告 JSON
- 报告 Markdown
- 放行结论
- 如未放行，列出阻断项和修复负责人
