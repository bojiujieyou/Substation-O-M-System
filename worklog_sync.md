# worklog_sync — 故障闭环 → 工作记录自动同步

> 运维平台闭环一条故障后，自动将故障信息写入 `E:\办公\工作记录\工作记录.xlsx`。

## 触发时机

`app.py` 的 `update_fault_status` 中，故障状态变为 `closed` 且 `db.commit()` 成功后，调用：

```python
_sync_closed_fault_to_worklog(fault_id)
```

这是一个 fire-and-forget hook，用 try/except 包裹，写入失败不影响闭环本身。

## 数据流

```
故障闭环 (app.py)
  │
  ├─ fault_reports: fault_type, handler_name, handler_note, closed_at,
  │                 equipment_type, equipment_quantity, system_type
  ├─ stations: name (→ 去掉 110kV/220kV 前缀), county
  ├─ fault_report_cameras → cameras: location_desc, area, camera_index
  │
  ▼
worklog_sync.sync_fault_to_worklog(db, fault_id)
  │
  ├─ 拼接故障描述：摄像机位置 + 故障类型 + 处理方式
  ├─ 定位工作记录.xlsx 中当前年份区域
  ├─ 追加一行
  │
  ▼
工作记录.xlsx（8列）
  序号 | 时间 | 变电站 | 地点 | 故障描述 | 类型 | 甲供 | 工作负责人
```

## 字段映射规则

| 工作记录列 | 来源 | 处理逻辑 |
|---|---|---|
| 序号 | 自增 | 当前年份区域内 max(seq) + 1 |
| 时间 | `closed_at` | 转为 `2026年5月6日` 格式 |
| 变电站 | `stations.name` | 去掉电压等级前缀：`220kV睦田变` → `睦田变` |
| 地点 | `stations.county` | 如 `缙云`、`云和`、`庆元` |
| 故障描述 | 拼接生成 | 见下方"描述拼接规则" |
| 类型 | `system_type` | `image_monitoring` → `图像监控`，`smart_patrol` → `智能巡视`，默认 `图像监控` |
| 甲供 | `equipment_type` + `equipment_quantity` | `摄像机` × 1 → `1台摄像机`，无量词自动补"台"，0或空则留空 |
| 工作负责人 | `handler_name` | 如 `殷彬` |

## 描述拼接规则

从 `handler_note` 中提取处理方式关键词（更换/维修/恢复/消缺/制作/拆除/敷设/调试等），拼接格式：

| 场景 | 输出 |
|---|---|
| 单摄像机，位置含"摄像机" | `主控楼门厅侧摄像机故障更换` |
| 单摄像机，位置不含 | `110kV场地西侧摄像机故障更换` |
| 多摄像机，总长度 ≤ 60字 | `大门北侧-1#、大门东侧-5#摄像机故障更换` |
| 多摄像机，总长度 > 60字 | `共6处交换机/供电异常更换` |
| 无摄像机关联 | 直接用 `handler_note` 原文 |

## 年份区域定位

工作记录.xlsx 按年份分段存储，每段第一列为 `2026年` 这样的标题行：

```
行1:  [表头]
行2:  [2022年]          ← 年份标题
行3:  [1, 2022年6月02日, 睦田变, ...]
...
行62: [2023年]          ← 下一年份标题
行63: [1, 2023年1月09日, 枫树变, ...]
```

定位逻辑：
1. 扫描所有行，匹配 `^\d{4}年$` 模式，建立年份→行号映射
2. 目标年份存在：取标题行+1 到下一个标题行-1
3. 目标年份不存在：在最后一个年份区域之后追加
4. 在区域内找第一个空行写入，序号取区域内最大值+1

## 对外接口

### `sync_fault_to_worklog(db, fault_id, worklog_path=None) → bool`

参数：
- `db` — 数据库连接（需要 `row_factory=True`，返回的行支持 `row["column"]` 字典式访问）
- `fault_id` — 故障ID（int）
- `worklog_path` — 工作记录.xlsx 路径（可选，默认自动检测 `E:\办公\工作记录\工作记录.xlsx`）

返回：
- `True` 写入成功
- `False` 故障不存在、未闭环、或其他预期内跳过

依赖的数据库查询：
```sql
-- 查故障 + 站点
SELECT fr.*, s.name AS station_name, s.county AS station_county
FROM fault_reports fr JOIN stations s ON fr.station_id = s.id WHERE fr.id = ?

-- 查关联摄像机
SELECT c.location_desc, c.area, c.camera_index
FROM fault_report_cameras frc JOIN cameras c ON frc.camera_id = c.id
WHERE frc.fault_report_id = ?
```

## 与差旅报销的衔接

本模块只负责写入工作记录，不直接触发差旅报销。

差旅报销由 cron 定时任务在每月20日运行 `trip-reimbursement/index.py`，该脚本从工作记录.xlsx 中提取当月出差记录（地点非"丽水""莲都"的记录），自动生成出差审批单、差旅费报销单、费用报销单。

链路：`故障闭环 → worklog_sync 写入工作记录 → [每月20日] trip-reimbursement 生成报销单`

## 文件清单

| 文件 | 职责 |
|---|---|
| `worklog_sync.py` | 同步逻辑（本模块） |
| `app.py` | 在闭环 hook `_sync_closed_fault_to_worklog` 中调用 |
| `E:\办公\工作记录\工作记录.xlsx` | 写入目标 |
| `E:\办公\工作记录\出差\trip-reimbursement/` | 下游差旅报销脚本 |

## 注意事项

- 写入操作是追加式的，不会修改或删除已有数据
- 如果工作记录.xlsx 被其他程序占用（如 Excel 打开），写入会失败，但不影响闭环
- 同一条故障如果被反复闭环（理论上不会，状态机不允许），会重复写入。去重由幂等性设计在平台侧保证
