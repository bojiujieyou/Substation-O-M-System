# -*- coding: utf-8 -*-
import json, re, sys, sqlite3
sys.path.insert(0, r'e:\项目\变电站图像监控运维平台')
from config import Config

conn = sqlite3.connect(Config.DATABASE_PATH)
conn.row_factory = sqlite3.Row
db_stations = {r['id']: r['name'] for r in conn.execute('SELECT id, name FROM stations').fetchall()}
conn.close()

# Build short name -> full name mapping
short_to_full = {}
for sid, name in db_stations.items():
    short = re.sub(r'^\d+kV', '', name).strip()
    short_to_full[short] = name
    short_to_full[name] = name

with open(r'e:\项目\变电站图像监控运维平台\tmp_unmatched.json', encoding='utf-8') as f:
    unmatched = json.load(f)

unique = {}
for u in unmatched:
    n = u.get('station', '')
    if n and n not in unique:
        unique[n] = {
            'station': n,
            'type': u['type'],
            'content': u.get('content', ''),
            'in_db_exact': n in short_to_full,
            'fuzzy_matches': [v for k, v in short_to_full.items() if (n in k or k in n) and k != n][:5]
        }

result = {
    'total_unmatched': len(unmatched),
    'unique_station_names': len(unique),
    'stations': list(unique.values()),
    'db_stations': list(db_stations.values()),
    'db_short_names': list(short_to_full.keys())
}

with open(r'e:\项目\变电站图像监控运维平台\tmp_match_analysis.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print('done')
