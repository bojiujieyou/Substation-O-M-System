"""
一次性迁移：将无 camera_slot_id 但有 camera_location_text 的故障记录
按位置描述匹配到对应的 camera_slot。

用法：
    python migrations/backfill_fault_camera_slots.py           # 正式执行
    python migrations/backfill_fault_camera_slots.py --dry-run # 只看结果不修改
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "station_monitor.db"

MATCH_THRESHOLD = 2  # 位置描述最少 2 个字符才参与匹配

STRIP_PREFIXES = ["更换", "维修", "修复", "新装", "安装", "拆除"]
STRIP_SUFFIXES = ["故障", "异常", "损坏", "断线"]


def normalize_location(loc: str) -> str:
    """去掉动作前缀和状态后缀，只保留位置描述。"""
    s = loc.strip()
    for prefix in STRIP_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for suffix in STRIP_SUFFIXES:
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            break
    return s.strip()


def simplify_slot_name(loc: str) -> str:
    """去掉摄像机编号后缀（如 -26#球机），返回纯位置名。"""
    return re.sub(r'-\d+#.*$', '', loc).strip()


def normalize_number(s: str) -> str:
    """归一化数字格式：#1主变 -> 1#主变, 1#主变 -> 1#主变。"""
    s = re.sub(r'#(\d)', r'\1#', s)
    return s


def _find_matches(text: str, slots: list[dict]) -> list[dict]:
    """用原始文本、去掉后缀的slot名、归一化数字后的文本做多轮匹配。"""
    matches = []
    for slot in slots:
        slot_loc = slot["location_desc"]
        if not slot_loc:
            continue
        slot_sim = simplify_slot_name(slot_loc)
        for a in (text, normalize_number(text)):
            for b in (slot_loc, slot_sim, normalize_number(slot_sim)):
                if len(a) >= MATCH_THRESHOLD and len(b) >= MATCH_THRESHOLD:
                    if a in b or b in a:
                        matches.append(slot)
                        break
            else:
                continue
            break
    # 去重
    seen = set()
    unique = []
    for m in matches:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique


def match_fault_to_slot(fault_loc: str, slots: list[dict]) -> dict | None:
    """返回唯一匹配的 slot，无匹配或多个匹配返回 None。"""
    raw = fault_loc.strip()
    cleaned = normalize_location(fault_loc)

    for text in (cleaned, raw):
        if len(text) < MATCH_THRESHOLD:
            continue
        matches = _find_matches(text, slots)
        if len(matches) == 1:
            return matches[0]

    return None


def main():
    parser = argparse.ArgumentParser(description="按位置描述回填故障记录的 camera_slot_id")
    parser.add_argument("--dry-run", action="store_true", help="只显示匹配结果，不修改数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"数据库不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    faults = conn.execute("""
        SELECT f.id, f.station_id, s.name AS station_name, f.camera_location_text
        FROM fault_reports f
        JOIN stations s ON f.station_id = s.id
        WHERE f.camera_slot_id IS NULL
          AND f.deleted_at IS NULL
          AND f.camera_location_text IS NOT NULL
          AND TRIM(f.camera_location_text) != ''
        ORDER BY f.station_id, f.id
    """).fetchall()

    print(f"待匹配故障: {len(faults)} 条\n")

    updated = 0
    skipped_multi = []
    skipped_none = []

    for fault in faults:
        slots = conn.execute(
            "SELECT id, location_desc FROM camera_slots WHERE station_id = ?",
            (fault["station_id"],),
        ).fetchall()

        matched = match_fault_to_slot(fault["camera_location_text"], [dict(s) for s in slots])

        if matched:
            updated += 1
            print(f"  匹配  ID={fault['id']:>3d} [{fault['station_name']}] "
                  f"\"{fault['camera_location_text']}\" -> slot {matched['id']} \"{matched['location_desc']}\"")
            if not args.dry_run:
                conn.execute(
                    "UPDATE fault_reports SET camera_slot_id = ? WHERE id = ?",
                    (matched["id"], fault["id"]),
                )
        else:
            cleaned = normalize_location(fault["camera_location_text"])
            all_matches = _find_matches(cleaned, [dict(s) for s in slots])
            if all_matches:
                skipped_multi.append((fault, all_matches))
            else:
                skipped_none.append(fault)

    if not args.dry_run and updated > 0:
        conn.commit()

    conn.close()

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}结果:")
    print(f"  已匹配: {updated}")
    print(f"  多个匹配(跳过): {len(skipped_multi)}")
    print(f"  无匹配(跳过): {len(skipped_none)}")

    if skipped_multi:
        print(f"\n--- 多个匹配的记录 ---")
        for fault, matches in skipped_multi:
            print(f"  ID={fault['id']:>3d} [{fault['station_name']}] \"{fault['camera_location_text']}\"")
            for m in matches:
                print(f"    -> slot {m['id']} \"{m['location_desc']}\"")

    if skipped_none:
        print(f"\n--- 无匹配的记录 ---")
        for fault in skipped_none:
            print(f"  ID={fault['id']:>3d} [{fault['station_name']}] \"{fault['camera_location_text']}\"")


if __name__ == "__main__":
    main()
