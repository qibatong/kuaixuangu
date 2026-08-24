import sys, os
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database
from app.services import kpl

d = "2026-08-24"
codes = ["002975","002412","603156","002667","300308","000657","002716"]
conn = database.get_conn()
print("code   表pct   sina收盘%   tencent收盘%")
for code in codes:
    row = conn.execute("SELECT pct FROM close_change_history WHERE date=? AND code=?", (d, code)).fetchone()
    dbpct = row[0] if row else None
    s = kpl._close_chg_pct_sina(d, code)
    t = kpl._close_chg_pct_tencent(d, code)
    print(f"{code}  {dbpct}   {s}   {t}")
conn.close()