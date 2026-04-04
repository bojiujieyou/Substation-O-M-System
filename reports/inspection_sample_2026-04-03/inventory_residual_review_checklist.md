# Inventory Residual Review Checklist

- Source report: `reports\inspection_sample_2026-04-03\migration_rehearsal_v4\import_excel_dry_run_report.json`
- Stations to review: `7`
- Updated rows to confirm: `27`
- Replaced rows to confirm: `0`
- Retired rows to confirm: `0`

## Station Checklist

### 220kV海口变电站
- Priority: `high`
- Change summary: updated `5`, replaced `0`, retired `0`
- Issue types: `duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\丽丽枫运检班\220kV海口变电站.xlsx`
- Suggested actions:
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate slot signature examples:
  - `#1主变西南侧#46 | 球机/可见光 | CH46` x 2
  - `#2主变西南侧#45 | 球机/可见光 | CH45` x 2
  - `#3电抗器南侧#48 | 球机/可见光 | CH48` x 2

### 220kV金亭变电站
- Priority: `high`
- Change summary: updated `6`, replaced `0`, retired `0`
- Issue types: `duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\丽丽枫运检班\220kV金亭变电站.xlsx`
- Suggested actions:
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate slot signature examples:
  - `220kV金亭变#1主变北侧#02 | 球机/可见光 | CH2` x 2
  - `220kV金亭变#1主变西侧#01 | 球机/可见光 | CH1` x 2
  - `220kV金亭变#2主变南侧#03 | 球机/可见光 | CH3` x 2

### 220kV青田变电站
- Priority: `medium`
- Change summary: updated `4`, replaced `0`, retired `0`
- Issue types: `duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\丽丽枫运检班\220kV青田变电站.xlsx`
- Suggested actions:
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate slot signature examples:
  - `位置缺失 | 球机/可见光 | CH1` x 2
  - `位置缺失 | 球机/可见光 | CH2` x 2
  - `位置缺失 | 球机/可见光 | CH3` x 2

### 220kV仙宫变电站
- Priority: `low`
- Change summary: updated `1`, replaced `0`, retired `0`
- Issue types: `duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\丽宏山运检班\220kV仙宫变电站.xlsx`
- Suggested actions:
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate slot signature examples:
  - `位置缺失 | 枪机/可见光 | CH-` x 2

### 220kV宏山变电站
- Priority: `high`
- Change summary: updated `6`, replaced `0`, retired `0`
- Issue types: `duplicate_device_code, duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\丽宏山运检班\220kV宏山变电站.xlsx`
- Suggested actions:
  - 核对样本内是否存在同设备编号重复台账行，必要时先在源文件去重。
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate device code examples:
  - `220kV宏山变#1主变东侧#69测温球机` x 2
  - `220kV宏山变#1主变南侧#70测温球机` x 2
  - `220kV宏山变#1主变西侧#68测温球机` x 2
- Duplicate slot signature examples:
  - `220kV宏山变#1主变东侧#69 | 球机/可见光 | CH69` x 2
  - `220kV宏山变#1主变南侧#70 | 球机/可见光 | CH70` x 2
  - `220kV宏山变#1主变西侧#68 | 球机/可见光 | CH68` x 2

### 220kV濛洲变电站
- Priority: `medium`
- Change summary: updated `3`, replaced `0`, retired `0`
- Issue types: `duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\丽宏山运检班\220kV濛洲变电站.xlsx`
- Suggested actions:
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate slot signature examples:
  - `220kV濛洲变#2主变东侧#08 | 球机/可见光 | CH8` x 2
  - `220kV濛洲变#2主变东侧#09 | 球机/可见光 | CH9` x 2
  - `220kV濛洲变#2主变西侧#06 | 球机/可见光 | CH6` x 2

### 110kV东亭变电站
- Priority: `medium`
- Change summary: updated `2`, replaced `0`, retired `0`
- Issue types: `duplicate_slot_signature`
- Source file: `C:\Users\Administrator\Desktop\26.4.2\遂昌县供电公司\110kV东亭变电站.xlsx`
- Suggested actions:
  - 核对同槽位记录是否应视为同一设备，确认 area/location/channel 的命名是否应规范化。
- Duplicate slot signature examples:
  - `#1主变-东南-37# | - | CH37` x 2
  - `#2主变-北侧-42# | - | CH42` x 2
