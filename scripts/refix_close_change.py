import sys, os, time
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database
from app.services import kpl

DRY = "--dry" in sys.argv
conn = database.get_conn()
maxdate = conn.execute("SELECT MAX(date) FROM close_change_history").fetchone()[0]
rows = conn.execute("SELECT date, code, pct FROM close_change_history ORDER BY date ASC").fetchall()
conn.close()
print("total records:", len(rows), "maxdate:", maxdate)

def correct_pct(code, date):
    s = kpl._close_chg_pct_sina(date, code)
    t = kpl._close_chg_pct_tencent(date, code)
    if s is not None and t is not None and abs(s - t) <= 0.5:
        return s
    return None

updates = []; fixed = kept = dead = skipped = 0
for i, (date, code, cur) in enumerate(rows):
    if date > maxdate:
        skipped += 1; continue
    v = correct_pct(code, date)
    if v is None:
        dead += 1; continue
    if abs(v - (cur or 0)) > 0.05:
        updates.append((date, code, v)); fixed += 1
    else:
        kept += 1
    if i % 100 == 0:
        print(f"  ...{i}/{len(rows)} fixed={fixed} kept={kept} nolabel={dead}")
    time.sleep(0.03)

print(f"RESULT fixed={fixed} kept={kept} nolabel/skip={dead}+{skipped}")
if fixed:
    print("样例(前10条修正):")
    for (d, c, v) in updates[:10]:
        print("  ", d, c, v)
if not DRY and fixed:
    conn = database.get_conn()
    conn.executemany("UPDATE close_change_history SET pct=? WHERE date=? AND code=?",
                     [(v, d, c) for d, c, v in updates])
    conn.commit(); conn.close()
    print("WRITTEN", len(updates), "rows")
print("DRY=", DRY)