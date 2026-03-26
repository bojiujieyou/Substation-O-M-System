#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_camera_ids.py — 从故障描述中提取摄像头编号，更新fault_reports的camera_id字段

处理逻辑：
1. 从description中提取摄像头编号（如"3#摄像头"、"11#球机"等）
2. 根据station_id在cameras表中查找对应编号的摄像头
3. 如果没有编号，根据位置描述匹配（如"110kV场地西侧摄像头" -> 匹配 location_desc="110kV场地西侧-1#球机"）
4. 更新fault_reports.camera_id
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
from init_db import get_db_path, set_wal_mode


def extract_camera_indices(description):
    """从描述中提取摄像头编号列表。匹配模式: 3#, 11#, 1#-6# 等"""
    if not description:
        return []
    indices = []
    for m in re.finditer(r'(\d+)#', description):
        indices.append(int(m.group(1)))
    return list(dict.fromkeys(indices))


def extract_location_part(description):
    """
    从描述中提取摄像头位置部分。
    例如: "110kV场地西侧摄像头故障" -> "110kV场地西侧"
          "蓄电池室东北侧摄像头故障" -> "蓄电池室东北侧"
    """
    if not description:
        return None
    camera_words = ['摄像头', '球机', '枪机', '摄像机', '云台']
    for cw in camera_words:
        idx = description.find(cw)
        if idx > 0:
            loc = description[:idx].strip()
            loc = re.sub(r'[,，、\s]+$', '', loc)
            if loc:
                return loc
    return None


def build_camera_lookup_by_index(conn):
    """构建 lookup: (station_id, camera_index_str) -> camera_id"""
    cursor = conn.cursor()
    rows = cursor.execute('SELECT id, station_id, camera_index FROM cameras').fetchall()
    lookup = {}
    for cam_id, station_id, camera_index in rows:
        if camera_index is not None:
            key = (station_id, str(camera_index).strip())
            lookup[key] = cam_id
    return lookup


def build_camera_location_index(conn):
    """
    构建摄像头位置索引。

    location_desc 格式: "110kV场地西侧-1#球机"
    我们提取位置部分（去掉 -编号 后缀）："110kV场地西侧"
    并建立前缀索引支持模糊匹配。
    """
    cursor = conn.cursor()
    rows = cursor.execute('SELECT id, station_id, location_desc FROM cameras').fetchall()

    # 索引: (station_id, keyword) -> [camera_id]
    keyword_index = {}

    # 忽略的词（太通用或太短的）
    stop_words = {
        '西侧', '东侧', '南侧', '北侧',
        '西北侧', '东北侧', '东南侧', '西南侧',
        '西', '东', '南', '北', '侧',
        '上', '下', '前', '后', '内', '外',
        '1#', '2#', '3#', '4#', '5#', '6#', '7#', '8#', '9#', '10#',
        '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
        '球机', '枪机', '摄像机', '机位', '摄像头',
        '室', '变', '侧'
    }

    for cam_id, station_id, location_desc in rows:
        if not location_desc:
            continue

        # 提取位置部分（去掉 -1#球机, -2#机位 等后缀）
        loc = re.sub(r'[-‑][\d]*#.*$', '', location_desc).strip()
        if not loc:
            loc = location_desc.strip()

        loc_lower = loc.lower()

        # 1. 整个位置字符串作为关键词
        k = (station_id, loc_lower)
        if k not in keyword_index:
            keyword_index[k] = []
        keyword_index[k].append(cam_id)

        # 2. 位置字符串的前缀（越来越长）
        # "110kV场地西侧" -> "110kV", "110kV场", "110kV场地", ...
        for i in range(3, len(loc)):
            prefix = loc[:i].lower()
            k = (station_id, prefix)
            if k not in keyword_index:
                keyword_index[k] = []
            keyword_index[k].append(cam_id)

        # 3. 按分隔符拆分后的每个有意义的词
        parts = re.split(r'[,，、\-\s]+', loc)
        for part in parts:
            part = part.strip()
            if not part or len(part) < 2:
                continue
            if part in stop_words:
                continue
            k = (station_id, part.lower())
            if k not in keyword_index:
                keyword_index[k] = []
            keyword_index[k].append(cam_id)

    return keyword_index


