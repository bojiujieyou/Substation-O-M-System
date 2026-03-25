# precheck_excel.py — Excel数据预检脚本
"""
预检71个变电站Excel文件，扫描格式差异

执行方式:
    python precheck_excel.py

输出:
    - 每个文件的行数
    - 关键字段位置
    - 格式不一致的文件报告
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from parse_excel import validate_excel_structure, ExcelParseError

DATA_SOURCE_PATH = r'e:\办公\图像监控\图像监控设备资料'

# 县区目录
COUNTIES = ['丽水', '云和', '庆元', '景宁', '松阳', '缙云', '遂昌', '青田', '龙泉']

def scan_excel_files():
    """扫描所有Excel文件"""
    results = []
    errors = []
    format_differs = []

    print("=" * 60)
    print("变电站Excel数据预检")
    print("=" * 60)
    print()

    for county in COUNTIES:
        county_path = os.path.join(DATA_SOURCE_PATH, county)
        if not os.path.exists(county_path):
            print(f"[警告] 县区目录不存在: {county_path}")
            continue

        print(f"\n[{county}]")
        print("-" * 40)

        # 查找该县区下所有xlsx文件
        xlsx_files = list(Path(county_path).glob('*.xlsx'))
        xlsx_files.extend(Path(county_path).glob('*.xls'))

        if not xlsx_files:
            print(f"  未找到Excel文件")
            continue

        for filepath in sorted(xlsx_files):
            filename = filepath.name
            try:
                result = validate_excel_structure(str(filepath))
                status = "OK" if result['valid'] else "FAIL"
                print(f"  {status} {filename}: {result['rows']}行")

                if result['errors']:
                    for err in result['errors']:
                        print(f"      错误: {err}")
                        errors.append({'file': str(filepath), 'error': err})

                # 记录用于格式比较
                results.append({
                    'county': county,
                    'file': filename,
                    'filepath': str(filepath),
                    'rows': result['rows'],
                    'valid': result['valid']
                })

            except Exception as e:
                print(f"  FAIL {filename}: 解析失败 - {e}")
                errors.append({'file': str(filepath), 'error': str(e)})

    # 格式差异分析
    print()
    print("=" * 60)
    print("格式差异分析")
    print("=" * 60)

    valid_results = [r for r in results if r['valid']]
    if not valid_results:
        print("没有有效的Excel文件可供分析")
        return

    row_counts = {}
    for r in valid_results:
        rows = r['rows']
        if rows not in row_counts:
            row_counts[rows] = []
        row_counts[rows].append(r['file'])

    print(f"\n共有 {len(valid_results)} 个有效文件")
    print(f"行数分布:")
    for rows in sorted(row_counts.keys()):
        files = row_counts[rows]
        print(f"  {rows}行: {len(files)}个文件")
        if len(files) <= 5:
            for f in files:
                print(f"    - {f}")
        else:
            print(f"    - {files[0]}, {files[1]}, ... 等{len(files)}个")

    # 检查格式异常的文件（行数与众数差异大于20%）
    if row_counts:
        mode_rows = max(row_counts.keys(), key=lambda k: len(row_counts[k]))
        for r in valid_results:
            diff_pct = abs(r['rows'] - mode_rows) / mode_rows * 100
            if diff_pct > 20:
                format_differs.append(r)

    if format_differs:
        print(f"\n[警告] 以下文件行数与多数差异超过20%:")
        for r in format_differs:
            print(f"  {r['county']}/{r['file']}: {r['rows']}行 (众数: {mode_rows}行)")

    if errors:
        print(f"\n[错误] 共 {len(errors)} 个文件解析失败:")
        for e in errors:
            print(f"  {e['file']}")
            print(f"    {e['error']}")

    print()
    print("=" * 60)
    print("预检完成")
    print("=" * 60)

    return {
        'total': len(results),
        'valid': len(valid_results),
        'errors': len(errors),
        'format_differs': len(format_differs),
        'details': results,
        'error_details': errors
    }

if __name__ == '__main__':
    scan_excel_files()
