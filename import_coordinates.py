# import_coordinates.py — 导入变电站经纬度坐标
"""
从Excel文件导入变电站经纬度坐标

使用方式:
    python import_coordinates.py
    python import_coordinates.py coordinates.xlsx
"""
import argparse
import sqlite3
import os
import sys

try:
    import openpyxl
except ImportError:
    print("错误: 需要 openpyxl 库，运行: pip install openpyxl")
    sys.exit(1)

DB_PATH = os.path.join(os.path.dirname(__file__), 'station_monitor.db')


def load_coordinates_from_excel(filepath):
    """从Excel加载坐标数据"""
    wb = openpyxl.load_workbook(filepath)
    ws = wb.active

    coords = {}
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        # 期望格式: 变电站名称, 电压等级, 纬度, 经度
        # 或者: 变电站名称, 纬度, 经度
        if not row[0]:
            continue

        name = str(row[0]).strip()
        if len(row) >= 4:
            # 格式: 名称, 电压等级, 纬度, 经度
            try:
                lat = float(row[2]) if row[2] else None
                lng = float(row[3]) if row[3] else None
            except (ValueError, TypeError):
                print(f"  行{i}: '{name}' 坐标格式错误，跳过")
                continue
        elif len(row) >= 3:
            # 格式: 名称, 纬度, 经度
            try:
                lat = float(row[1]) if row[1] else None
                lng = float(row[2]) if row[2] else None
            except (ValueError, TypeError):
                print(f"  行{i}: '{name}' 坐标格式错误，跳过")
                continue
        else:
            print(f"  行{i}: 列数不足，跳过")
            continue

        if lat and lng:
            coords[name] = (lat, lng)
        else:
            print(f"  行{i}: '{name}' 坐标为空，跳过")

    return coords


def match_and_update(coords):
    """匹配变电站并更新坐标"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 获取所有变电站
    cursor.execute("SELECT id, name, voltage_level FROM stations")
    stations = cursor.fetchall()

    updated = 0
    not_found = []

    for station_id, name, voltage_level in stations:
        # 优先精确匹配（名称+电压等级）
        key = f"{name}{voltage_level}" if voltage_level else name
        matched = False

        for coord_name, (lat, lng) in coords.items():
            if coord_name == name or coord_name == key:
                cursor.execute(
                    "UPDATE stations SET latitude = ?, longitude = ? WHERE id = ?",
                    (lat, lng, station_id)
                )
                updated += 1
                matched = True
                break
            # 部分匹配（名称包含）
            if name in coord_name or coord_name in name:
                cursor.execute(
                    "UPDATE stations SET latitude = ?, longitude = ? WHERE id = ?",
                    (lat, lng, station_id)
                )
                updated += 1
                matched = True
                break

        if not matched:
            not_found.append(name)

    conn.commit()
    conn.close()

    return updated, not_found


def show_current_coordinates():
    """显示当前已导入的坐标"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, county, latitude, longitude FROM stations ORDER BY county, name")
    stations = cursor.fetchall()

    has_coords = []
    no_coords = []

    for row in stations:
        if row[3] and row[4]:
            has_coords.append(row)
        else:
            no_coords.append(row)

    print(f"\n已导入坐标: {len(has_coords)} 个")
    print("-" * 60)
    for s in has_coords:
        print(f"  {s[1]} ({s[2]}) - 纬度:{s[3]}, 经度:{s[4]}")

    print(f"\n未导入坐标: {len(no_coords)} 个")
    if no_coords:
        for s in no_coords[:10]:
            print(f"  {s[1]} ({s[2]})")
        if len(no_coords) > 10:
            print(f"  ... 还有 {len(no_coords) - 10} 个")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description='导入变电站经纬度坐标')
    parser.add_argument('file', nargs='?', help='Excel文件路径')
    parser.add_argument('--show', action='store_true', help='显示当前坐标')
    args = parser.parse_args()

    if args.show:
        show_current_coordinates()
        return

    # 显示当前状态
    show_current_coordinates()

    if not args.file:
        print("\n用法:")
        print("  python import_coordinates.py coordinates.xlsx  # 导入坐标")
        print("  python import_coordinates.py --show           # 显示当前坐标")
        print("\nExcel格式要求:")
        print("  第1列: 变电站名称")
        print("  第2列: 电压等级(可选)")
        print("  第3列: 纬度")
        print("  第4列: 经度")
        return

    if not os.path.exists(args.file):
        print(f"文件不存在: {args.file}")
        return

    print(f"\n从 {args.file} 加载坐标...")
    coords = load_coordinates_from_excel(args.file)
    print(f"加载了 {len(coords)} 条坐标记录")

    if not coords:
        print("没有有效的坐标数据")
        return

    print("\n匹配并更新数据库...")
    updated, not_found = match_and_update(coords)
    print(f"成功更新: {updated} 个变电站")

    if not_found:
        print(f"\n未匹配的变电站 ({len(not_found)} 个):")
        for name in not_found[:20]:
            print(f"  - {name}")
        if len(not_found) > 20:
            print(f"  ... 还有 {len(not_found) - 20} 个")

    # 验证结果
    cursor.execute("SELECT COUNT(*) FROM stations WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    count = cursor.fetchone()[0]
    print(f"\n当前已设置坐标: {count} 个变电站")

    conn.close()


if __name__ == '__main__':
    main()
