import sys, os, time
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database
from app.services import kpl

target = sys.argv[1] if len(sys.argv) > 1 else "2026-08-24"
DRY = "--dry" in sys.argv
conn = database.get_conn()
rows = conn.execute("SELECT code, pct FROM close_change_history WHERE date=?",
                    (target,)).fetchall()
conn.close()
print(f"target {target} records:", len(rows), flush=True)

def correct_pct(code):
    s = kpl._close_chg_pct_sina(target, code)
    if s is None or abs(s) > 40:   # 异常值防御
        return None
    return round(s, 2)

updates = []; fixed = kept = dead = 0
for i, (code, cur) in enumerate(rows):
    v = correct_pct(code)
    if v is None:
        dead += 1
    elif abs(v - (cur or 0)) > 0.05:
        updates.append((code, v)); fixed += 1
    else:
        kept += 1
    if i % 50 == 0:
        print(f"  ...{i}/{len(rows)} fixed={fixed} kept={kept} nolabel={dead}", flush=True)
    time.sleep(0.03)

print(f"RESULT fixed={fixed} kept={kept} nolabel={dead}", flush=True)
if updates:
    for (c, v) in updates[:12]:
        print("  FIX", target, c, v, flush=True)
if not DRY and updates:
    conn = database.get_conn()
    conn.executemany("UPDATE close_change_history SET pct=? WHERE date=? AND code=?",
                     [(v, target, c) for c, v in updates])
    conn.commit(); conn.close()
    print("WRITTEN", len(updates), "rows", flush=True)
print("DONE", flush=True)