def match_by_location(description, station_id, keyword_index):
    """
    根据位置描述匹配摄像头。
    例如: "110kV场地西侧摄像头" -> 找 station_id=64 的摄像头中 location_desc 位置部分为 "110kV场地西侧" 的
    返回: camera_id or None
    """
    loc = extract_location_part(description)
    if not loc:
        return None

    loc_lower = loc.lower().strip()

    # 策略1: loc整体作为关键词（精确位置匹配）
    k = (station_id, loc_lower)
    if k in keyword_index:
        cams = keyword_index[k]
        if len(cams) == 1:
            return cams[0]

    # 策略2: loc的前缀匹配（"110kV场地" 匹配 "110kV场地西侧"）
    for i in range(3, len(loc)):
        prefix = loc[:i].lower()
        k = (station_id, prefix)
        if k in keyword_index:
            cams = keyword_index[k]
            if len(cams) == 1:
                return cams[0]

    # 策略3: loc拆分后的关键词（从右往左取越来越短的词）
    # "110kV场地西侧" -> "场地西侧", "场地", "场"
    words = re.split(r'[,，、\-\s]+', loc)
    for word in reversed(words):
        word = word.strip()
        if len(word) < 3:
            continue
        if word in {'西侧', '东侧', '南侧', '北侧', '西北侧', '东北侧', '东南侧', '西南侧'}:
            continue
        k = (station_id, word.lower())
        if k in keyword_index:
            cams = keyword_index[k]
            if len(cams) == 1:
                return cams[0]

    return None


def main():
    print("=" * 60)
    print("更新故障记录的 camera_id (增强版)")
    print("=" * 60)

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    set_wal_mode(conn)
    cursor = conn.cursor()

    # 构建摄像头查找表
    camera_lookup = build_camera_lookup_by_index(conn)
    keyword_index = build_camera_location_index(conn)
    print(f"摄像头总数: {len(camera_lookup)}")
    print(f"位置关键词索引条目: {len(keyword_index)}")

    # 读取所有故障记录
    records = cursor.execute('''
        SELECT id, station_id, description, camera_id
        FROM fault_reports
        ORDER BY id
    ''').fetchall()

    print(f"故障记录总数: {len(records)}")

    # 统计
    updated_by_index = 0
    updated_by_location = 0
    skipped_no_desc = 0
    skipped_no_match = 0
    skipped_already_set = 0
    errors = 0
    updated_examples = []

    for rec in records:
        fault_id = rec['id']
        station_id = rec['station_id']
        description = rec['description']
        existing_camera_id = rec['camera_id']

        if not description:
            skipped_no_desc += 1
            continue

        if existing_camera_id is not None:
            skipped_already_set += 1
            continue

        matched_cam_id = None
        matched_method = None

        # 方法1: 按摄像头编号匹配
        indices = extract_camera_indices(description)
        for idx in indices:
            key = (station_id, str(idx))
            if key in camera_lookup:
                matched_cam_id = camera_lookup[key]
                matched_method = 'index'
                break

        # 方法2: 按位置描述匹配（仅当没有编号时）
        if matched_cam_id is None:
            matched_cam_id = match_by_location(description, station_id, keyword_index)
            if matched_cam_id:
                matched_method = 'location'

        if matched_cam_id:
            try:
                cursor.execute(
                    'UPDATE fault_reports SET camera_id = ? WHERE id = ?',
                    (matched_cam_id, fault_id)
                )
                if matched_method == 'index':
                    updated_by_index += 1
                else:
                    updated_by_location += 1

                if len(updated_examples) < 15:
                    updated_examples.append({
                        'fault_id': fault_id,
                        'camera_id': matched_cam_id,
                        'method': matched_method,
                        'station_id': station_id,
                        'description': description[:70]
                    })
            except Exception as e:
                errors += 1
                print(f"  Error updating fault_id={fault_id}: {e}")
        else:
            skipped_no_match += 1

    conn.commit()

    # 结果
    print()
    print("=" * 60)
    print("结果")
    print("=" * 60)
    print(f"按编号匹配更新: {updated_by_index} 条")
    print(f"按位置匹配更新: {updated_by_location} 条")
    print(f"已设置camera_id跳过: {skipped_already_set} 条")
    print(f"无描述跳过: {skipped_no_desc} 条")
    print(f"无匹配跳过: {skipped_no_match} 条")
    print(f"错误: {errors} 条")

    total_updated = updated_by_index + updated_by_location
    with_cam = cursor.execute('SELECT COUNT(*) FROM fault_reports WHERE camera_id IS NOT NULL').fetchone()[0]
    print(f"\n更新后有camera_id的记录: {with_cam} 条")

    if updated_examples:
        print("\n更新示例:")
        for ex in updated_examples:
            print(f"  [{ex['fault_id']}] cam_id={ex['camera_id']} [{ex['method']}] {ex['description']}")

    conn.close()


if __name__ == '__main__':
    main()