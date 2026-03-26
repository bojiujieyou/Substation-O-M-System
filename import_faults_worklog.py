#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_faults_worklog.py — 将工作记录.xlsx导入为故障记录

数据源: e:\办公\图像监控\工作记录.xlsx
目标: fault_reports表

导入范围: 系统类型 in {图像监控, 智能巡视, 辅控系统}

字段映射:
  变电站(col2)      -> station_id (模糊匹配, 支持多站 / 或 、分隔)
  类型(col5)        -> system_type
  工作内容(col4)    -> description
  地点(col3)        -> 追加到description
  时间(col1)        -> created_at, closed_at
  工作负责人(col7)  -> handler_name
  (自动推断)        -> fault_type
  (固定: closed)    -> status
"""

import re
import sys
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime

import openpyxl

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
from init_db import get_db_path, set_wal_mode

SOURCE_FILE = r'e:\办公\图像监控\工作记录.xlsx'
TARGET_TYPES = {'图像监控', '智能巡视', '辅控系统'}


def parse_time(val):
    """解析时间字符串, 返回 'YYYY-MM-DD' 或 None"""
    if val is None:
        return None
    s = str(val).strip()
    # 处理范围如 '2022年8月22日-26日', 取第一个日期
    s = s.split('-')[0].split('~')[0].split('至')[0].strip()
    # 'YYYY年M月D日'
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # openpyxl 可能直接返回 datetime
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
    """构建站名查找表: 短名 -> [(id, full_name), ...]"""
    rows = conn.execute('SELECT id, name FROM stations').fetchall()
    lookup = {}  # short_name -> list of (id, full_name)
    for r in rows:
        full = r[0] if isinstance(r, (list, tuple)) else r['id']
        # sqlite3.Row
        sid = r[0]
        name = r[1]
        # 短名: 去掉电压等级前缀
        short = re.sub(r'^\d+kV', '', name).strip()
        if short not in lookup:
            lookup[short] = []
        lookup[short].append((sid, name))
        # 也加入完整名
        if name not in lookup:
            lookup[name] = []
        lookup[name].append((sid, name))
    return lookup


def find_station_ids(station_str, lookup):
    """
    从工作记录的变电站字段匹配数据库station_id列表。
    支持 '水阁变/龙石变'、'景宁变、壶镇变' 等多站格式。
    返回: [(station_id, matched_name), ...]
    """
    if not station_str:
        return []
    # 分割多站
    parts = re.split(r'[/、,，]', str(station_str))
    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in lookup:
            # 精确匹配
            for sid, name in lookup[part]:
                results.append((sid, name, part))
        else:
            # 模糊: 包含关系
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
    raw = f"{station_id}|{time_str}|{content or ''}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:24]


def main():
    print("=" * 60)
    print("工作记录 -> 故障记录 导入脚本")
    print("=" * 60)

    # 读取Excel
    print(f"读取: {SOURCE_FILE}")
    wb = openpyxl.load_workbook(SOURCE_FILE, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    print(f"总行数: {len(rows)} (含标题行)")

    # 过滤目标类型
    target_rows = []
    for r in rows[2:]:  # 跳过标题行和年份行
        if r[5] and str(r[5]).strip() in TARGET_TYPES:
            if any(c is not None for c in r[:8]):
                target_rows.append(r)
    print(f"目标记录数 (系统类型 in {TARGET_TYPES}): {len(target_rows)}")

    # 连接数据库
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    set_wal_mode(conn)
    cursor = conn.cursor()

    # 构建站名映射
    lookup = build_station_lookup(conn)
    print(f"数据库变电站数: {len(set(sid for vals in lookup.values() for sid, _ in vals))}")

    # 统计
    inserted = 0
    skipped_dup = 0
    unmatched_stations = []
    errors = []

    for r in target_rows:
        seq = r[0]
        time_str = parse_time(r[1])
        station_str = str(r[2]).strip() if r[2] else ''
        location = str(r[3]).strip() if r[3] else ''
        content = str(r[4]).strip() if r[4] else ''
        system_type = str(r[5]).strip() if r[5] else ''
        handler = str(r[7]).strip() if r[7] else None

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
            unmatched_stations.append({'seq': seq, 'station': station_str})
            continue

        for station_id, matched_name, raw_part in matches:
            if station_id is None:
                unmatched_stations.append({'seq': seq, 'station': raw_part})
                continue

            ikey = make_idempotency_key(station_id, time_str or '', description)

            # 检查幂等
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
    conn.close()

    print()
    print("=" * 60)
    print("导入完成")
    print("=" * 60)
    print(f"成功插入: {inserted} 条")
    print(f"重复跳过: {skipped_dup} 条")
    print(f"未匹配站: {len(unmatched_stations)} 条")
    if unmatched_stations:
        print("  未匹配变电站列表:")
        seen = set()
        for u in unmatched_stations:
            key = u['station']
            if key not in seen:
                print(f"    seq={u['seq']} station='{u['station']}'")
                seen.add(key)
    if errors:
        print(f"错误: {len(errors)} 条")
        for e in errors[:10]:
            print(f"  {e}")

    return inserted, unmatched_stations


if __name__ == '__main__':
    main()
