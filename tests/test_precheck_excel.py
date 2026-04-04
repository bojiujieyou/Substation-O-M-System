from pathlib import Path

import precheck_excel


def test_scan_excel_files_returns_structured_report(monkeypatch, tmp_path):
    county_dir = tmp_path / "测试县"
    county_dir.mkdir()
    good_file = county_dir / "good.xlsx"
    odd_file = county_dir / "odd.xlsx"
    bad_file = county_dir / "bad.xlsx"
    for item in (good_file, odd_file, bad_file):
        item.write_text("placeholder", encoding="utf-8")

    def fake_validate_excel_structure(filepath):
        filename = Path(filepath).name
        if filename == "good.xlsx":
            return {"valid": True, "rows": 100, "errors": []}
        if filename == "odd.xlsx":
            return {"valid": True, "rows": 140, "errors": []}
        return {"valid": False, "rows": 0, "errors": ["格式异常"]}

    monkeypatch.setattr(precheck_excel, "COUNTIES", ["测试县"])
    monkeypatch.setattr(precheck_excel, "DATA_SOURCE_PATH", str(tmp_path))
    monkeypatch.setattr(precheck_excel, "validate_excel_structure", fake_validate_excel_structure)

    report = precheck_excel.scan_excel_files()

    assert report["total"] == 3
    assert report["valid"] == 2
    assert report["errors"] == 1
    assert report["format_differs"] == 1
    assert report["mode_rows"] == 100
    assert report["row_count_distribution"] == {"100": 1, "140": 1}
    assert report["format_differs_details"][0]["file"] == "odd.xlsx"
    assert report["error_details"][0]["error"] == "格式异常"
