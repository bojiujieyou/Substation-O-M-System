# -*- coding: utf-8 -*-
import json, re, sys, sqlite3
sys.path.insert(0, r'e:\项目\变电站图像监控运维平台')
from config import Config

conn = sqlite3.connect(Config.DATABASE_PATH)
conn.row_factory = sqlite3.Row
db_stations = [(r['id'], r['name']) for r in conn.execute('SELECT id, name FROM stations').fetchall()]
conn.close()

db_short_names = set()
for sid, name in db_stations:
    short = re.sub(r'^\d+kV', '', name).strip()
    db_short_names.add(short)
    db_short_names.add(name)

print('DB station short names:')
for n in sorted(db_short_names):
    print(' ', n)

with open(r'e:\项目\变电站图像监控运维平台\tmp_unmatched.json', encoding='utf-8') as f:
    unmatched = json.load(f)

unique_names = set(u['station'] for u in unmatched if u['station'])
print(f'\nUnique unmatched names ({len(unique_names)}):')
for n in sorted(unique_names):
    # Check if it's in DB at all
    in_db = n in db_short_names
    # fuzzy check
    fuzzy = [s for s in db_short_names if n in s or s in n]
    print(f'  "{n}" -> in_db={in_db}, fuzzy={fuzzy[:3]}')
