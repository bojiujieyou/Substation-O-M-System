from worklog_fault_types import classify_worklog_entry, get_catalog_for_project, infer_worklog_fault_type


def test_catalog_contains_expected_richer_fault_types():
    catalog = get_catalog_for_project("unified")
    codes = {item["type_code"] for item in catalog}
    assert "CAMERA_DEVICE_FAULT" in codes
    assert "NETWORK_FAULT" in codes
    assert "POWER_SUPPLY_FAULT" in codes
    assert "RECORDER_STORAGE_FAULT" in codes
    assert "INSTALLATION_DEBUG" not in codes
    assert "INSPECTION_MAINTENANCE" not in codes
    assert "SITE_SURVEY_ACCEPTANCE" not in codes


def test_infer_worklog_fault_type_maps_specific_fault_patterns():
    assert infer_worklog_fault_type("110kV场地东侧摄像机故障更换")["type_code"] == "CAMERA_DEVICE_FAULT"
    assert infer_worklog_fault_type("全站离线抢修")["type_code"] == "NETWORK_FAULT"
    assert infer_worklog_fault_type("更换光纤收发器集中电源")["type_code"] == "POWER_SUPPLY_FAULT"
    assert infer_worklog_fault_type("更换故障硬盘一块，增加硬盘两块")["type_code"] == "RECORDER_STORAGE_FAULT"
    assert infer_worklog_fault_type("图像监控设备巡检") is None


def test_classify_worklog_entry_skips_non_fault_work_items():
    result = classify_worklog_entry("图像监控设备巡检")
    assert result["is_fault"] is False
    assert result["reason"] == "inspection_maintenance"


def test_classify_worklog_entry_reclassifies_repair_records_as_faults():
    result = classify_worklog_entry("主控室1#、主控室2#网线被拔出，插上后恢复正常")
    assert result["is_fault"] is True
    assert result["type_code"] == "CABLE_LINK_FAULT"
