# -*- coding: utf-8 -*-
"""v3 正式训练（新配方）：全样本 LambdaRank 主排序器 + 大面头风险标签 + 校准。

主人 2026-10-03 裁决落地：
  · 核心 = **当日高命中**；大面**不进模型排序**，只作风险标签/告警
  · 新配方（实测）：LGB LambdaRank（全样本，按日分组）各 topN 全面最优；
                   LGB BCE 用**可买组样本**训练强化 top3
  · 校准：封板头 isotonic（valid 拟合）；大面头 Platt
  · 60 日窗口复核：最近 60 交易日、可买组、topN 命中/大面/收益（A 规则 + 破板卖近似）
🔴 产物写 `models/v3/`，**绝不覆盖线上 model_xgb.json / model_lgb.txt**。
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.environ.get('KX_BACKEND_DIR', '/opt/kuaixuan/backend'))
FEATURES = ['bid_change', 'bid_amount', 'bid_turnover', 'price', 'mv_rank', 'amt_rank',
            'rank_diff', 'price_inv', 'yday_zt', 'yday_lb', 'prev_mkt_zt']
FEAT_VER = 'v3_official_pricelimit_20261002'
OUT = '/opt/kuaixuan/aipick/models/v3'
TOPN = (3, 5, 10, 30)


def norm8(v):
    return ''.join(ch for ch in str(v) if ch.isdigit())[:8]


def dash(d8):
    return '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:])


def load(db):
    import numpy as np
    import pandas as pd
    c = sqlite3.connect(db)
    # is_break / zt_pool 在 label_truth（涨停池真值表）里，必须 join（2026-10-03 踩到）
    df = pd.read_sql_query(
        "SELECT f.trade_date, f.code, %s, f.is_limit_up_v3, f.close_chg_v2, f.close_chg, "
        "f.next_open_chg, COALESCE(t.is_break,0) AS is_break, COALESCE(t.zt,0) AS zt_pool, "
        "COALESCE(f.exclude_v3,'') AS ex "
        "FROM features f LEFT JOIN label_truth t "
        "  ON t.trade_date=f.trade_date AND t.code=f.code"
        % ','.join('f.' + x for x in FEATURES), c)
    pl = pd.read_sql_query("SELECT trade_date, code, up_limit FROM price_limit "
                           "WHERE up_limit IS NOT NULL", c)
    c.close()
    df['trade_date'] = df['trade_date'].map(norm8)
    pl['trade_date'] = pl['trade_date'].map(norm8)
    for x in (df, pl):
        x['code'] = x['code'].astype(str).str.zfill(6)
    d = df.merge(pl, on=['trade_date', 'code'], how='left')
    for col in FEATURES + ['is_limit_up_v3', 'close_chg_v2', 'close_chg', 'next_open_chg']:
        d[col] = pd.to_numeric(d[col], errors='coerce')
    d = d[(d['ex'] == '') & d['is_limit_up_v3'].notna()].copy()
    d['chg'] = d['close_chg_v2'].where(d['close_chg_v2'].notna(), d['close_chg'])
    d = d[d['chg'].notna() & d['bid_change'].notna() & d['price'].notna()
          & d[FEATURES].notna().all(axis=1)].copy()
    d['ret_nxt'] = ((1 + d['next_open_chg'].fillna(0) / 100) * (1 + d['chg'] / 100)
                    / (1 + d['bid_change'] / 100) - 1)
    d['y_zt'] = d['is_limit_up_v3'].astype(int)
    d['y_bf'] = (d['ret_nxt'] <= -0.05).astype(int)
    d['soft'] = ((d['zt_pool'] > 0) & (d['y_zt'] == 0)).astype(int)
    d['yidzi'] = ((d['up_limit'].notna()) & (d['price'] >= d['up_limit'] - 0.005)).astype(int)
    d['buyable'] = 1 - d['yidzi']
    return d.sort_values('trade_date').reset_index(drop=True)


def evaluate(d, score, name, n_days=60, buyable=None):
    """命中率 = 当日封板率（核心指标）。buyable: True 仅可买组 / False 全组 / None 两者都打。"""
    import numpy as np
    import pandas as pd
    if buyable is None:
        a = evaluate(d, score, name + ' [全组]', n_days, False)
        b = evaluate(d, score, name + ' [可买组]', n_days, True)
        return {'all': a, 'buyable': b}
    days = sorted(d['trade_date'].unique())[-n_days:]
    mask = d['trade_date'].isin(days).values & ((d['buyable'] == 1).values if buyable else True)
    t = d[mask].copy()
    t['s'] = score[mask]
    outs = {}
    for n in TOPN:
        picks = pd.concat([g.sort_values('s', ascending=False).head(n) for _, g in t.groupby('trade_date')])
        outs['top%d' % n] = {'hit': round(100 * picks['y_zt'].mean(), 1),
                             'bigface': round(100 * picks['y_bf'].mean(), 1),
                             'ret_A': round(100 * picks['ret_nxt'].mean(), 2),
                             'n': int(len(picks))}
    print("  %-24s %s" % (name, json.dumps(outs, ensure_ascii=False)))
    return outs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='/opt/kuaixuan/aipick/scripts/data/aipick.db')
    ap.add_argument('--valid-days', type=int, default=20)
    ap.add_argument('--test-days', type=int, default=20)
    a = ap.parse_args()
    import numpy as np
    import lightgbm as lgb
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression
    t0 = time.time()
    d = load(a.db)
    days = sorted(d['trade_date'].unique())
    print("样本 %d / %d 天（%s~%s）| 可买组 %.1f%% | 涨停率 %.2f%%"
          % (len(d), len(days), days[0], days[-1], 100 * d['buyable'].mean(), 100 * d['y_zt'].mean()))
    te_d, va_d = set(days[-a.test_days:]), set(days[-(a.test_days + a.valid_days):-a.test_days])
    tr = d[~d['trade_date'].isin(te_d | va_d)]
    va = d[d['trade_date'].isin(va_d)]
    te = d[d['trade_date'].isin(te_d)]

    def w_of(x):
        w = np.ones(len(x))
        w[x['soft'].values == 1] *= 0.5
        w[x['y_bf'].values == 1] *= 0.5
        return w

    print("\n=== 训练（%d 天）===" % tr['trade_date'].nunique())
    o = tr.sort_values('trade_date')
    ranker = lgb.train(dict(objective='lambdarank', metric='ndcg', ndcg_eval_at=[10], num_leaves=15,
                            learning_rate=0.05, feature_fraction=0.8, bagging_fraction=0.8,
                            min_child_samples=40, seed=7, verbose=-1),
                       lgb.Dataset(o[FEATURES].values, label=o['y_zt'].values,
                                   group=o.groupby('trade_date').size().values), num_boost_round=400)
    b = tr[tr['buyable'] == 1]
    zt_head = lgb.train(dict(objective='binary', metric='auc', num_leaves=15, learning_rate=0.05,
                             feature_fraction=0.8, bagging_fraction=0.8, min_child_samples=40,
                             seed=7, verbose=-1),
                        lgb.Dataset(b[FEATURES].values, label=b['y_zt'].values, weight=w_of(b)),
                        num_boost_round=400)
    bf_head = lgb.train(dict(objective='binary', metric='auc', num_leaves=15, learning_rate=0.05,
                             feature_fraction=0.8, bagging_fraction=0.8, min_child_samples=40,
                             seed=7, verbose=-1),
                        lgb.Dataset(tr[FEATURES].values, label=tr['y_bf'].values), num_boost_round=300)
    # 校准（valid 上拟合）
    iso = IsotonicRegression(out_of_bounds='clip').fit(
        zt_head.predict(va[FEATURES].values), va['y_zt'].values)
    plat = LogisticRegression().fit(bf_head.predict(va[FEATURES].values).reshape(-1, 1),
                                    va['y_bf'].values)
    from sklearn.metrics import roc_auc_score
    print("  valid AUC: ranker %.4f / 封板头 %.4f / 大面头 %.4f"
          % (roc_auc_score(va['y_zt'].values, ranker.predict(va[FEATURES].values)),
             roc_auc_score(va['y_zt'].values, iso.predict(zt_head.predict(va[FEATURES].values))),
             roc_auc_score(va['y_bf'].values, plat.predict_proba(bf_head.predict(va[FEATURES].values).reshape(-1, 1))[:, 1])))

    print("\n=== 测试集（%d 天）命中率=当日封板率（核心指标；全组/可买组并列）===" % te['trade_date'].nunique())
    evaluate(te, ranker.predict(te[FEATURES].values), 'v3 ranker（主）')
    evaluate(te, iso.predict(zt_head.predict(te[FEATURES].values)), 'v3 封板头（校准）')
    pr = ranker.predict(te[FEATURES].values)
    pz = iso.predict(zt_head.predict(te[FEATURES].values))
    mix = 0.5 * (np.argsort(np.argsort(pr)) / len(pr)) + 0.5 * (np.argsort(np.argsort(pz)) / len(pz))
    evaluate(te, mix, 'v3 名次平均')
    print("\n=== 60 日窗口复核（可买组）===")
    evaluate(d, ranker.predict(d[FEATURES].values), 'v3 ranker · 近60日')

    os.makedirs(OUT, exist_ok=True)
    ranker.save_model(os.path.join(OUT, 'ranker_v3.txt'))
    zt_head.save_model(os.path.join(OUT, 'zt_head_v3.txt'))
    bf_head.save_model(os.path.join(OUT, 'bf_head_v3.txt'))
    meta = {'trained_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'feature_ver': FEAT_VER,
            'features': FEATURES, 'n_train': int(len(tr)), 'train_days': int(tr['trade_date'].nunique()),
            'valid_days': a.valid_days, 'test_days': a.test_days,
            'label': 'is_limit_up_v3 (官方涨停价口径)', 'note': '大面不进排序, 仅风险标签',
            'models': {'ranker': 'ranker_v3.txt', 'zt_head': 'zt_head_v3.txt', 'bf_head': 'bf_head_v3.txt'}}
    with open(os.path.join(OUT, 'meta_v3.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("\n产物: %s（ranker/zt_head/bf_head + meta）| 线上模型未动 | 耗时 %.0fs" % (OUT, time.time() - t0))


if __name__ == '__main__':
    main()
