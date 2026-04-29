#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_camera_location.py — 从故障描述中提取摄像头位置信息

对于无法匹配到camer表记录的，直接从描述中提取位置信息，
填入 fault_reports.camera_location_text 字段。
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import Config
from init_db import get_db_path
from utils import create_db_connection


def extract_camera_location(description):
    """
    从描述中提取摄像头位置信息。
    例如: "110kV场地西侧摄像头故障" -> "110kV场地西侧"
          "蓄电池室东北侧摄像头故障" -> "蓄电池室东北侧"
          "更换电容器室南侧摄像头" -> "电容器室南侧"
          "室外场地6个摄像头断电..." -> "室外场地"
    """
    if not description:
        return None

    camera_words = ['摄像头', '球机', '枪机', '摄像机', '云台']

    for cw in camera_words:
        idx = description.find(cw)
        if idx > 0:
            loc = description[:idx].strip()
            # 去掉末尾的分隔符
            loc = re.sub(r'[,，、\s]+$', '', loc)

            # 去掉开头的动词
            leading_verbs = ['更换', '维修', '检查', '查看', '处理', '修复', '重新', '新增', '安装', '调试']
            for verb in leading_verbs:
                if loc.startswith(verb):
                    loc = loc[len(verb):].strip()

            # 去掉末尾的数字+个/号等
            loc = re.sub(r'[\d]+个$', '', loc).strip()
            loc = re.sub(r'[\d]+号$', '', loc).strip()
            loc = re.sub(r'[\d]+#$', '', loc).strip()

            if loc:
                return loc

    return None


def main():
    print("=" * 60)
    print("提取摄像头位置信息")
    print("=" * 60)

    conn = create_db_connection(get_db_path(), row_factory=True, enable_wal=True)
    cursor = conn.cursor()

    # 获取未匹配且有摄像头关键词的记录
    patterns = ['摄像', '球机', '枪机', '云台', '机位']
    pattern_sql = ' OR '.join([f"description LIKE '%{p}%'" for p in patterns])

    records = cursor.execute(f'''
        SELECT id, description, camera_id
        FROM fault_reports
        WHERE camera_id IS NULL
        AND ({pattern_sql})
        ORDER BY id
    ''').fetchall()

    print(f"待处理记录: {len(records)}")

    updated = 0
    skipped = 0

    for rec in records:
        fault_id = rec['id']
        description = rec['description']

        loc = extract_camera_location(description)
        if loc:
            cursor.execute(
                'UPDATE fault_reports SET camera_location_text = ? WHERE id = ?',
                (loc, fault_id)
            )
            updated += 1
            if updated <= 15:
                print(f"  [{fault_id}] {loc}")
        else:
            skipped += 1

    conn.commit()

    print()
    print("=" * 60)
    print("结果")
    print("=" * 60)
    print(f"成功提取并更新: {updated} 条")
    print(f"跳过(无位置): {skipped} 条")

    # 验证
    with_loc = cursor.execute(
        'SELECT COUNT(*) FROM fault_reports WHERE camera_location_text IS NOT NULL'
    ).fetchone()[0]
    print(f"\ncamera_location_text 已填充: {with_loc} 条")

    conn.close()


if __name__ == '__main__':
    main()
