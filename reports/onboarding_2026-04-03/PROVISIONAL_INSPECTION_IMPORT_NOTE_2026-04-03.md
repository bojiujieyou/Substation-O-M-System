# 智慧巡视临时基线导入说明

日期：2026-04-03

## 说明

- 本次导入来源：`D:\sample-data\inspection`
- 目标项目：`inspection`
- 用途：作为“智慧巡视项目的当前临时基线数据”先行启用
- 风险前提：用户已明确说明这批数据“还不是很准确”，因此本次导入只作为临时可用数据，不作为正式验收结论

## 本次执行结果

- 导入文件数：`33`
- 成功：`33`
- 失败：`0`
- 批次模式：`best-effort`
- 批次记录：`import_batches.id = 1`

## 导入后项目状态

- `inspection` 槽位数：`2792`
- `inspection` 设备总数：`2792`
- `inspection` active 设备数：`2792`

## 已归档产物

- 备份库：`reports/onboarding_2026-04-03/station_monitor.pre_inspection_sample_import_2026-04-03.db`
- 导入报告：`reports/onboarding_2026-04-03/import_devices/inspection_import_excel_report.json`
- 复核清单：
  - `reports/onboarding_2026-04-03/import_devices/inspection_inventory_review.json`
  - `reports/onboarding_2026-04-03/import_devices/inspection_inventory_review.md`
  - `reports/onboarding_2026-04-03/import_devices/inspection_inventory_review.csv`

## 当前待复核项

- 待复核站点：`7`
- 待确认 `cameras_updated` 合计：`27`
- `cameras_replaced`：`0`
- `cameras_retired`：`0`

重点站点：

- `220kV海口变电站`
- `220kV金亭变电站`
- `220kV青田变电站`
- `220kV仙宫变电站`
- `220kV宏山变电站`
- `220kV濛洲变电站`
- `110kV东亭变电站`

其中：

- `220kV宏山变电站` 同时存在 `duplicate_device_code` 与 `duplicate_slot_signature`
- 其余站点以 `duplicate_slot_signature` 为主

## 当前结论

- 这批智慧巡视样本已经正式写入当前库，可先用于页面展示和后续联调
- 这批数据仍属于“临时基线”，后续一旦收到更准确版本，应基于同一项目继续覆盖导入并重新复核
- 在收到更准确版本前，建议将当前 7 个待复核站点作为优先人工确认对象

