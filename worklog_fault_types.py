from __future__ import annotations

from copy import deepcopy


DEFAULT_PROJECT_CODES = ("unified", "inspection", "auxiliary")

BASE_FAULT_TYPES = [
    {
        "type_code": "DEVICE_FAULT",
        "type_label": "设备故障",
        "semantic_group": "DEVICE_FAULT",
        "sort_order": 10,
        "is_active": 1,
    },
    {
        "type_code": "CAMERA_DEVICE_FAULT",
        "type_label": "摄像机故障",
        "semantic_group": "CAMERA_DEVICE_FAULT",
        "sort_order": 20,
        "is_active": 1,
    },
    {
        "type_code": "NETWORK_FAULT",
        "type_label": "网络通信故障",
        "semantic_group": "NETWORK_FAULT",
        "sort_order": 30,
        "is_active": 1,
    },
    {
        "type_code": "FIBER_TXRX_FAULT",
        "type_label": "光纤收发器故障",
        "semantic_group": "FIBER_TXRX_FAULT",
        "sort_order": 40,
        "is_active": 1,
    },
    {
        "type_code": "POWER_SUPPLY_FAULT",
        "type_label": "供电故障",
        "semantic_group": "POWER_SUPPLY_FAULT",
        "sort_order": 50,
        "is_active": 1,
    },
    {
        "type_code": "SWITCH_FAULT",
        "type_label": "交换机故障",
        "semantic_group": "SWITCH_FAULT",
        "sort_order": 60,
        "is_active": 1,
    },
    {
        "type_code": "CABLE_LINK_FAULT",
        "type_label": "网线/光纤链路故障",
        "semantic_group": "CABLE_LINK_FAULT",
        "sort_order": 70,
        "is_active": 1,
    },
    {
        "type_code": "RECORDER_STORAGE_FAULT",
        "type_label": "硬盘录像机/存储故障",
        "semantic_group": "RECORDER_STORAGE_FAULT",
        "sort_order": 80,
        "is_active": 1,
    },
    {
        "type_code": "PLATFORM_DISPLAY_FAULT",
        "type_label": "平台图像异常",
        "semantic_group": "PLATFORM_DISPLAY_FAULT",
        "sort_order": 90,
        "is_active": 1,
    },
    {
        "type_code": "INSPECTION_MAINTENANCE",
        "type_label": "巡检维护",
        "semantic_group": "INSPECTION_MAINTENANCE",
        "sort_order": 100,
        "is_active": 1,
    },
    {
        "type_code": "INSTALLATION_DEBUG",
        "type_label": "施工/调试/接入",
        "semantic_group": "INSTALLATION_DEBUG",
        "sort_order": 110,
        "is_active": 1,
    },
    {
        "type_code": "SITE_SURVEY_ACCEPTANCE",
        "type_label": "验收/勘察",
        "semantic_group": "SITE_SURVEY_ACCEPTANCE",
        "sort_order": 120,
        "is_active": 1,
    },
]

PROJECT_FAULT_TYPE_CATALOGS = {
    project_code: deepcopy(BASE_FAULT_TYPES) for project_code in DEFAULT_PROJECT_CODES
}

FAULT_TYPE_BY_CODE = {item["type_code"]: item for item in BASE_FAULT_TYPES}

RULES = [
    (
        "INSPECTION_MAINTENANCE",
        (
            "巡检",
            "巡查",
            "消缺",
            "后续工作配置",
            "设备巡检",
            "信通设备巡检",
        ),
    ),
    (
        "SITE_SURVEY_ACCEPTANCE",
        (
            "验收",
            "现场勘察",
            "现场勘查",
            "现场复勘",
            "现场复查",
        ),
    ),
    (
        "INSTALLATION_DEBUG",
        (
            "施工",
            "调试",
            "接入",
            "放线",
            "贴签",
            "配置",
            "优化提升",
            "培训",
            "搬迁",
            "复原",
            "拆除",
            "核相试验",
            "线缆敷设",
            "恢复正常",
        ),
    ),
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


def infer_worklog_fault_type(content: str | None):
    text = str(content or "").strip()
    for type_code, tokens in RULES:
        if any(token in text for token in tokens):
            return deepcopy(FAULT_TYPE_BY_CODE[type_code])
    return deepcopy(FAULT_TYPE_BY_CODE["DEVICE_FAULT"])
