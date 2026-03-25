# import_excel.py — Excel导入脚本
"""
将71个变电站Excel文件导入到SQLite数据库

执行方式:
    python import_excel.py

导入模式: Upsert（站名+电压等级唯一键）
"""

import os
import sys
from pathlib import Path
import sqlite3

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from parse_excel import parse_station_excel, ExcelParseError
from init_db import get_db_path, set_wal_mode

DATA_SOURCE_PATH = r'e:\办公\图像监控\图像监控设备资料'

# 县区目录
COUNTIES = ['丽水', '云和', '庆元', '景宁', '松阳', '缙云', '遂昌', '青田', '龙泉']

def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(get_db_path())
    set_wal_mode(conn)
    return conn

def upsert_station(cursor, station_data):
    """Upsert变电站（站名+电压等级唯一键）"""
    cursor.execute("""
        INSERT INTO stations (name, voltage_level, county, location, ip_range, nvr_ip, nvr_port)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name, voltage_level) DO UPDATE SET
            county = excluded.county,
            location = excluded.location,
            ip_range = excluded.ip_range,
            nvr_ip = excluded.nvr_ip,
            nvr_port = excluded.nvr_port,
            updated_at = CURRENT_TIMESTAMP
    """, (
        station_data['name'],
        station_data['voltage_level'],
        station_data['county'],
        station_data['location'],
        station_data['ip_range'],
        station_data['nvr_ip'],
        station_data['nvr_port'],
    ))

    # 返回插入/更新的station id
    cursor.execute("SELECT id FROM stations WHERE name = ? AND voltage_level = ?",
                   (station_data['name'], station_data['voltage_level']))
    return cursor.fetchone()[0]

def upsert_camera(cursor, station_id, camera_data):
    """Upsert摄像头"""
    cursor.execute("""
        INSERT INTO cameras (station_id, camera_index, area, location_desc, ip_address, channel_port, channel_number)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(station_id, camera_index, channel_number) DO UPDATE SET
            area = excluded.area,
            location_desc = excluded.location_desc,
            ip_address = excluded.ip_address,
            channel_port = excluded.channel_port
    """, (
        station_id,
        camera_data.get('camera_index', ''),
        camera_data.get('area', ''),
        camera_data.get('location', ''),
        camera_data.get('ip_address', ''),
        camera_data.get('channel_port'),
        camera_data.get('channel_number'),
    ))

def import_excel_file(filepath, county):
    """导入单个Excel文件"""
    try:
        data = parse_station_excel(filepath)

        # 确保县区信息正确
        data['station']['county'] = county

        return data, None
    except ExcelParseError as e:
        return None, str(e)
    except Exception as e:
        return None, f"未知错误: {e}"

def main():
    print("=" * 60)
    print("变电站数据导入")
    print("=" * 60)

    # 导入前自动备份
    db_path = get_db_path()
    if os.path.exists(db_path):
        backup_path = db_path + '.backup'
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"\n[备份] 已创建数据库备份: {backup_path}")

    conn = get_connection()
    cursor = conn.cursor()

    total_stations = 0
    total_cameras = 0
    errors = []

    for county in COUNTIES:
        county_path = os.path.join(DATA_SOURCE_PATH, county)
        if not os.path.exists(county_path):
            print(f"\n[警告] 县区目录不存在: {county_path}")
            continue

        print(f"\n[{county}]")

        # 查找该县区下所有xlsx文件
        xlsx_files = list(Path(county_path).glob('*.xlsx'))
        xlsx_files.extend(Path(county_path).glob('*.xls'))

        if not xlsx_files:
            print(f"  未找到Excel文件")
            continue

        county_stations = 0
        county_cameras = 0

        for filepath in sorted(xlsx_files):
            filename = filepath.name
            print(f"  导入: {filename}...", end=" ")

            data, error = import_excel_file(str(filepath), county)

            if error:
                print(f"失败 ({error})")
                errors.append({'file': str(filepath), 'error': error})
                continue

            try:
                # Upsert变电站
                station_id = upsert_station(cursor, data['station'])
                county_stations += 1

                # Upsert摄像头
                for camera in data['cameras']:
                    upsert_camera(cursor, station_id, camera)
                    county_cameras += 1

                print(f"成功 (站:{county_stations}, 摄像头:{len(data['cameras'])})")

            except Exception as e:
                print(f"数据库错误: {e}")
                errors.append({'file': str(filepath), 'error': str(e)})

        total_stations += county_stations
        total_cameras += county_cameras
        print(f"  [{county}] 小计: {county_stations}个变电站, {county_cameras}个摄像头")

    conn.commit()
    conn.close()

    print()
    print("=" * 60)
    print("导入完成")
    print("=" * 60)
    print(f"总计: {total_stations}个变电站, {total_cameras}个摄像头")
    if errors:
        print(f"失败: {len(errors)}个文件")
        for e in errors[:10]:
            print(f"  {e['file']}: {e['error']}")
        if len(errors) > 10:
            print(f"  ... 还有 {len(errors) - 10} 个错误")

if __name__ == '__main__':
    main()
