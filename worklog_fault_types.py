from __future__ import annotations

from copy import deepcopy


DEFAULT_PROJECT_CODES = ("unified", "inspection", "auxiliary")

BASE_FAULT_TYPES = [
    {
        "type_code": "DEVICE_FAULT",
        "type_label": "其他设备异常",
        "semantic_group": "DEVICE_FAULT",
        "sort_order": 10,
        "is_active": 1,
    },
    {
        "type_code": "CAMERA_DEVICE_FAULT",
        "type_label": "摄像机/球机/枪机故障",
        "semantic_group": "CAMERA_DEVICE_FAULT",
        "sort_order": 20,
        "is_active": 1,
    },
    {
        "type_code": "NETWORK_FAULT",
        "type_label": "网络掉线/通信异常",
        "semantic_group": "NETWORK_FAULT",
        "sort_order": 30,
        "is_active": 1,
    },
    {
        "type_code": "FIBER_TXRX_FAULT",
        "type_label": "光纤收发器/供电异常",
        "semantic_group": "FIBER_TXRX_FAULT",
        "sort_order": 40,
        "is_active": 1,
    },
    {
        "type_code": "POWER_SUPPLY_FAULT",
        "type_label": "集中电源/供电异常",
        "semantic_group": "POWER_SUPPLY_FAULT",
        "sort_order": 50,
        "is_active": 1,
    },
    {
        "type_code": "SWITCH_FAULT",
        "type_label": "交换机/供电异常",
        "semantic_group": "SWITCH_FAULT",
        "sort_order": 60,
        "is_active": 1,
    },
    {
        "type_code": "CABLE_LINK_FAULT",
        "type_label": "网线/尾纤/链路异常",
        "semantic_group": "CABLE_LINK_FAULT",
        "sort_order": 70,
        "is_active": 1,
    },
    {
        "type_code": "RECORDER_STORAGE_FAULT",
        "type_label": "录像机/硬盘异常",
        "semantic_group": "RECORDER_STORAGE_FAULT",
        "sort_order": 80,
        "is_active": 1,
    },
    {
        "type_code": "PLATFORM_DISPLAY_FAULT",
        "type_label": "平台无图像/图像不上送",
        "semantic_group": "PLATFORM_DISPLAY_FAULT",
        "sort_order": 90,
        "is_active": 1,
    },
]

PROJECT_FAULT_TYPE_CATALOGS = {
    project_code: deepcopy(BASE_FAULT_TYPES) for project_code in DEFAULT_PROJECT_CODES
}

FAULT_TYPE_BY_CODE = {item["type_code"]: item for item in BASE_FAULT_TYPES}

NON_FAULT_PATTERNS = [
    {
        "reason": "inspection_maintenance",
        "keywords": (
            "巡检",
            "巡查",
            "消缺",
            "后续工作配置",
            "设备巡检",
            "信通设备巡检",
        ),
    },
    {
        "reason": "site_survey_acceptance",
        "keywords": (
            "验收",
            "现场勘察",
            "现场勘查",
            "现场复勘",
            "现场复查",
        ),
    },
    {
        "reason": "installation_debug",
        "keywords": (
            "网络优化提升",
            "现场调试接入",
            "智巡系统接入、调试",
            "智能巡视设备现场调试接入",
            "远程智慧监控系统调试",
            "配合电建改造拆除",
            "摄像机拆除",
            "摄像机复原",
            "已拆除，无需更换",
        ),
    },
]

FAULT_RULES = [
    (
        "NETWORK_FAULT",
        (
            "全站离线",
            "网络",
            "断网",
            "掉线",
            "通信",
        ),
    ),
    (
        "PLATFORM_DISPLAY_FAULT",
        (
            "图像无法显示",
            "平台上不显示",
            "本地正常，平台上不显示",
            "系统原因无法处理",
            "平台图像",
        ),
    ),
    (
        "RECORDER_STORAGE_FAULT",
        (
            "硬盘",
            "录像机",
            "格式化硬盘",
            "存储",
        ),
    ),
    (
        "SWITCH_FAULT",
        (
            "交换机",
        ),
    ),
    (
        "POWER_SUPPLY_FAULT",
        (
            "断电",
            "空开跳闸",
            "集中电源",
            "电源插槽",
            "电源故障",
            "控制电源",
            "电机电源",
            "换接口",
            "接口故障",
        ),
    ),
    (
        "FIBER_TXRX_FAULT",
        (
            "光纤收发器",
        ),
    ),
    (
        "CABLE_LINK_FAULT",
        (
            "网线",
            "水晶头",
            "尾纤",
            "光纤被咬坏",
            "光纤信号弱",
            "重新布置光纤",
            "光纤故障",
            "光纤传输故障",
            "熔接",
            "插上后恢复正常",
        ),
    ),
    (
        "CAMERA_DEVICE_FAULT",
        (
            "摄像头",
            "摄像机",
            "球机",
            "枪机",
            "云台",
        ),
    ),
]


def get_catalog_for_project(project_code: str | None):
    project_key = (project_code or "").strip()
    catalog = PROJECT_FAULT_TYPE_CATALOGS.get(project_key) or BASE_FAULT_TYPES
    return deepcopy(catalog)


def fault_type_by_code(type_code: str | None):
    if not type_code:
        return None
    return FAULT_TYPE_BY_CODE.get(str(type_code).strip())


def classify_worklog_entry(content: str | None):
    text = str(content or "").strip()
    for pattern in NON_FAULT_PATTERNS:
        if any(token in text for token in pattern["keywords"]):
            return {
                "is_fault": False,
                "reason": pattern["reason"],
                "type_code": None,
                "type_label": None,
            }

    for type_code, tokens in FAULT_RULES:
        if any(token in text for token in tokens):
            fault_type = deepcopy(FAULT_TYPE_BY_CODE[type_code])
            return {
                "is_fault": True,
                "reason": "matched_fault_rule",
                **fault_type,
            }

    fault_type = deepcopy(FAULT_TYPE_BY_CODE["DEVICE_FAULT"])
    return {
        "is_fault": True,
        "reason": "fallback_default",
        **fault_type,
    }


def infer_worklog_fault_type(content: str | None):
    result = classify_worklog_entry(content)
    if not result["is_fault"]:
        return None
    return {
        "type_code": result["type_code"],
        "type_label": result["type_label"],
        "semantic_group": result["semantic_group"],
        "sort_order": result["sort_order"],
        "is_active": result["is_active"],
    }
