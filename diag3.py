import sys, os
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.services import auction_snapshot, kpl
from app.db import database

conn = database.get_conn()
d = conn.execute("SELECT MAX(date) FROM snapshot_bid").fetchone()[0]
print("snapshot_bid max date:", d)
cnt = conn.execute("SELECT COUNT(*) FROM close_change_history WHERE date=?", (d,)).fetchone()[0]
print("close_change_history rows for", d, "=", cnt)
conn.close()

rows = auction_snapshot.query_3points_board(d, 6)
print("3points rows(before apply):", len(rows))
for it in rows[:6]:
    p25 = (it.get('points') or {}).get('9_25') or {}
    print(" ", it['code'], it['name'],
          "| real_change=", it.get('real_change'),
          "| realChange=", it.get('realChange'),
          "| change=", it.get('change'),
          "| 9_25 bid_change=", p25.get('bid_change'))

# 重放 _apply_change_stats 逻辑（历史/盘后分支）
from app.api.stats import _apply_change_stats
_apply_change_stats(rows, d)
print("rows(after apply):")
for it in rows[:6]:
    print(" ", it['code'], it['name'],
          "| real_change=", it.get('real_change'),
          "| realChange=", it.get('realChange'),
          "| change=", it.get('change'))