import sys, os, json
os.chdir('/opt/kuaixuan/backend')
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database

codes = ["002975","002412","603156","002667","300308","000657","002716","300750","002466","000506","300476"]
conn = database.get_conn()
print("--- close_change_history 近期各股 pct (最近5个日期) ---")
for code in codes:
    rows = conn.execute(
        "SELECT date, pct FROM close_change_history WHERE code=? ORDER BY date DESC LIMIT 5", (code,)).fetchall()
    print(f"  {code}:", [(str(r[0]), r[1]) for r in rows])
print("--- 该表日期分布 (近10个日期条数) ---")
for r in conn.execute("SELECT date, COUNT(*) FROM close_change_history GROUP BY date ORDER BY date DESC LIMIT 10").fetchall():
    print("  ", r[0], "=", r[1])
conn.close()