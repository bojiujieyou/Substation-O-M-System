import json
from pathlib import Path

from openpyxl import Workbook

from build_inventory_review_checklist import build_review_checklist


def build_flat_inventory_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "序号",
            "变电站",
            "设备名称",
            "设备型号",
            "生产厂家",
            "使用单位",
            "使用类型",
            "视频类型",
            "安装位置",
            "运行状态",
            "设备来源",
        ]
    )
    ws.append(
        [
            "1",
            "220kV测试变电站",
            "测试变-#1主变西侧#1球机",
            "DS-1",
            "厂商A",
            "",
            "球机",
            "可见光",
            "#1主变西侧",
            "在线",
            "南瑞",
        ]
    )
    ws.append(
        [
            "2",
            "220kV测试变电站",
            "测试变-#1主变西侧#1球机",
            "DS-1",
            "厂商A",
            "",
            "球机",
            "可见光",
            "#1主变西侧",
            "在线",
            "南瑞",
        ]
    )
    ws.append(
        [
            "3",
            "220kV测试变电站",
            "测试变-#1主变西侧#2球机",
            "DS-2",
            "厂商A",
            "",
            "球机",
            "测温",
            "#1主变西侧",
            "在线",
            "南瑞",
        ]
    )
    wb.save(path)


def test_build_review_checklist_generates_station_level_signals(tmp_path):
    workbook_path = tmp_path / "220kV测试变电站.xlsx"
    build_flat_inventory_workbook(workbook_path)

    dry_run_report_path = tmp_path / "dry_run_report.json"
    dry_run_report = {
        "project": "inspection",
        "mode": "best-effort",
        "dry_run": True,
        "rows": [
            {
                "county": "测试县",
                "file": workbook_path.name,
                "filepath": str(workbook_path),
                "status": "imported",
                "station": "220kV测试变电站",
                "camera_rows": 3,
                "cameras_updated": 2,
                "cameras_replaced": 0,
                "cameras_retired": 0,
            }
        ],
    }
    dry_run_report_path.write_text(json.dumps(dry_run_report, ensure_ascii=False, indent=2), encoding="utf-8")

    json_out = tmp_path / "checklist.json"
    md_out = tmp_path / "checklist.md"
    csv_out = tmp_path / "checklist.csv"
    checklist = build_review_checklist(
        dry_run_report_path=dry_run_report_path,
        json_output_path=json_out,
        markdown_output_path=md_out,
        csv_output_path=csv_out,
    )

    assert checklist["summary"]["station_count"] == 1
    assert checklist["summary"]["updated_total"] == 2
    station = checklist["stations"][0]
    assert station["station"] == "220kV测试变电站"
    assert station["review_priority"] == "medium"
    assert "duplicate_device_code" in station["issue_types"]
    assert station["signals"]["duplicate_device_code_groups"] == 1
    assert station["signals"]["location_collision_groups"] == 1
    assert json_out.exists()
    assert md_out.exists()
    assert csv_out.exists()
    assert "220kV测试变电站" in md_out.read_text(encoding="utf-8")


def test_build_review_checklist_marks_parse_failures_for_missing_source_file(tmp_path):
    dry_run_report_path = tmp_path / "dry_run_report_missing.json"
    dry_run_report = {
        "project": "inspection",
        "mode": "best-effort",
        "dry_run": True,
        "rows": [
            {
                "county": "测试县",
                "file": "missing.xlsx",
                "filepath": str(tmp_path / "missing.xlsx"),
                "status": "imported",
                "station": "220kV缺失变电站",
                "camera_rows": 5,
                "cameras_updated": 5,
                "cameras_replaced": 0,
                "cameras_retired": 0,
            }
        ],
    }
    dry_run_report_path.write_text(json.dumps(dry_run_report, ensure_ascii=False, indent=2), encoding="utf-8")

    checklist = build_review_checklist(dry_run_report_path=dry_run_report_path)

    assert checklist["summary"]["station_count"] == 1
    station = checklist["stations"][0]
    assert station["issue_types"] == ["report_parse_failed"]
    assert station["review_priority"] == "high"
