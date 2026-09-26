
import sys, os
sys.path.insert(0, "/opt/kuaixuan/aipick/scripts")
# 强制 time_point='9_24' 的快照
import collector
import predict_daily
# 读取 snapshot_bid 9_24 并构造 5554 只特征
KX_DB = "/opt/kuaixuan/kuaixuan.db"
import sqlite3, json
conn = sqlite3.connect(KX_DB)
cur = conn.execute(
    "SELECT code, name, bid_change, bid_amt, float_mv FROM snapshot_bid "
    "WHERE date='2026-08-27' AND time_point='9_24'")
rows_924 = {r[0]: {"code":r[0],"name":r[1],"bid_change":float(r[2] or 0),"bid_amount":float(r[3] or 0),"circ_mv":float(r[4] or 0)} for r in cur.fetchall()}
conn.close()
# 打印 9_24 康盛
print("9_24 康盛快照:", json.dumps(rows_924.get("002418"), ensure_ascii=False))
# 用 fetch_from_kuaixuan 拉全量(用 9_25)后把 bid_change/bid_amt 覆盖成 9_24 的
stocks = collector.fetch_from_kuaixuan("2026-08-27")
mapped = 0
for r in (stocks or []):
    k = str(r.get("code",""))
    if k in rows_924:
        r["bid_change"] = rows_924[k]["bid_change"]
        r["bid_amount"] = rows_924[k]["bid_amount"]
        # circ_mv 保留 9_25 的(都差不多)
        mapped += 1
print("用 9_24 覆盖 bid_chg/bid_amt %d 只" % mapped)
import pandas as pd, numpy as np
import xgboost as xgb
FEATURES = ["bid_change","bid_amount","bid_turnover","circ_mv","yesterday_chg","price"]
MODEL = "/opt/kuaixuan/aipick/models/model_xgb.json"
df = pd.DataFrame(stocks or [])
for col in FEATURES:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df = df.dropna(subset=FEATURES)
model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
model.load_model(MODEL)
proba = model.predict_proba(df[FEATURES].astype(float))[:, 1]
df["ai_prob"] = np.round(proba, 4)
r = df[df["code"].astype(str)=="002418"]
if len(r):
    row = r.iloc[0]
    for f in FEATURES:
        print(f"  {f}={row[f]}")
    print(f"→ 按 9_24 快照: ai_prob = {row['ai_prob']*100:.2f}%")
else:
    print("NOT IN DF")
