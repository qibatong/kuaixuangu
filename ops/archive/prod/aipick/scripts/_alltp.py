
import sys, os, sqlite3, json
sys.path.insert(0, "/opt/kuaixuan/aipick/scripts")
import collector
import pandas as pd, numpy as np
import xgboost as xgb
FEATURES = ["bid_change","bid_amount","bid_turnover","circ_mv","yesterday_chg","price"]
MODEL = "/opt/kuaixuan/aipick/models/model_xgb.json"
KX_DB = "/opt/kuaixuan/kuaixuan.db"
# 拉基准 stocks (bid_turnover / yesterday_chg / price 都用 fetch_from_kuaixuan 产出)
stocks_base = collector.fetch_from_kuaixuan("2026-08-27")
base_df = pd.DataFrame(stocks_base or [])
for col in FEATURES:
    base_df[col] = pd.to_numeric(base_df[col], errors="coerce")
base_map = {str(r.code): r for r in base_df.dropna(subset=FEATURES).itertuples()}
model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
model.load_model(MODEL)
def predict_with(tp, bc_override=None, ba_override=None):
    conn = sqlite3.connect(KX_DB)
    cur = conn.execute(
        "SELECT code, bid_change, bid_amt, float_mv FROM snapshot_bid "
        "WHERE date='2026-08-27' AND time_point=?", (tp,))
    snap = {r[0]: (float(r[1] or 0), float(r[2] or 0), float(r[3] or 0)) for r in cur.fetchall()}
    conn.close()
    ks = str("002418")
    bc, ba, fm = snap.get(ks, (None, None, None))
    if bc is None:
        print(f"tp={tp}: 康盛无快照")
        return
    bc = bc_override if bc_override is not None else bc
    ba = ba_override if ba_override is not None else ba
    row = base_map.get(ks)
    if row is None:
        print(f"tp={tp}: base_map 无康盛")
        return
    X = pd.DataFrame([{
        "bid_change": bc,
        "bid_amount": ba/1e4 if ba and ba>1e5 else ba,   # 若单位为分元则转万，否则照原值
        "bid_turnover": row.bid_turnover,
        "circ_mv": fm if fm>0 else row.circ_mv,
        "yesterday_chg": row.yesterday_chg,
        "price": row.price,
    }])
    Xb = pd.DataFrame([{
        "bid_change": bc,
        "bid_amount": ba/1e4 if ba and ba>1e5 else ba,
        "bid_turnover": 22.53,
        "circ_mv": 51.59,
        "yesterday_chg": 10.09,
        "price": 4.91,
    }])
    p = model.predict_proba(Xb[FEATURES].astype(float))[0,1]
    print(f"tp={tp}: bid_chg={bc:.2f}% bid_amt={ba:,.0f} 转万={ba/1e4:.0f}万 → prob={p*100:.2f}%  (turnover=22.53, circ_mv=51.59, ychg=10.09)")
# 四个时点
for tp in ("9_15","9_20","9_24","9_25"):
    predict_with(tp)
print("")
# 额外尝试: 63.9% 接近的 bid_chg 值扫描
print("=== 反推 bid_chg → 63.9% 附近 ===")
for bc in [0.5, 1.0, 1.5, 1.79, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
    X = pd.DataFrame([{"bid_change":bc,"bid_amount":8460.0,"bid_turnover":22.53,"circ_mv":51.59,"yesterday_chg":10.09,"price":4.91}])
    p = model.predict_proba(X[FEATURES].astype(float))[0,1]
    mark = "  ←★" if 0.62 < p < 0.66 else ""
    print(f"  bid_chg={bc:>5.2f}%  → prob={p*100:>5.2f}% {mark}")
