#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
full_import_worklog.py — 完整导入工作记录到故障记录表

处理:
1. 添加缺失的22个电站到stations表
2. 拆分复合电站名 (如 "峰源变/雅溪变")
3. 跳过非电站条目 (缙云, 遂昌公司 等)
4. 跳过空电站名记录
5. 完整导入所有三种系统类型 (图像监控, 智能巡视, 辅控系统)
6. 验证导入结果
"""

import re
import sys
import hashlib
import sqlite3
import openpyxl
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
from init_db import get_db_path, set_wal_mode

SOURCE_FILE = r'e:\办公\图像监控\工作记录.xlsx'
TARGET_TYPES = {'图像监控', '智能巡视', '辅控系统'}

# 需要添加到数据库的22个电站
# 格式: (短名, 电压等级, 县区)
STATIONS_TO_ADD = [
    ("东方变",    "110kV", "青田"),
    ("仙宫变",    "110kV", "云和"),
    ("北界变",    "35kV",  "丽水"),
    ("叶村变",    "35kV",  "松阳"),
    ("城关变",    "35kV",  "松阳"),
    ("大东坝变",  "35kV",  "云和"),
    ("大源变",    "35kV",  "缙云"),
    ("妙高变",    "35kV",  "丽水"),
    ("寿元变",    "110kV", "丽水"),
    ("小顺变",    "35kV",  "云和"),
    ("新兴变",    "110kV", "松阳"),
    ("椤林变",    "110kV", "遂昌"),
    ("汤公变",    "110kV", "遂昌"),
    ("玉岩变",    "110kV", "松阳"),
    ("王村口变",  "35kV",  "遂昌"),
    ("紧水滩变",  "110kV", "云和"),
    ("若寮变",    "35kV",  "松阳"),
    ("象溪变",    "110kV", "松阳"),
    ("赤寿变",    "35kV",  "松阳"),
    ("靖居变",    "35kV",  "松阳"),
    ("黄岗变",    "35kV",  "龙泉"),
    ("黄沙腰变",  "35kV",  "遂昌"),
    ("峰源变",    "35kV",  "莲都"),
    ("雅溪变",    "35kV",  "莲都"),
    ("船寮变",    "35kV",  "青田"),
]

# 已知在DB中不需要添加的电站名（精确匹配）
SKIP_STATIONS = {'缙云', '遂昌公司', '丽水', '湖州', '钼矿变'}


def parse_time(val):
    """解析时间字符串, 返回 'YYYY-MM-DD' 或 None"""
    if val is None:
        return None
    s = str(val).strip()
    s = s.split('-')[0].split('~')[0].split('至')[0].strip()
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    return None


def infer_fault_type(content, system_type):
    """根据描述推断故障类型"""
    if not content:
        return '设备故障'
    c = str(content)
    if any(k in c for k in ['网络', '断网', '掉线', '通信']):
        return '网络故障'
    return '设备故障'


def build_station_lookup(conn):
    """构建站名查找表: 短名 -> list of (id, full_name)"""
    rows = conn.execute('SELECT id, name FROM stations').fetchall()
    lookup = {}
    for sid, name in rows:
        short = re.sub(r'^\d+kV', '', name).strip()
        if short not in lookup:
            lookup[short] = []
        lookup[short].append((sid, name))
        if name not in lookup:
            lookup[name] = []
        lookup[name].append((sid, name))
    return lookup


def find_station_ids(station_str, lookup):
    """
    从工作记录的变电站字段匹配数据库station_id列表。
    支持 '水阁变/龙石变'、'景宁变、壶镇变' 等多站格式。
    返回: [(station_id, matched_name, raw_part), ...]
    """
    if not station_str:
        return []
    parts = re.split(r'[/、,，]', str(station_str))
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in lookup:
            for sid, name in lookup[part]:
                results.append((sid, name, part))
        else:
            matched = False
            for key, vals in lookup.items():
                if part in key or key in part:
                    for sid, name in vals:
                        results.append((sid, name, part))
                    matched = True
                    break
            if not matched:
                results.append((None, None, part))
    return results


def make_idempotency_key(station_id, time_str, content):
    raw = f"{station_id}|{time_str or ''}|{content or ''}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:24]


def add_missing_stations(conn):
    """添加缺失的电站到数据库"""
    cursor = conn.cursor()
    added = []
    skipped = []

    for short_name, voltage_level, county in STATIONS_TO_ADD:
        full_name = f"{voltage_level}{short_name}"

        # 检查是否已存在
        existing = cursor.execute(
            'SELECT id FROM stations WHERE name = ?', (full_name,)
        ).fetchone()
        if existing:
            skipped.append(full_name)
            continue

        try:
            cursor.execute("""
                INSERT INTO stations (name, voltage_level, county)
                VALUES (?, ?, ?)
            """, (full_name, voltage_level, county))
            added.append(full_name)
        except Exception as e:
            skipped.append(f"{full_name} (error: {e})")

    conn.commit()
    return added, skipped


def main():
    print("=" * 60)
    print("工作记录 -> 故障记录 完整导入")
    print("=" * 60)

    # Step 1: 连接数据库并添加缺失电站
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    set_wal_mode(conn)
    cursor = conn.cursor()

    print("\n[Step 1] 添加缺失电站...")
    added, skipped = add_missing_stations(conn)
    print(f"  新增电站: {len(added)} 个")
    for a in added:
        print(f"    + {a}")
    if skipped:
        print(f"  跳过(已存在): {len(skipped)} 个")

    # Step 2: 读取Excel
    print(f"\n[Step 2] 读取工作记录: {SOURCE_FILE}")
    wb = openpyxl.load_workbook(SOURCE_FILE, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    print(f"  总行数: {len(rows)}")

    # 过滤目标类型
    target_rows = []
    for r in rows[2:]:
        if r[5] and str(r[5]).strip() in TARGET_TYPES:
            if any(c is not None for c in r[:8]):
                target_rows.append(r)
    print(f"  目标记录数: {len(target_rows)} (系统类型 in {TARGET_TYPES})")

    # Step 3: 重新构建站名映射(包含新加的电站)
    lookup = build_station_lookup(conn)
    db_station_count = len(set(sid for vals in lookup.values() for sid, _ in vals))
    print(f"\n[Step 3] 数据库变电站总数: {db_station_count}")

    # Step 4: 导入
    print("\n[Step 4] 开始导入...")
    inserted = 0
    skipped_dup = 0
    skipped_no_station = 0
    skipped_non_station = 0
    errors = []
    unmatched = []

    for r in target_rows:
        seq = r[0]
        time_str = parse_time(r[1])
        station_str = str(r[2]).strip() if r[2] else ''
        location = str(r[3]).strip() if r[3] else ''
        content = str(r[4]).strip() if r[4] else ''
        system_type = str(r[5]).strip()
        handler = str(r[7]).strip() if r[7] else None

        # 跳过空白电站名
        if not station_str:
            skipped_no_station += 1
            continue

        # 跳过非电站条目
        if station_str in SKIP_STATIONS:
            skipped_non_station += 1
            continue

        # 构建description
        desc_parts = []
        if content:
            desc_parts.append(content)
        if location and location not in content:
            desc_parts.append(f'地点: {location}')
        description = ' | '.join(desc_parts) if desc_parts else content

        fault_type = infer_fault_type(content, system_type)

        # 匹配变电站
        matches = find_station_ids(station_str, lookup)

        if not matches:
            unmatched.append({'seq': seq, 'station': station_str, 'type': system_type})
            continue

        for station_id, matched_name, raw_part in matches:
            if station_id is None:
                # 跳过非电站条目
                if raw_part in SKIP_STATIONS:
                    continue
                unmatched.append({'seq': seq, 'station': raw_part, 'type': system_type})
                continue

            ikey = make_idempotency_key(station_id, time_str or '', description)

            existing = cursor.execute(
                'SELECT id FROM fault_reports WHERE idempotency_key = ?', (ikey,)
            ).fetchone()
            if existing:
                skipped_dup += 1
                continue

            try:
                cursor.execute("""
                    INSERT INTO fault_reports (
                        station_id, system_type, fault_type, description,
                        reporter_name, handler_name, status,
                        closed_at, created_at, updated_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, 'closed', ?, ?, ?, ?)
                """, (
                    station_id,
                    system_type,
                    fault_type,
                    description,
                    '工作记录导入',
                    handler,
                    time_str,
                    time_str,
                    time_str,
                    ikey,
                ))
                inserted += 1
            except Exception as e:
                errors.append({'seq': seq, 'station': station_str, 'error': str(e)})

    conn.commit()

    # Step 5: 验证
    print("\n" + "=" * 60)
    print("导入结果")
    print("=" * 60)
    print(f"成功插入: {inserted} 条")
    print(f"重复跳过: {skipped_dup} 条")
    print(f"空白电站: {skipped_no_station} 条")
    print(f"非电站跳过: {skipped_non_station} 条")

    # 验证统计
    total_in_db = cursor.execute('SELECT COUNT(*) FROM fault_reports').fetchone()[0]
    print(f"\n数据库故障记录总数: {total_in_db}")

    # 按系统类型统计
    type_dist = cursor.execute(
        'SELECT system_type, COUNT(*) FROM fault_reports GROUP BY system_type'
    ).fetchall()
    print("系统类型分布:")
    for r in type_dist:
        print(f"  {r[0]}: {r[1]}")

    # 按电站统计前10
    print("\n前10个电站故障数:")
    top_stations = cursor.execute("""
        SELECT s.name, COUNT(f.id) as cnt
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        GROUP BY s.name
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    for r in top_stations:
        print(f"  {r[0]}: {r[1]}")

    conn.close()

    if unmatched:
        print(f"\n未匹配电站: {len(unmatched)} 条 (共 {len(set(u['station'] for u in unmatched))} 个电站)")
        seen = set()
        for u in unmatched:
            key = u['station']
            if key not in seen:
                print(f"  [{u['type']}] {u['station']} (seq={u['seq']})")
                seen.add(key)

    if errors:
        print(f"\n错误: {len(errors)} 条")
        for e in errors[:10]:
            print(f"  {e}")

    return inserted


if __name__ == '__main__':
    main()