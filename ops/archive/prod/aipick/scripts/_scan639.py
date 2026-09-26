
import sys, itertools
sys.path.insert(0, "/opt/kuaixuan/aipick/scripts")
import pandas as pd, numpy as np
import xgboost as xgb
FEATURES = ["bid_change","bid_amount","bid_turnover","circ_mv","yesterday_chg","price"]
MODEL = "/opt/kuaixuan/aipick/models/model_xgb.json"
model = xgb.XGBClassifier(use_label_encoder=False, verbosity=0)
model.load_model(MODEL)
# 基础: 康盛 9_25 特征
BASE = dict(bid_change=1.79, bid_amount=8460.0, bid_turnover=22.53, circ_mv=51.59, yesterday_chg=10.09, price=4.91)
def pred(**kw):
    d = dict(BASE); d.update(kw)
    X = pd.DataFrame([d])
    return float(model.predict_proba(X[FEATURES].astype(float))[0,1])
# 找 63.9% 的组合: 粗扫 bid_turnover × bid_change
print("=== bid_turnover vs bid_chg → 最接近 63.9% ===")
best = None
for to in np.arange(0.1, 25.0, 0.5):
    for bc in np.arange(0.5, 11.0, 0.25):
        p = pred(bid_change=bc, bid_turnover=float(to))
        diff = abs(p - 0.639)
        if best is None or diff < best[0]:
            best = (diff, to, bc, p)
            if diff < 0.002:
                print(f"  turnover={to:>5.2f}%  bid_chg={bc:>5.2f}%  →  prob={p*100:>5.2f}%   diff={diff:.4f}")
print(f"BEST: turnover={best[1]:.2f}% bid_chg={best[2]:.2f}% → prob={best[3]*100:.2f}%  误差={best[0]*100:.2f}%")
# 再细扫 turnover vs circ_mv vs bid_amount
print("\n=== 细扫 turnover × circ_mv → 最接近 63.9% (bc=4.0%固定) ===")
best2=None
for to in np.arange(18.0,24.0,0.2):
    for mv in np.arange(30.0, 70.0, 1.0):
        for amt in np.arange(2000,12000,500):
            p = pred(bid_change=4.0, bid_turnover=float(to), circ_mv=float(mv), bid_amount=float(amt))
            diff = abs(p - 0.639)
            if best2 is None or diff < best2[0]:
                best2 = (diff, to, mv, amt, p)
                if diff < 0.0005:
                    print(f"  to={to:>5.2f}% mv={mv:>5.1f}亿 amt={amt:>6.0f}万  → prob={p*100:>5.2f}% diff={diff:.5f}")
print(f"BEST: to={best2[1]:.2f}% mv={best2[2]:.1f}亿 amt={best2[3]:.0f}万  → prob={best2[4]*100:.2f}% err={best2[0]*10000:.1f}bps")
# 还有一种可能：模型不同。8/26 训练旧模型(训练时间 8/26 19:00) vs 8/27 18:59 新训练模型
# 查 model_xgb.json 备份 & 若有旧模型跑康盛 bid_*=(1.79, 8460万) 的概率
